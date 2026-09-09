"""Local, read-only source snapshots shared by chat and exported reports."""
from __future__ import annotations

import hashlib
from html import escape
import json
from pathlib import Path, PureWindowsPath

from .state import atomic_write_text

SOURCE_TYPES = {".txt", ".md", ".csv", ".json", ".docx", ".xlsx", ".pdf",
                ".png", ".jpg", ".jpeg", ".webp", ".bmp"}

def write_source_links(output: Path, candidates, *, input_root=None) -> dict[str, str]:
    """Return opaque local links, never source names or raw text in chat JSON.

    Pages are export snapshots, not live approvals. Only validated local source
    files get an open-original link; stale/missing/unsafe files remain labelled.
    """
    from .workflow import safe_file, safe_output

    output = safe_output(output)
    folder = output / "source-links"
    if not folder.exists():
        folder.mkdir()
    safe_output(folder)
    if input_root is None:
        state = output / "analysis-state.json"
        if state.exists():
            input_root = json.loads(safe_file(state, max_bytes=100 * 1024 * 1024))["input_root"]
    root = Path(input_root).absolute() if input_root is not None else None
    sources = {}
    links = {}
    for candidate in candidates:
        c = candidate if isinstance(candidate, dict) else candidate.to_dict()
        name = str(c["source_file"])
        if name not in sources:
            sources[name] = (None, None)
            if root is not None:
                source = root / name
                # Reject absolute/UNC/drive paths on either host OS, not just ../.
                if (not Path(name).is_absolute() and not PureWindowsPath(name).drive
                        and Path(name).suffix.lower() in SOURCE_TYPES
                        and not root.as_posix().startswith("//")
                        and source.resolve().is_relative_to(root.resolve())):
                    try:
                        raw = safe_file(source, max_bytes=100 * 1024 * 1024)
                        sources[name] = (source.as_uri(), hashlib.sha256(raw).hexdigest())
                    except (OSError, ValueError):
                        pass
        source_uri, current_hash = sources[name]
        current = bool(c.get("source_current", True) and c.get("status") != "stale"
                       and current_hash and current_hash == c["file_hash"])
        locator = c.get("locator") or {}
        page = locator.get("page")
        if current and Path(name).suffix.lower() == ".pdf" and type(page) is int and page > 0:
            source_uri += f"#page={page}"
        position_labels = {"page": "页码", "sheet": "工作表", "cell": "单元格", "line": "行",
                           "paragraph": "段落", "row": "行", "column": "列", "table": "表格"}
        position = "；".join(f"{label}：{locator[key]}" for key, label in position_labels.items()
                             if locator.get(key) is not None) or "见来源文件；未提供精确位置"
        original = (f'<a href="{escape(source_uri, quote=True)}">打开来源文件</a>' if current else
                    '<p class="warning">来源已变化、不可用或尚未验证，请重新分析后核对。</p>')
        html = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="referrer" content="no-referrer">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'">'
                '<title>查看原文</title><style>body{max-width:850px;margin:40px auto;padding:0 24px;'
                'font:16px/1.7 system-ui,sans-serif;color:#183d4d}pre{white-space:pre-wrap;overflow-wrap:anywhere;'
                'background:#f1f5f6;padding:20px}a{color:#006b68}.warning{color:#a83031}</style>'
                f'<h1>查看原文</h1><p>{escape(name)}</p><p>{escape(position)}</p>'
                f'<pre>{escape(str(c.get("raw_text", "")))}</pre>{original}'
                '<p>这是分析时的证据摘录，不代表人工批准。图片或扫描件的摘录可能存在识别误差，'
                '请打开来源文件核对。来源变更后请重新分析；链接仅在本机可用。</p></html>')
        opaque_id = hashlib.sha256(str(c["candidate_id"]).encode("utf-8")).hexdigest()[:32]
        target = folder / f"{opaque_id}.html"
        existing = safe_file(target, max_bytes=10_000_000) if target.exists() else None
        if existing != html.encode("utf-8"):
            atomic_write_text(target, html)
        links[c["candidate_id"]] = target.as_uri()
    return links
