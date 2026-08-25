from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Callable
import uuid
import ctypes

try:
    from protocol import AlreadyRunningError, RuntimeIdentityGuard
except ImportError:  # Imported as scripts.model_download by the test suite.
    from scripts.protocol import AlreadyRunningError, RuntimeIdentityGuard


REPO_ROOT = Path(__file__).resolve().parents[1]
INFO_PATH = REPO_ROOT / "info.json"
RUNTIME_DIR = REPO_ROOT / ".runtime"
DEFAULT_REVISION = "f3d0bc7"
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
DOWNLOAD_WORKER_DIR_NAME = "download-worker"
DOWNLOAD_STATE_NAME = "download-state.json"
DOWNLOAD_DISK_RESERVE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    model_id: str
    target: Path
    required_files: tuple[str, ...]
    revision: str = DEFAULT_REVISION
    runtime_dir: Path = RUNTIME_DIR
    expected_size_bytes: int | None = None
    minimum_memory_bytes: int | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_info() -> dict[str, Any]:
    data = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise ValueError("info.json 缺少 models 配置。")
    return data


def _gb_to_bytes(value: Any, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"info.json 的 {field} 必须是非负数字。")
    if value < 0:
        raise ValueError(f"info.json 的 {field} 必须是非负数字。")
    return int(float(value) * 1_000_000_000)


def load_download_spec(
    *,
    model_id: str | None = None,
    target: str | Path | None = None,
    revision: str = DEFAULT_REVISION,
    runtime_dir: str | Path = RUNTIME_DIR,
) -> DownloadSpec:
    info = _load_info()
    models = [item for item in info["models"] if isinstance(item, dict)]
    selected = next(
        (
            item
            for item in models
            if model_id is None
            or model_id
            in {
                str(item.get("model_id", "")),
                str(item.get("id", "")),
            }
        ),
        None,
    )
    if selected is None:
        raise ValueError(f"info.json 中没有模型：{model_id}")
    legacy_model_id = str(selected.get("id", "")).strip()
    official_model_id = str(selected.get("model_id", "")).strip()
    if (
        legacy_model_id
        and official_model_id
        and legacy_model_id != official_model_id
    ):
        raise ValueError("info.json 的 id 与 model_id 不一致。")
    resolved_model_id = official_model_id or legacy_model_id
    if not resolved_model_id:
        raise ValueError("info.json 的 model_id 无效。")
    legacy_dir = str(selected.get("local_dir", "")).strip()
    official_dir = str(selected.get("dir_name", "")).strip()
    if legacy_dir and official_dir and legacy_dir != official_dir:
        raise ValueError("info.json 的 local_dir 与 dir_name 不一致。")
    resolved_dir = official_dir or legacy_dir
    if not resolved_dir:
        raise ValueError("info.json 的 dir_name 无效。")
    configured = REPO_ROOT / resolved_dir
    destination = Path(target).expanduser() if target else configured
    if not destination.is_absolute():
        destination = REPO_ROOT / destination
    required = selected.get("required_files")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("info.json 的 required_files 无效。")
    return DownloadSpec(
        model_id=resolved_model_id,
        target=destination.resolve(),
        required_files=tuple(required),
        revision=revision,
        runtime_dir=Path(runtime_dir).expanduser().resolve(),
        expected_size_bytes=_gb_to_bytes(
            selected.get("repository_size_gb"),
            field="repository_size_gb",
        ),
        minimum_memory_bytes=_gb_to_bytes(
            info.get("mem_need_gb"),
            field="mem_need_gb",
        ),
    )


def verify_model_directory(path: Path, required_files: tuple[str, ...]) -> list[str]:
    """Return human-readable integrity errors for a downloaded snapshot."""

    errors: list[str] = []
    if not path.is_dir():
        return [f"目录不存在：{path}"]
    for relative in required_files:
        file_path = path / relative
        if not file_path.is_file():
            errors.append(f"缺少文件：{relative}")
            continue
        size = file_path.stat().st_size
        if size <= 0:
            errors.append(f"空文件：{relative}")
            continue
        if size <= 4096:
            prefix = file_path.read_bytes()[:256]
            if prefix.startswith(LFS_POINTER_PREFIX) or b"oid sha256:" in prefix:
                errors.append(f"仍是 Git LFS 指针：{relative}")
    return errors


def available_physical_memory_bytes() -> int | None:
    """Return currently available physical memory without adding a dependency."""

    if os.name == "nt":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        try:
            success = ctypes.windll.kernel32.GlobalMemoryStatusEx(  # type: ignore[attr-defined]
                ctypes.byref(status)
            )
        except (AttributeError, OSError):
            return None
        return int(status.ullAvailPhys) if success else None
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        available_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return page_size * available_pages


