"""Metamorphic invariants across values/families, separate from business samples."""
import unittest

from product_evidence_guard.normalization import normalize_value
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.graph import build_graph
from product_evidence_guard.models import SourceBlock


class MeasurementSemanticsTests(unittest.TestCase):
    def test_tolerance_location_and_units_do_not_change_meaning(self):
        for center in (12, 275, 1500):
            for raw in (f"{center}±5g", f"{center}g±5g", f"{center}g(+/-5g)", f"{center}g ± 5"):
                with self.subTest(raw=raw):
                    result = normalize_value("net_weight", raw)
                    self.assertEqual((result.value, result.unit), (f"{center}±5", "g"))

    def test_equivalent_bound_wordings_keep_operators(self):
        for word, op in (("不小于", "≥"), ("不大于", "≤"), ("不得超过", "≤"),
                         ("at least ", "≥"), ("at most ", "≤"), ("not less than ", "≥"),
                         ("no more than ", "≤"), ("小于", "<"), ("大于", ">")):
            for n in (12, 275, 1500):
                with self.subTest(word=word, n=n):
                    self.assertEqual(normalize_value("net_weight", f"{word}{n}g").value, f"{op}{n}")

    def test_extra_alternatives_or_compound_units_never_erase_to_first_number(self):
        for field, raw in (("net_weight", "275g±5stone"), ("net_weight", "275g±5 stone"),
                           ("net_weight", "275g/m²"), ("net_weight", "275g·m"),
                           ("temperature", "-10~50°C / -20~70°C"),
                           ("voltage", "5~12V/24V"), ("net_weight", "200~300g或400g"),
                           ("dimensions", "12×23×34×45mm"), ("dimensions", "12×23mm²")):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_value(field, raw).unit)

    def test_dual_temperature_units_only_merge_when_they_actually_agree(self):
        good = normalize_value("temperature", "0°C~40°C (32°F~104°F)")
        self.assertEqual((good.value, good.unit), ([0, 40], "°C"))
        self.assertIsNone(normalize_value("temperature", "0°C~40°C (32°F~140°F)").unit)
        self.assertEqual(normalize_value("dimensions", "1x2cm (10x20mm)").value, [10, 20])
        self.assertIsNone(normalize_value("dimensions", "1x2cm (10x30mm)").unit)

    def test_standard_mains_frequency_suffix_and_dimension_order_note_still_work(self):
        self.assertEqual(normalize_value("voltage", "100-240V~50-60Hz").value, [100, 240])
        self.assertEqual(normalize_value("dimensions", "99*82*30mm (L*W*H)").value, [99, 82, 30])

    def test_labeled_electrical_path_keeps_complete_bound_and_tolerance(self):
        for raw, value, unit in (("至少5V", "≥5", "V"), ("5V以下", "≤5", "V"),
                                  ("5±0.2V", "5±0.2", "V"), ("5V/Hz", "5V/Hz", None)):
            with self.subTest(raw=raw):
                rows = extract_rule_candidates(SourceBlock("b", "a.txt", "text", "hash", {}, "输入电压："+raw))
                self.assertEqual(len(rows), 1)
                self.assertEqual((rows[0].normalized_value, rows[0].normalized_unit), (value, unit))

    def test_alternative_values_are_not_inverted_ranges(self):
        rows = extract_rule_candidates(SourceBlock("b", "a.txt", "text", "hash", {}, "输入：12V/5V"))
        self.assertEqual(rows[0].normalized_value, [12, 5])
        self.assertEqual(build_graph(rows)[1][0].fact_status, "verified")

    def test_unclear_whitespace_label_is_retained_as_reviewable_evidence(self):
        rows = extract_rule_candidates(SourceBlock("b", "a.txt", "text", "hash", {}, "NET WT 8.0oz (0.501b)"))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].normalized_unit)
        self.assertEqual(build_graph(rows)[1][0].fact_status, "pending_confirmation")
