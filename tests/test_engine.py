from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.engine import analyze_directory


class ProductEvidenceGuardTests(unittest.TestCase):
    def test_unit_conversion_is_not_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            (root / "b.txt").write_text("净重：0.32kg\n", encoding="utf-8")

            summary = analyze_directory(root, output)
            self.assertEqual(summary["blocking_conflict_count"], 0)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            group = next(item for item in data["fact_groups"] if item["field"] == "net_weight")
            self.assertEqual(group["classification"], "converted_match")
            self.assertEqual(group["evidence_consistency"], 1.0)

    def test_true_value_difference_blocks_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            (root / "b.txt").write_text("净重：350g\n", encoding="utf-8")

            summary = analyze_directory(root, output)
            self.assertEqual(summary["blocking_conflict_count"], 1)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            group = next(item for item in data["fact_groups"] if item["field"] == "net_weight")
            self.assertEqual(group["classification"], "strong_conflict")

    def test_net_and_gross_weight_are_not_forced_into_one_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n包装重量：400g\n", encoding="utf-8")

            summary = analyze_directory(root, output)
            self.assertEqual(summary["blocking_conflict_count"], 0)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            relation = data["cross_field_relations"][0]
            self.assertEqual(relation["relation_type"], "semantic_scope_mismatch")

    def test_source_locations_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "params.csv").write_text("字段,值\n净重,320g\n", encoding="utf-8")

            analyze_directory(root, output)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate = next(item for item in data["candidates"] if item["field"] == "net_weight")
            self.assertEqual(candidate["source_file"], "params.csv")
            self.assertEqual(candidate["locator"]["row"], 2)

    def test_unchanged_files_are_reused_and_changed_file_is_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            source = root / "a.txt"
            source.write_text("净重：320g\n", encoding="utf-8")

            first = analyze_directory(root, output)
            self.assertEqual(first["changed_or_new_files"], ["a.txt"])
            second = analyze_directory(root, output)
            self.assertEqual(second["unchanged_files_reused"], ["a.txt"])
            source.write_text("净重：350g\n", encoding="utf-8")
            third = analyze_directory(root, output)
            self.assertEqual(third["changed_or_new_files"], ["a.txt"])
            self.assertEqual(third["unchanged_files_reused"], [])

    def test_ocr_sidecar_becomes_image_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "box.jpg.ocr.json").write_text(
                json.dumps(
                    {
                        "image": "box.jpg",
                        "blocks": [{"text": "净重 300g", "bbox": [1, 2, 3, 4], "confidence": 0.91}],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            analyze_directory(root, output)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate = next(item for item in data["candidates"] if item["field"] == "net_weight")
            self.assertEqual(candidate["source_kind"], "image_ocr")
            self.assertEqual(candidate["locator"]["bbox"], [1, 2, 3, 4])
            self.assertAlmostEqual(candidate["recognition_confidence"], 0.91)


if __name__ == "__main__":
    unittest.main()
