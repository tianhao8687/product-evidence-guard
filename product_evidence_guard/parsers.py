from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
from math import ceil
from pathlib import Path
from typing import Any, Iterable

from .models import SourceBlock


TEXT_EXTENSIONS = {".txt", ".md"}
DOCUMENT_EXTENSIONS = {".docx", ".xlsx", ".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | {".csv", ".json"} | DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS
MAX_PDF_PAGES = 100
PDF_RENDER_SCALE = 2.0
MAX_PDF_RENDER_PIXELS = 25_000_000


@dataclass(frozen=True, slots=True)
class PdfParseResult:
    blocks: tuple[SourceBlock, ...]
    scanned_page_numbers: tuple[int, ...]
    page_count: int


@dataclass(frozen=True, slots=True)
class RenderedPdfPage:
    page_number: int
    image_path: Path
    pixel_width: int
    pixel_height: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _block_id(relative_path: str, file_hash: str, locator: dict[str, Any], text: str) -> str:
    payload = json.dumps(
        {"path": relative_path, "hash": file_hash, "locator": locator, "text": text},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _make_block(
    *,
    relative_path: str,
    source_kind: str,
    file_hash: str,
    locator: dict[str, Any],
    text: str,
    confidence: float = 1.0,
    method: str = "deterministic_parser",
    confidence_source: str = "deterministic",
) -> SourceBlock:
    return SourceBlock(
        block_id=_block_id(relative_path, file_hash, locator, text),
        source_file=relative_path,
        source_kind=source_kind,
        file_hash=file_hash,
        locator=locator,
        text=text,
        recognition_confidence=max(0.0, min(1.0, confidence)),
        extraction_method=method,
        recognition_confidence_source=confidence_source,
        provenance={"recognition_confidence_source": confidence_source},
    )


def parse_text(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    text = path.read_text(encoding="utf-8-sig")
    blocks: list[SourceBlock] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if value:
            blocks.append(
                _make_block(
                    relative_path=relative_path,
                    source_kind="text",
                    file_hash=file_hash,
                    locator={"line": line_number},
                    text=value,
                )
            )
    return blocks


def parse_csv(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        for row_number, row in enumerate(reader, start=1):
            nonempty = [(index + 1, cell.strip()) for index, cell in enumerate(row) if cell.strip()]
            if not nonempty:
                continue
            if len(nonempty) >= 2:
                combined = " | ".join(cell for _, cell in nonempty)
                blocks.append(
                    _make_block(
                        relative_path=relative_path,
                        source_kind="csv_row",
                        file_hash=file_hash,
                        locator={"row": row_number, "columns": [index for index, _ in nonempty]},
                        text=combined,
                    )
                )
            else:
                column, value = nonempty[0]
                blocks.append(
                    _make_block(
                        relative_path=relative_path,
                        source_kind="csv_cell",
                        file_hash=file_hash,
                        locator={"row": row_number, "column": column},
                        text=value,
                    )
                )
    return blocks


def _flatten_json(value: Any, path: str = "$") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _flatten_json(item, f"{path}/{escaped}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten_json(item, f"{path}/{index}")
    elif value is not None:
        yield path, value


def parse_json(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    blocks: list[SourceBlock] = []
    for json_path, value in _flatten_json(data):
        label = json_path.rsplit("/", 1)[-1].replace("~1", "/").replace("~0", "~")
        text = f"{label}: {value}"
        blocks.append(
            _make_block(
                relative_path=relative_path,
                source_kind="json_value",
                file_hash=file_hash,
                locator={"json_path": json_path},
                text=text,
            )
        )
    return blocks


def parse_ocr_sidecar(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    blocks_data = data.get("blocks")
    if not isinstance(blocks_data, list):
        raise ValueError(f"OCR sidecar blocks must be an array: {relative_path}")
    image_name = str(data.get("image") or relative_path.removesuffix(".ocr.json"))
    blocks: list[SourceBlock] = []
    for index, item in enumerate(blocks_data):
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            continue
        locator = {"image": image_name, "block": index}
        if isinstance(item.get("bbox"), list):
            locator["bbox"] = item["bbox"]
        blocks.append(
            _make_block(
                relative_path=relative_path,
                source_kind="image_ocr",
                file_hash=file_hash,
                locator=locator,
                text=str(item["text"]).strip(),
                confidence=float(item.get("confidence", 0.75)),
                method=str(item.get("method", "ocr_sidecar")),
                confidence_source=str(
                    item.get("confidence_source", "ocr_sidecar")
                ),
            )
        )
    return blocks


def parse_docx(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    try:
        from docx import Document
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("读取 DOCX 需要安装 python-docx：pip install -e '.[documents]'") from exc

    document = Document(path)
    blocks: list[SourceBlock] = []
    paragraph_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            text = Paragraph(child, document).text.strip()
            if text:
                blocks.append(
                    _make_block(
                        relative_path=relative_path,
                        source_kind="docx_paragraph",
                        file_hash=file_hash,
                        locator={"paragraph": paragraph_index},
                        text=text,
                    )
                )
            paragraph_index += 1
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row_index, row in enumerate(table.rows):
                values = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if values:
                    blocks.append(
                        _make_block(
                            relative_path=relative_path,
                            source_kind="docx_table_row",
                            file_hash=file_hash,
                            locator={"table": table_index, "row": row_index},
                            text=" | ".join(values),
                        )
                    )
            table_index += 1
    return blocks


def parse_xlsx(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("读取 XLSX 需要安装 openpyxl：pip install -e '.[documents]'") from exc

    workbook = load_workbook(path, read_only=True, data_only=False)
    blocks: list[SourceBlock] = []
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                cells = [(cell.coordinate, cell.value) for cell in row if cell.value not in (None, "")]
                if not cells:
                    continue
                text = " | ".join(str(value) for _, value in cells)
                blocks.append(
                    _make_block(
                        relative_path=relative_path,
                        source_kind="xlsx_row",
                        file_hash=file_hash,
                        locator={"sheet": sheet.title, "cells": [coordinate for coordinate, _ in cells]},
                        text=text,
                    )
                )
    finally:
        workbook.close()
    return blocks


def parse_pdf_document(path: Path, relative_path: str, file_hash: str) -> PdfParseResult:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("读取 PDF 需要安装 pypdf：pip install -e '.[documents]'") from exc

    reader = PdfReader(path)
    page_count = len(reader.pages)
    if page_count > MAX_PDF_PAGES:
        raise ValueError(f"PDF pages exceed safety limit: {page_count} > {MAX_PDF_PAGES}")
    blocks: list[SourceBlock] = []
    scanned_page_numbers: list[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if not text:
            scanned_page_numbers.append(page_number)
            continue
        for block_index, paragraph in enumerate(part.strip() for part in text.split("\n") if part.strip()):
            blocks.append(
                _make_block(
                    relative_path=relative_path,
                    source_kind="pdf_text",
                    file_hash=file_hash,
                    locator={"page": page_number, "text_block": block_index},
                    text=paragraph,
                )
            )
    return PdfParseResult(
        blocks=tuple(blocks),
        scanned_page_numbers=tuple(scanned_page_numbers),
        page_count=page_count,
    )


def parse_pdf(path: Path, relative_path: str, file_hash: str) -> list[SourceBlock]:
    """Parse text-layer PDF pages while preserving the original public contract."""

    return list(parse_pdf_document(path, relative_path, file_hash).blocks)


def render_pdf_page(
    path: Path,
    page_number: int,
    output_dir: Path,
    *,
    scale: float = PDF_RENDER_SCALE,
) -> RenderedPdfPage:
    """Render one 1-based PDF page to a temporary PNG with bounded pixels.

    The caller owns ``output_dir`` and is responsible for removing it. The
    engine uses ``TemporaryDirectory`` so rendered customer pages are removed
    on success and on every exception path.
    """

    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError(
            "渲染扫描 PDF 需要安装 pypdfium2：pip install -e '.[documents]'"
        ) from exc

    if page_number < 1:
        raise ValueError("PDF page_number must be 1-based and positive.")
    if not 0.25 <= scale <= 4.0:
        raise ValueError("PDF render scale must be between 0.25 and 4.0.")

    output_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(path))
    try:
        if page_number > len(document):
            raise ValueError(f"PDF page {page_number} does not exist.")
        page = document[page_number - 1]
        try:
            width_points, height_points = page.get_size()
            expected_width = max(1, ceil(float(width_points) * scale))
            expected_height = max(1, ceil(float(height_points) * scale))
            if expected_width * expected_height > MAX_PDF_RENDER_PIXELS:
                raise ValueError(
                    "Rendered PDF page would exceed the pixel safety limit: "
                    f"{expected_width}x{expected_height}"
                )
            bitmap = page.render(scale=scale)
            try:
                image = bitmap.to_pil()
                try:
                    pixel_width, pixel_height = image.size
                    image_path = output_dir / f"page-{page_number:04d}.png"
                    image.save(image_path, format="PNG")
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()

    return RenderedPdfPage(
        page_number=page_number,
        image_path=image_path,
        pixel_width=int(pixel_width),
        pixel_height=int(pixel_height),
    )


def parse_file(path: Path, root: Path) -> list[SourceBlock]:
    relative_path = path.relative_to(root).as_posix()
    file_hash = sha256_file(path)
    lowered = path.name.casefold()
    if lowered.endswith(".ocr.json"):
        return parse_ocr_sidecar(path, relative_path, file_hash)
    suffix = path.suffix.casefold()
    if suffix in TEXT_EXTENSIONS:
        return parse_text(path, relative_path, file_hash)
    if suffix == ".csv":
        return parse_csv(path, relative_path, file_hash)
    if suffix == ".json":
        return parse_json(path, relative_path, file_hash)
    if suffix == ".docx":
        return parse_docx(path, relative_path, file_hash)
    if suffix == ".xlsx":
        return parse_xlsx(path, relative_path, file_hash)
    if suffix == ".pdf":
        return parse_pdf(path, relative_path, file_hash)
    if suffix in IMAGE_EXTENSIONS:
        return []
    raise ValueError(f"Unsupported file type: {relative_path}")


def discover_files(root: Path) -> list[Path]:
    canonical_root = root.expanduser().resolve(strict=True)
    files: set[Path] = set()
    for path in canonical_root.rglob("*"):
        lexical_relative = path.relative_to(canonical_root)
        if any(part.startswith(".") for part in lexical_relative.parts):
            continue
        try:
            resolved = path.resolve(strict=True)
            resolved_relative = resolved.relative_to(canonical_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(
                f"Input path escapes the selected directory or is unsafe: {lexical_relative.as_posix()}"
            ) from exc
        if not resolved.is_file():
            continue
        if any(part.startswith(".") for part in resolved_relative.parts):
            continue
        name = resolved.name.casefold()
        if name.endswith(".ocr.json") or resolved.suffix.casefold() in SUPPORTED_EXTENSIONS:
            files.add(resolved)
    return sorted(files)
