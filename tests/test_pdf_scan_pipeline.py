from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.models import FactCandidate, SourceBlock
from product_evidence_guard.parsers import (
    PdfParseResult,
    RenderedPdfPage,
    parse_pdf_document,
    render_pdf_page,
    sha256_file,
)
from product_evidence_guard.qwen_vl_reader import QwenVlReadResult


class _FakePdfTextPage:
    def __init__(self, text: str | None) -> None:
        self._text = text

    def extract_text(self) -> str | None:
        return self._text


class PdfParserTests(unittest.TestCase):
    def test_parse_pdf_document_marks_only_textless_pages_for_visual_reading(self) -> None:
        fake_reader = types.SimpleNamespace(
            pages=[
                _FakePdfTextPage("净重：320g\n型号：PEG-100"),
                _FakePdfTextPage(" \n "),
                _FakePdfTextPage(None),
            ]
        )
        fake_pypdf = types.SimpleNamespace(PdfReader=lambda path: fake_reader)

        with patch.dict(sys.modules, {"pypdf": fake_pypdf}):
            result = parse_pdf_document(
                Path("catalog.pdf"),
                "catalog.pdf",
                "pdf-hash",
            )

        self.assertEqual(result.page_count, 3)
        self.assertEqual(result.scanned_page_numbers, (2, 3))
        self.assertEqual([block.text for block in result.blocks], ["净重：320g", "型号：PEG-100"])
        self.assertTrue(all(block.source_file == "catalog.pdf" for block in result.blocks))
        self.assertTrue(all(block.source_kind == "pdf_text" for block in result.blocks))
        self.assertEqual([block.locator["page"] for block in result.blocks], [1, 1])

    def test_render_pdf_page_uses_pdfium_and_closes_all_resources(self) -> None:
        closed: list[str] = []

        class FakeImage:
            size = (200, 400)

            def save(self, path: Path, format: str) -> None:
                self.saved_format = format
                path.write_bytes(b"png")

            def close(self) -> None:
                closed.append("image")

        class FakeBitmap:
            def to_pil(self) -> FakeImage:
                return FakeImage()

            def close(self) -> None:
                closed.append("bitmap")

        class FakePage:
            def get_size(self) -> tuple[float, float]:
                return (100.0, 200.0)

            def render(self, *, scale: float) -> FakeBitmap:
                self.scale = scale
                return FakeBitmap()

            def close(self) -> None:
                closed.append("page")

        class FakeDocument:
            def __init__(self, path: str) -> None:
                self.pages = [FakePage()]

            def __len__(self) -> int:
                return len(self.pages)

            def __getitem__(self, index: int) -> FakePage:
                return self.pages[index]

            def close(self) -> None:
                closed.append("document")

        fake_pdfium = types.SimpleNamespace(PdfDocument=FakeDocument)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            with patch.dict(sys.modules, {"pypdfium2": fake_pdfium}):
                rendered = render_pdf_page(
                    Path("scan.pdf"),
                    1,
                    output,
                )

            self.assertEqual(rendered.page_number, 1)
            self.assertEqual(rendered.pixel_width, 200)
            self.assertEqual(rendered.pixel_height, 400)
            self.assertEqual(rendered.image_path.name, "page-0001.png")
            self.assertTrue(rendered.image_path.is_file())

        self.assertCountEqual(closed, ["image", "bitmap", "page", "document"])


