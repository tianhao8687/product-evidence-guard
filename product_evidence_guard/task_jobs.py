"""Persistent job metadata; inference stays in the existing resident worker."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import threading
from typing import Any, Callable
import uuid

from .confirmation import ConfirmationError, ConfirmationRequestError, _load_json_object
from .state import atomic_write_json
from .workflow import now


class TaskJobs:
    def __init__(self, runtime_dir: Path, execute: Callable, cancel_running: Callable | None = None) -> None:
        self.directory = runtime_dir / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.execute = execute
        self.cancel_running = cancel_running
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.threads: list[threading.Thread] = []
        self.unreadable_records = 0
        for path in self.directory.glob("*.json"):
            try:
                data = _load_json_object(path)
            except (ConfirmationError, OSError, UnicodeError):
                self.unreadable_records += 1
                continue  # Preserve corrupt records for diagnosis; no content in logs.
            if (data.get("job_id") != path.stem or not isinstance(data.get("payload"), dict)
                    or not data["payload"].get("output_dir") or not isinstance(data.get("progress"), dict)
                    or data.get("phase") not in {"queued", "running", "cancel_requested", "cancelled", "completed", "failed", "interrupted"}
                    or not all(k in data for k in ("created_at", "updated_at"))):
                self.unreadable_records += 1
                continue
            if data.get("phase") in {"queued", "running", "cancel_requested"}:
                data.update(phase="interrupted", updated_at=now())
                atomic_write_json(path, data)
            self.jobs[path.stem] = data

    def _write(self, job: dict) -> None:
        job["updated_at"] = now()
        atomic_write_json(self.directory / (job["job_id"] + ".json"), job)

    def submit(self, payload: dict) -> dict:
        clean = {k: v for k, v in payload.items() if k not in {"background", "brief"}}
        clean["input_dir"] = str(Path(clean["input_dir"]).resolve())
        output = str(Path(clean.get("output_dir") or Path(clean["input_dir"]) / ".peg-output").resolve())
        clean["output_dir"] = output
        with self.lock:
            active = [j for j in self.jobs.values() if j["phase"] in {"queued", "running", "cancel_requested"}]
            for job in active:
                if job["payload"]["output_dir"] == output:
                    if job["payload"] == clean:
                        return self.snapshot(job["job_id"])
                    raise ConfirmationRequestError("这个输出目录已有不同配置的任务，请等待完成或取消后再提交。")
            if len(active) >= 8:
                raise ConfirmationRequestError("已有多个任务等待处理，请在当前任务完成后重试。")
            job = {"job_id": uuid.uuid4().hex, "phase": "queued", "payload": clean,
                   "created_at": now(), "updated_at": now(), "progress": {"stage": "queued"}}
            self.jobs[job["job_id"]] = job
            self._write(job)
            thread = threading.Thread(target=self._run, args=(job["job_id"],), daemon=True, name="peg-analysis-job")
            self.threads = [t for t in self.threads if t.is_alive()]
            self.threads.append(thread)
            thread.start()
            return self.snapshot(job["job_id"])

    def _run(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            if job["phase"] == "cancelled":
                return
            payload = dict(job["payload"])
        def progress(stage: str, details: dict) -> None:
            with self.lock:
                if job["phase"] in {"cancel_requested", "cancelled"}:
                    raise ConfirmationRequestError("任务已取消，已完成文件可在恢复时复用。")
                # The executor emits progress only after acquiring the model's
                # business lock; creating a waiting thread is not running AI.
                if stage != "queued":
                    job["phase"] = "running"
                job["progress"] = {"stage": stage, **details}
                self._write(job)
        try:
            # Attach ownership to the callback, not the persisted/API payload.
            progress.job_id = job_id
            result = self.execute(payload, progress)
            with self.lock:
                phase = ("cancelled" if job["phase"] in {"cancel_requested", "cancelled"} else
                         "completed" if result.get("ok") else "interrupted" if (result.get("error") or {}).get("code") == "shutdown" else "failed")
                job.update(phase=phase, response=result)
                self._write(job)
        except Exception as exc:
            with self.lock:
                job.update(phase="cancelled" if job["phase"] in {"cancel_requested", "cancelled"} else "failed", error_type=type(exc).__name__)
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
                "resume" if job["phase"] in {"failed", "interrupted", "cancelled"} else "check_job_later")
            return result

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ConfirmationRequestError("找不到该任务。")
            phase = job["phase"]
            if phase not in {"queued", "running"}:
                return self.snapshot(job_id)
            job["phase"] = "cancel_requested" if phase == "running" else "cancelled"
            self._write(job)
        if phase == "running" and self.cancel_running:
            self.cancel_running(job_id)
        return self.snapshot(job_id)

    def resume(self, job_id: str) -> dict:
        job = self.snapshot(job_id, detailed=True)
        if job["phase"] in {"queued", "running", "cancel_requested"}:
            return self.snapshot(job_id)
        # The existing engine reuses successful cached files and retries failures.
        return self.submit(job["payload"])

    def for_output(self, output: Path) -> list[dict]:
        with self.lock:
            return [self.snapshot(j["job_id"], detailed=True) for j in sorted(self.jobs.values(), key=lambda j: (j["created_at"], j["job_id"]))
                    if Path(j["payload"]["output_dir"]).resolve() == output.resolve()][-8:]

    @property
    def busy(self) -> bool:
        with self.lock:
            return any(j["phase"] in {"queued", "running", "cancel_requested"} for j in self.jobs.values())
