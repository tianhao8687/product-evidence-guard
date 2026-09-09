from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.confirmation import apply_decision
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.models import FactCandidate, FactGroup
from product_evidence_guard.reports import (
    write_conflicts_markdown,
    write_html_report,
)


class EvidenceReportTests(unittest.TestCase):
    def test_visual_provenance_bbox_confidence_sources_and_status_are_visible(self) -> None:
        candidate = FactCandidate(
            candidate_id="candidate-1",
            field="net_weight",
            field_label="净重",
            raw_value="300g",
            normalized_value=300,
            normalized_unit="g",
            source_block_id="block-1",
            source_file="包装扫描.pdf",
            source_kind="pdf_scan_qwen_vl",
            file_hash="abc",
            locator={
                "pdf": "包装扫描.pdf",
                "page": 2,
                "bbox_1000": [100, 200, 500, 280],
                "position_precision": "approximate",
            },
            raw_text="净重 300g",
            recognition_confidence=0.84,
            mapping_confidence=0.92,
            extraction_method="qwen_vl_field_mapping",
            scope="net",
            mapping_confidence_source="model_self_assessment",
            status="confirmed",
            provenance={
                "recognition_confidence_source": "model_self_assessment",
                "mapping_confidence_source": "model_self_assessment",
                "render_method": "pypdfium2",
            },
        )
        group = FactGroup(
            field="net_weight",
            field_label="净重",
            classification="insufficient_evidence",
            severity="review",
            reason="只找到一条证据。",
            candidate_ids=[candidate.candidate_id],
            normalized_values=[{"value": 300, "unit": "g"}],
            recognition_confidence=0.84,
            mapping_confidence=0.92,
            evidence_consistency=1.0,
            recommendation="人工确认。",
            scope="net",
        )
        run_summary = {
            "confirmation_status_counts": {
                "pending": 0,
                "confirmed": 1,
                "rejected": 0,
                "stale": 1,
            }
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            markdown_path = root / "conflicts.md"
            html_path = root / "report.html"
            write_conflicts_markdown(
                markdown_path,
                [candidate],
                [group],
                [],
                run_summary=run_summary,
            )
            write_html_report(
                html_path,
                [candidate],
                [group],
                [],
                run_summary,
            )
            markdown = markdown_path.read_text(encoding="utf-8")
            html = html_path.read_text(encoding="utf-8")

        for report in (markdown, html):
            self.assertIn("pdf_scan_qwen_vl", report)
            self.assertIn("qwen_vl_field_mapping", report)
            self.assertIn("第 2 页", report)
            self.assertIn("近似坐标 bbox_1000=[100, 200, 500, 280]", report)
            self.assertIn("模型自评（未校准）", report)
            self.assertIn("已人工确认", report)
            self.assertIn("已确认", report)
            self.assertIn("300", report)
            self.assertIn("<details>", report)
            self.assertIn("已失效", report)
            self.assertIn("源文件或文件哈希变化而失效", report)

    def test_source_change_surfaces_stale_confirmation_in_both_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            source = root / "facts.txt"
            source.write_text("净重：320g\n", encoding="utf-8")

            first = analyze_directory(root, output)
            facts = json.loads(
                (output / "product-facts.json").read_text(encoding="utf-8")
            )
            candidate_id = facts["candidates"][0]["candidate_id"]
            apply_decision(
                output,
                session_id=first["session_id"],
                candidate_id=candidate_id,
                reason="供应商已确认",
                action="confirm",
            )

            source.write_text("净重：350g\n", encoding="utf-8")
            second = analyze_directory(root, output)
            markdown = (output / "conflicts.md").read_text(encoding="utf-8")
            html = (output / "evidence-report.html").read_text(encoding="utf-8")

        self.assertEqual(second["confirmation_status_counts"]["pending"], 1)
        self.assertEqual(second["confirmation_status_counts"]["stale"], 1)
        for report in (markdown, html):
            self.assertIn("已失效", report)
            self.assertIn("旧确认", report)


if __name__ == "__main__":
    unittest.main()
