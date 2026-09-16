"""Negative controls ensure a broken extractor cannot grade itself correct."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark
from evaluate_scenarios import run_case, score


class EvaluationSafetyTests(unittest.TestCase):
    def test_wrong_product_and_scope_cannot_match_conflict(self):
        gold = [{"product_id": "A", "field": "net_weight", "scope": "net", "classification": "strong_conflict"}]
        bad = [SimpleNamespace(product_id="B", field="net_weight", scope="gross", classification="strong_conflict")]
        self.assertEqual(benchmark._conflict_recall(gold, bad)["matched"], 0)
        metric = benchmark._conflict_classification_metrics(gold, bad, ["net_weight"])
        self.assertEqual((metric["true_positive"], metric["false_positive"], metric["false_negative"]), (0, 1, 1))

    def test_duplicate_predictions_do_not_disappear_from_precision(self):
        gold = [{"product_id": "A", "field": "net_weight", "scope": "net", "classification": "strong_conflict"}]
        item = SimpleNamespace(**gold[0])
        metric = benchmark._conflict_classification_metrics(gold, [item, item], ["net_weight"])
        self.assertEqual(metric["precision"], 0.5)

    def test_no_production_normalizer_is_used_to_grade_gold(self):
        expected = {"field": "net_weight", "normalized_value": 300, "normalized_unit": "g", "raw_value": "0.3kg"}
        with patch("product_evidence_guard.normalization.normalize_value", side_effect=AssertionError("oracle coupling")):
            metrics, _ = benchmark._quality_metrics([{"sample_id": "a", "expected_mappings": [expected]}],
                                                    {"a": {"fact_candidates": [dict(expected)]}})
        self.assertEqual(metrics["field_mapping"]["true_positive"], 1)

    def test_missing_gold_value_or_unit_is_not_silently_inferred(self):
        for expected in ({"field": "net_weight", "raw_value": "300g"},
                         {"field": "net_weight", "normalized_value": 300}):
            with self.assertRaisesRegex(ValueError, "normalized_value.*normalized_unit"):
                benchmark._quality_metrics([{"sample_id": "a", "expected_mappings": [expected]}], {})

    def test_mixed_injection_page_legitimate_fact_is_not_false_alarm(self):
        value = {"field": "net_weight", "normalized_value": 300, "normalized_unit": "g"}
        metrics, _ = benchmark._quality_metrics([{"sample_id": "a", "expected_mappings": [value],
            "contains_prompt_injection_text": True}], {"a": {"fact_candidates": [value]}})
        self.assertEqual(metrics["prompt_injection_fact_rate"]["value"], 0)

    def test_whole_output_scorer_rejects_wrong_value_unit_sku_scope_status_and_duplicates(self):
        good = ["A", "net_weight", "net", "verified", [[300, "g"]]]
        for index, wrong in ((0, "B"), (2, "gross"), (3, "conflict"), (4, [[301, "g"]]), (4, [[300, "kg"]])):
            altered = list(good)
            altered[index] = wrong
            self.assertFalse(score([good], [altered])["passed"])
        self.assertFalse(score([good], [good, good])["passed"])

    def test_open_field_label_case_is_cosmetic_not_a_wrong_value(self):
        good = ["A", "custom:CO2范围", None, "verified", [["500ppm", None]]]
        alternate = ["A", "custom:co2范围", None, "verified", [["500ppm", None]]]
        result = score([good], [alternate])
        self.assertTrue(result["passed"])
        self.assertEqual((result["wrong_auto_confirmed"], result["missing_fact_keys"]), (0, 0))


class GeneralizationScenarioTests(unittest.TestCase):
    pass


for fixture in ("generalization_cases.json", "public_source_cases.json"):
    for case in json.loads((Path(__file__).parent / "fixtures" / fixture).read_text("utf-8"))["cases"]:
        def test(self, case=case):
            result = run_case(case)
            self.assertTrue(result["passed"], result)
        setattr(GeneralizationScenarioTests, "test_" + case["id"], test)
