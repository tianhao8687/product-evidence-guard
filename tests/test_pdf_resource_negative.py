from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.openvino_adapter import OpenVinoDeviceSelection
from product_evidence_guard.parsers import (
    MAX_PDF_PAGES,
    PdfParseResult,
    render_pdf_page,
    sha256_file,
)


_FAKE_CPU_SELECTION = OpenVinoDeviceSelection(
    requested="CPU",
    actual="CPU",
    available_devices=("CPU",),
    full_device_names={"CPU": "Negative-test CPU"},
    policy="explicit",
)


def _write_blank_pdf(
    path: Path,
    *,
    pages: int = 1,
    password: str | None = None,
) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    if password is not None:
        writer.encrypt(password)
    with path.open("wb") as handle:
        writer.write(handle)


def _snapshot(path: Path) -> tuple[int, str]:
    payload = path.read_bytes()
    return len(payload), hashlib.sha256(payload).hexdigest()


class GeneratedPdfNegativeTests(unittest.TestCase):
    def _analyze_one(self, name: str, writer) -> tuple[Path, dict[str, object], tuple[int, str]]:
        temporary = tempfile.TemporaryDirectory(prefix="peg-pdf-negative-")
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        root = base / "input"
        output = base / "output"
        root.mkdir()
        source = root / name
        writer(source)
        before = _snapshot(source)
        summary = analyze_directory(root, output)
        self.assertTrue(source.is_file())
        self.assertEqual(_snapshot(source), before)
        return source, summary, before

    def test_blank_pdf_is_reported_as_scanned_without_model_and_source_survives(self) -> None:
        source, summary, _ = self._analyze_one(
            "blank.pdf",
            _write_blank_pdf,
        )

        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["scanned_pdf"]["pages_detected"], 1)
        self.assertEqual(summary["scanned_pdf"]["pages_processed_with_qwen_vl"], 0)
        self.assertEqual(summary["scanned_pdf"]["pages_skipped"], 1)
        self.assertIn(
            {
                "file": source.name,
                "reason": "scanned_pdf_pages_require_local_image_model",
                "pages": "1",
            },
            summary["skipped_files"],
        )

    def test_damaged_pdf_fails_per_file_without_deleting_source(self) -> None:
        def write_damaged(path: Path) -> None:
            path.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\n")

        source, summary, _ = self._analyze_one("damaged.pdf", write_damaged)

        self.assertEqual(summary["candidate_count"], 0)
        error = next(item for item in summary["errors"] if item["file"] == source.name)
        self.assertRegex(error["error"], r"Pdf(?:Read|Stream)Error")

    def test_encrypted_pdf_fails_per_file_without_deleting_source(self) -> None:
        source, summary, _ = self._analyze_one(
            "encrypted.pdf",
            lambda path: _write_blank_pdf(path, password="local-test-password"),
        )

        self.assertEqual(summary["candidate_count"], 0)
        error = next(item for item in summary["errors"] if item["file"] == source.name)
        self.assertIn("FileNotDecryptedError", error["error"])

    def test_pdf_page_limit_fails_closed_without_deleting_source(self) -> None:
        source, summary, _ = self._analyze_one(
            "too-many-pages.pdf",
            lambda path: _write_blank_pdf(path, pages=MAX_PDF_PAGES + 1),
        )

        self.assertEqual(summary["candidate_count"], 0)
        error = next(item for item in summary["errors"] if item["file"] == source.name)
        self.assertIn("PDF pages exceed safety limit", error["error"])
        self.assertIn(f"{MAX_PDF_PAGES + 1} > {MAX_PDF_PAGES}", error["error"])


