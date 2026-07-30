from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
INFO_PATH = REPO_ROOT / "info.json"
RUNTIME_DIR = REPO_ROOT / ".runtime"
PENDING_REQUEST_PATH = RUNTIME_DIR / "pending-request.json"
SERVER_STATE_PATH = RUNTIME_DIR / "server-state.json"
DEFAULT_REVISION = "f3d0bc7"
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    model_id: str
    target: Path
    required_files: tuple[str, ...]
    revision: str = DEFAULT_REVISION
    runtime_dir: Path = RUNTIME_DIR


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
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


def load_download_spec(
    *,
    model_id: str | None = None,
    target: str | Path | None = None,
    revision: str = DEFAULT_REVISION,
) -> DownloadSpec:
    info = _load_info()
    models = [item for item in info["models"] if isinstance(item, dict)]
    selected = next(
        (
            item
            for item in models
            if model_id is None or str(item.get("id", "")) == model_id
        ),
        None,
    )
    if selected is None:
        raise ValueError(f"info.json 中没有模型：{model_id}")
    configured = REPO_ROOT / str(selected["local_dir"])
    destination = Path(target).expanduser() if target else configured
    if not destination.is_absolute():
        destination = REPO_ROOT / destination
    required = selected.get("required_files")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise ValueError("info.json 的 required_files 无效。")
    return DownloadSpec(
        model_id=str(selected["id"]),
        target=destination.resolve(),
        required_files=tuple(required),
        revision=revision,
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


def _write_pending(spec: DownloadSpec, *, error: str | None = None) -> None:
    pending_path = spec.runtime_dir / "pending-request.json"
    server_state_path = spec.runtime_dir / "server-state.json"
    _atomic_json(
        pending_path,
        {
            "schema_version": 1,
            "status": "downloading",
            "model_id": spec.model_id,
            "target": str(spec.target),
            "partial": str(spec.target.with_name(spec.target.name + ".partial")),
            "revision": spec.revision,
            "updated_at": _utc_now(),
            "last_error": error,
        },
    )
    _atomic_json(
        server_state_path,
        {
            "schema_version": 1,
            "status": "downloading",
            "model_id": spec.model_id,
            "updated_at": _utc_now(),
            "last_error": error,
        },
    )


def _mark_ready(spec: DownloadSpec) -> None:
    pending_path = spec.runtime_dir / "pending-request.json"
    server_state_path = spec.runtime_dir / "server-state.json"
    if pending_path.exists():
        pending_path.unlink()
    _atomic_json(
        server_state_path,
        {
            "schema_version": 1,
            "status": "stopped",
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
        raise RuntimeError(
            "正式模型目录存在但完整性检查失败；为防止覆盖，已停止。"
            f"错误：{'; '.join(existing_errors[:5])}"
        )
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
        _write_pending(spec, error=f"{type(exc).__name__}: {exc}")
        return {
            "status": "downloading",
            "downloaded": False,
            "model_id": spec.model_id,
            "model_path": str(spec.target),
            "partial_path": str(partial),
            "revision": spec.revision,
            "error": f"{type(exc).__name__}: {exc}",
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
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        spec = load_download_spec(
            model_id=args.model_id,
            target=args.target,
            revision=args.revision,
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
        result = download_model(spec)
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
    print(
        "模型正在下载，请运行 scripts\\run.ps1 --continue 继续。",
        file=sys.stderr,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
