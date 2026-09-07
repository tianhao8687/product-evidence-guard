"""Service adapter shared by the Qoder CLI and the local review surface."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import time
import uuid

from protocol import build_request, success_response, error_response, read_json_object
from product_evidence_guard.confirmation import ConfirmationRequestError
from product_evidence_guard import workflow, conversation
from product_evidence_guard.state import atomic_write_json, atomic_write_text
from product_evidence_guard.task_jobs import TaskJobs


OPERATIONS = {"warmup", "residency", "review", "task", "job", "resume", "authorize", "handoff",
              "revoke", "check-content", "deliverables", "export-table", "export-local", "export-review"}
OPERATIONS |= {"allow-review", "review-summary", "decide", "revoke-review"}


class WorkflowService:
    def __init__(self, app):
        self.app = app
        self.resident = {"model_ready": False, "keep_alive": False}
        self.review = None
        self.cached = {}
        self.request_path = app.state.runtime_dir / "analysis-requests.json"
        self.requests = read_json_object(self.request_path) or {}
        self.jobs = TaskJobs(app.state.runtime_dir, self._execute_job)

    def remember(self, payload):
        output = str(Path(payload.get("output_dir") or Path(payload["input_dir"]) / ".peg-output").resolve())
        self.requests[output] = {k: payload.get(k) for k in
                                 ("input_dir", "openvino_model", "openvino_vlm_model", "device")}
        self.requests[output]["output_dir"] = output
        atomic_write_json(self.request_path, self.requests)

    def _execute_job(self, payload, progress):
        request = build_request("analyze", payload)
        with self.app._operation_lock:
            if self.app.stop_event.is_set():
                return error_response(request, status="shutdown", code="shutdown", message="服务已关闭，任务可恢复。")
            self.app._progress_callback = progress
            try:
                progress("loading", {})
                return self.app._dispatch_analyze(request, payload)
            except Exception as exc:
                self.app.state.transition("error", error=type(exc).__name__)
                return error_response(request, status="error", code="analysis_failed",
                                      message=f"{type(exc).__name__}: {exc}")
            finally:
                self.app._progress_callback = None
                self.app.state.touch()

    def worker_progress(self, stage, details):
        self.app.state.touch()
        if stage == "model_ready" and details.get("has_model", True):
            self.resident.update(model_loaded=True, load_seconds=details.get("load_seconds"),
                                 model_reused=details.get("reused"))
            if not details.get("reused"):
                self.resident["model_ready"] = False
        callback = self.app._progress_callback
        if callback:
            callback(stage, details)

    def resident_snapshot(self):
        if self.resident.get("model_loaded") or self.resident.get("model_ready"):
            process = getattr(self.app._analysis_worker, "_process", None)
            if process is None or not process.is_alive():
                self.resident.update(model_ready=False, model_loaded=False)
        return dict(self.resident)

    def snapshot(self, output: Path, *, detailed=False):
        output = workflow.safe_output(output)
        # A long generation holds the business lock, but progress remains readable.
        if self.app._operation_lock.acquire(blocking=False):
            try:
                data = workflow.task_snapshot(output, detailed=detailed)
                self.cached[(str(output), detailed)] = deepcopy(data)
            finally:
                self.app._operation_lock.release()
        else:
            data = deepcopy(self.cached.get((str(output), detailed),
                            {"phase": "analyzing", "counts": {}, "next_action": "wait_for_analysis"}))
        jobs = self.jobs.for_output(output)
        data["jobs"] = jobs if detailed else [self.jobs.snapshot(j["job_id"]) for j in jobs]
        data["resident"] = self.resident_snapshot()
        if any(j["phase"] in {"queued", "running"} for j in jobs):
            data["phase"] = "analyzing"
            data["next_action"] = "wait_for_analysis"
            data.pop("performance", None)
        return data

    def handle(self, operation, payload):
        if operation == "analyze":
            return {"job": self.jobs.submit(payload)}
        if operation == "job":
            return {"job": self.jobs.snapshot(payload["job_id"])}
        if operation == "resume":
            return {"job": self.jobs.resume(payload["job_id"])}
        if operation == "residency":
            if not isinstance(payload.get("keep_alive"), bool):
                raise ConfirmationRequestError("keep_alive 必须是布尔值。")
            self.resident["keep_alive"] = payload["keep_alive"]
            return {"resident": dict(self.resident)}
        if operation == "warmup":
            model = payload.get("openvino_vlm_model")
            if not model or not Path(model).is_dir():
                raise ConfirmationRequestError("请通过 --model 指定已完整下载的千问模型目录。")
            with self.app._operation_lock:
                self.app.state.transition("loading")
                started = time.perf_counter()
                result = self.app._analysis_worker.analyze(
                    {**payload, "warmup_only": True},
                    on_model_ready=lambda: self.app.state.transition("running"),
                    on_progress=self.worker_progress,
                )
                self.resident.update(model_ready=True, device=payload.get("device", "CPU"),
                                     keep_alive=bool(payload.get("keep_alive", True)),
                                     warmup_seconds=round(time.perf_counter() - started, 4),
                                     model_reused=result.get("model_reused", False))
                self.app.state.transition("running")
                return {"resident": dict(self.resident)}
        output = workflow.safe_output(payload["output_dir"])
        if operation == "review":
            with self.app._operation_lock:
                workflow.context(output, payload.get("session_id"))
                if self.review is None:
                    from product_evidence_guard.review_server import ReviewServer
                    self.review = ReviewServer(self)
                return {"review_url": self.review.open(output), "processing": "local"}
        if operation == "deliverables":
            data = self.snapshot(output, detailed=True)
            return {"deliverables": [{k: d[k] for k in
                     ("artifact_id", "status", "affected_references", "finding_count", "revision", "next_action")}
                     for d in data.get("deliverables", [])]}
        if operation == "task":
            return self.snapshot(output)
        with self.app._operation_lock:
            kwargs = {"session_id": payload.get("session_id")}
            if operation == "allow-review":
                return conversation.allow_review(output, recipient=payload["recipient"], reason=payload["reason"],
                    fields=payload.get("fields"), all_fields=payload.get("all_fields", False), **kwargs)
            if operation == "review-summary":
                return conversation.review_summary(output, review_id=payload["review_id"], recipient=payload["recipient"], **kwargs)
            if operation == "decide":
                return conversation.decide(output, summary_id=payload["summary_id"], choice=payload["choice"],
                    recipient=payload["recipient"], action=payload["action"], reason=payload["reason"], **kwargs)
            if operation == "revoke-review":
                return conversation.revoke_review(output, review_id=payload["review_id"], recipient=payload["recipient"], **kwargs)
            if operation == "authorize":
                ids = payload.get("candidate_ids")
                if payload.get("summary_id"):
                    _, ids = conversation.resolve_choices(output, summary_id=payload["summary_id"],
                        choices=payload.get("choices", []), recipient=payload["recipient"], **kwargs)
                return workflow.create_handoff(output, candidate_ids=ids,
                    recipient=payload["recipient"], purpose=payload["purpose"], **kwargs)
            if operation == "handoff":
                return workflow.get_handoff(output, bundle_id=payload["bundle_id"], recipient=payload["recipient"], **kwargs)
            if operation == "revoke":
                return workflow.revoke_handoff(output, bundle_id=payload["bundle_id"], **kwargs)
            if operation == "check-content":
                return workflow.check_content(output, content_file=payload["content_file"],
                    bundle_id=payload["bundle_id"], recipient=payload["recipient"], **kwargs)
            if operation == "export-table":
                return workflow.export_table(output, **kwargs)
            if operation == "export-review":
                from product_evidence_guard.review_workbook import export_review
                return export_review(output, **kwargs)
            if operation == "export-local":
                return workflow.export_local(output, reason=payload.get("reason", "local_only"), **kwargs)
        raise ConfirmationRequestError("不支持的工作流操作。")

    def review_action(self, output, action, body):
        if action == "decision":
            operation = body.get("action")
            if operation not in {"confirm", "reject"}:
                raise ConfirmationRequestError("请明确选择采用或拒绝。")
            response = self.app.dispatch(build_request(operation, {
                "output_dir": str(output), "session_id": body["session_id"],
                "candidate_id": body["candidate_id"], "reason": body["reason"]}))
            if not response["ok"]:
                raise ConfirmationRequestError(response["error"]["message"])
            return response["result"]
        if action in {"authorize", "revoke", "residency"}:
            return self.handle(action, {**body, "output_dir": str(output)})
        if action == "reanalyze":
            payload = self.requests.get(str(output))
            if not payload:
                raise ConfirmationRequestError("请先在 Qoder 中运行一次新版分析，以保存当前模型与输入配置。")
            return self.jobs.submit(payload)
        if action == "check":
            text = body.get("text")
            if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > workflow.MAX_CONTENT_BYTES:
                raise ConfirmationRequestError("请输入有效文案，最大为 2 MB。")
            draft_id = body.get("draft_id") or uuid.uuid4().hex
            if not re.fullmatch(r"[a-f0-9]{32}", draft_id):
                raise ConfirmationRequestError("草稿编号无效。")
            with self.app._operation_lock:
                # Check the grant before writing even a local generated draft.
                workflow.get_handoff(output, bundle_id=body["bundle_id"], recipient=body["recipient"], session_id=body.get("session_id"))
                folder = output / "drafts"
                if not folder.exists():
                    folder.mkdir()
                workflow.safe_output(folder)
                path = folder / (draft_id + ".md")
                atomic_write_text(path, text)
                result = workflow.check_content(output, content_file=str(path), bundle_id=body["bundle_id"],
                                                recipient=body["recipient"], session_id=body.get("session_id"))
                result["draft_id"] = draft_id
                return result
        raise ConfirmationRequestError("操作不存在。")

    def close(self):
        for thread in self.jobs.threads:
            thread.join(timeout=5)
        if self.review:
            self.review.close()
