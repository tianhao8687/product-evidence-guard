from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any


STATE_SCHEMA_VERSION = 4


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "engine_signature": "",
        "session_id": "",
        "input_root": "",
        "files": {},
    }


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            return _empty_state()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if data.get("schema_version") not in {1, 2, 3, STATE_SCHEMA_VERSION} or not isinstance(data.get("files"), dict):
        return _empty_state()
    data["schema_version"] = STATE_SCHEMA_VERSION
    data.setdefault("session_id", "")
    data.setdefault("input_root", "")
    return data


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace ``path`` without opening a predictable temp path.

    A unique file created with ``O_EXCL`` prevents a pre-created symlink or
    hardlink named ``<destination>.tmp`` from redirecting the write.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )
