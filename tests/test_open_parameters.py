"""Realistic inputs outside the specialist registry; no model download needed."""
import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard import conversation, workflow
from product_evidence_guard.content_check import check_draft
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.field_registry import field_definition, field_for_label
from product_evidence_guard.graph import build_graph
from product_evidence_guard.identity import resolve_product_identities
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.normalization import normalize_value
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.review_workbook import export_review
from product_evidence_guard.model_output_schema import parse_field_mapping_output
from tests.test_qwen_vl_output import FakeBackend, _mapping_item, _payload


def extract(text, index=0):
    return extract_rule_candidates(SourceBlock(str(index), f"{index}.txt", "text", str(index), {"line":1}, text))


def facts(*texts):
    rows = [c for i, text in enumerate(texts) for c in extract(text, i)]
    resolve_product_identities(rows, dataset_root=".")
    return build_graph(rows)[1]


class OpenParameterTests(unittest.TestCase):
    def test_unknown_fields_are_retained_and_clear_single_sources_are_verified(self):
        groups = facts("噪声：45 dB", "转速：3000 rpm", "保修期：2年", "适用材质：木材、铝材")
        self.assertEqual(len(groups), 4)
        self.assertEqual({g.fact_status for g in groups}, {"verified"})
        self.assertEqual({g.field_label for g in groups}, {"噪声", "转速", "保修期", "适用材质"})
        for group in groups:
            self.assertEqual(field_definition(group.field).label, group.field_label)
            self.assertFalse(group.human_approved)

    def test_multi_parameter_lines_keep_every_value_and_scope(self):
        rows = extract("净重：300g；额定功率：20W；噪声：45dB")
        self.assertEqual(len(rows), 3)
        self.assertEqual([c.normalized_value for c in rows], [300, 20, "45db"])
        self.assertEqual(rows[1].scope, "rating:rated")
        rows = extract("该产品净重为300g，额定功率20W")
        self.assertEqual([c.normalized_value for c in rows], [300, 20])

    def test_material_lists_are_not_truncated(self):
        self.assertEqual(extract("材质：ABS；不锈钢")[0].normalized_value, "abs；不锈钢")

    def test_thousands_and_qualifiers_are_preserved(self):
        for raw, value in [("1,000g", 1000), ("1,000.5g", 1000.5),
                           ("≤300g", "≤300"), ("300g±5g", "300±5"),
                           ("0.3kg±5g", "300±5"), ("约0.3kg", "≈300")]:
            with self.subTest(raw=raw):
                normalized = normalize_value("net_weight", raw)
                self.assertEqual((normalized.value, normalized.unit), (value, "g"))
                self.assertEqual(extract("净重：" + raw)[0].normalized_value, value)
        for raw in ("1,00g", "1,000,00g", "300g/400g", "300g±unknown"):
            value = normalize_value("net_weight", raw)
            self.assertIsNone(value.unit)
            self.assertIn("unparsed_unit", value.notes)

    def test_compatible_bounds_are_not_exact_matches_or_conflicts(self):
        group = facts("净重：≤300g", "净重：300g")[0]
        self.assertEqual((group.fact_status, group.review_reason_code), ("pending_confirmation", "compatible_expression"))
        self.assertEqual(facts("净重：≤300g", "净重：400g")[0].fact_status, "conflict")
        self.assertEqual(facts("净重：≤0.3kg", "净重：≤300g")[0].fact_status, "verified")

    def test_unknown_unit_in_known_numeric_field_is_kept_but_not_guessed(self):
        group = facts("净重：5 stone")[0]
        self.assertEqual(group.fact_status, "pending_confirmation")
        self.assertEqual(group.normalized_values[0]["value"], "5 stone")

    def test_product_and_package_dimensions_are_not_conflicts(self):
        groups = facts("产品尺寸：10×20×30cm", "包装尺寸：12×22×32cm")
        self.assertEqual(len(groups), 2)
        self.assertEqual({g.scope for g in groups}, {"product", "packaging"})
        self.assertEqual({g.fact_status for g in groups}, {"verified"})
        groups = facts("产品尺寸：10×20×30cm", "产品尺寸：12×22×32cm")
        self.assertEqual(groups[0].fact_status, "conflict")

    def test_missing_optional_scope_does_not_create_review_work(self):
        self.assertEqual(facts("续航时间：2h")[0].fact_status, "verified")
        self.assertEqual(facts("电压：12V")[0].review_reason_code, "scope_unclear")
        groups = facts("续航时间：2h", "典型续航时间：3h")
        self.assertEqual(groups[0].fact_status, "pending_confirmation")
        groups = facts("温度：20°C", "工作温度：20°C", "储存温度：30°C")
        self.assertEqual(next(g for g in groups if g.scope is None).review_reason_code, "scope_unclear")

    def test_generic_charge_capacity_merges_only_with_product_context(self):
        groups = facts("型号：DEMO-1", "电池容量：2000mAh", "容量：2Ah")
        capacity = [g for g in groups if g.field != "model"]
        self.assertEqual(len(capacity), 1)
        self.assertEqual(capacity[0].field, "capacity_charge")
        self.assertEqual(capacity[0].fact_status, "verified")
        self.assertEqual(facts("容量：2Ah")[0].field, "capacity")
        ref = [{"fact_id":"battery", "field":"capacity_charge", "value":2000, "unit":"mAh", "scope":None}]
        self.assertEqual(check_draft("容量：2Ah", ref)["status"], "covered_fields_match")

    def test_unknown_fields_are_checked_not_silently_covered(self):
        references = [{"fact_id":"f1", "field":"net_weight", "value":300, "unit":"g", "scope":"net"}]
        result = check_draft("净重：300g。噪声：45dB。保修期：2年。", references)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["claim_count"], 3)
        self.assertEqual(result["counts"]["unsupported"], 2)
        noise = field_for_label("噪声").name
        ref = [{"fact_id":"f2", "field":noise, "value":"45db", "unit":None}]
        self.assertEqual(check_draft("噪声：45 dB", ref)["status"], "covered_fields_match")
        self.assertEqual(check_draft("噪声：65 dB", ref)["status"], "blocked")

    def test_content_check_preserves_bounds_and_thousands(self):
        ref = [{"fact_id":"f1", "field":"net_weight", "value":1000, "unit":"g", "scope":"net"}]
        self.assertEqual(check_draft("净重：1,000g", ref)["status"], "covered_fields_match")
        ref[0]["value"] = "≤300"
        self.assertEqual(check_draft("净重：≤300g", ref)["status"], "covered_fields_match")
        self.assertNotEqual(check_draft("净重：300g", ref)["status"], "covered_fields_match")

    def test_custom_model_field_must_be_grounded_and_cannot_be_code(self):
        parsed = parse_field_mapping_output(_payload([
            _mapping_item(field="噪声", raw_value="45dB"),
            _mapping_item(field="secret_instruction", raw_value="45dB"),
        ]), transcriptions={"visual-001":"噪声：45dB"})
        self.assertEqual(len(parsed.items), 1)
        self.assertEqual(parsed.items[0].field, field_for_label("噪声").name)
        self.assertIn("field_not_allowed", {e.code for e in parsed.errors})
        self.assertIsNone(field_for_label("__import__('os').system('x')"))
        self.assertIsNone(field_definition("custom_ffff"))
        parsed = parse_field_mapping_output(_payload([_mapping_item(field="net_weight", raw_value="300g")]),
                                            transcriptions={"visual-001":"净重：≤300g"})
        self.assertEqual(parsed.items, [])
        self.assertIn("truncated_measurement", {e.code for e in parsed.errors})

    def test_table_conversation_export_and_reload_keep_custom_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "inputs"
            output = Path(tmp) / "outputs"
            root.mkdir()
            (root / "table.csv").write_text("型号,净重,噪声,保修期\nDEMO-1,300g,45dB,2年\n", encoding="utf-8")
            analyze_directory(root, output)
            snapshot = workflow.task_snapshot(output)
            self.assertEqual(snapshot["counts"]["facts"], 4)
            self.assertIn("噪声", {f["label"] for f in snapshot["review_fields"]})
            grant = conversation.allow_review(output, recipient="test", reason="本地测试", all_fields=True)
            summary = conversation.review_summary(output, review_id=grant["review_id"], recipient="test")
            self.assertEqual(len(summary["groups"]), 4)
            self.assertEqual({g["fact_status"] for g in summary["groups"]}, {"verified"})
            self.assertTrue(Path(export_review(output)["path"]).is_file())
            workflow.export_table(output, mode="verified")
            analyze_directory(root, output)
            self.assertEqual(workflow.task_snapshot(output)["counts"]["facts"], 4)

    def test_identity_with_only_unfamiliar_columns_is_a_valid_table(self):
        from product_evidence_guard.structured_rows import bind_structured_rows
        cells = bind_structured_rows([(1,[(1,"型号"),(2,"噪声")]), (2,[(1,"A1"),(2,"45dB")])], table_id="fixture")
        self.assertEqual(len(cells), 2)
        cells = bind_structured_rows([(1,[(1,"噪声"),(2,"转速")]), (2,[(1,"45dB"),(2,"3000rpm")])], table_id="fixture")
        self.assertEqual(len(cells), 2)
        self.assertIsNone(bind_structured_rows([(1,[(1,"型号"),(2,"A1")]), (2,[(1,"净重"),(2,"300g")])], table_id="fixture"))
        self.assertEqual(extract("噪声 | 45dB")[0].normalized_value, "45db")

    def test_custom_conflict_confirmation_and_stale_links(self):
        from product_evidence_guard.confirmation import resolve_conflict_group
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "input", Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("噪声：45dB\n", encoding="utf-8")
            (root / "b.txt").write_text("噪声：60dB\n", encoding="utf-8")
            run = analyze_directory(root, output)
            grant = conversation.allow_review(output, recipient="test", reason="核对噪声", all_fields=True)
            summary = conversation.review_summary(output, review_id=grant["review_id"], recipient="test")
            self.assertEqual(summary["groups"][0]["fact_status"], "conflict")
            self.assertTrue(all(c.get("source_url") for c in summary["groups"][0]["choices"]))
            _, product, _ = workflow.context(output)
            group = product["fact_groups"][0]
            selected = next(c for c in product["candidates"] if c["normalized_value"] == "45db")
            resolve_conflict_group(output, session_id=run["session_id"], group_id=group["group_id"],
                                   selected_candidate_id=selected["candidate_id"], expected_candidate_ids=group["candidate_ids"],
                                   reject_others=True, reason="采用45dB")
            self.assertEqual(workflow.task_snapshot(output)["counts"]["verified_facts"], 1)
            workflow.export_table(output, mode="human")
            (root / "a.txt").write_text("噪声：46dB\n", encoding="utf-8")
            self.assertTrue(conversation.review_summary(output, review_id=grant["review_id"], recipient="test")["needs_reanalysis"])

    def test_unlabeled_new_units_are_reported_as_unchecked(self):
        ref = [{"fact_id":"f1", "field":"net_weight", "value":300, "unit":"g", "scope":"net"}]
        result = check_draft("净重：300g。噪声45dB，保修2年。", ref)
        self.assertEqual(result["status"], "needs_review")
        self.assertGreaterEqual(result["finding_count"], 2)

    def test_electrical_bounds_and_thousands_survive_label_mapping(self):
        self.assertEqual(extract("INPUT: ≤12V")[0].normalized_value, "≤12")
        self.assertEqual(extract("INPUT: 1,000V")[0].normalized_value, 1000)
        row = extract("INPUT: 12V±0.5V")
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].normalized_value, "12±0.5")

    def test_new_ocr_parameters_use_safe_fast_path_without_forced_model_call(self):
        from product_evidence_guard.hybrid_image_reader import HybridImageReader, OcrLine
        from tests.test_hybrid_image_reader import FakeOcrBackend, FakeQwenReader
        qwen = FakeQwenReader()
        ocr = FakeOcrBackend([OcrLine(text="噪声：45dB", confidence=.999, bbox_1000=(10,10,500,100))])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.png"
            path.write_bytes(b"fake backend image")
            result = HybridImageReader(qwen, ocr_backend=ocr).analyze_image(path)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates[0].field_label, "噪声")

    def test_visual_mapping_keeps_equal_values_with_different_scopes(self):
        backend = FakeBackend([json.dumps({"schema_version":1,"lines":["输入电压：5V；输出电压：5V"]})])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.png"
            path.write_bytes(b"fake backend image")
            result = QwenVlReader(backend).analyze_image(path, ocr_hints=[{"text":"输入电压：5V；输出电压：5V","score":.9}])
        self.assertEqual({c.scope for c in result.fact_candidates}, {"input", "output"})
        self.assertEqual(len({c.candidate_id for c in result.fact_candidates}), 2)

    def test_dense_ocr_reviews_all_batches_without_duplicate_ids(self):
        lines = [f"参数{i}：{i}rpm" for i in range(25)]
        backend = FakeBackend([json.dumps({"schema_version":1,"lines":lines[i:i+8]},ensure_ascii=False) for i in range(0,25,8)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.png"
            path.write_bytes(b"fake backend image")
            result = QwenVlReader(backend).analyze_image(path, ocr_hints=[{"text":line,"score":.9} for line in lines])
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(len(backend.calls), 4)
        self.assertEqual(len(result.fact_candidates), 25)
        self.assertEqual(len({c.candidate_id for c in result.fact_candidates}), 25)
        self.assertEqual(len({c.source_block_id for c in result.fact_candidates}), 25)
        self.assertEqual({m.transcription_id for m in result.mappings}, {t.id for t in result.transcriptions})

    def test_incomplete_batch_is_not_success(self):
        backend = FakeBackend([json.dumps({"schema_version":1,"lines":["噪声：45dB"]})] * 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.png"
            path.write_bytes(b"fake backend image")
            result = QwenVlReader(backend).analyze_image(path, ocr_hints=[{"text":f"参数{i}：{i}rpm","score":.9} for i in range(10)])
        self.assertFalse(result.ok)
        self.assertIn("incomplete_review_coverage", {e.code for e in result.errors})


if __name__ == "__main__":
    unittest.main()
