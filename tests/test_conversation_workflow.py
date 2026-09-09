from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from protocol import build_request
from server import ServerApplication, ServerState
from product_evidence_guard import conversation, workflow
from product_evidence_guard.confirmation import ConfirmationRequestError
from product_evidence_guard.engine import analyze_directory


class ConversationWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.input = self.root / "input"
        self.output = self.root / "output"
        self.input.mkdir()
        (self.input / "private-client-manual.txt").write_text("净重：320g\n型号：PRIVATE-42\n", encoding="utf-8")
        (self.input / "private-packaging.txt").write_text("净重：300g\n", encoding="utf-8")
        self.analysis = analyze_directory(self.input, self.output)

    def permit(self):
        return conversation.allow_review(self.output, recipient="Qoder", fields=["net_weight"],
                                         reason="允许在 Qoder 中展示本批净重摘要以供核对")

    def summary(self):
        permission = self.permit()
        self.permission = permission
        return conversation.review_summary(self.output, review_id=permission["review_id"], recipient="Qoder")

    def test_permission_is_required_and_scope_excludes_unapproved_values_and_sources(self):
        with self.assertRaises(ConfirmationRequestError):
            conversation.review_summary(self.output, review_id="unknown", recipient="Qoder")
        summary = self.summary()
        text = json.dumps(summary, ensure_ascii=False)
        for secret in ("PRIVATE-42", "private-client", "private-packaging", "raw_text", "file_hash", "candidate_id"):
            self.assertNotIn(secret, text)
        self.assertEqual([g["field"] for g in summary["groups"]], ["net_weight"])
        self.assertEqual({c["value"] for c in summary["groups"][0]["choices"]}, {300, 320})

    def test_identical_permission_is_reused_and_recipient_is_enforced(self):
        first = self.permit()
        second = self.permit()
        self.assertEqual(first["review_id"], second["review_id"])
        self.assertTrue(second["reused"])
        with self.assertRaises(ConfirmationRequestError):
            conversation.review_summary(self.output, review_id=first["review_id"], recipient="Other")

    def test_chat_decisions_use_stable_choices_without_auto_confirming_other_values(self):
        summary = self.summary()
        choices = summary["groups"][0]["choices"]
        selected = next(c["choice"] for c in choices if c["value"] == 320)
        other = next(c["choice"] for c in choices if c["value"] == 300)
        for choice, action in [(selected, "confirm"), (other, "reject")]:
            result = conversation.decide(self.output, summary_id=summary["summary_id"], choice=choice,
                                         recipient="Qoder", action=action, reason="用户明确选择对应选项")
            self.assertEqual(result["status"], "confirmed" if action == "confirm" else "rejected")
        details = workflow.task_snapshot(self.output, detailed=True)
        self.assertEqual(len(details["confirmed"]), 1)
        self.assertEqual(details["confirmed"][0]["normalized_value"], 320)
        self.assertTrue(any(c["field"] == "model" and c["status"] == "pending" for c in details["product"]["candidates"]))

    def test_changed_source_hides_old_value_and_rejects_an_old_dialog_choice(self):
        summary = self.summary()
        choice = next(c["choice"] for c in summary["groups"][0]["choices"] if c["value"] == 320)
        (self.input / "private-client-manual.txt").write_text("净重：330g", encoding="utf-8")
        with self.assertRaises(ConfirmationRequestError):
            conversation.decide(self.output, summary_id=summary["summary_id"], choice=choice,
                                 recipient="Qoder", action="confirm", reason="旧对话选项")
        new = conversation.review_summary(self.output, review_id=self.permission["review_id"], recipient="Qoder")
        stale = [c for c in new["groups"][0]["choices"] if c["status"] == "source_changed"]
        self.assertTrue(new["needs_reanalysis"])
        self.assertTrue(stale)
        self.assertNotIn("value", stale[0])

    def test_revocation_stops_summary_and_decision(self):
        summary = self.summary()
        conversation.revoke_review(self.output, review_id=self.permission["review_id"], recipient="Qoder")
        with self.assertRaises(ConfirmationRequestError):
            conversation.review_summary(self.output, review_id=self.permission["review_id"], recipient="Qoder")

    def test_review_prioritizes_conflict_and_splits_input_output_scope(self):
        (self.input / "electrical.txt").write_text("输入电压：12V\n输出电压：5V\n额定功率：20W\n", encoding="utf-8")
        analyze_directory(self.input, self.output)
        permit = conversation.allow_review(self.output, recipient="Qoder", all_fields=True, reason="允许展示合成参数")
        result = conversation.review_summary(self.output, review_id=permit["review_id"], recipient="Qoder")
        self.assertEqual(result["groups"][0]["field"], "net_weight")
        self.assertEqual(result["groups"][0]["severity"], "block")
        voltage = [g for g in result["groups"] if g["field"] == "voltage"]
        self.assertEqual({g["scope"] for g in voltage}, {"input", "output"})
        self.assertTrue(all(g["fact_status"] == "verified" for g in voltage))
        self.assertEqual(next(g for g in result["groups"] if g["field"] == "power")["scope_label"], "额定")
        self.assertNotIn("electrical.txt", json.dumps(result))

    def test_full_skill_flow_never_starts_the_review_server(self):
        state = ServerState(runtime_dir=self.root / "runtime", startup_id="test", idle_timeout=300,
                             pid=1, process_start_marker="test")
        app = ServerApplication(state, threading.Event(), analyze=lambda a,b,**kw: analyze_directory(a,b,**kw))
        self.addCleanup(app.workflow.close)
        def call(op, **data):
            response = app.dispatch(build_request(op, {"output_dir": str(self.output), **data}))
            self.assertTrue(response["ok"], response.get("error"))
            return response["result"]
        before = call("task")
        self.assertEqual(before["next_action"], "request_review_permission")
        permit = call("allow-review", recipient="Qoder", fields=["net_weight"], reason="同意展示净重摘要")
        summary = call("review-summary", recipient="Qoder", review_id=permit["review_id"])
        choice = next(c["choice"] for c in summary["groups"][0]["choices"] if c["value"] == 320)
        call("decide", summary_id=summary["summary_id"], choice=choice, recipient="Qoder",
             action="confirm", reason="用户在对话中选择 320g")
        # Adopting one value does not silently reject a contradictory source.
        unresolved = app.dispatch(build_request("authorize", {"output_dir": str(self.output),
            "summary_id": summary["summary_id"], "choices": [choice], "recipient": "Qoder", "purpose": "生成商品介绍"}))
        self.assertFalse(unresolved["ok"])
        summary = call("review-summary", recipient="Qoder", review_id=permit["review_id"])
        other = next(c["choice"] for c in summary["groups"][0]["choices"] if c["value"] != 320)
        call("decide", summary_id=summary["summary_id"], choice=other, recipient="Qoder",
             action="reject", reason="用户明确排除另一个值")
        summary = call("review-summary", recipient="Qoder", review_id=permit["review_id"])
        self.assertEqual(summary["next_action"], "review_complete")
        choice = next(c["choice"] for c in summary["groups"][0]["choices"] if c.get("value") == 320)
        grant = call("authorize", summary_id=summary["summary_id"], choices=[choice], recipient="Qoder", purpose="生成商品介绍")
        packet = call("handoff", bundle_id=grant["bundle_id"], recipient="Qoder")
        self.assertEqual(packet["facts"][0]["value"], 320)
        draft = self.root / "draft.md"
        draft.write_text("净重：0.32kg", encoding="utf-8")
        result = call("check-content", bundle_id=grant["bundle_id"], recipient="Qoder", content_file=str(draft))
        self.assertEqual(result["status"], "covered_fields_match")
        self.assertEqual(call("export-table")["confirmed_count"], 1)
        fallback = call("export-local", reason="cloud_unavailable")
        self.assertEqual(fallback["status"], "local_delivery_ready")
        self.assertFalse(fallback["model_called"])
        self.assertIsNone(app.workflow.review)
        self.assertNotIn("review_url", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
