from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


STATE_SCHEMA_VERSION = 1


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": STATE_SCHEMA_VERSION, "engine_signature": "", "files": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": STATE_SCHEMA_VERSION, "engine_signature": "", "files": {}}
    if data.get("schema_version") != STATE_SCHEMA_VERSION or not isinstance(data.get("files"), dict):
        return {"schema_version": STATE_SCHEMA_VERSION, "engine_signature": "", "files": {}}
    return data


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
