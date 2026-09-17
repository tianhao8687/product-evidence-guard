from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from protocol import build_request
from server import ServerApplication, ServerState
from product_evidence_guard.engine import analyze_directory


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "input"
        self.output = self.root / "output"
        self.source.mkdir()
        (self.source / "spec.txt").write_text("净重：320g", encoding="utf-8")
        self.summary = analyze_directory(self.source, self.output)
        self.state = ServerState(runtime_dir=self.root / "runtime", startup_id="test", idle_timeout=300,
                                 pid=1, process_start_marker="test")
        self.app = ServerApplication(self.state, threading.Event(),
            analyze=lambda a, b, **kw: analyze_directory(a, b, **kw))
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.app.workflow.close)

    def review(self):
        response = self.app.dispatch(build_request("review", {"output_dir": str(self.output)}))
        self.assertTrue(response["ok"])
        parsed = urlsplit(response["result"]["review_url"])
        self.base = f"{parsed.scheme}://{parsed.netloc}"
        self.token = parse_qs(parsed.fragment)["token"][0]

    def test_semantic_fallback_option_reaches_nonresident_engine(self):
        seen = []
        def analyze(source, output, **options):
            seen.append(options)
            return analyze_directory(source, output, **options)
        self.app._analyze = analyze
        response = self.app.dispatch(build_request("analyze", {"input_dir": str(self.source),
            "output_dir": str(self.output), "no_semantic_assist": True}))
        self.assertTrue(response["ok"], response)
        self.assertIs(seen[0]["semantic_assist"], False)
        self.assertIs(self.app.workflow.requests[str(self.output.resolve())]["no_semantic_assist"], True)
        invalid = self.app.dispatch(build_request("analyze", {"input_dir": str(self.source),
            "output_dir": str(self.output), "no_semantic_assist": "false"}))
        self.assertFalse(invalid["ok"])
        self.assertIs(self.app.workflow.requests[str(self.output.resolve())]["no_semantic_assist"], True)

    def request(self, path, *, authorized=True, origin=None, body=None):
        headers = {"X-PEG-Token": self.token} if authorized else {}
        if origin:
            headers["Origin"] = origin
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base + path, headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        return urlopen(request, timeout=3)

    def test_review_token_and_origin_are_enforced(self):
        self.review()
        with self.request("/api/state") as response:
            self.assertEqual(json.load(response)["counts"]["candidates"], 1)
            self.assertNotIn("*", response.headers.get("Access-Control-Allow-Origin", ""))
        for arguments in ({"authorized": False}, {"origin": "https://example.com"}):
            with self.assertRaises(HTTPError) as rejected:
                self.request("/api/state", **arguments)
            self.assertEqual(rejected.exception.code, 403)

    def test_decision_reason_is_optional_and_updates_review(self):
        self.review()
        candidate = json.loads((self.output / "product-facts.json").read_text(encoding="utf-8"))["candidates"][0]
        body = {"session_id": self.summary["session_id"], "candidate_id": candidate["candidate_id"], "action": "confirm", "reason": ""}
        with self.request("/api/decision", body=body) as response:
            self.assertEqual(json.load(response)["decision"]["status"], "confirmed")
        body["reason"] = "人工已核对该测试说明书"
        with self.request("/api/decision", body=body) as response:
            self.assertEqual(json.load(response)["decision"]["status"], "confirmed")
        with self.request("/api/state") as response:
            self.assertEqual(json.load(response)["counts"]["confirmed"], 1)

    def test_preview_fails_when_original_changes(self):
        self.review()
        candidate = json.loads((self.output / "product-facts.json").read_text(encoding="utf-8"))["candidates"][0]
        with self.request("/api/preview?candidate=" + candidate["candidate_id"]) as response:
            self.assertIn("320g", json.load(response)["text"])
        (self.source / "spec.txt").write_text("净重：999g", encoding="utf-8")
        with self.assertRaises(HTTPError):
            self.request("/api/preview?candidate=" + candidate["candidate_id"])

    def test_conversation_review_action_can_edit_skip_and_undo(self):
        def call(operation, **payload):
            response = self.app.dispatch(build_request(operation, {"output_dir":str(self.output), **payload}))
            self.assertTrue(response["ok"], response)
            return response["result"]
        grant = call("allow-review", recipient="WorkBuddy", fields=["net_weight"], reason="允许本次核对净重")
        def summary():
            return call("review-summary", recipient="WorkBuddy", review_id=grant["review_id"])
        first = summary()
        def act(s, group_key, action, **payload):
            return call("review-action", summary_id=s["summary_id"], choice=s[group_key][0]["choices"][0]["choice"],
                        recipient="WorkBuddy", action=action, **payload)
        act(first, "groups", "edit", value="310g")
        self.assertEqual(summary()["groups"][0]["selected_value"], 310)
        skipped = act(summary(), "groups", "skip")
        current = summary()
        self.assertEqual(current["groups"], [])
        self.assertEqual(len(current["excluded_groups"]), 1)
        act(current, "excluded_groups", "undo", undo_token=skipped["undo_token"])
        self.assertEqual(summary()["groups"][0]["selected_value"], 310)

    def test_background_job_deduplicates_active_output_and_persists_completion(self):
        started, release = threading.Event(), threading.Event()
        def analyze(a, b, **kw):
            started.set()
            release.wait(3)
            return analyze_directory(a, b, **kw)
        self.app._analyze = analyze
        payload = {"input_dir": str(self.source), "output_dir": str(self.output), "background": True}
        # A held business lock must leave the job queued, including on repeat
        # analysis of an output that already has a reviewable result.
        self.app.workflow.snapshot(self.output)
        with self.app._operation_lock:
            response = self.app.dispatch(build_request("analyze", payload))
            job_id = response["result"]["job"]["job_id"]
            self.assertEqual(self.app.workflow.jobs.snapshot(job_id)["phase"], "queued")
            waiting = self.app.workflow.snapshot(self.output)
            self.assertEqual(waiting["next_action"], "wait_for_analysis")
            self.assertNotIn("performance", waiting)
        self.assertTrue(started.wait(1))
        try:
            duplicate = self.app.dispatch(build_request("analyze", payload))
            self.assertEqual(duplicate["result"]["job"]["job_id"], job_id)
            status = self.app.dispatch(build_request("job", {"job_id": job_id}))
            self.assertEqual(status["result"]["job"]["phase"], "running")
            self.assertNotIn("spec.txt", json.dumps(status))
        finally:
            release.set()
        for thread in self.app.workflow.jobs.threads:
            thread.join(3)
        self.assertEqual(self.app.workflow.jobs.snapshot(job_id)["phase"], "completed")
        stored = json.loads((self.root / "runtime" / "jobs" / (job_id + ".json")).read_text())
        self.assertEqual(stored["phase"], "completed")

    def test_residency_does_not_claim_an_unloaded_model_is_ready(self):
        response = self.app.dispatch(build_request("residency", {"keep_alive": True}))
        self.assertTrue(response["result"]["resident"]["keep_alive"])
        self.assertFalse(response["result"]["resident"]["model_ready"])
        self.app.dispatch(build_request("residency", {"keep_alive": False}))
        self.assertFalse(self.app.workflow.resident["keep_alive"])

    def test_failed_update_marks_previous_results_and_a_later_success_clears_warning(self):
        def fail(*args, **kwargs):
            raise RuntimeError("injected update failure")
        self.app._analyze = fail
        (self.source / "new.txt").write_text("净重：350g", encoding="utf-8")
        payload = {"input_dir": str(self.source), "output_dir": str(self.output), "background": True}
        self.app.dispatch(build_request("analyze", payload))
        for thread in self.app.workflow.jobs.threads:
            thread.join(5)
        snapshot = self.app.workflow.snapshot(self.output)
        self.assertEqual(snapshot["phase"], "needs_attention")
        self.assertTrue(snapshot["showing_previous_result"])
        self.assertTrue(snapshot["coverage"]["can_retry"])
        self.app._analyze = lambda a, b, **kw: analyze_directory(a, b, **kw)
        self.app.workflow.jobs.resume(snapshot["retry_job_id"])
        for thread in self.app.workflow.jobs.threads:
            thread.join(5)
        snapshot = self.app.workflow.snapshot(self.output)
        self.assertNotIn("showing_previous_result", snapshot)
        self.assertEqual(snapshot["phase"], "blocked")


if __name__ == "__main__":
    unittest.main()
