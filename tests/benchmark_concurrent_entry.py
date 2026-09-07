"""Opt-in, bounded real-model concurrency experiment through the public entry.

One resident model, one single-file baseline, and at most two concurrent jobs.
Never terminates model or application processes. Uses read-only Win32 monitoring.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import threading
import time
import uuid


GiB = 1024 ** 3


class MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD)] + [
        (name, ctypes.c_ulonglong) for name in
        ("total_physical", "available_physical", "commit_limit", "commit_available",
         "total_virtual", "available_virtual", "extended_virtual")]


class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                ("exe", wintypes.WCHAR * 260)]


class ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("page_faults", wintypes.DWORD)] + [
        (name, ctypes.c_size_t) for name in ("peak_working_set", "working_set", "peak_paged_pool",
        "paged_pool", "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile", "private_bytes")]


kernel = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.Process32FirstW.restype = wintypes.BOOL
kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.Process32NextW.restype = wintypes.BOOL
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
psapi.GetProcessMemoryInfo.restype = wintypes.BOOL


def system_memory():
    data = MemoryStatus()
    data.length = ctypes.sizeof(data)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(data)):
        raise OSError("Cannot read system memory")
    return {name: int(getattr(data, name)) for name in
            ("total_physical", "available_physical", "commit_limit", "commit_available")}


def process_memory():
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return []
    entry = ProcessEntry()
    entry.size = ctypes.sizeof(entry)
    rows = []
    try:
        okay = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while okay:
            if entry.exe.casefold() in {"python.exe", "pythonw.exe"}:
                handle = kernel.OpenProcess(0x0400 | 0x0010, False, entry.pid)
                if handle:
                    try:
                        data = ProcessMemory()
                        data.cb = ctypes.sizeof(data)
                        if psapi.GetProcessMemoryInfo(handle, ctypes.byref(data), data.cb):
                            rows.append({"pid": int(entry.pid), "parent": int(entry.parent),
                                         "working_set": int(data.working_set), "private_bytes": int(data.private_bytes),
                                         "peak_working_set": int(data.peak_working_set)})
                    finally:
                        kernel.CloseHandle(handle)
            okay = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return rows


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    baseline = system_memory()
    if args.preflight_only:
        print(json.dumps({"system_memory": baseline, "python_processes": process_memory()}, ensure_ascii=False))
        return 0
    # This experiment's headroom checks are conservative stop conditions, not
    # a general minimum hardware specification for the model.
    if min(baseline["available_physical"], baseline["commit_available"]) < 12 * GiB:
        print(json.dumps({"status": "not_started", "reason": "insufficient_preflight_headroom", "memory": baseline}))
        return 1
    if any(p["working_set"] > 2 * GiB for p in process_memory()):
        print(json.dumps({"status": "not_started", "reason": "another_large_python_process_is_running"}))
        return 1
    case = root / ".runtime" / ("concurrency-" + uuid.uuid4().hex[:10])
    source = case / "input"
    source.mkdir(parents=True)
    image = Path(args.image)
    shutil.copyfile(image, source / image.name)
    transcript, readings = [], []
    lock, stop = threading.Lock(), threading.Event()
    phase = {"name": "warmup"}
    timeline_start = time.perf_counter()
    result = {"status": "running", "case": str(case), "baseline_memory": baseline,
              "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "model_path": args.model, "device": "CPU", "started_at": datetime.now(timezone.utc).isoformat(),
              "experiments": []}

    def save():
        (case / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def monitor():
        with (case / "memory-samples.jsonl").open("w", encoding="utf-8") as output:
            while not stop.is_set():
                row = {"elapsed": round(time.perf_counter() - timeline_start, 3),
                       "phase": phase["name"], "system": system_memory(), "processes": process_memory()}
                with lock:
                    readings.append(row)
                output.write(json.dumps(row) + "\n")
                output.flush()
                stop.wait(.5)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()

    def call(operation, *arguments):
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as wait_pool:
            pending = wait_pool.submit(subprocess.run,
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", args.entry, operation, *arguments],
                capture_output=True, encoding="utf-8", errors="replace")
            # No subprocess timeout: the benchmark must not kill a launched
            # process tree. The existing service provides model-stage bounds.
            process = pending.result()
        response = json.loads(process.stdout.strip())
        item = {"operation": operation, "wall_seconds": round(time.perf_counter() - start, 4), "response": response}
        with lock:
            transcript.append(item)
            (case / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        if process.returncode or not response["ok"]:
            raise RuntimeError(str(response.get("error")))
        return response["result"], item["wall_seconds"]

    def enough_headroom():
        data = system_memory()
        return min(data["available_physical"], data["commit_available"]) >= 3 * GiB

    def experiment(name, count):
        if not enough_headroom():
            result["experiments"].append({"name": name, "status": "not_submitted", "reason": "less_than_3_GiB_remaining"})
            return False
        phase["name"] = name
        started = time.perf_counter()
        outputs = [case / f"{name}-{i + 1}" for i in range(count)]
        submit_times = {}
        def submit(index):
            submit_times[index] = time.perf_counter()
            response, elapsed = call("analyze", str(source), "--output", str(outputs[index]),
                                     "--model", args.model, "--device", "CPU", "--background", "--brief")
            return {"index": index, "job_id": response["job"]["job_id"], "submission_seconds": elapsed}
        with ThreadPoolExecutor(max_workers=count) as pool:
            jobs = list(pool.map(submit, range(count)))
        events = {}
        latencies = []
        previous_probe = 0.0
        while len([j for j in jobs if j.get("completed")]) != count:
            if time.perf_counter() - started > 380 * count:
                raise TimeoutError("Experiment observation window expired; processes left untouched.")
            for job in jobs:
                if job.get("completed"):
                    continue
                path = root / ".runtime" / "jobs" / (job["job_id"] + ".json")
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                stage = state.get("progress", {}).get("stage", "queued")
                key = (job["job_id"], state["phase"], stage)
                if key not in events:
                    events[key] = {"job": job["index"] + 1, "phase": state["phase"], "stage": stage,
                                   "seconds_since_submit": round(time.perf_counter() - submit_times[job["index"]], 3)}
                if stage != "queued" and "first_execution_observed_seconds" not in job:
                    job["first_execution_observed_seconds"] = round(time.perf_counter() - submit_times[job["index"]], 3)
                if state["phase"] in {"completed", "failed", "interrupted"}:
                    job.update(completed=True, phase=state["phase"], wall_seconds=round(time.perf_counter()-submit_times[job["index"]],3))
                    summary_path = outputs[job["index"]] / "run-summary.json"
                    if summary_path.exists():
                        summary = json.loads(summary_path.read_text(encoding="utf-8"))
                        local = summary.get("local_ai", {})
                        job["analysis"] = {"seconds": summary.get("analysis_seconds"),
                            "model_load_seconds": summary.get("model_load_seconds"), "model_reused": local.get("model_reused"),
                            "routes": local.get("image_route_counts"), "files_reused": len(summary.get("unchanged_files_reused", [])),
                            "candidates": summary.get("candidate_count"), "errors": len(summary.get("errors", []))}
            if time.perf_counter() - previous_probe >= 5:
                status, elapsed = call("status", "--brief")
                latencies.append(elapsed)
                previous_probe = time.perf_counter()
            stop.wait(.25)
        total = time.perf_counter() - started
        with lock:
            samples = [r for r in readings if r["phase"] == name]
        entry = {"name": name, "status": "passed" if all(j["phase"]=="completed" for j in jobs) else "failed",
                 "submitted_jobs": count, "total_seconds": round(total,3), "jobs": jobs, "events": list(events.values()),
                 "status_latency_seconds": {"count":len(latencies), "median":round(statistics.median(latencies),4) if latencies else None,
                                              "max":max(latencies) if latencies else None},
                 "memory": summarize_memory(samples)}
        result["experiments"].append(entry)
        save()
        print(json.dumps({"event":"experiment_completed", **entry}, ensure_ascii=False), flush=True)
        return entry["status"] == "passed"

    def summarize_memory(samples):
        if not samples:
            return {}
        processes = [p for r in samples for p in r["processes"]]
        large_pids = sorted({p["pid"] for p in processes if p["working_set"] > 2 * GiB})
        return {"min_available_physical": min(r["system"]["available_physical"] for r in samples),
                "min_commit_available": min(r["system"]["commit_available"] for r in samples),
                "large_python_pids": large_pids,
                "max_large_processes_at_once": max(sum(p["working_set"]>2*GiB for p in r["processes"]) for r in samples),
                "peak_largest_python_working_set": max((p["working_set"] for p in processes), default=0),
                "peak_largest_python_private_bytes": max((p["private_bytes"] for p in processes), default=0)}
    try:
        save()
        print(json.dumps({"event":"preflight_passed", "case":str(case), "memory":baseline}), flush=True)
        warmed, elapsed = call("warmup", "--model", args.model, "--device", "CPU")
        result["warmup"] = {"wall_seconds":elapsed, **warmed, "memory":summarize_memory(list(readings))}
        save()
        print(json.dumps({"event":"warmup_completed", **result["warmup"]}), flush=True)
        if experiment("single",1):
            experiment("two_requests",2)
        result["status"] = "completed"
    except Exception as exc:
        result.update(status="stopped", reason=f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()
        watcher.join(2)
        result["overall_memory"] = summarize_memory(list(readings))
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["model_left_resident"] = True
        save()
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
