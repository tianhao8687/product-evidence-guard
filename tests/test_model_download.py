from __future__ import annotations

import errno
import json
from pathlib import Path
import tempfile
import unittest

from scripts.model_download import (
    DOWNLOAD_DISK_RESERVE_BYTES,
    DownloadSpec,
    download_resource_preflight,
    download_model,
    is_retryable_download_error,
    load_download_spec,
    verify_model_directory,
)


class ModelDownloadTests(unittest.TestCase):
    def test_official_info_aliases_resolve_to_the_same_download_spec(self) -> None:
        spec = load_download_spec()

        self.assertEqual(
            spec.model_id,
            "OpenVINO/Qwen3-VL-8B-Instruct-int4-ov",
        )
        self.assertEqual(
            spec.target.name,
            "Qwen3-VL-8B-Instruct-int4-ov",
        )
        self.assertEqual(spec.expected_size_bytes, 5_460_000_000)
        self.assertEqual(spec.minimum_memory_bytes, 16_000_000_000)

    def test_incomplete_snapshot_stays_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model"
            spec = DownloadSpec(
                model_id="owner/model",
                target=target,
                required_files=("config.json", "model.bin"),
                revision="abc",
                runtime_dir=Path(tmp) / "runtime",
            )

            def fake_download(**kwargs: str) -> str:
                partial = Path(kwargs["local_dir"])
                (partial / "config.json").write_text("{}", encoding="utf-8")
                return str(partial)

            result = download_model(spec, downloader=fake_download)
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["retryable"])
            self.assertFalse(target.exists())
            self.assertTrue(target.with_name("model.partial").exists())

    def test_complete_snapshot_is_atomically_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "model"
            spec = DownloadSpec(
                model_id="owner/model",
                target=target,
                required_files=("config.json", "model.bin"),
                revision="abc",
                runtime_dir=Path(tmp) / "runtime",
            )

            def fake_download(**kwargs: str) -> str:
                partial = Path(kwargs["local_dir"])
                (partial / "config.json").write_text("{}", encoding="utf-8")
                (partial / "model.bin").write_bytes(b"weights")
                return str(partial)

            result = download_model(spec, downloader=fake_download)
            self.assertEqual(result["status"], "ready")
            self.assertTrue(target.is_dir())
            self.assertFalse(target.with_name("model.partial").exists())

    def test_lfs_pointer_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "model.bin").write_text(
                "version https://git-lfs.github.com/spec/v1\noid sha256:abc\n",
                encoding="utf-8",
            )
            errors = verify_model_directory(root, ("model.bin",))
            self.assertTrue(any("LFS" in item for item in errors))

    def test_retryable_network_failure_stays_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = DownloadSpec(
                model_id="owner/model",
                target=root / "model",
                required_files=("config.json",),
                revision="abc",
                runtime_dir=root / "runtime",
            )

            def interrupted(**_kwargs: str) -> str:
                raise TimeoutError("network timed out")

            result = download_model(spec, downloader=interrupted)
            self.assertEqual(result["status"], "downloading")
            self.assertTrue(result["retryable"])
            pending = (spec.runtime_dir / "pending-request.json").read_text(
                encoding="utf-8"
            )
            self.assertIn('"status": "downloading"', pending)

    def test_permanent_download_failure_is_not_reported_as_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = DownloadSpec(
                model_id="owner/model",
                target=root / "model",
                required_files=("config.json",),
                revision="bad",
                runtime_dir=root / "runtime",
            )

            class RevisionNotFoundError(RuntimeError):
                pass

            def invalid_revision(**_kwargs: str) -> str:
                raise RevisionNotFoundError("invalid revision")

            result = download_model(spec, downloader=invalid_revision)
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["retryable"])
            state = (spec.runtime_dir / "download-state.json").read_text(
                encoding="utf-8"
            )
            self.assertIn('"status": "error"', state)

    def test_disk_full_and_auth_are_permanent_but_timeout_is_retryable(self) -> None:
        self.assertFalse(is_retryable_download_error(PermissionError("forbidden")))
        self.assertTrue(is_retryable_download_error(TimeoutError("timed out")))
        self.assertFalse(
            is_retryable_download_error(OSError(errno.ENOSPC, "disk full"))
        )

        class HttpFailure(RuntimeError):
            def __init__(self, status_code: int) -> None:
                self.response = type("Response", (), {"status_code": status_code})()
                super().__init__(f"HTTP {status_code}")

        self.assertTrue(is_retryable_download_error(HttpFailure(503)))
        self.assertFalse(is_retryable_download_error(HttpFailure(404)))

    def test_download_state_never_overwrites_resident_server_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir()
            server_state = runtime / "server-state.json"
            original = '{"status":"running","pid":4242,"startup_id":"keep"}\n'
            server_state.write_text(original, encoding="utf-8")
            spec = DownloadSpec(
                model_id="owner/model",
                target=root / "model",
                required_files=("config.json",),
                revision="abc",
                runtime_dir=runtime,
            )

            result = download_model(
                spec,
                downloader=lambda **_kwargs: (_ for _ in ()).throw(
                    TimeoutError("network timed out")
                ),
            )
            self.assertEqual(result["status"], "downloading")
            self.assertEqual(server_state.read_text(encoding="utf-8"), original)
            self.assertFalse(
                json.loads(
                    (runtime / "download-state.json").read_text(encoding="utf-8")
                )["active"]
            )

    def test_complete_target_is_read_only_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "model"
            target.mkdir()
            config = target / "config.json"
            config.write_text("{}", encoding="utf-8")
            original_mtime = config.stat().st_mtime_ns
            spec = DownloadSpec(
                model_id="owner/model",
                target=target,
                required_files=("config.json",),
                runtime_dir=root / "runtime",
            )
            called = False

            def forbidden(**_kwargs: str) -> str:
                nonlocal called
                called = True
                return str(target)

            result = download_model(spec, downloader=forbidden)
            self.assertEqual(result["status"], "ready")
            self.assertFalse(result["downloaded"])
            self.assertFalse(called)
            self.assertEqual(config.stat().st_mtime_ns, original_mtime)

    def test_incomplete_formal_target_is_terminal_and_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "model"
            target.mkdir()
            marker = target / "user-file.txt"
            marker.write_text("preserve", encoding="utf-8")
            spec = DownloadSpec(
                model_id="owner/model",
                target=target,
                required_files=("config.json",),
                runtime_dir=root / "runtime",
            )
            called = False

            def forbidden(**_kwargs: str) -> str:
                nonlocal called
                called = True
                return str(target)

            result = download_model(spec, downloader=forbidden)
            self.assertEqual(result["status"], "error")
            self.assertFalse(result["retryable"])
            self.assertFalse(called)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_resource_preflight_blocks_download_before_disk_or_memory_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = DownloadSpec(
                model_id="owner/model",
                target=root / "model",
                required_files=("config.json",),
                runtime_dir=root / "runtime",
                expected_size_bytes=10_000,
                minimum_memory_bytes=20_000,
            )
            called = False

            def forbidden(**_kwargs: str) -> str:
                nonlocal called
                called = True
                return str(spec.target)

            def failed_probe(_spec: DownloadSpec) -> dict[str, object]:
                return {
                    "ok": False,
                    "disk_free_bytes": 1,
                    "disk_required_bytes": 10_000,
                    "memory_available_bytes": 2,
                    "memory_required_bytes": 20_000,
                    "errors": ["磁盘空间不足", "可用物理内存不足"],
                }

            result = download_model(
                spec,
                downloader=forbidden,
                resource_probe=failed_probe,
            )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["code"], "resource_preflight_failed")
            self.assertFalse(result["retryable"])
            self.assertFalse(called)

    def test_partial_payload_reduces_remaining_download_disk_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "model"
            partial = root / "model.partial"
            partial.mkdir()
            (partial / "downloaded.bin").write_bytes(b"x" * 600)
            spec = DownloadSpec(
                model_id="owner/model",
                target=target,
                required_files=("config.json",),
                expected_size_bytes=1_000,
                minimum_memory_bytes=2_000,
            )
            usage = type("Usage", (), {"free": 10_000_000_000})()
            result = download_resource_preflight(
                spec,
                disk_usage_reader=lambda _path: usage,
                memory_reader=lambda: 3_000,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["partial_bytes"], 600)
            self.assertEqual(
                result["disk_required_bytes"],
                400 + DOWNLOAD_DISK_RESERVE_BYTES,
            )


if __name__ == "__main__":
    unittest.main()
