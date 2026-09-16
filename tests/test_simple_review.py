"""User-facing review actions retain original evidence and authorization bounds."""
import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard import workflow
from product_evidence_guard.confirmation import apply_review_action, ConfirmationRequestError
from product_evidence_guard.engine import analyze_directory


class SimpleReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "inputs"
        self.output = Path(self.tmp.name) / "output"
        self.root.mkdir()
        (self.root / "a.txt").write_text("净重：300g\n噪声：45dB\n", encoding="utf8")
        (self.root / "b.txt").write_text("净重：320g\n", encoding="utf8")
        self.run = analyze_directory(self.root, self.output)

    def snapshot(self):
        return workflow.task_snapshot(self.output, detailed=True)

    def act(self, action, field="net_weight", **changes):
        snap = self.snapshot()
        group = next(g for g in snap["product"]["facts"] if g["field"] == field)
        return apply_review_action(self.output, session_id=self.run["session_id"], action=action,
            group_id=group["group_id"], expected_candidate_ids=group["candidate_ids"],
            candidate_id=group["candidate_ids"][0], **changes)

    def undo(self, token):
        return apply_review_action(self.output, session_id=self.run["session_id"], action="undo", undo_token=token)

    def test_adopt_once_resolves_conflict_without_reason_and_can_undo(self):
        result = self.act("adopt")
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 0)
        self.assertEqual(self.snapshot()["counts"]["verified_facts"], 2)
        self.undo(result["undo_token"])
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 1)

    def test_correction_keeps_original_evidence_and_survives_reload(self):
        result = self.act("edit", value="310g")
        snap = self.snapshot()
        group = next(g for g in snap["product"]["facts"] if g["field"] == "net_weight")
        self.assertEqual(group["fact_status"], "verified")
        self.assertEqual(group["selected_value"], 310)
        edited = next(c for c in snap["product"]["candidates"] if c["extraction_method"] == "human_correction")
        self.assertIn(edited["raw_value"], {"300g", "320g"})
        self.assertEqual(edited["provenance"]["human_correction"]["input_value"], "310g")
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf8"), "净重：300g\n噪声：45dB\n")
        grant = workflow.create_handoff(self.output, candidate_ids=[edited["candidate_id"]], recipient="WorkBuddy", purpose="本次介绍")
        self.assertEqual(workflow.get_handoff(self.output, bundle_id=grant["bundle_id"], recipient="WorkBuddy")["facts"][0]["value"], 310)
        analyze_directory(self.root, self.output)
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 0)
        self.undo(result["undo_token"])
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 1)
        with self.assertRaises(ConfirmationRequestError):
            workflow.get_handoff(self.output, bundle_id=grant["bundle_id"], recipient="WorkBuddy")

    def test_change_to_any_evidence_invalidates_correction_and_undo(self):
        result = self.act("edit", value="310g")
        (self.root / "b.txt").write_text("净重：999g", encoding="utf8")
        snap = self.snapshot()
        self.assertGreater(snap["counts"]["pending_facts"], 0)
        self.assertFalse(any(f["field"] == "net_weight" for f in snap["available_facts"]))
        with self.assertRaises(ConfirmationRequestError):
            self.undo(result["undo_token"])
        analyze_directory(self.root, self.output)
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 1)

    def test_skip_is_not_a_pending_fact_and_can_be_undone(self):
        result = self.act("skip")
        snap = self.snapshot()
        self.assertEqual(snap["counts"]["conflicts"], 0)
        self.assertEqual(snap["counts"]["pending_facts"], 0)
        self.assertEqual(snap["counts"]["excluded_facts"], 1)
        self.assertEqual(workflow.export_table(self.output)["confirmed_count"], 1)
        analyze_directory(self.root, self.output)
        self.assertEqual(self.snapshot()["counts"]["excluded_facts"], 1)
        self.undo(result["undo_token"])
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 1)

    def test_auto_facts_are_usable_but_never_auto_authorized(self):
        snap = self.snapshot()
        self.assertEqual(snap["bundles"], [])
        fact = snap["available_facts"][0]
        grant = workflow.create_handoff(self.output, candidate_ids=[fact["candidate_id"]], recipient="WorkBuddy", purpose="本次介绍")
        self.assertEqual(len(workflow.get_handoff(self.output, bundle_id=grant["bundle_id"], recipient="WorkBuddy")["facts"]), 1)
        self.assertEqual(self.snapshot()["counts"]["confirmed"], 0)
        with self.assertRaises(ConfirmationRequestError):
            workflow.export_local(self.output)
        self.assertEqual(workflow.export_local(self.output, allow_partial=True)["confirmed_count"], 1)
        with self.assertRaises(ConfirmationRequestError):
            workflow.export_table(self.output, mode="human")

    def test_partial_reading_is_separate_from_fact_status(self):
        coverage = workflow.coverage_summary({"files_discovered": 4, "skipped_files": [{"file":"private.pdf","reason":"timeout"}],
            "scanned_pdf":{"pages_skipped":2}})
        self.assertEqual(coverage["status"], "partial")
        self.assertEqual(coverage["unfinished_files"], 1)
        self.assertEqual(coverage["unfinished_pages"], 2)
        self.assertNotIn("private.pdf", json.dumps(coverage))

    def test_selected_product_export_readiness_does_not_borrow_another_conflict(self):
        (self.root / "a.txt").write_text("SKU：A1\n净重：300g", encoding="utf8")
        (self.root / "b.txt").write_text("SKU：B2\n净重：320g", encoding="utf8")
        (self.root / "c.txt").write_text("SKU：B2\n净重：330g", encoding="utf8")
        analyze_directory(self.root, self.output)
        snap = self.snapshot()
        self.assertFalse(snap["delivery_readiness"]["complete"])
        ready = next(p["product_id"] for p in snap["product"]["products"] if p["sku"] == "A1")
        blocked = next(p["product_id"] for p in snap["product"]["products"] if p["sku"] == "B2")
        self.assertTrue(snap["delivery_by_product"][ready]["complete"])
        self.assertFalse(snap["delivery_by_product"][blocked]["complete"])
        self.assertTrue(workflow.export_table(self.output, product_ids=[ready])["delivery_scope"]["complete"])

    def test_stale_membership_and_empty_value_are_rejected(self):
        with self.assertRaises(ConfirmationRequestError):
            self.act("edit", value="")
        with self.assertRaises(ConfirmationRequestError):
            apply_review_action(self.output, session_id=self.run["session_id"], action="skip", group_id="old", expected_candidate_ids=["old"])
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 1)

    def test_scope_can_be_selected_without_editing_source(self):
        (self.root / "scope.txt").write_text("电压：5V", encoding="utf8")
        analyze_directory(self.root, self.output)
        self.assertEqual(next(g for g in self.snapshot()["product"]["facts"] if g["field"] == "voltage")["review_reason_code"], "scope_unclear")
        result = self.act("edit", field="voltage", value="5V", scope="input")
        groups = [g for g in self.snapshot()["product"]["facts"] if g["field"] == "voltage" and not g["excluded"]]
        self.assertEqual([(g["scope"], g["fact_status"]) for g in groups], [("input", "verified")])
        self.undo(result["undo_token"])
        self.assertTrue(any(g["review_reason_code"] == "scope_unclear" for g in self.snapshot()["product"]["facts"]))

    def test_product_assignment_is_one_action_and_bound_to_identity_evidence(self):
        (self.root / "a.txt").write_text("型号：A1\n净重：300g", encoding="utf8")
        (self.root / "b.txt").write_text("型号：B2\n净重：320g", encoding="utf8")
        (self.root / "unknown.txt").write_text("噪声：45dB", encoding="utf8")
        analyze_directory(self.root, self.output)
        snap = self.snapshot()
        noise = next(g for g in snap["product"]["facts"] if g["field_label"] == "噪声")
        target = next(p["product_id"] for p in snap["product"]["products"] if p["model"] == "A1")
        result = self.act("edit", field=noise["field"], value="45dB", product_id=target)
        fact = next(f for f in self.snapshot()["available_facts"] if f["field"] == noise["field"])
        self.assertEqual(fact["product_id"], target)
        self.assertEqual(fact["product_identity_status"], "human_assigned")
        (self.root / "a.txt").write_text("型号：C3\n净重：300g", encoding="utf8")
        self.assertFalse(any(f["field"] == noise["field"] for f in self.snapshot()["available_facts"]))
        with self.assertRaises(ConfirmationRequestError):
            self.undo(result["undo_token"])

    def test_correction_recovery_from_interrupted_report_write(self):
        from unittest.mock import patch
        with patch("product_evidence_guard.confirmation._synchronize_analysis_outputs", side_effect=OSError("interrupted")):
            # The first synchronize belongs to the read-only validation pass.
            with self.assertRaises(OSError):
                self.act("edit", value="310g")
        self.assertEqual(self.snapshot()["counts"]["conflicts"], 1)
        from product_evidence_guard import confirmation
        original = confirmation._synchronize_analysis_outputs
        calls = []
        def fail_after_state(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("interrupted after state commit")
            return original(*args, **kwargs)
        snap = self.snapshot()
        group = next(g for g in snap["product"]["facts"] if g["field"] == "net_weight")
        with patch.object(confirmation, "_synchronize_analysis_outputs", side_effect=fail_after_state):
            with self.assertRaises(OSError):
                apply_review_action(self.output, session_id=self.run["session_id"], action="edit", value="310g",
                    group_id=group["group_id"], candidate_id=group["candidate_ids"][0], expected_candidate_ids=group["candidate_ids"])
        self.assertEqual(next(g["selected_value"] for g in self.snapshot()["product"]["facts"] if g["field"] == "net_weight"), 310)

    def test_model_correction_updates_display_and_export_without_rewriting_sources(self):
        (self.root / "model.txt").write_text("型号：OLD-1", encoding="utf8")
        analyze_directory(self.root, self.output)
        self.act("edit", field="model", value="NEW-2")
        snap = self.snapshot()
        self.assertEqual(snap["product"]["products"][0]["label"], "NEW-2")
        table = workflow.export_table(self.output, allow_partial=True)
        self.assertIn("NEW-2", Path(table["path"]).read_text(encoding="utf-8-sig"))
        self.assertEqual((self.root / "model.txt").read_text(encoding="utf8"), "型号：OLD-1")


if __name__ == "__main__":
    unittest.main()
