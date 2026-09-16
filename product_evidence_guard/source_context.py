"""Small, explicit document cues; never execute instructions in source text."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from .models import SourceBlock


def document_context(source: str, blocks: list[SourceBlock]) -> dict:
    name = PurePosixPath(source).stem
    heading = "\n".join(block.text for block in blocks[:6])
    # A draft being checked must not certify its own marketing claims.
    draft = bool(re.search(r"文案待(?:回检|核查)|(?:商品介绍|电商文案)(?:测试稿|草稿)|content[_ -]?draft", name + "\n" + heading, re.I))
    versions = set()
    for block in blocks:
        match = re.match(r"^\s*(?:资料版本|文档版本|document version)\s*[:：|=]\s*v?(\d+(?:\.\d+){0,2})\b", block.text, re.I)
        if match:
            versions.add(match[1])
    return {"role": "draft" if draft else "evidence",
            "version": next(iter(versions)) if len(versions) == 1 else None,
            "historical": bool(re.search(r"旧版|历史版|obsolete|historical", name, re.I))}