class ResourceBoundaryNegativeTests(unittest.TestCase):
    def test_file_size_limit_skips_small_fixture_via_injected_boundary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peg-file-limit-") as temporary:
            base = Path(temporary)
            root = base / "input"
            output = base / "output"
            root.mkdir()
            source = root / "oversized.pdf"
            _write_blank_pdf(source)
            before = _snapshot(source)

            with patch("product_evidence_guard.engine.MAX_FILE_BYTES", 8):
                summary = analyze_directory(root, output)

            self.assertIn(
                {
                    "file": source.name,
                    "reason": "file_size_limit_exceeded:8",
                },
                summary["skipped_files"],
            )
            self.assertEqual(summary["candidate_count"], 0)
            self.assertEqual(_snapshot(source), before)

    def test_file_count_limit_rejects_task_before_touching_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="peg-count-limit-") as temporary:
            base = Path(temporary)
            root = base / "input"
            output = base / "output"
            root.mkdir()
            sources = []
            for index in range(3):
                source = root / f"source-{index}.txt"
                source.write_text(f"净重：{index + 1}g\n", encoding="utf-8")
                sources.append((source, _snapshot(source)))

            with (
                patch("product_evidence_guard.engine.MAX_FILES_PER_TASK", 2),
                self.assertRaisesRegex(ValueError, "Too many input files: 3 .*limit: 2"),
            ):
                analyze_directory(root, output)

            for source, before in sources:
                self.assertTrue(source.is_file())
                self.assertEqual(_snapshot(source), before)

    def test_pdf_pixel_limit_closes_resources_and_preserves_source(self) -> None:
        closed: list[str] = []
        render_called = False

        class FakePage:
            def get_size(self) -> tuple[float, float]:
                return (5000.0, 5000.0)

            def render(self, *, scale: float):
                nonlocal render_called
                render_called = True
                raise AssertionError(f"render must not run at scale {scale}")

            def close(self) -> None:
                closed.append("page")

        class FakeDocument:
            def __init__(self, path: str) -> None:
                self.path = path

            def __len__(self) -> int:
                return 1

            def __getitem__(self, index: int) -> FakePage:
                if index != 0:
                    raise IndexError(index)
                return FakePage()

            def close(self) -> None:
                closed.append("document")

        fake_pdfium = types.SimpleNamespace(PdfDocument=FakeDocument)
        with tempfile.TemporaryDirectory(prefix="peg-pixel-limit-") as temporary:
            base = Path(temporary)
            source = base / "huge-canvas.pdf"
            output = base / "rendered"
            source.write_bytes(b"small controlled placeholder")
            before = _snapshot(source)

            with (
                patch.dict(sys.modules, {"pypdfium2": fake_pdfium}),
                self.assertRaisesRegex(ValueError, "pixel safety limit"),
            ):
                render_pdf_page(source, 1, output)

            self.assertFalse(render_called)
            self.assertCountEqual(closed, ["page", "document"])
            self.assertEqual(list(output.iterdir()), [])
            self.assertEqual(_snapshot(source), before)


class TemporaryRenderFailureTests(unittest.TestCase):
    def test_partial_render_is_removed_and_source_survives_renderer_failure(self) -> None:
        temporary_paths: list[Path] = []

        def fail_after_partial_write(
            path: Path,
            page_number: int,
            output_dir: Path,
        ):
            partial = output_dir / f"page-{page_number:04d}.png"
            partial.write_bytes(b"partial rendered customer page")
            temporary_paths.append(partial)
            raise RuntimeError("controlled renderer failure")

        class NeverCalledReader:
            def analyze_image(self, *args, **kwargs):
                raise AssertionError("visual reader must not run after render failure")

        with tempfile.TemporaryDirectory(prefix="peg-render-failure-") as temporary:
            base = Path(temporary)
            root = base / "input"
            output = base / "output"
            root.mkdir()
            source = root / "scan.pdf"
            _write_blank_pdf(source)
            before = _snapshot(source)

            with (
                patch(
                    "product_evidence_guard.engine.parse_pdf_document",
                    return_value=PdfParseResult(
                        blocks=(),
                        scanned_page_numbers=(1,),
                        page_count=1,
                        visual_page_reasons=(
                            {
                                "page": 1,
                                "page_mode": "scanned",
                                "reason": "no_text_layer",
                            },
                        ),
                    ),
                ),
                patch(
                    "product_evidence_guard.engine.QwenVlReader.from_openvino",
                    return_value=NeverCalledReader(),
                ),
                patch(
                    "product_evidence_guard.engine.render_pdf_page",
                    side_effect=fail_after_partial_write,
                ),
            ):
                summary = analyze_directory(
                    root,
                    output,
                    openvino_vlm_model=base / "fake-model",
                    device_selection=_FAKE_CPU_SELECTION,
                )

            self.assertTrue(
                any(
                    item["file"] == source.name
                    and item["error"] == "pdf_page:1:RuntimeError"
                    for item in summary["errors"]
                )
            )
            self.assertEqual(_snapshot(source), before)

        self.assertEqual(len(temporary_paths), 1)
        self.assertFalse(temporary_paths[0].exists())
        self.assertFalse(temporary_paths[0].parent.exists())


if __name__ == "__main__":
    unittest.main()
