from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.model_download import (
    DownloadSpec,
    download_model,
    verify_model_directory,
)


class ModelDownloadTests(unittest.TestCase):
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
            self.assertEqual(result["status"], "downloading")
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


if __name__ == "__main__":
    unittest.main()