def _directory_file_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def download_resource_preflight(
    spec: DownloadSpec,
    *,
    disk_usage_reader: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
    memory_reader: Callable[[], int | None] = available_physical_memory_bytes,
) -> dict[str, Any]:
    """Fail before a multi-GB download when disk or load-time RAM is insufficient."""

    partial = spec.target.with_name(spec.target.name + ".partial")
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial_bytes = _directory_file_bytes(partial)
    expected = spec.expected_size_bytes
    remaining = max(0, expected - partial_bytes) if expected is not None else None
    disk_required = (
        remaining + DOWNLOAD_DISK_RESERVE_BYTES
        if remaining is not None
        else DOWNLOAD_DISK_RESERVE_BYTES
    )
    disk_free = int(disk_usage_reader(partial.parent).free)
    memory_available = memory_reader()
    memory_required = spec.minimum_memory_bytes
    errors: list[str] = []
    if disk_free < disk_required:
        errors.append(
            "磁盘空间不足："
            f"可用 {disk_free} bytes，需要至少 {disk_required} bytes。"
        )
    if (
        memory_required is not None
        and memory_available is not None
        and memory_available < memory_required
    ):
        errors.append(
            "可用物理内存不足："
            f"可用 {memory_available} bytes，需要至少 {memory_required} bytes。"
        )
    return {
        "ok": not errors,
        "disk_free_bytes": disk_free,
        "disk_required_bytes": disk_required,
        "partial_bytes": partial_bytes,
        "expected_model_bytes": expected,
        "memory_available_bytes": memory_available,
        "memory_required_bytes": memory_required,
        "errors": errors,
    }


def _write_pending(
    spec: DownloadSpec,
    *,
    error: str | None = None,
    active: bool = True,
) -> None:
    pending_path = spec.runtime_dir / "pending-request.json"
    state = {
        "schema_version": 1,
        "status": "downloading",
        "active": active,
        "retryable": True,
        "model_id": spec.model_id,
        "target": str(spec.target),
        "partial": str(spec.target.with_name(spec.target.name + ".partial")),
        "revision": spec.revision,
        "updated_at": _utc_now(),
        "last_error": error,
    }
    _atomic_json(pending_path, state)
    # Download state is deliberately separate from the resident server state.
    # A background download must never erase the server pid/startup identity.
    _atomic_json(spec.runtime_dir / DOWNLOAD_STATE_NAME, state)


def _write_failure(spec: DownloadSpec, *, error: str) -> None:
    """Persist a non-retryable download failure without claiming progress."""

    pending_path = spec.runtime_dir / "pending-request.json"
    state = {
        "schema_version": 1,
        "status": "error",
        "active": False,
        "retryable": False,
        "model_id": spec.model_id,
        "target": str(spec.target),
        "partial": str(spec.target.with_name(spec.target.name + ".partial")),
        "revision": spec.revision,
        "updated_at": _utc_now(),
        "last_error": error,
    }
    _atomic_json(pending_path, state)
    _atomic_json(spec.runtime_dir / DOWNLOAD_STATE_NAME, state)


def is_retryable_download_error(exc: BaseException) -> bool:
    """Return whether another ``--continue`` call can reasonably help.

    Authentication, invalid repository/revision, permissions and disk-full
    failures need user intervention and must not masquerade as an active
    download. Network interruptions and an incomplete partial snapshot are
    resumable.
    """

    if isinstance(exc, OSError):
        if exc.errno in {
            errno.ENOSPC,
            getattr(errno, "EDQUOT", -1),
            errno.EACCES,
            errno.EPERM,
            errno.EROFS,
            errno.EINVAL,
        }:
            return False
        if exc.errno in {
            errno.EINTR,
            errno.ECONNABORTED,
            errno.ECONNREFUSED,
            errno.ECONNRESET,
            errno.ENETDOWN,
            errno.ENETUNREACH,
            errno.ETIMEDOUT,
        }:
            return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {408, 425, 429} or (
        isinstance(status_code, int) and 500 <= status_code <= 599
    ):
        return True
    if status_code in {400, 401, 403, 404}:
        return False
    permanent_markers = (
        "gatedrepo",
        "repositorynotfound",
        "revisionnotfound",
        "entrynotfound",
        "localentrynotfound",
        "hfvalidation",
        "badrequest",
        "xetauthorization",
        "authentication",
        "permission",
        "unauthorized",
        "forbidden",
        "invalid revision",
        "repository not found",
        "unexpected directory",
        "意外目录",
        "磁盘空间",
        "正式模型目录存在但完整性检查失败",
    )
    if any(marker in name or marker in message for marker in permanent_markers):
        return False
    retryable_markers = (
        "timeout",
        "connection",
        "network",
        "temporar",
        "xetdownload",
        "timed out",
        "connection reset",
    )
    return any(marker in name or marker in message for marker in retryable_markers)


def _mark_ready(spec: DownloadSpec) -> None:
    pending_path = spec.runtime_dir / "pending-request.json"
    if pending_path.exists():
        pending_path.unlink()
    _atomic_json(
        spec.runtime_dir / DOWNLOAD_STATE_NAME,
        {
            "schema_version": 1,
            "status": "ready",
            "active": False,
            "retryable": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "updated_at": _utc_now(),
            "last_error": None,
        },
    )


