"""Small resumable corpus runner. Reuses the independent whole-output scorer.

One isolated process per package, bounded concurrency and timeout; no network.
Resume requires identical gold, production code, scorer and input file hashes.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from evaluate_scenarios import ROOT, canonical, run_case, score
from product_evidence_guard.state import atomic_write_json


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def code_hash():
    paths = [*sorted((ROOT / "product_evidence_guard").glob("*.py")),
             ROOT / "product_evidence_guard/field_registry.json", Path(__file__),
             ROOT / "scripts/evaluate_scenarios.py", ROOT / "scripts/evaluation_metrics.py"]
    return sha(b"\n".join(p.relative_to(ROOT).as_posix().encode() + b"\0" + p.read_bytes() for p in paths))


def validate(dataset):
    cases = dataset.get("cases")
    if not cases:
        raise ValueError("Empty corpus")
    ids, inputs = set(), set()
    for c in cases:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", c["id"]) or c["id"] in ids:
            raise ValueError("Duplicate or unsafe package ID")
        ids.add(c["id"])
        for key in ("domain", "title", "family", "source_kind", "split", "facts"):
            if key not in c:
                raise ValueError("Missing corpus metadata: " + key)
        if not c.get("files") and not c.get("native_files"):
            raise ValueError("Package has no input")
        digest = canonical([c.get("files", {}), c.get("native_files", [])])
        if digest in inputs:
            raise ValueError("Identical package inputs counted twice")
        inputs.add(digest)
        for row in c["facts"]:
            if len(row) != 5 or row[3] not in {"verified", "conflict", "pending_confirmation"} or not row[4]:
                raise ValueError("Incomplete gold row")
        if c["source_kind"] == "public_adapted" and not (c.get("source_url", "").startswith("https://") and c.get("source_model")):
            raise ValueError("Public adaptations need source and original model")
    return cases


def atomic_json(path, payload):
    # Windows indexing/antivirus can briefly hold the just-written checkpoint.
    # Never truncate the last good checkpoint; bounded retry, then surface error.
    for attempt in range(7):
        try:
            atomic_write_json(path, payload)
            return
        except PermissionError:
            if attempt == 6:
                raise
            time.sleep(.025 * 2**attempt)


def failed(case, error):
    return {"id": case["id"], "family": case["family"], **score(case["facts"], []),
            "passed": False, "error": error, "actual": []}


def run_isolated(case, fixture, directory, *, timeout, native_root):
    command = [sys.executable, str(Path(__file__).resolve()), "--fixture", str(fixture),
               "--output", str(directory), "--case", case["id"]]
    if native_root:
        command += ["--native-root", str(native_root)]
    start = time.perf_counter()
    try:
        child = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if child.returncode:
            result = failed(case, "worker_error: " + child.stderr[-2000:])
        else:
            result = json.loads(child.stdout)
    except subprocess.TimeoutExpired:
        result = failed(case, "timeout")
    except (OSError, ValueError) as exc:
        result = failed(case, f"{type(exc).__name__}: {exc}")
    result["elapsed_seconds"] = time.perf_counter() - start
    return result


def totals(rows):
    names = ["passed", "expected_business_facts", "predicted_business_facts", "auto_confirmed",
             "wrong_auto_confirmed", "missing_fact_keys", "missed_conflicts", "false_conflicts",
             "review_items", "unnecessary_review_items"]
    return {"packages": len(rows), "execution_errors": sum(bool(r.get("error")) for r in rows),
            **{k: sum(int(r.get(k, 0)) for r in rows) for k in names}}


def run(fixture, directory, *, split="all", resume=False, workers=2, timeout=90, native_root=None):
    raw = fixture.read_bytes()
    dataset = json.loads(raw)
    cases = validate(dataset)
    selected = [c for c in cases if split == "all" or c["split"] == split]
    if not selected:
        raise ValueError("No packages selected")
    fingerprint = {"fixture_sha256":sha(raw), "code_sha256":code_hash(), "split":split,
                   "python":sys.version, "timeout":timeout}
    # Validate original inputs even when resuming. File descriptors alone are not sufficient.
    for c in selected:
        for source in c.get("native_files", []):
            if native_root is None:
                raise ValueError("native_root required")
            path = (native_root / source["path"]).resolve()
            if not path.is_relative_to(native_root.resolve()) or sha(path.read_bytes()) != source["sha256"]:
                raise ValueError("Native input changed or escaped root")
    directory.mkdir(parents=True, exist_ok=True)
    checkpoint = directory / "checkpoint.json"
    rows = {}
    if checkpoint.exists():
        saved = json.loads(checkpoint.read_text("utf-8"))
        if not resume:
            raise ValueError("Output already contains a run; choose a new directory or --resume")
        if saved["fingerprint"] != fingerprint:
            raise ValueError("Cannot resume changed inputs, gold, engine or scorer; start a new run")
        rows = {r["id"]:r for r in saved["cases"]}
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(run_isolated, c, fixture, directory, timeout=timeout, native_root=native_root):c
                   for c in selected if c["id"] not in rows}
        for future in as_completed(pending):
            c = pending[future]
            rows[c["id"]] = future.result()
            atomic_json(checkpoint, {"fingerprint":fingerprint, "cases":[rows[k] for k in sorted(rows)]})
            print(f'{len(rows)}/{len(selected)} {c["id"]} {"PASS" if rows[c["id"]]["passed"] else "FAIL"}', flush=True)
    ordered = [{**rows[c["id"]], **{k:c[k] for k in ("domain","title","source_kind","split")}} for c in selected]
    report = {**fingerprint, "protocol":dataset["protocol"], "source_mix":dict(Counter(c["source_kind"] for c in selected)),
              "domain_count":len({c["domain"] for c in selected}), "counts":totals(ordered),
              "by_domain":{d:totals([r for r in ordered if r["domain"]==d]) for d in sorted({c["domain"] for c in selected})},
              "elapsed_this_invocation_seconds":time.perf_counter()-start, "cases":ordered}
    atomic_json(directory / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/cross_domain_100.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=("all","development","holdout"), default="all")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1,5), default=2)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--native-root", type=Path)
    parser.add_argument("--case", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.case:
        c = next(c for c in json.loads(args.fixture.read_text("utf-8"))["cases"] if c["id"] == args.case)
        try:
            result = run_case(c, native_root=args.native_root)
        except Exception as exc:
            result = failed(c, f"{type(exc).__name__}: {exc}")
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    try:
        result = run(args.fixture.resolve(), args.output.resolve(), split=args.split, resume=args.resume,
                     workers=args.workers, timeout=args.timeout, native_root=args.native_root)
    except (ValueError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result["counts"]))
    return 0 if result["counts"]["passed"] == result["counts"]["packages"] else 1


if __name__ == "__main__":
    sys.exit(main())
