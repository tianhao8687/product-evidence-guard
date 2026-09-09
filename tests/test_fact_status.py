"""User-facing facts are not candidate decisions or cloud permissions."""
from dataclasses import replace
import csv
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.graph import build_graph
from product_evidence_guard.models import FactCandidate
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.confirmation import ConfirmationRequestError, apply_decision, resolve_conflict_group
from product_evidence_guard import workflow


def candidate(cid="a", **changes):
    data = dict(candidate_id=cid, field="net_weight", field_label="净重", raw_value="320g",
                normalized_value=320, normalized_unit="g", source_block_id=cid,
                source_file=f"{cid}.txt", source_kind="txt", file_hash=cid,
                locator={"line": 1}, raw_text="净重：320g", recognition_confidence=1,
                mapping_confidence=1, extraction_method="deterministic_parser", scope="net",
                product_id="product_test", product_identity_status="explicit")
    data.update(changes)
    return FactCandidate(**data)


class FactStatusTests(unittest.TestCase):
    def fact(self, *items):
        groups = build_graph(items)[1]
        self.assertEqual(len(groups), 1)
        return groups[0]

    def test_single_value_is_usable_without_inventing_human_approval(self):
        item = candidate()
        group = self.fact(item)
        self.assertEqual((group.fact_status, group.verification_method), ("verified", "single_value"))
        self.assertEqual((item.status, item.decision_status), ("pending", "undecided"))
        self.assertFalse(group.human_approved)
        self.assertEqual(group.selected_value, 320)

    def test_agreement_merges_and_copied_files_do_not_count_as_independent(self):
        a = candidate()
        copy = candidate("copy", file_hash=a.file_hash)
        self.assertEqual(self.fact(a, copy).independent_source_count, 1)
        b = candidate("b", raw_value="0.32kg")
        group = self.fact(a, copy, b)
        self.assertEqual((group.fact_status, group.verification_method), ("verified", "cross_source_converted"))
        self.assertEqual(group.independent_source_count, 2)
        self.assertEqual(len(group.verified_candidate_ids), 3)

    def test_scopes_are_separate_and_missing_required_scope_is_pending(self):
        a = candidate(field="voltage", field_label="电压", scope="input", normalized_unit="V", normalized_value=12)
        b = replace(a, candidate_id="b", source_block_id="b", scope="output", normalized_value=5)
        groups = build_graph([a, b])[1]
        self.assertEqual(len(groups), 2)
        self.assertEqual({g.fact_status for g in groups}, {"verified"})
        unclear = self.fact(replace(a, scope=None))
        self.assertEqual(unclear.review_reason_code, "scope_unclear")

    def test_real_ambiguities_are_pending_not_conflicts(self):
        for changes, reason in [({"product_identity_status": "ambiguous"}, "identity_ambiguous"),
                                ({"source_current": False}, "source_changed"),
                                ({"recognition_confidence": .4}, "unclear_value"),
                                ({"notes": ["unparsed_unit"]}, "unclear_value")]:
            with self.subTest(reason=reason):
                group = self.fact(candidate(**changes), candidate("b", normalized_value=999))
                self.assertEqual((group.fact_status, group.review_reason_code), ("pending_confirmation", reason))

    def test_majority_and_confirmation_alone_cannot_hide_conflict(self):
        a, b, c = candidate(), candidate("b"), candidate("c", normalized_value=585)
        self.assertEqual(self.fact(a, b, c).fact_status, "conflict")
        a.status = "confirmed"
        self.assertEqual(self.fact(a, b, c).fact_status, "conflict")
        c.status = "rejected"
        group = self.fact(a, b, c)
        self.assertEqual((group.fact_status, group.verification_method), ("verified", "human_resolved_conflict"))
        self.assertTrue(group.human_approved)
        self.assertEqual(group.selected_value, 320)

    def test_versions_only_need_review_if_the_value_differs(self):
        a = candidate(source_file="spec_v1.txt")
        b = candidate("b", source_file="manual_v1.txt")
        c = candidate("c", source_file="spec_v2.txt")
        self.assertEqual(self.fact(a, b, c).fact_status, "verified")
        c.normalized_value = 585
        self.assertEqual(self.fact(a, b, c).review_reason_code, "version_unclear")

    def test_rejected_values_and_stale_sources_never_become_selected(self):
        self.assertEqual(self.fact(candidate(status="rejected")).review_reason_code, "all_rejected")
        group = self.fact(candidate(status="confirmed", source_current=False))
        self.assertFalse(group.current)
        self.assertFalse(group.human_approved)
        self.assertIsNone(group.selected_value)


class AutomaticFactWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.output = self.root / "source", self.root / "output"
        self.source.mkdir()
        (self.source / "a.txt").write_text("净重：320g\n", encoding="utf-8")
        (self.source / "b.txt").write_text("净重：0.32kg\n", encoding="utf-8")
        self.summary = analyze_directory(self.source, self.output)

    def test_auto_export_is_one_fact_but_not_a_human_decision_or_handoff(self):
        _, product, human = workflow.context(self.output)
        self.assertEqual(product["status"], "verified")
        self.assertEqual(len(product["facts"]), 1)
        self.assertEqual(len(product["evidence_candidates"]), 2)
        self.assertEqual(human, [])
        with self.assertRaises(ConfirmationRequestError):
            workflow.export_table(self.output)
        with self.assertRaises(ConfirmationRequestError):
            workflow.create_handoff(self.output, candidate_ids=[product["candidates"][0]["candidate_id"]],
                                    recipient="Qoder", purpose="test")
        result = workflow.export_table(self.output, mode="verified")
        with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][-2:], ["自动核验", "否"])
        self.assertEqual(workflow.task_snapshot(self.output, detailed=True)["deliverables"][0]["status"], "covered_fields_match")
        (self.source / "b.txt").write_text("净重：500g\n", encoding="utf-8")
        snapshot = workflow.task_snapshot(self.output, detailed=True)
        self.assertEqual(snapshot["counts"]["pending_facts"], 1)
        self.assertEqual(snapshot["counts"]["conflicts"], 0)
        self.assertEqual(snapshot["deliverables"][0]["status"], "source_stale")
        with self.assertRaises(ConfirmationRequestError):
            workflow.export_table(self.output, mode="verified")

    def test_human_confirmation_and_reanalysis_preserve_separate_approval(self):
        _, product, _ = workflow.context(self.output)
        apply_decision(self.output, session_id=self.summary["session_id"],
                       candidate_id=product["candidates"][0]["candidate_id"], action="confirm", reason="采用此参数")
        analyze_directory(self.source, self.output)
        _, product, human = workflow.context(self.output)
        self.assertEqual(product["status"], "ready_to_export")
        self.assertEqual(len(human), 1)
        self.assertEqual(product["facts"][0]["verification_method"], "human_confirmed")

    def test_resolving_conflict_keeps_agreeing_sources_and_records_only_real_choices(self):
        (self.source / "c.txt").write_text("净重：585g\n", encoding="utf-8")
        analyze_directory(self.source, self.output)
        _, product, _ = workflow.context(self.output)
        group = product["facts"][0]
        chosen = next(c for c in product["candidates"] if c["source_file"] == "a.txt")
        resolve_conflict_group(self.output, session_id=product["run_summary"]["session_id"],
            group_id=group["group_id"], selected_candidate_id=chosen["candidate_id"],
            expected_candidate_ids=group["candidate_ids"], reject_others=True, reason="采用 320g，排除 585g")
        _, product, human = workflow.context(self.output)
        self.assertEqual(len(human), 1)
        self.assertEqual(product["facts"][0]["independent_source_count"], 2)
        self.assertEqual(product["facts"][0]["verification_method"], "human_resolved_conflict")
        self.assertEqual(next(c for c in product["candidates"] if c["source_file"] == "b.txt")["decision_status"], "undecided")

    def test_existing_handoff_is_blocked_when_an_agreeing_source_changes(self):
        _, product, _ = workflow.context(self.output)
        chosen = next(c for c in product["candidates"] if c["source_file"] == "a.txt")
        apply_decision(self.output, session_id=self.summary["session_id"], candidate_id=chosen["candidate_id"],
                       action="confirm", reason="采用 320g")
        handoff = workflow.create_handoff(self.output, candidate_ids=[chosen["candidate_id"]], recipient="Qoder", purpose="test")
        (self.source / "b.txt").write_text("净重：585g\n", encoding="utf-8")
        with self.assertRaises(ConfirmationRequestError):
            workflow.get_handoff(self.output, bundle_id=handoff["bundle_id"], recipient="Qoder")
        self.assertEqual(workflow.task_snapshot(self.output, detailed=True)["bundles"][0]["status"], "stale")


if __name__ == "__main__":
    unittest.main()
