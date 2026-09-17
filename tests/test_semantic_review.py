"""Production-path admission, fallback, cache and human-decision contracts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from product_evidence_guard import workflow
from product_evidence_guard.confirmation import apply_review_action
from product_evidence_guard.engine import analyze_directory, _engine_signature
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.graph import build_graph
from product_evidence_guard.identity import resolve_product_identities
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.openvino_adapter import OpenVinoDeviceSelection
from product_evidence_guard.semantic_review import assist_candidates, validate_proposal, context_record, prompt_for


def proposal(**changes):
    return {"id": "r1", "kind": "fact", "label": "净重", "value": "135g", "operator": "le",
            "qualifier": "≤", "condition": "运输状态", **changes}


class FakeBackend:
    model_id = "semantic-test"
    device = "CPU"

    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback

    def generate_semantic(self, prompt, **options):
        self.calls.append((prompt, options))
        if self.callback:
            return self.callback(prompt)
        return json.dumps([proposal()], ensure_ascii=False)


class SemanticReviewTests(unittest.TestCase):
    def setUp(self):
        # A synthetic backend must not require a real OpenVINO installation.
        resolver = patch("product_evidence_guard.engine.resolve_openvino_device", return_value=
                         OpenVinoDeviceSelection("CPU", "CPU", ("CPU",), {"CPU": "test"}, "test"))
        resolver.start()
        self.addCleanup(resolver.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.root, self.output = self.base / "input", self.base / "output"
        self.root.mkdir()
        self.backend = FakeBackend()
        self.reader = QwenVlReader(self.backend)

    def write(self, text="SKU: TEST-1\n产品规格\n净重：≤135g（运输状态）", name="a.txt"):
        (self.root / name).write_text(text, encoding="utf8")

    def run_analysis(self, **options):
        options.setdefault("semantic_assist", True)
        summary = analyze_directory(self.root, self.output, preloaded_image_reader=self.reader,
                                    device="CPU", preprocessing_workers=1, **options)
        self.session = summary["session_id"]
        return summary, json.loads((self.output / "product-facts.json").read_text("utf8"))

    def weights(self, product):
        return [g for g in product["facts"] if g["field"] == "net_weight" and not g.get("excluded")]

    def test_actual_pipeline_adopts_guarded_value_without_human_approval(self):
        self.write()
        summary, product = self.run_analysis()
        self.assertEqual(summary["errors"], [])
        group = self.weights(product)[0]
        self.assertEqual((group["fact_status"], group["selected_value"], group["selected_unit"]), ("verified", "≤135", "g"))
        self.assertFalse(group["human_approved"])
        self.assertEqual(group["independent_source_count"], 1)
        item = next(c for c in product["candidates"] if c["field"] == "net_weight")
        self.assertEqual(item["raw_text"], "净重：≤135g（运输状态）")
        self.assertEqual(item["locator"]["line"], 3)
        self.assertEqual(item["decision_status"], "undecided")
        self.assertEqual(item["mapping_confidence"], .95)
        self.assertEqual(summary["local_ai"]["semantic_assist"]["accepted"], 1)
        self.assertEqual(len(self.backend.calls), 1)

    def test_unchanged_cache_reuses_admission_without_a_second_source_or_call(self):
        self.write()
        _, first = self.run_analysis()
        summary, second = self.run_analysis()
        self.assertEqual(self.weights(first), self.weights(second))
        self.assertEqual(len(self.backend.calls), 1)
        self.assertEqual(summary["local_ai"]["semantic_assist"]["calls"], 0)

    def test_switch_off_rebuilds_rules_only_output(self):
        self.write()
        self.run_analysis()
        summary, product = self.run_analysis(semantic_assist=False)
        self.assertTrue(summary["engine_signature_changed"])
        self.assertEqual(self.weights(product)[0]["fact_status"], "pending_confirmation")
        self.assertFalse(summary["local_ai"]["semantic_assist"]["enabled"])
        self.assertEqual(len(self.backend.calls), 1)

    def test_bad_json_abstention_ignore_or_changed_field_keep_original_evidence(self):
        self.write()
        for raw in ("not json", "[]", '[{"id":"r1","kind":"ignore"}]',
                    json.dumps([proposal(label="毛重")]), json.dumps([proposal(value="235g")]),
                    json.dumps([proposal(), proposal()])):
            with self.subTest(raw=raw):
                self.backend.callback = lambda _prompt, raw=raw: raw
                _, product = self.run_analysis()
                self.assertEqual(self.weights(product)[0]["fact_status"], "pending_confirmation")
                self.assertEqual(len([c for c in product["candidates"] if c["field"] == "net_weight"]), 1)

    def test_runtime_failure_keeps_rules_result_without_breaking_readiness(self):
        self.write()
        def fail(_prompt):
            raise TimeoutError("model timeout")
        self.backend.callback = fail
        summary, product = self.run_analysis()
        self.assertEqual(summary["errors"], [])
        self.assertEqual(self.weights(product)[0]["fact_status"], "pending_confirmation")
        trace = json.loads((self.output / "semantic-review.json").read_text("utf8"))
        self.assertIn("TimeoutError", trace["attempts"][0]["error"])

    def test_clear_values_real_conflicts_and_versions_do_not_call_model(self):
        for first, second in (("净重：135g", None), ("净重：135g", "净重：140g")):
            with self.subTest(second=second):
                self.write("SKU: TEST-1\n" + first)
                if second:
                    self.write("SKU: TEST-1\n" + second, "b.txt")
                _, product = self.run_analysis()
                self.assertEqual(self.weights(product)[0]["fact_status"], "conflict" if second else "verified")
        self.assertEqual(self.backend.calls, [])

    def test_different_versions_are_not_chosen_by_ai(self):
        self.write("SKU: TEST-1\n净重：135g", "manual-v1.txt")
        self.write("SKU: TEST-1\n净重：140g", "manual-v2.txt")
        _, product = self.run_analysis()
        self.assertEqual(self.weights(product)[0]["review_reason_code"], "version_unclear")
        self.assertEqual(self.backend.calls, [])

    def test_human_correction_survives_enabling_ai(self):
        self.write()
        _, product = self.run_analysis(semantic_assist=False)
        group = self.weights(product)[0]
        apply_review_action(self.output, session_id=self.session, action="edit", group_id=group["group_id"],
                            expected_candidate_ids=group["candidate_ids"], candidate_id=group["candidate_ids"][0], value="140g")
        _, result = self.run_analysis()
        self.assertEqual(self.weights(result)[0]["selected_value"], 140)
        self.assertTrue(self.weights(result)[0]["human_approved"])
        self.assertEqual(self.backend.calls, [])

    def test_ai_result_can_be_adopted_and_exported_as_human_confirmed(self):
        self.write()
        _, product = self.run_analysis()
        group = self.weights(product)[0]
        apply_review_action(self.output, session_id=self.session, action="adopt", group_id=group["group_id"],
                            expected_candidate_ids=group["candidate_ids"], candidate_id=group["candidate_ids"][0])
        _, result = self.run_analysis()
        self.assertTrue(self.weights(result)[0]["human_approved"])
        exported = workflow.export_table(self.output, mode="human")
        self.assertIn("135", Path(exported["path"]).read_text("utf-8-sig"))

    def test_source_change_during_generation_discards_mixed_version_result(self):
        self.write()
        def change(_prompt):
            self.write("SKU: TEST-1\n净重：999g")
            return json.dumps([proposal()])
        self.backend.callback = change
        summary, product = self.run_analysis()
        self.assertEqual(self.weights(product), [])
        self.assertTrue(any(e["error"] == "source_changed_during_read" for e in summary["errors"]))

    def test_multi_sku_list_does_not_broadcast_values_or_inherit_last_sku(self):
        self.write("SKU: CTRL-A / CTRL-B\n净重：245g\nSKU: CTRL-C\n净重：270g")
        _, product = self.run_analysis()
        groups = self.weights(product)
        self.assertEqual(len(groups), 2)
        self.assertEqual(sum(g["fact_status"] == "verified" for g in groups), 1)
        self.assertEqual(next(g for g in groups if g["fact_status"] == "verified")["selected_value"], 270)
        self.assertEqual(next(g for g in groups if g["fact_status"] != "verified")["review_reason_code"], "identity_ambiguous")

    def test_literal_guard_rejects_number_hiding_and_dropped_words(self):
        for text, item in (("净重：不多于135g", proposal(value="135G")),
                           ("净重：不是235g135g", proposal(qualifier="不是235g")),
                           ("净重：不多于135g extra", proposal()),
                           ("净重：135g except other product", proposal(operator="exact", qualifier="", condition="except other product")),
                           ("净重：大于135g", proposal(operator="le", qualifier="大于"))):
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_proposal(item, text)

    def test_context_is_complete_or_model_does_not_run(self):
        block = SourceBlock("r1", "a.txt", "text", "hash", {"line": 1}, "x" * 12001)
        self.assertEqual(context_record([block]), {"complete": False})
        self.write("SKU: TEST-1\n" + "资料" * 6100 + "\n净重：不多于135g")
        _, product = self.run_analysis()
        self.assertEqual(self.weights(product)[0]["fact_status"], "pending_confirmation")
        self.assertEqual(self.backend.calls, [])

    def test_low_reading_confidence_and_missing_conditions_are_not_laundered(self):
        block = SourceBlock("b1", "a.txt", "text", "hash", {"line": 1}, "净重：不多于135g")
        for change in ("low_confidence", "missing_condition"):
            with self.subTest(change=change):
                candidates = extract_rule_candidates(block)
                if change == "low_confidence":
                    candidates[0].recognition_confidence = .5
                else:
                    candidates[0].notes.append("unparsed_source_condition")
                resolve_product_identities(candidates, dataset_root=self.root)
                candidates, groups, _ = build_graph(candidates)
                before = copy.deepcopy(candidates)
                result, trace = assist_candidates(candidates, groups, {"a.txt": context_record([block])}, self.backend.generate_semantic)
                self.assertEqual(trace["calls"], 0)
                self.assertEqual(result, before)

    def test_long_document_uses_whole_page_with_heading_conditions_and_footer(self):
        blocks = [SourceBlock("title", "a.pdf", "pdf", "hash", {"page": 1}, "SKU: TEST-1")]
        blocks += [SourceBlock(f"noise{i}", "a.pdf", "pdf", "hash", {"page": i}, "资料" * 3000) for i in (2, 3, 4, 5, 6, 7)]
        # Short document heading must not accidentally include huge next pages.
        blocks[1:1] = [SourceBlock(f"head{i}", "a.pdf", "pdf", "hash", {"page": 1}, "产品规格") for i in range(5)]
        target = SourceBlock("target", "a.pdf", "pdf", "hash", {"page": 8}, "净重：≤135g（运输状态）",
                             provenance={"parent_labels": ["运输状态"]})
        blocks += [target, SourceBlock("footer", "a.pdf", "pdf", "hash", {"page": 8}, "本页适用于当前批次")]
        context = context_record(blocks)
        self.assertFalse(context["complete"])
        candidates = extract_rule_candidates(target)
        resolve_product_identities(candidates, dataset_root=self.root)
        candidates, groups, _ = build_graph(candidates)
        _, trace = assist_candidates(candidates, groups, {"a.pdf": context}, self.backend.generate_semantic)
        self.assertEqual(trace["accepted"], 1)
        prompt = self.backend.calls[0][0]
        for text in ("SKU: TEST-1", "运输状态", "当前批次", "本页适用于当前批次"):
            self.assertIn(text, prompt)
        self.assertNotIn("资料" * 3000, prompt)

    def test_oversized_region_and_unstructured_tail_never_become_complete(self):
        blocks = [SourceBlock("b1", "a.pdf", "pdf", "hash", {"page": 1}, "资料" * 6500),
                  SourceBlock("b2", "a.pdf", "pdf", "hash", {"page": 1}, "净重：不多于135g")]
        self.assertEqual(context_record(blocks), {"complete": False})

    def test_unknown_qualifier_cannot_be_auto_adopted_even_if_every_word_is_copied(self):
        self.write("SKU: TEST-1\n电池容量：no fewer than 3100mAh")
        for operator in ("le", "ge"):
            self.backend.callback = lambda _prompt, operator=operator: json.dumps([proposal(
                label="电池容量", value="3100mAh", qualifier="no fewer than", operator=operator, condition="")])
            _, product = self.run_analysis()
            group = next(g for g in product["facts"] if g["field"] == "capacity_charge")
            self.assertEqual(group["fact_status"], "pending_confirmation")
            trace = json.loads((self.output / "semantic-review.json").read_text("utf8"))
            self.assertEqual(trace["attempts"][0]["changes"][0]["result"], "unverified_qualifier_meaning")

    def test_zero_budget_falls_back_and_repeat_changes_cache_signature(self):
        block = SourceBlock("b1", "a.txt", "text", "hash", {"line": 1}, "净重：不多于135g")
        candidates = extract_rule_candidates(block)
        resolve_product_identities(candidates, dataset_root=self.root)
        candidates, groups, _ = build_graph(candidates)
        result, trace = assist_candidates(candidates, groups, {"a.txt": context_record([block])},
                                          self.backend.generate_semantic, max_calls=0)
        self.assertEqual(result, candidates)
        self.assertTrue(trace["budget_exhausted"])
        single = prompt_for("a.txt", block.text, [{"id": "r1", "text": block.text}])
        repeated = prompt_for("a.txt", block.text, [{"id": "r1", "text": block.text}], repeat=True)
        self.assertEqual(repeated, single + "\n" + single)
        self.assertNotEqual(_engine_signature(None, None, "CPU", semantic_assist=True),
                            _engine_signature(None, None, "CPU", semantic_assist=True, semantic_repeat=True))


if __name__ == "__main__":
    unittest.main()
