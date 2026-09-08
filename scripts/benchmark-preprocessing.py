"""Compare serial and bounded-parallel deterministic preprocessing."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from statistics import median
import sys
import tempfile
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from product_evidence_guard.engine import MAX_FILE_BYTES  # noqa: E402
from product_evidence_guard.parsers import discover_files  # noqa: E402
from product_evidence_guard.preprocessing import preprocess_files  # noqa: E402


def _digest(results: dict[Path, object], root: Path) -> str:
    rows: list[object] = []
    for path, item in results.items():
        parsed = getattr(item, "parsed", None)
        if isinstance(parsed, list):
            payload = [block.to_dict() for block in parsed]
        elif parsed is not None and hasattr(parsed, "blocks"):
            payload = [block.to_dict() for block in parsed.blocks]
        else:
            payload = None
        rows.append({"path": path.relative_to(root).as_posix(), "payload": payload,
                     "error": type(getattr(item, "error", None)).__name__})
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _measure(paths: list[Path], root: Path, *, workers: int, runs: int) -> dict[str, object]:
    timings: list[float] = []
    digests: set[str] = set()
    files_parsed = 0
    for _ in range(runs):
        started = time.perf_counter()
        results, stats = preprocess_files(
            paths,
            root,
            previous_files={},
            max_file_bytes=MAX_FILE_BYTES,
            workers=workers,
        )
        timings.append(time.perf_counter() - started)
        digests.add(_digest(results, root))
        files_parsed = int(stats["files_parsed"])
    middle = median(timings)
    return {
        "workers": workers,
        "runs": runs,
        "input_operations": len(paths),
        "parsed_operations": files_parsed,
        "median_seconds": round(middle, 6),
        "operations_per_second": round(len(paths) / middle, 2),
        "stable_digest": next(iter(digests)) if len(digests) == 1 else None,
        "all_runs_stable": len(digests) == 1,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--copies", type=int, default=10)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    root = args.input_dir.expanduser().resolve()
    if args.copies < 1 or args.runs < 1 or args.workers < 2:
        parser.error("copies/runs must be positive and workers must be at least 2")
    discovered = discover_files(root)
    # Prime imports and parser caches outside the measured samples.
    preprocess_files(discovered, root, previous_files={}, max_file_bytes=MAX_FILE_BYTES, workers=1)
    with tempfile.TemporaryDirectory(prefix=".preprocess-benchmark-", dir=REPO_ROOT) as temporary:
        benchmark_root = Path(temporary)
        for copy_index in range(args.copies):
            copy_root = benchmark_root / f"set-{copy_index:03d}"
            copy_root.mkdir()
            for source in discovered:
                shutil.copy2(source, copy_root / source.name)
        paths = discover_files(benchmark_root)
        serial = _measure(paths, benchmark_root, workers=1, runs=args.runs)
        parallel = _measure(paths, benchmark_root, workers=args.workers, runs=args.runs)
    same_output = serial["stable_digest"] == parallel["stable_digest"]
    speedup = float(serial["median_seconds"]) / float(parallel["median_seconds"])
    print(json.dumps({
        "input_root": str(root),
        "unique_files": len(discovered),
        "temporary_unique_operations_per_run": len(paths),
        "serial": serial,
        "parallel": parallel,
        "business_output_identical": same_output,
        "speedup": round(speedup, 3),
        "improvement_percent": round((speedup - 1.0) * 100.0, 1),
        "scope": "deterministic hash/parse only; OCR and Qwen excluded",
    }, ensure_ascii=False, indent=2))
    return 0 if same_output else 1


if __name__ == "__main__":
    raise SystemExit(main())
