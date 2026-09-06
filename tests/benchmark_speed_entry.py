"""Opt-in real-image speed comparison using an already warmed resident service.

The script never starts a second model, shuts down a service, or kills a process.
Use fresh output directories to separate image recognition from report reuse.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from benchmark_concurrent_entry import process_memory, system_memory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--baseline", required=True, help="Existing visual-transcription.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    case = root / ".runtime" / ("speed-" + uuid.uuid4().hex[:10])
    source = case / "input"
    source.mkdir(parents=True)
    image = Path(args.image)
    shutil.copyfile(image, source / image.name)
    expected = json.loads(Path(args.baseline).read_text("utf-8"))["images"][0]
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running", "case": str(case),
              "entry": args.entry, "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "baseline": args.baseline, "runs": []}
    transcript = []
    stop = threading.Event()
    phase = {"name": "preflight"}

    def save():
        (case / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
        (case / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), "utf-8")

    def call(operation, *arguments):
        started = time.perf_counter()
        completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                    args.entry, operation, *arguments], capture_output=True, encoding="utf-8")
        response = json.loads(completed.stdout.strip())
        transcript.append(response)
        save()
        assert completed.returncode == 0 and response["ok"], response.get("error")
        return response["result"], round(time.perf_counter() - started, 4)

    status, _ = call("status")
    assert status["resident"]["model_ready"], "Warm the service once before benchmarking."
    result["initial_resident"] = status["resident"]
    initial_model_pids = {p["pid"] for p in process_memory() if p["private_bytes"] > 2 * 1024 ** 3}
    assert len(initial_model_pids) == 1, "Expected exactly one resident model process."

    def monitor():
        with (case / "memory-samples.jsonl").open("w", encoding="utf-8") as output:
            while not stop.is_set():
                row = {"phase": phase["name"], "system": system_memory(), "processes": process_memory()}
                output.write(json.dumps(row) + "\n")
                output.flush()
                stop.wait(.5)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()

    def signatures(candidates):
        return Counter((c["field"], c.get("scope"), json.dumps(c["normalized_value"]), c["normalized_unit"])
                       for c in candidates)

    try:
        renamed = case / "another-task" / "renamed-source"
        renamed.mkdir(parents=True)
        shutil.copyfile(image, renamed / ("renamed" + image.suffix))
        runs = [("fresh_first", source, ["--no-visual-cache"]),
                ("fresh_warmed", source, ["--no-visual-cache"]),
                ("cache_fill", source, []), ("cache_hit_renamed", renamed, [])]
        for name, input_dir, extra in runs:
            phase["name"] = name
            output = case / name
            response, wall = call("analyze", str(input_dir), "--output", str(output), "--device", "CPU", *extra)
            summary = json.loads((output / "run-summary.json").read_text("utf-8"))
            visual = json.loads((output / "visual-transcription.json").read_text("utf-8"))["images"][0]
            row = {"name": name, "wall_seconds": wall, "analysis_seconds": summary["analysis_seconds"],
                   "model_load_seconds": summary["model_load_seconds"],
                   "model_reused": summary.get("local_ai", {}).get("model_reused"),
                   "route": visual["image_route"], "stage_timings": visual["stage_timings"],
                   "review_region": visual.get("review_region"), "recognition_cache": visual.get("recognition_cache"),
                   "transcription": visual["raw_transcription_output"], "candidates": visual["fact_candidates"],
                   "errors": summary["errors"], "resident_cache": summary.get("resident_recognition_cache"),
                   "baseline_facts_retained": not bool(signatures(expected["fact_candidates"]) - signatures(visual["fact_candidates"]))}
            result["runs"].append(row)
            save()
            print(json.dumps({k: row[k] for k in ("name", "wall_seconds", "analysis_seconds", "route", "baseline_facts_retained")}), flush=True)
            assert not summary["errors"] and visual["ok"] and row["baseline_facts_retained"], row
            assert summary["model_load_seconds"] == 0, "Unexpected model reload."
            assert not summary["unchanged_files_reused"], "Report cache invalidates recognition measurement."
            assert all(c["status"] == "pending" for c in visual["fact_candidates"])
            if name == "cache_hit_renamed":
                assert visual["recognition_cache"]["hit"] and visual["stage_timings"]["visual_seconds"] == 0
                for item in [*visual["source_blocks"], *visual["fact_candidates"]]:
                    assert item["source_file"] == "renamed" + image.suffix
                    assert item["locator"]["image"] == "renamed" + image.suffix
        final_status, _ = call("status")
        result["final_resident"] = final_status["resident"]
        final_pids = {p["pid"] for p in process_memory() if p["private_bytes"] > 2 * 1024 ** 3}
        assert final_pids == initial_model_pids, "Resident model changed during benchmark."
        result["resident_model_pid"] = next(iter(final_pids))
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        raise
    finally:
        stop.set()
        watcher.join()
        save()
        print(json.dumps({"status": result["status"], "case": str(case)}), flush=True)


if __name__ == "__main__":
    main()
