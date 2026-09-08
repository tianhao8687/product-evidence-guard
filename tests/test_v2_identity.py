from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.confirmation import (
    CONFIRMED_FACTS_NAME,
    ConfirmationRequestError,
    apply_batch_decisions,
    resolve_conflict_group,
)
from product_evidence_guard.content_check import check_draft
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.graph import build_graph
from product_evidence_guard.models import FactCandidate, SourceBlock
from product_evidence_guard.review_server import _highlight_bbox
from product_evidence_guard.review_workbook import group_candidates
from product_evidence_guard.workflow import export_table, _public_fact


class V2IdentityTests(unittest.TestCase):
    @staticmethod
    def candidate(*, candidate_id: str, scope: str, unit: str = "V") -> FactCandidate:
        return FactCandidate(
            candidate_id=candidate_id,
            field="voltage",
            field_label="电压",
            raw_value="12V",
            normalized_value=12,
            normalized_unit=unit,
            source_block_id="same-block",
            source_file="label.txt",
            source_kind="text",
            file_hash="a" * 64,
            locator={"line": 1},
            raw_text="12V",
            recognition_confidence=1,
            mapping_confidence=1,
            extraction_method="test",
            scope=scope,
            product_id="product-a",
            product_identity_status="explicit_sku",
        )

    def test_scope_and_unit_are_part_of_evidence_identity(self) -> None:
        input_voltage = self.candidate(candidate_id="input", scope="input")
        output_voltage = self.candidate(candidate_id="output", scope="output")
        odd_unit = self.candidate(candidate_id="odd", scope="input", unit="mV")

        candidates, groups, _ = build_graph([input_voltage, output_voltage, odd_unit])

        self.assertEqual(len(candidates), 3)
        self.assertEqual({group.scope for group in groups}, {"input", "output"})
        self.assertFalse(any(group.severity == "block" for group in groups if group.scope == "output"))
        self.assertTrue(any(group.severity == "block" for group in groups if group.scope == "input"))

    def test_structured_rows_create_separate_sku_products(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "products.csv").write_text(
                "SKU,Model,Color,Weight\n"
                "A100-B,A100,Black,320g\n"
                "A100-W,A100,White,325g\n"
                "A200-B,A200,Black,450g\n",
                encoding="utf-8",
            )

            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))

        self.assertEqual(summary["product_count"], 3)
        self.assertEqual(summary["blocking_conflict_count"], 0)
        weights = [item for item in product["candidates"] if item["field"] == "weight"]
        self.assertEqual(len({item["product_id"] for item in weights}), 3)
        self.assertEqual({item["product_sku"] for item in weights}, {"A100-B", "A100-W", "A200-B"})

    def test_same_product_field_and_scope_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "one.txt").write_text("SKU: A100-B\n重量: 320g\n", encoding="utf-8")
            (root / "two.txt").write_text("SKU: A100-B\n重量: 450g\n", encoding="utf-8")

            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))

        self.assertEqual(summary["product_count"], 1)
        group = next(item for item in product["fact_groups"] if item["field"] == "weight")
        self.assertEqual((group["scope"], group["classification"], group["severity"]),
                         ("unspecified", "strong_conflict", "block"))

    def test_registry_additions_are_extracted_without_core_field_switches(self) -> None:
        block = SourceBlock(
            block_id="frequency-line", source_file="spec.txt", source_kind="text",
            file_hash="a" * 64, locator={"line": 1}, text="Frequency: 2.4kHz",
        )
        candidate = extract_rule_candidates(block)[0]
        self.assertEqual((candidate.field, candidate.normalized_value, candidate.normalized_unit),
                         ("frequency", 2400, "Hz"))

    def test_registry_scopes_separate_facts_but_preserve_same_scope_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "input", Path(tmp) / "output"
            root.mkdir()
            (root / "spec.csv").write_text(
                "Model,工作温度,储存温度,额定扭矩,最大扭矩,工作压力,最大压力\n"
                "A100,0~40°C,-20~60°C,5 N·m,8 N·m,0.5 MPa,0.8 MPa\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
            self.assertEqual(summary["blocking_conflict_count"], 0)
            self.assertEqual({(g["field"], g["scope"]) for g in product["fact_groups"] if g["field"] != "model"},
                             {("temperature", "operating"), ("temperature", "storage"),
                              ("torque", "rating:rated"), ("torque", "rating:max"),
                              ("pressure", "operating"), ("pressure", "rating:max")})
            apply_batch_decisions(output, session_id=summary["session_id"], decisions=[
                {"candidate_id": c["candidate_id"], "action": "confirm", "reason": "人工核对口径"}
                for c in product["candidates"]])
            confirmed = json.loads((output / CONFIRMED_FACTS_NAME).read_text("utf-8"))["facts"]
            public = [_public_fact(f, summary["session_id"]) for f in confirmed]
            checked = check_draft("工作温度 0~40°C 储存温度 -20~60°C；额定扭矩 5 N·m；最大压力 0.8 MPa", public)
            self.assertTrue(all(c["status"] == "supported" for c in checked["claims"]), checked)
            self.assertEqual(len(checked["claims"]), 4)
            wrong = check_draft("工作温度 -20~60°C", public)
            self.assertNotEqual(wrong["claims"][0]["status"], "supported")
            table = Path(export_table(output)["path"]).read_text("utf-8-sig")
            self.assertIn("operating", table)
            self.assertIn("storage", table)
            (root / "other.txt").write_text("Model: A100\n工作温度: 10~50°C\n", encoding="utf-8")
            analyze_directory(root, output)
            updated = json.loads((output / "product-facts.json").read_text("utf-8"))
            blocked = [g for g in updated["fact_groups"] if g["severity"] == "block"]
            self.assertEqual([(g["field"], g["scope"]) for g in blocked], [("temperature", "operating")])

    def test_registry_variant_dimensions_split_explicit_model_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "input", Path(tmp) / "output"
            root.mkdir()
            (root / "capacity.csv").write_text(
                "Model,Capacity,Weight\nA100,1L,320g\nA100,2L,450g\nA100,1000mL,320g\n", encoding="utf-8")
            (root / "length.csv").write_text(
                "Model,Length,Weight\nA200,10m,320g\nA200,20m,450g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
            self.assertEqual(summary["product_count"], 4)
            self.assertEqual(summary["blocking_conflict_count"], 0)
            capacity = [c for c in product["candidates"] if c["field"] == "capacity"]
            self.assertEqual(len({c["product_id"] for c in capacity}), 2)
            self.assertTrue(all(c["product_identity_status"] == "explicit_model_variant" for c in capacity))
            # Neither ordinary weight nor power differences create variants.
            (root / "ordinary.csv").write_text(
                "Model,Weight,Power\nA300,320g,10W\nA300,450g,20W\n", encoding="utf-8")
            (root / "unbound-one.txt").write_text("Model: A400\nCapacity: 1L\nWeight: 320g\n", encoding="utf-8")
            (root / "unbound-two.txt").write_text("Model: A400\nCapacity: 2L\nWeight: 450g\n", encoding="utf-8")
            analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
            self.assertEqual({g["field"] for g in product["fact_groups"] if g["severity"] == "block"},
                             {"weight", "power"})
            self.assertTrue(all(g["classification"] == "identity_ambiguous" for g in product["fact_groups"]
                                if g["product_label"] == "A400"))

    def test_ambiguous_product_requires_ownership_review_before_value_comparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root, output = Path(tmp) / "input", Path(tmp) / "output"
            root.mkdir()
            (root / "products.csv").write_text("SKU,Weight\nA100,320g\nA200,450g\n", encoding="utf-8")
            (root / "orphan.txt").write_text("重量: 320g\n重量: 450g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
            orphan = [c for c in product["candidates"] if c["source_file"] == "orphan.txt"]
            self.assertEqual(summary["blocking_conflict_count"], 0)
            groups = [g for g in product["fact_groups"] if g["classification"] == "identity_ambiguous"]
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["severity"], "review")
            self.assertIn("属于哪个商品", groups[0]["reason"])
            hashes = {c["source_file"]: c["file_hash"] for c in orphan}
            self.assertEqual(group_candidates(orphan, hashes)[0]["classification"], "identity_ambiguous")
            unresolved = [FactCandidate.from_dict(c) for c in orphan]
            for c in unresolved:
                c.product_identity_status = "unresolved"
            self.assertEqual(build_graph(unresolved)[1][0].classification, "identity_ambiguous")
            apply_batch_decisions(output, session_id=summary["session_id"], decisions=[
                {"candidate_id": c["candidate_id"], "action": "confirm", "reason": "只核对原文数值"} for c in orphan])
            confirmed = json.loads((output / CONFIRMED_FACTS_NAME).read_text("utf-8"))["facts"]
            with self.assertRaisesRegex(ConfirmationRequestError, "属于哪个商品"):
                export_table(output)
            result = check_draft("重量 320g", [_public_fact(f, summary["session_id"]) for f in confirmed])
            self.assertEqual(result["claims"][0]["status"], "needs_review")

    def test_batch_confirmation_keeps_per_candidate_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "facts.txt").write_text("净重: 320g\n颜色: Black\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
            decisions = [
                {"candidate_id": row["candidate_id"], "action": "confirm", "reason": "批量人工核对"}
                for row in product["candidates"]
            ]

            result = apply_batch_decisions(output, session_id=summary["session_id"], decisions=decisions)
            events = [json.loads(line) for line in (output / "confirmation-audit.jsonl").read_text("utf-8").splitlines()]
            state = json.loads((output / "confirmation-state.json").read_text("utf-8"))

        prepared = [event for event in events if event.get("transaction_id") == result.transaction_id
                    and event.get("phase") == "prepared"]
        self.assertEqual(len(prepared), len(decisions))
        self.assertEqual({event["candidate_id"] for event in prepared},
                         {item["candidate_id"] for item in decisions})
        self.assertTrue(all(value["transaction_id"] == result.transaction_id
                            for value in state["decisions"].values()))
        self.assertTrue(any(event.get("action") == "batch_commit" for event in events))

    def test_changed_conflict_group_requires_a_fresh_explicit_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "one.txt").write_text("SKU: A100\n重量: 320g\n", encoding="utf-8")
            (root / "two.txt").write_text("SKU: A100\n重量: 450g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
            group = next(item for item in product["fact_groups"] if item["severity"] == "block")

            with self.assertRaises(ConfirmationRequestError):
                resolve_conflict_group(
                    output,
                    session_id=summary["session_id"],
                    group_id=group["group_id"],
                    selected_candidate_id=group["candidate_ids"][0],
                    expected_candidate_ids=group["candidate_ids"][:1],
                    reason="页面中的旧冲突组",
                    reject_others=True,
                )
            state = json.loads((output / "confirmation-state.json").read_text("utf-8"))
            self.assertEqual(state["decisions"], {})

    def test_capacity_does_not_support_runtime_claim(self) -> None:
        result = check_draft(
            "连续运行 48 小时",
            [{"fact_id": "capacity", "product_id": "p1", "field": "capacity_charge",
              "value": 5000, "unit": "mAh", "scope": None}],
        )
        self.assertEqual(result["status"], "blocked")
        runtime = next(claim for claim in result["claims"] if claim["field"] == "runtime")
        self.assertEqual((runtime["status"], runtime["fact_ids"]), ("unsupported", []))

    def test_bbox_highlight_uses_real_coordinates(self) -> None:
        from PIL import Image

        image = Image.new("RGB", (100, 100), "white")
        precision = _highlight_bbox(
            image,
            {"bbox_1000": [100, 200, 300, 400], "position_precision": "approximate"},
        )
        self.assertEqual(precision, "approximate")
        self.assertNotEqual(image.getpixel((10, 20)), (255, 255, 255))
        untouched = Image.new("RGB", (100, 100), "white")
        self.assertEqual(_highlight_bbox(untouched, {}), "unavailable")
        self.assertEqual(untouched.getpixel((10, 20)), (255, 255, 255))

    def test_serial_and_parallel_preprocessing_have_identical_business_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            serial_output = Path(tmp) / "serial"
            parallel_output = Path(tmp) / "parallel"
            root.mkdir()
            (root / "a.txt").write_text("SKU: PEG-1\n输入电压: 12V\n", encoding="utf-8")
            (root / "b.csv").write_text("field,value\n净重,320g\n", encoding="utf-8")
            (root / "c.json").write_text('{"颜色":"Black"}', encoding="utf-8")
            analyze_directory(root, serial_output, preprocessing_workers=1)
            analyze_directory(root, parallel_output, preprocessing_workers=4)
            serial = json.loads((serial_output / "product-facts.json").read_text("utf-8"))
            parallel = json.loads((parallel_output / "product-facts.json").read_text("utf-8"))

        for key in ("products", "candidates", "fact_groups", "cross_field_relations"):
            self.assertEqual(serial[key], parallel[key])


if __name__ == "__main__":
    unittest.main()
