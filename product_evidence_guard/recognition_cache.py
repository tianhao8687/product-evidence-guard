"""Bounded, process-local image recognition reuse; never stores decisions."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading

from .qwen_vl_reader import QwenVlReadResult


def bind_image_result(result: QwenVlReadResult, path: Path, root: Path, content_hash: str) -> QwenVlReadResult:
    """Bind detached recognition evidence to this source, with stable per-source IDs."""
    relative = path.relative_to(root).as_posix()
    bound = deepcopy(result)
    bound.image_file = relative
    bound.file_hash = content_hash
    block_ids = {}
    for block in bound.source_blocks:
        old_id = block.block_id
        identity = [relative, content_hash, block.locator.get("transcription_id"), block.text, block.extraction_method]
        block.block_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        block_ids[old_id] = block.block_id
        block.source_file = relative
        block.file_hash = content_hash
        block.locator = deepcopy(block.locator)
        block.locator["image"] = relative
    for candidate in bound.fact_candidates:
        candidate.source_file = relative
        candidate.file_hash = content_hash
        candidate.source_block_id = block_ids[candidate.source_block_id]
        candidate.locator = deepcopy(candidate.locator)
        candidate.locator["image"] = relative
        identity = [candidate.source_block_id, candidate.field, candidate.raw_value, candidate.scope, candidate.extraction_method]
        candidate.candidate_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        candidate.status = "pending"
    return bound


class RecognitionCache:
    """LRU memory cache confined to one resident reader/model instance."""
    def __init__(self, *, max_entries=64, max_bytes=8 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.entries = OrderedDict()
        self.size = 0
        self.hits = 0
        self.misses = 0
        self.lock = threading.RLock()

    def get(self, key):
        with self.lock:
            item = self.entries.get(key)
            if item is None:
                self.misses += 1
                return None
            self.entries.move_to_end(key)
            self.hits += 1
            return deepcopy(item[0])

    def put(self, key, result):
        if not result.ok or any(c.status != "pending" for c in result.fact_candidates):
            return
        size = len(json.dumps(result.to_dict(), ensure_ascii=False).encode("utf-8"))
        if size > self.max_bytes or self.max_entries < 1:
            return
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous:
                self.size -= previous[1]
            while self.entries and (len(self.entries) >= self.max_entries or self.size + size > self.max_bytes):
                _, evicted = self.entries.popitem(last=False)
                self.size -= evicted[1]
            self.entries[key] = (deepcopy(result), size)
            self.size += size

    def stats(self):
        with self.lock:
            return {"entries": len(self.entries), "serialized_bytes": self.size,
                    "hits": self.hits, "misses": self.misses, "max_entries": self.max_entries,
                    "max_serialized_bytes": self.max_bytes, "scope": "resident_model"}
