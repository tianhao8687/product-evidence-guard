from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.normalization import normalize_value
from product_evidence_guard.parsers import discover_files


class ProductEvidenceGuardTests(unittest.TestCase):
    def test_progress_callback_marks_each_file_and_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            (root / "b.txt").write_text("数量：2件\n", encoding="utf-8")
            events: list[tuple[str, dict[str, object]]] = []

            analyze_directory(
                root,
                output,
                progress_callback=lambda stage, details: events.append(
                    (stage, dict(details))
                ),
            )

        self.assertEqual(
            [event[0] for event in events],
            ["file_started", "file_started", "finalizing"],
        )
        self.assertEqual(
            [event[1]["file"] for event in events[:2]],
            ["a.txt", "b.txt"],
        )

    def test_ounce_weight_with_visual_label_normalizes_to_grams(self) -> None:
        normalized = normalize_value("net_weight", "NET WT 8.0oz")

        self.assertEqual(normalized.unit, "g")
        self.assertAlmostEqual(float(normalized.value), 226.796185)

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

    def test_numeric_filenames_are_not_mistaken_for_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "label-001.txt").write_text("净重：320g\n", encoding="utf-8")
            (root / "label-002.txt").write_text("净重：350g\n", encoding="utf-8")

            analyze_directory(root, output)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            group = next(item for item in data["fact_groups"] if item["field"] == "net_weight")
            self.assertEqual(group["classification"], "strong_conflict")

    def test_explicit_version_filenames_remain_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "spec-v1.txt").write_text("净重：320g\n", encoding="utf-8")
            (root / "spec-v2.txt").write_text("净重：350g\n", encoding="utf-8")

            analyze_directory(root, output)
            data = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            group = next(item for item in data["fact_groups"] if item["field"] == "net_weight")
            self.assertEqual(group["classification"], "likely_version_update")

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

    def test_discovery_rejects_file_link_that_escapes_input_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "input"
            root.mkdir()
            outside = base / "outside.txt"
            outside.write_text("净重：999g\n", encoding="utf-8")
            link = root / "linked.txt"
            try:
                os.symlink(outside, link)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "escapes"):
                discover_files(root)

    def test_output_ancestor_does_not_hide_all_input_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "input"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")

            summary = analyze_directory(root, base)
            self.assertEqual(summary["files_discovered"], 1)
            self.assertEqual(summary["candidate_count"], 1)


if __name__ == "__main__":
    unittest.main()
