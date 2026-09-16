"""Root-level invariants, not vendor-specific fixes or an accuracy oracle."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from product_evidence_guard.content_check import check_draft
from product_evidence_guard.extractor import extract_rule_candidates, infer_semantic_scope
from product_evidence_guard.field_registry import FIELD_SPECS, field_for_label
from product_evidence_guard.graph import build_graph
from product_evidence_guard.identity import candidate_identity, evidence_identity
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.normalization import normalize_value
from product_evidence_guard.openvino_adapter import OpenVinoFactExtractor
from product_evidence_guard.pdf_layout import _lines, _wrapped_pairs, read_layout_page
from product_evidence_guard.review_workbook import scope_label


def extract(text, block_id="b"):
    return extract_rule_candidates(SourceBlock(block_id, "source.txt", "text", "hash", {}, text))


class UnitIntegrityTests(unittest.TestCase):
    def test_old_result_requires_one_reanalysis_not_mass_reconfirmation(self):
        from product_evidence_guard import workflow
        from product_evidence_guard.engine import analyze_directory
        from product_evidence_guard.confirmation import apply_decision, export_confirmed
        from product_evidence_guard.state import atomic_write_json
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "input", Path(tmp) / "output"
            source.mkdir()
            (source / "spec.txt").write_text("净重: 300g", encoding="utf-8")
            run = analyze_directory(source, output)
            path = output / "product-facts.json"
            product = json.loads(path.read_text("utf-8"))
            cid = product["candidates"][0]["candidate_id"]
            apply_decision(output, session_id=run["session_id"], candidate_id=cid, action="confirm", reason="checked")
            product = json.loads(path.read_text("utf-8"))
            product["run_summary"].pop("value_semantics_revision")
            atomic_write_json(path, product)
            snapshot = workflow.task_snapshot(output, detailed=True)
            self.assertFalse(snapshot["delivery_readiness"]["can_export_partial"])
            self.assertEqual(snapshot["available_facts"], [])
            self.assertEqual(snapshot["counts"]["pending_facts"], 0)
            for allow_partial in (True, False):
                with self.assertRaisesRegex(ValueError, "单位读取规则"):
                    workflow.export_table(output, allow_partial=allow_partial)
            with self.assertRaises(ValueError):
                workflow.create_handoff(output, candidate_ids=[cid], recipient="test", purpose="upgrade safety")
            export_confirmed(output, session_id=run["session_id"])
            self.assertEqual(json.loads((output / "confirmed-product-facts.json").read_text("utf-8"))["facts"], [])
            state_path = output / "analysis-state.json"
            state = json.loads(state_path.read_text("utf-8"))
            state["engine_signature"] = "old-engine"
            atomic_write_json(state_path, state)
            analyze_directory(source, output)
            self.assertTrue(workflow.task_snapshot(output)["delivery_readiness"]["complete"])
            self.assertEqual(len(workflow.context(output)[2]), 1)

    def test_si_prefix_case_controls_scale_in_every_supported_family(self):
        for field, small, large, small_value, large_value in (
            ("power", "mW", "MW", .001, 1_000_000),
            ("current", "mA", "MA", .001, 1_000_000),
            ("voltage", "mV", "MV", .001, 1_000_000),
            ("pressure", "mPa", "MPa", .001, 1_000_000),
            ("frequency", "mHz", "MHz", .001, 1_000_000),
        ):
            for unit, expected in ((small, small_value), (large, large_value)):
                with self.subTest(field=field, unit=unit):
                    self.assertEqual(normalize_value(field, "1" + unit).value, expected)
                    self.assertEqual(normalize_value(field, "1~2" + unit).value, [expected, expected * 2])
            self.assertNotEqual(normalize_value(field, "1±0.1" + small).value,
                                normalize_value(field, "1±0.1" + large).value)

    def test_unknown_units_keep_case_without_a_field_whitelist(self):
        field = field_for_label("任意新参数").name
        for a, b in (("1mW", "1MW"), ("2MB/s", "2Mb/s"), ("3MHz", "3mHz"),
                     ("4mΩ", "4MΩ"), ("5mS", "5ms"), ("6mF", "6MF")):
            with self.subTest(a=a, b=b):
                self.assertNotEqual(normalize_value(field, a).value, normalize_value(field, b).value)
                self.assertEqual(normalize_value(field, a).value, a)

    def test_unsupported_casing_never_guesses_the_scale(self):
        for field, raw in (("frequency", "1mhz"), ("pressure", "1mpa"),
                           ("torque", "1nm"), ("dimensions", "1x2Mm"),
                           ("capacity_charge", "1MAh")):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_value(field, raw).unit)

    def test_prose_and_cosmetic_spacing_still_merge(self):
        field = field_for_label("新参数").name
        self.assertEqual(normalize_value(field, "Idle  31 mW").value, normalize_value(field, "idle 31mW").value)
        self.assertEqual(normalize_value("material", "ABS").value, normalize_value("material", "abs").value)
        self.assertEqual(normalize_value(field, "2年").value, normalize_value(field, "24个月").value)

    def test_evidence_deduplication_cannot_hide_unit_case_conflicts(self):
        # Same source block, not merely different files: both values must survive.
        rows = extract("新参数: 1mW; 新参数: 1MW")
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(evidence_identity(rows[0]), evidence_identity(rows[1]))
        self.assertNotEqual(candidate_identity(rows[0]), candidate_identity(rows[1]))
        kept, groups, _ = build_graph(rows)
        self.assertEqual(len(kept), 2)
        self.assertEqual(groups[0].fact_status, "conflict")

    def test_old_wrong_normalization_cannot_inherit_confirmation_identity(self):
        current = extract("额定功率: 1MW")[0]
        old = replace(current, normalized_value=.001)
        self.assertNotEqual(candidate_identity(old), candidate_identity(current))

    def test_labeled_io_alternatives_do_not_casefold_away_one_value(self):
        row = extract("输出: 1mW/1MW")[0]
        self.assertEqual(row.normalized_value, [.001, 1_000_000])

    def test_content_check_rejects_byte_bit_and_prefix_substitution(self):
        for a, b in (("1mW", "1MW"), ("2MB/s", "2Mb/s")):
            fact = extract("新参数: " + a)[0]
            refs = [{"fact_id": "f", "field": fact.field, "value": fact.normalized_value, "unit": None}]
            self.assertEqual(check_draft("新参数: " + a, refs)["status"], "covered_fields_match")
            self.assertEqual(check_draft("新参数: " + b, refs)["status"], "blocked")

    def test_model_cannot_change_unit_case_when_copying_evidence(self):
        adapter = OpenVinoFactExtractor.__new__(OpenVinoFactExtractor)
        adapter._allowed = {spec.name: spec for spec in FIELD_SPECS}
        for proposed, expected_count in (("1mW", 1), ("1MW", 0)):
            answer = json.dumps([dict(block_id="b", field="power", raw_value=proposed, mapping_confidence=.95)])
            adapter._pipe = SimpleNamespace(generate=lambda *a, **k: answer)
            result = adapter.extract([SourceBlock("b", "a.txt", "text", "hash", {}, "额定功率: 1mW")])
            self.assertEqual(len(result), expected_count)

    def test_ac_dc_and_dimension_order_remain_in_adopted_scope(self):
        for raw, expected in (("输入电压: 12V DC", "input|signal:dc"),
                              ("输入电压: 12VAC", "input|signal:ac"),
                              ("Input power: 1.8–5.5V DC", "input|signal:dc")):
            self.assertEqual(extract(raw)[0].scope, expected)
        self.assertEqual(infer_semantic_scope("dimensions", "尺寸: 10x20x30mm (W x H x D)"), "axes:W×H×D")
        self.assertEqual(scope_label("input|signal:dc"), "输入 / 直流")
        self.assertEqual(scope_label("axes:W×H×D"), "尺寸顺序：宽×高×深")


def word(text, x, top, width=55):
    return dict(text=text, x0=x, x1=x + width, top=top, bottom=top + 10)


class WrappedPdfValueTests(unittest.TestCase):
    def page(self, words):
        page = SimpleNamespace(width=600, height=800, chars=[], edges=[], extract_words=lambda **k: words)
        page.dedupe_chars = lambda: page
        return page

    def test_frequency_and_variant_continuations_stay_with_their_label(self):
        words = [word("Processor:", 30, 20), word("Dual core @", 200, 20), word("175MHz", 200, 34),
                 word("Wireless:", 30, 58), word("Wi-Fi (Model", 200, 58), word("W only)", 200, 72),
                 word("Memory:", 30, 96), word("8 MB", 200, 96)]
        records, issues = read_layout_page(self.page(words), 1)
        self.assertEqual([r["text"] for r in records],
                         ["Processor: Dual core @ 175MHz", "Wireless: Wi-Fi (Model W only)", "Memory: 8 MB"])
        self.assertFalse(issues)
        self.assertGreater(records[0]["locator"]["bbox_1000"][3], 34 / 800 * 1000)

    def test_continued_bullet_list_is_one_fact_not_only_first_item(self):
        words = [word("Peripherals:", 30, 20), word("• 2 UART", 200, 20),
                 word("• 3 SPI", 200, 38), word("• 8 PWM", 200, 56),
                 word("Temperature:", 30, 80), word("10~40°C", 200, 80)]
        records, _ = read_layout_page(self.page(words), 1)
        self.assertEqual(records[0]["text"], "Peripherals: • 2 UART • 3 SPI • 8 PWM")
        self.assertEqual(len(extract(records[0]["text"])), 1)

    def test_new_section_gap_or_wrong_indent_stops_continuation(self):
        for next_word in (word("Other section", 30, 34), word("Wrong indent", 250, 34), word("Far paragraph", 200, 90)):
            lines = _lines([word("Clock:", 30, 20), word("175MHz", 200, 20), next_word])
            self.assertEqual(len(_wrapped_pairs(lines)[0][1]), 1)

    def test_parallel_prose_is_not_part_of_value_or_label(self):
        words = [word("Unrelated copy", 10, 20, 70), word("Clock:", 150, 20), word("175MHz", 300, 20),
                 word("Other copy", 10, 34, 70), word("maximum", 300, 34)]
        records, _ = read_layout_page(self.page(words), 1)
        self.assertIn("Clock: 175MHz maximum", [r["text"] for r in records])
        self.assertFalse(any("Clock:" in r["text"] and "copy" in r["text"] for r in records))

    def test_overlong_wrapped_value_is_not_approved_as_a_truncated_prefix(self):
        words = [word("Details:", 30, 20), word("a" * 300, 200, 20), word("b" * 300, 200, 34)]
        records, issues = read_layout_page(self.page(words), 1)
        self.assertFalse(records)
        self.assertTrue(issues)

    def test_multiline_physical_cell_retains_conditions_and_variants(self):
        from product_evidence_guard.pdf_layout import _cell_text
        value = _cell_text("Max data rate (ATTO)\n240GB — 500MB/s\n480GB — 550MB/s")
        rows = extract("基准性能: " + value)
        self.assertEqual(len(rows), 1)
        self.assertIn("480GB", rows[0].normalized_value)
        self.assertIn("550MB/s", rows[0].normalized_value)
        self.assertIn("atto", rows[0].normalized_value)


if __name__ == "__main__":
    unittest.main()
