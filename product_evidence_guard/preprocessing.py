"""Deterministic bounded file preprocessing with stable ordered results."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Sequence

from .models import SourceBlock
from .parsers import (
    IMAGE_EXTENSIONS,
    DocumentParseResult,
    PdfParseResult,
    parse_docx_document,
    parse_file,
    parse_pdf_document,
    parse_xlsx_document,
    sha256_file,
)


PARALLEL_SOURCE_BYTES_BUDGET = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreprocessedFile:
    path: Path
    relative: str
    size: int
    file_hash: str
    parsed: list[SourceBlock] | DocumentParseResult | PdfParseResult | None
    error: Exception | None
    cache_reusable: bool
    seconds: float


def _cache_reusable(entry: Any, file_hash: str) -> bool:
    return bool(
        isinstance(entry, dict)
        and entry.get("file_hash") == file_hash
        and not entry.get("last_error")
        and not entry.get("skipped_reason")
        and not entry.get("retry_required")
    )


def _preprocess_one(
    path: Path,
    root: Path,
    previous_files: Mapping[str, Any],
    max_file_bytes: int,
    parsers: Mapping[str, Callable[..., Any]],
) -> PreprocessedFile:
    started = time.perf_counter()
    relative = path.relative_to(root).as_posix()
    size = path.stat().st_size
    file_hash = sha256_file(path) if size <= max_file_bytes else ""
    reusable = _cache_reusable(previous_files.get(relative), file_hash)
    parsed: list[SourceBlock] | DocumentParseResult | PdfParseResult | None = None
    error: Exception | None = None
    if size <= max_file_bytes and not reusable and path.suffix.casefold() not in IMAGE_EXTENSIONS:
        try:
            suffix = path.suffix.casefold()
            if suffix == ".pdf":
                parsed = parsers["pdf"](path, relative, file_hash)
            elif suffix == ".docx":
                parsed = parsers["docx"](path, relative, file_hash)
            elif suffix == ".xlsx":
                parsed = parsers["xlsx"](path, relative, file_hash)
            else:
                parsed = parsers["generic"](path, root, file_hash=file_hash)
        except Exception as exc:  # replayed in deterministic input order by the engine
            error = exc
    return PreprocessedFile(
        path=path,
        relative=relative,
        size=size,
        file_hash=file_hash,
        parsed=parsed,
        error=error,
        cache_reusable=reusable,
        seconds=round(time.perf_counter() - started, 6),
    )


def preprocess_files(
    paths: Sequence[Path],
    root: Path,
    *,
    previous_files: Mapping[str, Any] | None = None,
    max_file_bytes: int,
    workers: int | None = None,
    parser_callbacks: Mapping[str, Callable[..., Any]] | None = None,
) -> tuple[dict[Path, PreprocessedFile], dict[str, Any]]:
    """Hash and parse independent deterministic sources concurrently.

    The returned mapping is built in the original sorted input order. AI/OCR
    readers are deliberately absent from this layer.
    """
    started = time.perf_counter()
    previous = previous_files or {}
    parsers = parser_callbacks or {
        "pdf": parse_pdf_document,
        "docx": parse_docx_document,
        "xlsx": parse_xlsx_document,
        "generic": parse_file,
    }
    ordered_paths = sorted(paths, key=lambda path: path.relative_to(root).as_posix())
    requested = workers if workers is not None else min(8, max(1, os.cpu_count() or 1))
    largest_source = max((path.stat().st_size for path in ordered_paths), default=0)
    memory_bound = (
        max(1, PARALLEL_SOURCE_BYTES_BUDGET // largest_source)
        if largest_source
        else max(1, len(ordered_paths))
    )
    worker_count = max(1, min(int(requested), 16, memory_bound, max(1, len(ordered_paths))))
    if worker_count == 1:
        ordered = [_preprocess_one(path, root, previous, max_file_bytes, parsers) for path in ordered_paths]
    else:
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="peg-preprocess") as pool:
            ordered = list(pool.map(
                lambda path: _preprocess_one(path, root, previous, max_file_bytes, parsers),
                ordered_paths,
            ))
    result = {item.path: item for item in ordered}
    stats = {
        "mode": "serial" if worker_count == 1 else "parallel",
        "workers": worker_count,
        "files_hashed": sum(bool(item.file_hash) for item in ordered),
        "files_parsed": sum(item.parsed is not None for item in ordered),
        "cache_candidates_reused": sum(item.cache_reusable for item in ordered),
        "seconds": round(time.perf_counter() - started, 4),
        "business_order": "sorted_relative_path",
        "ai_parallelized": False,
        "source_bytes_memory_budget": PARALLEL_SOURCE_BYTES_BUDGET,
    }
    return result, stats
