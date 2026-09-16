"""Counterexamples for the audit fixes, including safety and read-only paths."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.graph import build_graph
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.content_check import check_draft
from product_evidence_guard import workflow
from tests.test_fact_status import candidate
from tests.test_qwen_vl_output import FakeBackend
from tests.test_hybrid_image_reader import FakeOcrBackend
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.hybrid_image_reader import HybridImageReader, OcrLine


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root, self.output = Path(tmp.name) / "inputs", Path(tmp.name) / "output"
        self.root.mkdir()

    def analyze(self, files):
        for name, text in files.items():
            (self.root / name).write_text(text, encoding="utf-8")
        summary = analyze_directory(self.root, self.output, preprocessing_workers=1)
        self.assertEqual(summary["errors"], [])
        return json.loads((self.output / "product-facts.json").read_text("utf-8"))

    def test_shared_sku_list_and_inline_subsets_are_not_fictitious_products(self):
        product = self.analyze({"master.csv": "SKU,型号,净重\nP-1,BASE,100g\nP-2,BASE,200g\nP-3,BASE,200g",
            "manual.txt": "SKU：P-1 / P-2 / P-3\n输入电压：5V\nP-1 净重：100g\nP-2 / P-3 净重：200g\n额定电压：12V"})
        self.assertEqual({p["sku"] for p in product["products"]}, {"P-1", "P-2", "P-3"})
        for sku in ("P-1", "P-2", "P-3"):
            facts = [c for c in product["candidates"] if c["source_file"] == "manual.txt" and c["product_sku"] == sku]
            self.assertEqual({(c["field"], c["scope"]) for c in facts},
                             {("sku", None), ("voltage", "input"), ("voltage", "rating:rated"), ("net_weight", "net")})
        self.assertTrue(all(g["fact_status"] == "verified" for g in product["facts"]))
        before = product["candidates"]
        analyze_directory(self.root, self.output)
        self.assertEqual(before, json.loads((self.output / "product-facts.json").read_text("utf8"))["candidates"])

    def test_sku_sections_do_not_borrow_the_last_product_for_every_line(self):
        product = self.analyze({"a.txt": "SKU：A1\n型号：SHARED\n净重：100g\nSKU：A2\n型号：SHARED\n净重：200g"})
        self.assertEqual({(c["product_sku"], c["normalized_value"]) for c in product["candidates"] if c["field"] == "net_weight"},
                         {("A1", 100), ("A2", 200)})

    def test_single_sku_does_not_override_multiple_explicit_models(self):
        product = self.analyze({"a.txt": "SKU：A1\n型号：ONE\n净重：100g\n型号：TWO\n净重：200g"})
        weight = [c for c in product["candidates"] if c["field"] == "net_weight"]
        self.assertTrue(all(c["product_identity_status"] == "ambiguous" for c in weight))
        self.assertFalse(any(g["fact_status"] == "conflict" for g in product["facts"]))

    def test_common_model_does_not_assign_orphan_by_matching_value(self):
        product = self.analyze({"a.csv": "SKU,型号,净重\nP-1,BASE,100g\nP-2,BASE,200g",
                               "b.txt": "型号：BASE\n净重：100g"})
        orphan = next(c for c in product["candidates"] if c["source_file"] == "b.txt" and c["field"] == "net_weight")
        self.assertIsNone(orphan["product_sku"])
        self.assertEqual(orphan["product_identity_status"], "ambiguous")

    def test_true_conflict_never_enters_default_export(self):
        self.analyze({"a.csv": "SKU,型号,净重,颜色\nP-1,BASE,100g,黑色\nP-2,BASE,200g,白色",
                      "b.txt": "SKU：P-1\n型号：BASE\n净重：110g"})
        table = Path(workflow.export_table(self.output)["path"]).read_text("utf-8-sig")
        self.assertNotIn("100", table)
        self.assertNotIn("110", table)
        self.assertIn("200", table)

    def test_draft_cannot_certify_its_own_claims_and_metadata_is_not_a_fact(self):
        product = self.analyze({"evidence.txt": "资料版本：v2.0\n净重：100g",
                               "文案待回检.md": "净重：999g"})
        self.assertEqual(len(product["candidates"]), 1)
        self.assertEqual(product["facts"][0]["selected_value"], 100)

    def test_named_image_draft_is_not_treated_as_source_evidence(self):
        (self.root / "商品介绍草稿.png").write_bytes(b"not evidence; must not enter visual inference")
        product = self.analyze({"evidence.txt": "净重：100g"})
        self.assertEqual(len(product["candidates"]), 1)
        self.assertEqual(product["run_summary"]["skipped_files"], [])

    def test_ocr_joined_numeric_label_but_not_model_prefix(self):
        def extract(text):
            return extract_rule_candidates(SourceBlock("b", "a.txt", "text", "hash", {}, text))
        self.assertEqual(extract("Dimensions120x80x30mm")[0].normalized_value, [120, 80, 30])
        self.assertEqual(extract("MODEL120"), [])
        self.assertEqual(extract("凈重：100g")[0].field, "net_weight")

    def test_partial_review_keeps_clear_ocr_facts_without_bypassing_failed_review(self):
        image = self.root / "image.png"
        image.write_bytes(b"fake pixels for injected backend")
        ocr = FakeOcrBackend([OcrLine("净重：100g", .999, (10, 10, 900, 80)),
                             OcrLine("输入电压：5V", .6, (10, 200, 900, 280))])
        backend = FakeBackend([json.dumps({"schema_version": 1, "lines": []})])
        result = HybridImageReader(QwenVlReader(backend), ocr_backend=ocr).analyze_image(image)
        self.assertIn("incomplete_review_coverage", {e.code for e in result.errors})
        self.assertEqual([(c.field, c.normalized_value) for c in result.fact_candidates], [("net_weight", 100)])
        self.assertNotIn('净重', backend.calls[0]["prompt"])

    def test_visual_agreement_is_not_a_fake_probability_or_human_decision(self):
        image = self.root / "image.png"
        image.write_bytes(b"fake pixels")
        backend = FakeBackend([json.dumps({"schema_version": 1, "lines": ["净重：100g"]})])
        result = QwenVlReader(backend).analyze_image(image, ocr_hints=[{"text": "净重：0.1kg", "score": .9}])
        item = result.fact_candidates[0]
        self.assertEqual(item.recognition_confidence, .75)
        group = build_graph([item])[1][0]
        self.assertEqual(group.fact_status, "verified")
        self.assertFalse(group.human_approved)
        item.provenance["visual_review"]["ocr_agrees"] = False
        self.assertEqual(build_graph([item])[1][0].fact_status, "pending_confirmation")

    def test_weak_agreeing_copy_does_not_demote_clear_evidence_but_disagreement_does(self):
        clear, weak = candidate(), candidate("weak", recognition_confidence=.4)
        self.assertEqual(build_graph([clear, weak])[1][0].fact_status, "verified")
        weak.normalized_value = 400
        self.assertEqual(build_graph([clear, weak])[1][0].fact_status, "pending_confirmation")

    def test_equal_unscoped_copy_does_not_certify_a_scope_or_human_approval(self):
        scoped = candidate(field="charging_time", normalized_value=60, normalized_unit="s", scope="rating:typical")
        unscoped = replace(scoped, candidate_id="u", source_block_id="u", scope=None, status="confirmed")
        group = build_graph([scoped, unscoped])[1][0]
        self.assertEqual(group.fact_status, "verified")
        self.assertEqual(group.verified_candidate_ids, [scoped.candidate_id])
        self.assertFalse(group.human_approved)
        self.assertEqual(group.reason, "参数清楚，未发现冲突。")

    def test_snapshot_does_not_rewrite_reports_and_still_detects_source_changes(self):
        self.analyze({"a.txt": "净重：100g"})
        paths = [self.output / name for name in ("product-facts.json", "evidence-report.html", "confirmed-product-facts.json")]
        before = [path.stat().st_mtime_ns for path in paths]
        with patch("product_evidence_guard.workflow.export_confirmed", side_effect=AssertionError("read caused export")):
            self.assertEqual(workflow.task_snapshot(self.output)["phase"], "verified")
            workflow.task_snapshot(self.output, detailed=True)
        self.assertEqual([path.stat().st_mtime_ns for path in paths], before)
        (self.root / "a.txt").write_text("净重：200g", encoding="utf-8")
        snapshot = workflow.task_snapshot(self.output)
        self.assertEqual(snapshot["counts"]["pending_facts"], 1)
        self.assertEqual(snapshot["counts"]["verified_facts"], 0)

    def test_benchmark_does_not_score_wrong_values_or_units_as_correct(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import benchmark
        expected = [{"sample_id": "a", "expected_mappings": [{"field": "net_weight", "raw_value": "100g", "normalized_value": 100, "normalized_unit": "g"}]}]
        for value, unit in ((999, "g"), (100, "kg")):
            metrics, _ = benchmark._quality_metrics(expected, {"a": {"fact_candidates": [
                {"field": "net_weight", "normalized_value": value, "normalized_unit": unit}]}})
            self.assertEqual(metrics["field_mapping"]["f1"], 0)

    def test_snapshot_recovers_missing_output_and_marks_missing_analysis_stale(self):
        self.analyze({"a.txt": "净重：100g"})
        (self.output / "confirmed-product-facts.json").unlink()
        self.assertEqual(workflow.task_snapshot(self.output)["counts"]["verified_facts"], 1)
        (self.output / "analysis-state.json").unlink()
        snapshot = workflow.task_snapshot(self.output)
        self.assertEqual(snapshot["counts"]["verified_facts"], 0)
        self.assertEqual(snapshot["counts"]["pending_facts"], 1)

    def test_known_sku_prefix_supported_but_unknown_sku_still_reviewed(self):
        facts = [{"fact_id": "sku", "product_id": "p", "field": "sku", "value": "p-1", "unit": None, "scope": None},
                 {"fact_id": "weight", "product_id": "p", "field": "net_weight", "value": 100, "unit": "g", "scope": "net"}]
        self.assertEqual(check_draft("P-1 净重：100g", facts)["status"], "covered_fields_match")
        self.assertNotEqual(check_draft("P-2 净重：100g", facts)["status"], "covered_fields_match")


if __name__ == "__main__":
    unittest.main()