class _FakeVisualReader:
    def __init__(self, *, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.seen_images: list[Path] = []

    def analyze_image(
        self,
        image_path: Path,
        root: Path,
        *,
        deadline: float | None = None,
    ) -> QwenVlReadResult:
        self.seen_images.append(image_path)
        if self.raise_error:
            raise RuntimeError("model failed")
        file_hash = sha256_file(image_path)
        block = SourceBlock(
            block_id="temporary-block",
            source_file=image_path.relative_to(root).as_posix(),
            source_kind="image_openvino_qwen_vl",
            file_hash=file_hash,
            locator={
                "image": image_path.name,
                "transcription_id": "visual-001",
                "bbox_1000": [100, 200, 500, 280],
                "position_precision": "approximate",
                "legibility": "clear",
                "recognition_confidence_source": "model_self_assessment",
                "model_id": "fake-qwen",
                "inference_device": "CPU",
            },
            text="净重 300g",
            recognition_confidence=0.84,
            extraction_method="qwen_vl_visual_transcription",
            recognition_confidence_source="model_self_assessment",
            provenance={
                "model_id": "fake-qwen",
                "inference_device": "CPU",
            },
        )
        candidate = FactCandidate(
            candidate_id="temporary-candidate",
            field="net_weight",
            field_label="净重",
            raw_value="300g",
            normalized_value=300,
            normalized_unit="g",
            source_block_id=block.block_id,
            source_file=block.source_file,
            source_kind=block.source_kind,
            file_hash=file_hash,
            locator=block.locator,
            raw_text=block.text,
            recognition_confidence=0.84,
            mapping_confidence=0.92,
            extraction_method="qwen_vl_field_mapping",
            scope="net",
            mapping_confidence_source="model_self_assessment",
            provenance={
                "recognition_confidence_source": "model_self_assessment",
                "mapping_confidence_source": "model_self_assessment",
            },
        )
        return QwenVlReadResult(
            image_file=block.source_file,
            file_hash=file_hash,
            source_blocks=[block],
            fact_candidates=[candidate],
        )


class PdfEnginePipelineTests(unittest.TestCase):
    @staticmethod
    def _fake_render(
        rendered_paths: list[Path],
    ):
        def render(
            path: Path,
            page_number: int,
            output_dir: Path,
        ) -> RenderedPdfPage:
            image_path = output_dir / f"page-{page_number:04d}.png"
            image_path.write_bytes(b"temporary rendered page")
            rendered_paths.append(image_path)
            return RenderedPdfPage(
                page_number=page_number,
                image_path=image_path,
                pixel_width=1200,
                pixel_height=1600,
            )

        return render

    def test_scanned_pdf_candidate_keeps_original_pdf_page_and_temp_is_removed(self) -> None:
        reader = _FakeVisualReader()
        rendered_paths: list[Path] = []
        pdf_parse_result = PdfParseResult(
            blocks=(),
            scanned_page_numbers=(1,),
            page_count=1,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            pdf = root / "包装扫描.pdf"
            pdf.write_bytes(b"fake pdf")
            original_hash = sha256_file(pdf)

            with (
                patch(
                    "product_evidence_guard.engine.QwenVlReader.from_openvino",
                    return_value=reader,
                ),
                patch(
                    "product_evidence_guard.engine.parse_pdf_document",
                    return_value=pdf_parse_result,
                ),
                patch(
                    "product_evidence_guard.engine.render_pdf_page",
                    side_effect=self._fake_render(rendered_paths),
                ),
            ):
                summary = analyze_directory(
                    root,
                    output,
                    openvino_vlm_model=Path(tmp) / "fake-model",
                )

            facts = json.loads(
                (output / "product-facts.json").read_text(encoding="utf-8")
            )
            candidate = facts["candidates"][0]
            self.assertEqual(candidate["source_file"], "包装扫描.pdf")
            self.assertEqual(candidate["source_kind"], "pdf_scan_qwen_vl")
            self.assertEqual(candidate["file_hash"], original_hash)
            self.assertEqual(candidate["locator"]["pdf"], "包装扫描.pdf")
            self.assertEqual(candidate["locator"]["page"], 1)
            self.assertEqual(
                candidate["locator"]["position_precision"],
                "approximate",
            )
            self.assertEqual(
                candidate["locator"]["bbox_1000"],
                [100, 200, 500, 280],
            )
            self.assertEqual(
                candidate["mapping_confidence_source"],
                "model_self_assessment",
            )
            self.assertFalse(candidate["provenance"]["temporary_render_retained"])

            visual_text = (output / "visual-transcription.json").read_text(
                encoding="utf-8"
            )
            self.assertIn("包装扫描.pdf#page=1", visual_text)
            self.assertNotIn("page-0001.png", visual_text)
            self.assertEqual(summary["scanned_pdf"]["pages_detected"], 1)
            self.assertEqual(
                summary["scanned_pdf"]["pages_processed_with_qwen_vl"],
                1,
            )
            self.assertFalse(summary["scanned_pdf"]["temporary_pages_retained"])

            markdown = (output / "conflicts.md").read_text(encoding="utf-8")
            html = (output / "evidence-report.html").read_text(encoding="utf-8")
            for report in (markdown, html):
                self.assertIn("pdf_scan_qwen_vl", report)
                self.assertIn("qwen_vl_field_mapping", report)
                self.assertIn("模型自评", report)
                self.assertIn("近似坐标", report)
                self.assertIn("待人工确认", report)

        self.assertEqual(len(rendered_paths), 1)
        self.assertFalse(rendered_paths[0].exists())
        self.assertFalse(rendered_paths[0].parent.exists())

    def test_scanned_pdf_without_model_is_reported_and_never_rendered(self) -> None:
        pdf_parse_result = PdfParseResult(
            blocks=(),
            scanned_page_numbers=(1, 2),
            page_count=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "scan.pdf").write_bytes(b"fake pdf")

            with (
                patch(
                    "product_evidence_guard.engine.parse_pdf_document",
                    return_value=pdf_parse_result,
                ),
                patch(
                    "product_evidence_guard.engine.render_pdf_page",
                    side_effect=AssertionError("must not render without a model"),
                ),
            ):
                first = analyze_directory(root, output)
                second = analyze_directory(root, output)

        self.assertEqual(first["scanned_pdf"]["pages_detected"], 2)
        self.assertEqual(first["scanned_pdf"]["pages_processed_with_qwen_vl"], 0)
        self.assertEqual(first["scanned_pdf"]["pages_skipped"], 2)
        self.assertIn(
            "scanned_pdf_pages_require_local_image_model",
            {item["reason"] for item in first["skipped_files"]},
        )
        self.assertIn(
            "scanned_pdf_pages_require_local_image_model",
            {item["reason"] for item in second["skipped_files"]},
        )

    def test_temp_render_is_removed_when_visual_reader_raises(self) -> None:
        reader = _FakeVisualReader(raise_error=True)
        rendered_paths: list[Path] = []
        pdf_parse_result = PdfParseResult(
            blocks=(),
            scanned_page_numbers=(1,),
            page_count=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "scan.pdf").write_bytes(b"fake pdf")

            with (
                patch(
                    "product_evidence_guard.engine.QwenVlReader.from_openvino",
                    return_value=reader,
                ),
                patch(
                    "product_evidence_guard.engine.parse_pdf_document",
                    return_value=pdf_parse_result,
                ),
                patch(
                    "product_evidence_guard.engine.render_pdf_page",
                    side_effect=self._fake_render(rendered_paths),
                ),
            ):
                summary = analyze_directory(
                    root,
                    output,
                    openvino_vlm_model=Path(tmp) / "fake-model",
                )

        self.assertEqual(summary["candidate_count"], 0)
        self.assertTrue(
            any("pdf_page:1:RuntimeError" in item["error"] for item in summary["errors"])
        )
        self.assertEqual(len(rendered_paths), 1)
        self.assertFalse(rendered_paths[0].exists())
        self.assertFalse(rendered_paths[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
