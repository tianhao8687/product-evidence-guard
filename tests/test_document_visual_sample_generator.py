from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.parsers import (
    parse_docx_document,
    parse_pdf_document,
    parse_xlsx_document,
    sha256_file,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_GENERATOR_PATH = (
    _PROJECT_ROOT / "scripts" / "generate-document-visual-samples.py"
)


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "document_visual_sample_generator",
        _GENERATOR_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load generator: {_GENERATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DocumentVisualSampleGeneratorTests(unittest.TestCase):
    def test_consecutive_generations_are_identical_and_parseable(self) -> None:
        generator = _load_generator()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_root = root / "first"
            second_root = root / "second"

            first_manifest = generator.generate(first_root)
            second_manifest = generator.generate(second_root)

            input_names = (
                "controlled-embedded-image.docx",
                "controlled-chart-and-image.xlsx",
                "controlled-mixed-visual.pdf",
            )
            for name in input_names:
                first_path = first_root / "input" / name
                second_path = second_root / "input" / name
                self.assertEqual(
                    _sha256(first_path),
                    _sha256(second_path),
                    name,
                )

            self.assertEqual(
                [item["sha256"] for item in first_manifest["files"]],
                [item["sha256"] for item in second_manifest["files"]],
            )

            docx_path = (
                first_root / "input" / "controlled-embedded-image.docx"
            )
            docx = parse_docx_document(
                docx_path,
                docx_path.name,
                sha256_file(docx_path),
            )
            self.assertEqual(len(docx.visual_assets), 1)

            xlsx_path = (
                first_root / "input" / "controlled-chart-and-image.xlsx"
            )
            xlsx = parse_xlsx_document(
                xlsx_path,
                xlsx_path.name,
                sha256_file(xlsx_path),
            )
            self.assertEqual(len(xlsx.visual_assets), 1)
            self.assertEqual(len(xlsx.structured_visuals), 1)
            self.assertEqual(xlsx.structured_visuals[0].status, "structured")

            pdf_path = (
                first_root / "input" / "controlled-mixed-visual.pdf"
            )
            pdf = parse_pdf_document(
                pdf_path,
                pdf_path.name,
                sha256_file(pdf_path),
            )
            self.assertEqual(pdf.scanned_page_numbers, ())
            self.assertEqual(pdf.mixed_visual_page_numbers, (1,))


if __name__ == "__main__":
    unittest.main()