def _snapshot_download(
    *,
    repo_id: str,
    local_dir: str,
    revision: str,
) -> str:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError(
            "缺少 huggingface-hub，请先运行 scripts\\install-env.ps1。"
        ) from exc
    return snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
        revision=revision,
    )


def download_model(
    spec: DownloadSpec,
    *,
    downloader: Callable[..., str] = _snapshot_download,
    resource_probe: Callable[[DownloadSpec], dict[str, Any]] = download_resource_preflight,
) -> dict[str, Any]:
    existing_errors = verify_model_directory(spec.target, spec.required_files)
    if not existing_errors:
        _mark_ready(spec)
        return {
            "status": "ready",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "revision": spec.revision,
        }

    partial = spec.target.with_name(spec.target.name + ".partial")
    if spec.target.exists():
        error_text = (
            "RuntimeError: 正式模型目录存在但完整性检查失败；"
            "为防止覆盖，已停止。"
            f"错误：{'; '.join(existing_errors[:5])}"
        )
        _write_failure(spec, error=error_text)
        return {
            "status": "error",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "revision": spec.revision,
            "error": error_text,
            "retryable": False,
        }
    preflight = resource_probe(spec)
    if not preflight.get("ok"):
        errors = preflight.get("errors")
        message = "; ".join(str(item) for item in errors or [])
        error_text = f"ResourcePreflightError: {message or '资源预检失败。'}"
        _write_failure(spec, error=error_text)
        return {
            "status": "error",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "revision": spec.revision,
            "error": error_text,
            "code": "resource_preflight_failed",
            "retryable": False,
            "preflight": preflight,
        }
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.mkdir(parents=True, exist_ok=True)
    _write_pending(spec)

    try:
        downloaded_path = Path(
            downloader(
                repo_id=spec.model_id,
                local_dir=str(partial),
                revision=spec.revision,
            )
        ).resolve()
        if downloaded_path != partial.resolve():
            # huggingface_hub should honor local_dir. Copying would duplicate 5+ GB,
            # so fail explicitly instead of silently filling another disk.
            raise RuntimeError(
                f"下载器返回了意外目录：{downloaded_path}（预期 {partial.resolve()}）"
            )
        errors = verify_model_directory(partial, spec.required_files)
        if errors:
            raise RuntimeError("模型快照不完整：" + "; ".join(errors[:10]))
        os.replace(partial, spec.target)
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        if not is_retryable_download_error(exc):
            _write_failure(spec, error=error_text)
            return {
                "status": "error",
                "downloaded": False,
                "model_id": spec.model_id,
                "model_path": str(spec.target),
                "partial_path": str(partial),
                "revision": spec.revision,
                "error": error_text,
                "retryable": False,
            }
        _write_pending(spec, error=error_text, active=False)
        return {
            "status": "downloading",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "partial_path": str(partial),
            "revision": spec.revision,
            "error": error_text,
            "retryable": True,
            "continue_command": "scripts\\run.ps1 --continue",
        }

    _mark_ready(spec)
    return {
        "status": "ready",
        "downloaded": True,
        "model_id": spec.model_id,
        "model_path": str(spec.target),
        "revision": spec.revision,
        "size_bytes": sum(
            item.stat().st_size for item in spec.target.rglob("*") if item.is_file()
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download the configured local VLM safely.")
    parser.add_argument("--model-id", default=None)
    parser.add_argument("--target", default=None)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--runtime-dir", default=str(RUNTIME_DIR))
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = load_download_spec(
            model_id=args.model_id,
            target=args.target,
            revision=args.revision,
            runtime_dir=args.runtime_dir,
        )
        if args.verify_only:
            errors = verify_model_directory(spec.target, spec.required_files)
            result = {
                "status": "ready" if not errors else "incomplete",
                "model_id": spec.model_id,
                "model_path": str(spec.target),
                "errors": errors,
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if not errors else 1
        worker_dir = spec.runtime_dir / DOWNLOAD_WORKER_DIR_NAME
        try:
            with RuntimeIdentityGuard(
                runtime_dir=worker_dir,
                startup_id=uuid.uuid4().hex,
                server_script=Path(__file__).resolve(),
            ):
                result = download_model(spec)
        except AlreadyRunningError:
            result = {
                "status": "downloading",
                "downloaded": False,
                "model_id": spec.model_id,
                "model_path": str(spec.target),
                "partial_path": str(
                    spec.target.with_name(spec.target.name + ".partial")
                ),
                "revision": spec.revision,
                "retryable": True,
                "continue_command": "scripts\\run.ps1 --continue",
                "message": "模型下载 worker 已在运行。",
            }
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] == "ready":
        return 0
    if result["status"] == "error":
        print("模型下载失败，需要修复错误后重试。", file=sys.stderr)
        return 1
    print(
        "模型正在下载，请运行 scripts\\run.ps1 --continue 继续。",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
