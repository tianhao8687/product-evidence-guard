from __future__ import annotations

import json
import hashlib
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
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict) or data.get("schema_version") not in {1, 2, 3, STATE_SCHEMA_VERSION} or not isinstance(data.get("files"), dict):
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


# A bounded two-generation report publication journal. Evidence HTML is
# content-addressed and need not be rolled back; no original source is touched.
REPORT_FILES = ("product-facts.json", "analysis-state.json", "run-summary.json", "conflicts.md",
                "evidence-report.html", "visual-transcription.json", "document-visuals.json",
                "confirmation-state.json", "confirmed-product-facts.json")
PUBLICATION_MARKER = ".publication-pending.json"
PUBLICATION_BACKUP = ".previous-complete-result.json"


def _safe_report_file(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400):
            raise ValueError("报告恢复路径不能经过符号链接或重解析点。")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > 256 * 1024 * 1024):
        raise ValueError("报告恢复文件不安全或超过上限。")


def recover_publication(output: Path) -> bool:
    marker, backup = output / PUBLICATION_MARKER, output / PUBLICATION_BACKUP
    _safe_report_file(marker)
    if not marker.exists():
        return False
    _safe_report_file(backup)
    raw = backup.read_bytes()
    record = json.loads(marker.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("上次报告写入中断，备份校验未通过；请保留目录并从完整备份恢复。")
    previous = json.loads(raw)
    if not isinstance(previous, dict) or set(previous) != set(REPORT_FILES) or any(v is not None and not isinstance(v, str) for v in previous.values()):
        raise ValueError("报告恢复记录格式不正确。")
    for name in REPORT_FILES:
        _safe_report_file(output / name)
    for name, text in previous.items():
        if text is None:
            (output / name).unlink(missing_ok=True)
        else:
            atomic_write_text(output / name, text)
    marker.unlink()
    return True


def begin_publication(output: Path) -> None:
    recover_publication(output)
    previous = {}
    for name in REPORT_FILES:
        path = output / name
        _safe_report_file(path)
        previous[name] = path.read_text(encoding="utf-8") if path.exists() else None
    backup = output / PUBLICATION_BACKUP
    _safe_report_file(backup)
    atomic_write_json(backup, previous)
    atomic_write_json(output / PUBLICATION_MARKER, {"sha256": hashlib.sha256(backup.read_bytes()).hexdigest()})


def finish_publication(output: Path) -> None:
    marker = output / PUBLICATION_MARKER
    _safe_report_file(marker)
    marker.unlink(missing_ok=True)
