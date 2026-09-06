"""Persistent job metadata; inference stays in the existing resident worker."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading
from typing import Any, Callable
import uuid

from .confirmation import ConfirmationRequestError, _load_json_object
from .state import atomic_write_json
from .workflow import now


class TaskJobs:
    def __init__(self, runtime_dir: Path, execute: Callable) -> None:
        self.directory = runtime_dir / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.execute = execute
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.threads: list[threading.Thread] = []
        for path in self.directory.glob("*.json"):
            data = _load_json_object(path)
            if data.get("job_id") != path.stem:
                continue
            if data.get("phase") in {"queued", "running"}:
                data.update(phase="interrupted", updated_at=now())
                atomic_write_json(path, data)
            self.jobs[path.stem] = data

    def _write(self, job: dict) -> None:
        job["updated_at"] = now()
        atomic_write_json(self.directory / (job["job_id"] + ".json"), job)

    def submit(self, payload: dict) -> dict:
        clean = {k: v for k, v in payload.items() if k not in {"background", "brief"}}
        output = str(Path(clean.get("output_dir") or Path(clean["input_dir"]) / ".peg-output").resolve())
        clean["output_dir"] = output
        with self.lock:
            active = [j for j in self.jobs.values() if j["phase"] in {"queued", "running"}]
            for job in active:
                if job["payload"]["output_dir"] == output:
                    return self.snapshot(job["job_id"])
            if len(active) >= 8:
                raise ConfirmationRequestError("已有多个任务等待处理，请在当前任务完成后重试。")
            job = {"job_id": uuid.uuid4().hex, "phase": "queued", "payload": clean,
                   "created_at": now(), "updated_at": now(), "progress": {"stage": "queued"}}
            self.jobs[job["job_id"]] = job
            self._write(job)
            thread = threading.Thread(target=self._run, args=(job["job_id"],), daemon=True, name="peg-analysis-job")
            self.threads.append(thread)
            thread.start()
            return self.snapshot(job["job_id"])

    def _run(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            payload = dict(job["payload"])
        def progress(stage: str, details: dict) -> None:
            with self.lock:
                # The executor emits progress only after acquiring the model's
                # business lock; creating a waiting thread is not running AI.
                if stage != "queued":
                    job["phase"] = "running"
                job["progress"] = {"stage": stage, **details}
                self._write(job)
        try:
            result = self.execute(payload, progress)
            with self.lock:
                phase = "completed" if result.get("ok") else "interrupted" if (result.get("error") or {}).get("code") == "shutdown" else "failed"
                job.update(phase=phase, response=result)
                self._write(job)
        except Exception as exc:
            with self.lock:
                job.update(phase="failed", error_type=type(exc).__name__)
                self._write(job)

    def snapshot(self, job_id: str, *, detailed: bool = False) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ConfirmationRequestError("找不到该任务。")
            if detailed:
                return deepcopy(job)
            result = {k: job[k] for k in ("job_id", "phase", "created_at", "updated_at")}
            result["progress"] = {k: v for k, v in job["progress"].items() if k in
                                  {"stage", "index", "total", "files_processed", "load_seconds", "reused"}}
            result["next_action"] = "inspect_task" if job["phase"] == "completed" else (
                "resume" if job["phase"] in {"failed", "interrupted"} else "check_job_later")
            return result

    def resume(self, job_id: str) -> dict:
        job = self.snapshot(job_id, detailed=True)
        if job["phase"] in {"queued", "running"}:
            return self.snapshot(job_id)
        # The existing engine reuses successful cached files and retries failures.
        return self.submit(job["payload"])

    def for_output(self, output: Path) -> list[dict]:
        with self.lock:
            return [self.snapshot(j["job_id"], detailed=True) for j in self.jobs.values()
                    if Path(j["payload"]["output_dir"]).resolve() == output.resolve()][-8:]

    @property
    def busy(self) -> bool:
        with self.lock:
            return any(j["phase"] in {"queued", "running"} for j in self.jobs.values())
