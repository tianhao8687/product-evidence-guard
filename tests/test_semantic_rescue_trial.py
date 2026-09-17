"""Test the isolated pilot harness, not the model's claimed correctness."""
import json
from pathlib import Path
import tempfile
import unittest

from scripts.semantic_rescue_trial import validated_candidate, prompt_for, measure, apply_shadow, prepare_case
from tests.fixtures.semantic_rescue_pilot import CASES


class SemanticRescueTrialTests(unittest.TestCase):
    def record(self, text):
        return {"id": "r1", "text": text, "file": "source.txt", "file_hash": "hash", "line": 2}

    def proposal(self, **kwargs):
        return {"id": "r1", "kind": "fact", "label": "净重", "value": "135g",
                "operator": "le", "qualifier": "不多于", "condition": "", **kwargs}

    def test_gold_and_categories_are_never_sent_to_the_model(self):
        case = {**CASES[0], "expected": ["SECRET_GOLD"], "family": "SECRET_FAMILY"}
        prompt = prompt_for(case, [self.record("净重：不多于135g")])
        self.assertNotIn("SECRET", prompt)
        self.assertEqual(len(CASES), 24)
        self.assertEqual(len({c["family"] for c in CASES}), 6)

    def test_guard_anchors_every_semantic_component(self):
        record = self.record("净重：不多于135g")
        good = validated_candidate(self.proposal(), record)
        self.assertEqual((good.normalized_value, good.normalized_unit), ("≤135", "g"))
        self.assertEqual(good.raw_text, record["text"])
        self.assertTrue(good.provenance["shadow_only"])
        for changes in ({"value": "235g"}, {"label": "毛重"}, {"qualifier": "不少于"},
                        {"condition": "实验模式"}, {"operator": "exact"}, {"qualifier": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validated_candidate(self.proposal(**changes), record)

    def test_unit_case_cannot_be_silently_changed(self):
        with self.assertRaises(ValueError):
            validated_candidate(self.proposal(label="输出功率", value="2MW", operator="exact", qualifier=""),
                                self.record("输出功率：2mW"))

    def test_missing_duplicate_and_unknown_target_ids_fall_back(self):
        baseline = {"candidates": [], "facts": []}
        for raw in ("[]", "not JSON", '[{"id":"other","kind":"ignore"}]',
                    '[{"id":"r1","kind":"ignore"},{"id":"r1","kind":"ignore"}]'):
            value, changes = apply_shadow(CASES[0], [self.record("净重：不多于135g")], baseline, raw, Path("."))
            self.assertEqual(value, baseline)
            self.assertEqual(changes[0]["reason"], "response_coverage_or_schema")

    def test_pending_is_not_counted_as_correct_automatic_understanding(self):
        # An empty result cannot pass a case requiring an adopted fact or a
        # review warning, even when it has no incorrect confirmed facts.
        empty = {"candidates": [], "facts": []}
        self.assertFalse(measure(CASES[0], empty)["passed"])
        self.assertFalse(measure(CASES[7], empty)["passed"])

    def test_model_cannot_overwrite_clear_facts_or_resolve_a_conflict(self):
        for case_id in ("b1", "c1", "c2", "s4"):
            case = next(c for c in CASES if c["id"] == case_id)
            with self.subTest(case=case_id), tempfile.TemporaryDirectory() as temp:
                inputs, records, baseline, _ = prepare_case(case, Path(temp))
                raw = json.dumps([{"id": r["id"], "kind": "ignore"} for r in records])
                shadow, changes = apply_shadow(case, records, baseline, raw, inputs)
                self.assertEqual(measure(case, baseline), measure(case, shadow))
                self.assertTrue(all(c["reason"] == "protected_existing_fact" for c in changes))


if __name__ == "__main__":
    unittest.main()
