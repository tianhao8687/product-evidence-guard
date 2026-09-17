"""Cross-entry contracts: current values, ownership and delivery must agree."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard import workflow
from product_evidence_guard.confirmation import apply_review_action, ConfirmationRequestError
from product_evidence_guard.content_check import check_draft
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.graph import build_graph
from product_evidence_guard.identity import resolve_product_identities
from product_evidence_guard.models import SourceBlock
from tests.test_fact_status import candidate


class ReviewConsistencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.output = self.base / "input", self.base / "output"
        self.root.mkdir()

    def analyze(self, files):
        for name, content in files.items():
            (self.root / name).write_text(content, encoding="utf8")
        self.session = analyze_directory(self.root, self.output)["session_id"]

    def snapshot(self):
        return workflow.task_snapshot(self.output, detailed=True)

    def fact(self, field):
        return next(g for g in self.snapshot()["product"]["facts"]
                    if g["field"] == field and not g.get("excluded"))

    def act(self, field, action, product_id=None, **changes):
        snap = self.snapshot()
        group = next(g for g in snap["product"]["facts"] if g["field"] == field and not g.get("excluded")
                     and (product_id is None or g["product_id"] == product_id))
        selected = next(c for c in snap["product"]["candidates"]
                        if c["candidate_id"] in group["candidate_ids"] and c["status"] != "rejected")
        return apply_review_action(self.output, session_id=self.session, action=action,
            group_id=group["group_id"], expected_candidate_ids=group["candidate_ids"],
            candidate_id=selected["candidate_id"], **changes)

    def export_rows(self, mode):
        result = workflow.export_table(self.output, mode=mode)
        with Path(result["path"]).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    def test_invalid_edits_are_rejected_before_decisions_change(self):
        self.analyze({"a.txt": "SKU: A-1\n净重: 80g\n"})
        for value in ("待定", "TBD", "未提供", "banana", "80", "-80g", "100-80g"):
            with self.subTest(value=value):
                with self.assertRaises(ConfirmationRequestError):
                    self.act("net_weight", "edit", value=value)
                self.assertEqual(self.fact("net_weight")["selected_value"], 80)
                state = json.loads((self.output / "confirmation-state.json").read_text("utf8"))
                self.assertEqual(state["decisions"], {})
                self.assertFalse(state.get("review_history"))

    def test_correcting_placeholder_survives_reload_and_undo(self):
        self.analyze({"a.txt": "SKU: A-1\n材质: 待定\n"})
        result = self.act("material", "edit", value="铝合金")
        self.assertEqual(self.fact("material")["selected_value"], "铝合金")
        manual = next(c for c in self.snapshot()["product"]["candidates"] if c["extraction_method"] == "human_correction")
        self.assertEqual(manual["raw_value"], "待定")
        self.assertEqual(self.export_rows("human")[0]["值"], "铝合金")
        analyze_directory(self.root, self.output)
        self.assertEqual(self.fact("material")["fact_status"], "verified")
        apply_review_action(self.output, session_id=self.session, action="undo", undo_token=result["undo_token"])
        self.assertEqual(self.fact("material")["review_reason_code"], "invalid_value")

    def test_corrected_range_uses_new_input_and_invalid_adopt_is_rejected(self):
        self.analyze({"a.txt": "净重: 100-80g\n"})
        with self.assertRaises(ConfirmationRequestError):
            self.act("net_weight", "adopt")
        self.act("net_weight", "edit", value="80-100g")
        self.assertEqual(self.fact("net_weight")["selected_value"], [80, 100])
        self.assertEqual(self.fact("net_weight")["fact_status"], "verified")

    def test_legacy_manual_values_are_rechecked_on_graph_rebuild(self):
        for value, unit, notes, expected in (("待定", None, ["unparsed_unit"], "invalid_value"),
                                           ("80", None, ["unparsed_unit"], "unclear_value"),
                                           (80, "g", [], None)):
            with self.subTest(value=value):
                item = candidate(raw_value="待定", status="confirmed", extraction_method="human_correction",
                    normalized_value=value, normalized_unit=unit, notes=notes,
                    provenance={"human_correction": {"input_value": str(value) + (unit or "")}})
                group = build_graph([item])[1][0]
                self.assertEqual(group.review_reason_code, expected)
                self.assertEqual(group.fact_status == "verified", expected is None)

    def test_unknown_text_parameter_remains_editable(self):
        self.analyze({"a.txt": "安装方式: 待定\n"})
        field = self.snapshot()["product"]["facts"][0]["field"]
        self.act(field, "edit", value="免工具壁挂安装")
        self.assertEqual(self.fact(field)["fact_status"], "verified")
        self.assertEqual(self.export_rows("human")[0]["值"], "免工具壁挂安装")

    def test_export_modes_share_corrected_identity_and_one_row_per_fact(self):
        self.analyze({"a.txt": "SKU: A-1\n型号: OLD-1\n净重: 80g\n", "b.txt": "SKU: A-1\n净重: 0.08kg\n"})
        self.act("net_weight", "adopt")
        self.act("model", "edit", value="NEW-2")
        human, verified = self.export_rows("human"), self.export_rows("verified")
        self.assertEqual(len(human), 2)
        self.assertTrue(all(row["型号"] == "NEW-2" for row in human + verified))
        self.assertTrue(all(row["SKU"] == "A-1" for row in human + verified))
        for row in human:
            other = next(v for v in verified if v["参数"] == row["参数"])
            self.assertEqual([row[k] for k in ("商品 ID", "SKU", "型号", "值", "单位")],
                             [other[k] for k in ("商品 ID", "SKU", "型号", "值", "单位")])
        self.assertTrue(all(d["status"] == "covered_fields_match" for d in self.snapshot()["deliverables"]))

    def test_identity_correction_invalidates_old_export_and_grant(self):
        self.analyze({"a.txt": "型号: OLD-1\n净重: 80g\n"})
        self.act("net_weight", "adopt")
        weight = next(f for f in self.snapshot()["available_facts"] if f["field"] == "net_weight")
        grant = workflow.create_handoff(self.output, candidate_ids=[weight["candidate_id"]], recipient="test", purpose="test")
        workflow.export_table(self.output, mode="human")
        self.act("model", "edit", value="NEW-2")
        with self.assertRaises(ConfirmationRequestError):
            workflow.get_handoff(self.output, bundle_id=grant["bundle_id"], recipient="test")
        self.assertEqual(self.snapshot()["deliverables"][0]["status"], "source_stale")
        self.assertTrue(all(row["型号"] == "NEW-2" for row in self.export_rows("human")))

    def test_multi_product_brief_keeps_owners_and_line_references(self):
        self.analyze({"a.txt": "SKU: AUDIT-A\n净重: 80g\n", "b.txt": "SKU: AUDIT-B\n净重: 120g\n"})
        for group in self.snapshot()["product"]["facts"]:
            if group["field"] == "net_weight":
                self.act("net_weight", "adopt", product_id=group["product_id"])
        for mode in ("human", "verified"):
            result = workflow.export_local(self.output, mode=mode)
            brief = Path(result["artifacts"]["brief"]).read_text("utf8")
            self.assertIn("### AUDIT-A", brief)
            self.assertIn("### AUDIT-B", brief)
            lines = brief.splitlines()
            record = next(d for d in workflow._manifest(self.output, self.session)["deliverables"].values()
                          if d["path"] == result["artifacts"]["brief"])
            for reference in record["references"]:
                self.assertTrue(all(lines[line - 1].startswith("- ") for line in reference["lines"]))
        analyze_directory(self.root, self.output)
        self.assertTrue(all(g["fact_status"] == "verified" for g in self.snapshot()["product"]["facts"]))

    def test_authorized_content_checks_preserve_product(self):
        self.analyze({"a.txt": "SKU: AUDIT-A\n净重: 80g\n", "b.txt": "SKU: AUDIT-B\n净重: 120g\n"})
        facts = self.snapshot()["available_facts"]
        owner = next(f["product_id"] for f in facts if f["field"] == "sku" and f["normalized_value"] == "audit-a")
        grant = workflow.create_handoff(self.output,
            candidate_ids=[f["candidate_id"] for f in facts if f["product_id"] == owner], recipient="test", purpose="test")
        for sku, expected in (("AUDIT-A", "covered_fields_match"), ("AUDIT-B", "needs_review")):
            draft = self.base / "draft.txt"
            draft.write_text(sku + "：净重：80g", encoding="utf8")
            result = workflow.check_content(self.output, content_file=str(draft), bundle_id=grant["bundle_id"], recipient="test")
            self.assertEqual(result["status"], expected)

    def test_authorized_ip_check_preserves_condition_after_manual_edit(self):
        self.analyze({"a.csv": "Parameter,Value,Conditions\n防护等级,IP65,端口盖闭合\n"})
        self.act("ip_rating", "edit", value="IP65")
        fact = self.snapshot()["available_facts"][0]
        self.assertEqual(fact["scope"], "context:端口盖闭合")
        grant = workflow.create_handoff(self.output, candidate_ids=[fact["candidate_id"]], recipient="test", purpose="test")
        draft = self.base / "draft.txt"
        draft.write_text("端口盖打开时 IP65", encoding="utf8")
        result = workflow.check_content(self.output, content_file=str(draft), bundle_id=grant["bundle_id"], recipient="test")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["findings"][0]["kind"], "scope_ambiguous")

    def test_changed_source_invalidates_corrected_placeholder(self):
        self.analyze({"a.txt": "材质: 待定\n"})
        self.act("material", "edit", value="铝合金")
        (self.root / "a.txt").write_text("材质: ABS\n", encoding="utf8")
        self.assertEqual(self.fact("material")["review_reason_code"], "source_changed")
        with self.assertRaises(ConfirmationRequestError):
            workflow.export_table(self.output)


class ClaimAndSectionContracts(unittest.TestCase):
    def test_ip_shorthand_and_label_use_the_same_matching_rules(self):
        fact = {"fact_id": "ip", "field": "ip_rating", "value": "ip65", "unit": None, "product_id": "p", "scope": None}
        for scope in (None, "context:端口盖闭合"):
            reference = [{**fact, "scope": scope}]
            for value in ("IP65", "IP67"):
                short = check_draft(value, reference)
                labeled = check_draft("防护等级：" + value, reference)
                self.assertEqual(short["status"], labeled["status"])
                self.assertEqual(short["claims"][0]["evidence_ids"], labeled["claims"][0]["evidence_ids"])
            if scope:
                self.assertEqual(check_draft("端口盖打开时 IP65", reference)["status"], "needs_review")
            else:
                self.assertEqual(check_draft("IP65", reference)["status"], "covered_fields_match")

    def test_declared_identity_never_falls_back_to_only_available_product(self):
        for sku in ("ALPHA", "AUDIT-A", "A100"):
            facts = [{"field": "sku", "value": sku.casefold(), "unit": None, "product_id": "p"},
                     {"field": "net_weight", "value": 80, "unit": "g", "scope": "net", "product_id": "p"}]
            for separator in ("：", ":", " "):
                with self.subTest(sku=sku, separator=separator):
                    self.assertEqual(check_draft(sku + separator + "净重：80g", facts)["status"], "covered_fields_match")
                    self.assertNotEqual(check_draft("OTHER-B" + separator + "净重：80g", facts)["status"], "covered_fields_match")
        facts = [{"field": "voltage", "value": 5, "unit": "V", "scope": "input"}]
        self.assertEqual(check_draft("Input: Voltage: 5V", facts)["status"], "covered_fields_match")

    def test_identity_token_boundary_does_not_confuse_a1_and_a10(self):
        facts = [{"field": "sku", "value": "a1", "product_id": "a"},
                 {"field": "sku", "value": "a10", "product_id": "b"},
                 {"field": "net_weight", "value": 80, "unit": "g", "scope": "net", "product_id": "b"}]
        result = check_draft("A10 净重：80g", facts)
        self.assertEqual(result["status"], "covered_fields_match")
        self.assertEqual(result["claims"][0]["product_id"], "b")

    def sections(self, texts):
        rows = [c for i, text in enumerate(texts) for c in extract_rule_candidates(
            SourceBlock(str(i), "multi.txt", "text", "hash", {"line": i + 1}, text))]
        resolve_product_identities(rows, dataset_root="section-test")
        return rows, build_graph(rows)[1]

    def test_sku_and_model_sections_preserve_the_same_ownership(self):
        for label in ("SKU", "型号", "Model"):
            with self.subTest(label=label):
                rows, groups = self.sections([label + ": A-1", "净重: 80g", label + ": B-2", "净重: 120g"])
                weights = [g for g in groups if g.field == "net_weight"]
                self.assertEqual(len(weights), 2)
                self.assertTrue(all(g.fact_status == "verified" for g in weights))
                self.assertEqual({(c.product_sku or c.product_model, c.normalized_value)
                                  for c in rows if c.field == "net_weight"}, {("A-1", 80), ("B-2", 120)})

    def test_evidence_before_sections_is_not_assigned_to_a_heading(self):
        rows, groups = self.sections(["净重: 99g", "型号: A-1", "净重: 80g", "型号: B-2", "净重: 120g"])
        orphan = next(c for c in rows if c.normalized_value == 99)
        self.assertEqual(orphan.product_identity_status, "ambiguous")
        self.assertEqual(sum(g.fact_status == "verified" for g in groups if g.field == "net_weight"), 2)

    def test_model_sections_do_not_invent_variants(self):
        _, groups = self.sections(["型号: A-1", "容量: 1L", "型号: A-1", "容量: 2L"])
        self.assertTrue(all(g.fact_status == "pending_confirmation" for g in groups))


if __name__ == "__main__":
    unittest.main()
