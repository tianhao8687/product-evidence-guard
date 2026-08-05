from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET


MAX_EMBEDDED_VISUALS_PER_DOCUMENT = 24
MAX_EMBEDDED_VISUAL_BYTES = 20 * 1024 * 1024
MAX_TOTAL_EMBEDDED_VISUAL_BYTES = 50 * 1024 * 1024
MAX_NATIVE_CHARTS_PER_DOCUMENT = 24
MAX_CHART_SERIES = 16
MAX_CHART_POINTS_PER_SERIES = 256

SUPPORTED_IMAGE_EXTENSIONS = {
    ".bmp",
    ".jpeg",
    ".jpg",
    ".png",
    ".webp",
}

_CONTENT_TYPE_EXTENSIONS = {
    "image/bmp": ".bmp",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}

_CHART_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
}


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:16]}"


def _safe_extension(
    *,
    filename: str | None,
    content_type: str | None,
    format_hint: str | None = None,
) -> str | None:
    candidates: list[str] = []
    if filename:
        candidates.append(Path(filename).suffix.casefold())
    if format_hint:
        candidates.append(f".{format_hint.lstrip('.').casefold()}")
    if content_type:
        mapped = _CONTENT_TYPE_EXTENSIONS.get(content_type.casefold())
        if mapped:
            candidates.append(mapped)
    for candidate in candidates:
        if candidate == ".jpe":
            candidate = ".jpg"
        if candidate in SUPPORTED_IMAGE_EXTENSIONS:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class EmbeddedVisualAsset:
    asset_id: str
    source_file: str
    source_kind: str
    file_hash: str
    content_hash: str
    content_type: str | None
    extension: str
    embedded_name: str
    locator: dict[str, Any]
    alt_text: str | None
    data: bytes

    def to_record(self, *, status: str = "detected") -> dict[str, Any]:
        return {
            "schema_version": 1,
            "asset_type": "embedded_image",
            "asset_id": self.asset_id,
            "source_file": self.source_file,
            "source_kind": self.source_kind,
            "file_hash": self.file_hash,
            "content_hash": self.content_hash,
            "content_type": self.content_type,
            "extension": self.extension,
            "embedded_name": self.embedded_name,
            "byte_count": len(self.data),
            "locator": dict(self.locator),
            "alt_text": self.alt_text,
            "status": status,
        }


@dataclass(frozen=True, slots=True)
class StructuredDocumentVisual:
    visual_id: str
    source_file: str
    source_kind: str
    file_hash: str
    locator: dict[str, Any]
    title: str | None
    structured_data: dict[str, Any]
    status: str = "structured"

    def to_record(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "schema_version": 1,
                "asset_type": "native_chart",
            }
        )
        return payload


def _issue(
    *,
    source_file: str,
    source_kind: str,
    locator: dict[str, Any],
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "asset_type": "document_visual_issue",
        "source_file": source_file,
        "source_kind": source_kind,
        "locator": dict(locator),
        "code": code,
        "message": message,
        "status": "skipped",
    }


def _docx_alt_text(element: Any) -> str | None:
    values: list[str] = []
    try:
        doc_properties = element.xpath(".//wp:docPr")
    except (AttributeError, KeyError, TypeError, ValueError):
        doc_properties = []
    for item in doc_properties:
        for attribute in ("descr", "title", "name"):
            value = str(item.get(attribute) or "").strip()
            if value and value not in values:
                values.append(value)
    return " | ".join(values)[:500] or None


def _docx_visual_elements(element: Any, expression: str) -> list[Any]:
    try:
        return list(element.xpath(expression))
    except (AttributeError, KeyError, TypeError, ValueError):
        return []


def _chart_point_values(parent: ET.Element | None) -> list[Any]:
    if parent is None:
        return []
    points: list[tuple[int, Any]] = []
    for point in parent.findall(".//c:pt", _CHART_NS):
        try:
            index = int(point.attrib.get("idx", len(points)))
        except (TypeError, ValueError):
            index = len(points)
        value_node = point.find("c:v", _CHART_NS)
        if value_node is None:
            value_node = point.find(".//c:v", _CHART_NS)
        if value_node is None or value_node.text is None:
            continue
        raw = value_node.text.strip()
        if not raw:
            continue
        try:
            value: Any = float(raw)
            if value.is_integer():
                value = int(value)
        except ValueError:
            value = raw
        points.append((index, value))
    points.sort(key=lambda item: item[0])
    return [value for _, value in points[:MAX_CHART_POINTS_PER_SERIES]]


def _chart_formula(parent: ET.Element | None) -> str | None:
    if parent is None:
        return None
    formula = parent.find(".//c:f", _CHART_NS)
    if formula is None or formula.text is None:
        return None
    return formula.text.strip() or None


def _parse_chart_xml(
    chart_xml: bytes,
    *,
    source_file: str,
    source_kind: str,
    file_hash: str,
    locator: dict[str, Any],
) -> StructuredDocumentVisual:
    root = ET.fromstring(chart_xml)
    title_values = [
        (item.text or "").strip()
        for item in root.findall(".//c:title//a:t", _CHART_NS)
        if (item.text or "").strip()
    ]
    title = " ".join(title_values)[:500] or None
    chart_type = "unknown"
    plot_area = root.find(".//c:plotArea", _CHART_NS)
    if plot_area is not None:
        for child in plot_area:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name.endswith("Chart"):
                chart_type = local_name.removesuffix("Chart")
                break

    series_rows: list[dict[str, Any]] = []
    for series_index, series in enumerate(
        root.findall(".//c:ser", _CHART_NS)[:MAX_CHART_SERIES],
        start=1,
    ):
        name_values = [
            (item.text or "").strip()
            for item in series.findall(".//c:tx//c:v", _CHART_NS)
            if (item.text or "").strip()
        ]
        if not name_values:
            name_values = [
                (item.text or "").strip()
                for item in series.findall(".//c:tx//a:t", _CHART_NS)
                if (item.text or "").strip()
            ]
        category_parent = series.find("c:cat", _CHART_NS)
        if category_parent is None:
            category_parent = series.find("c:xVal", _CHART_NS)
        value_parent = series.find("c:val", _CHART_NS)
        if value_parent is None:
            value_parent = series.find("c:yVal", _CHART_NS)
        categories = _chart_point_values(category_parent)
        values = _chart_point_values(value_parent)
        point_count = min(
            max(len(categories), len(values)),
            MAX_CHART_POINTS_PER_SERIES,
        )
        points = [
            {
                "category": (
                    categories[index] if index < len(categories) else index + 1
                ),
                "value": values[index] if index < len(values) else None,
            }
            for index in range(point_count)
        ]
        series_rows.append(
            {
                "index": series_index,
                "name": " ".join(name_values)[:500] or f"Series {series_index}",
                "category_formula": _chart_formula(category_parent),
                "value_formula": _chart_formula(value_parent),
                "points": points,
            }
        )

    visual_id = _stable_id(
        "chart",
        {
            "source_file": source_file,
            "file_hash": file_hash,
            "source_kind": source_kind,
            "locator": locator,
            "title": title,
        },
    )
    return StructuredDocumentVisual(
        visual_id=visual_id,
        source_file=source_file,
        source_kind=source_kind,
        file_hash=file_hash,
        locator=dict(locator),
        title=title,
        structured_data={
            "chart_type": chart_type,
            "series": series_rows,
        },
        status=("structured" if any(row["points"] for row in series_rows) else "detected"),
    )


def extract_docx_visuals(
    document: Any,
    *,
    relative_path: str,
    file_hash: str,
) -> tuple[
    list[EmbeddedVisualAsset],
    list[StructuredDocumentVisual],
    list[dict[str, Any]],
]:
    from docx.oxml.ns import qn
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table

    assets: list[EmbeddedVisualAsset] = []
    charts: list[StructuredDocumentVisual] = []
    issues: list[dict[str, Any]] = []
    seen_occurrences: set[tuple[str, str, str]] = set()
    total_bytes = 0

    def inspect_element(element: Any, locator: dict[str, Any]) -> None:
        nonlocal total_bytes
        alt_text = _docx_alt_text(element)
        for visual_index, blip in enumerate(
            _docx_visual_elements(element, ".//a:blip"),
            start=1,
        ):
            relationship_id = (
                blip.get(qn("r:embed"))
                or blip.get(qn("r:link"))
                or ""
            )
            occurrence = (
                "image",
                relationship_id,
                json.dumps(locator, sort_keys=True),
            )
            if not relationship_id or occurrence in seen_occurrences:
                continue
            seen_occurrences.add(occurrence)
            asset_locator = {
                **locator,
                "relationship_id": relationship_id,
                "visual_index": visual_index,
            }
            part = document.part.related_parts.get(relationship_id)
            blob = getattr(part, "blob", None)
            if not isinstance(blob, bytes):
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_embedded_image",
                        locator=asset_locator,
                        code="missing_image_part",
                        message="DOCX 图片关系没有可读取的二进制内容。",
                    )
                )
                continue
            embedded_name = str(getattr(part, "partname", relationship_id))
            content_type = getattr(part, "content_type", None)
            extension = _safe_extension(
                filename=embedded_name,
                content_type=content_type,
            )
            if extension is None:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_embedded_image",
                        locator=asset_locator,
                        code="unsupported_image_format",
                        message=f"不支持的 DOCX 内嵌图片格式：{embedded_name}",
                    )
                )
                continue
            if len(assets) >= MAX_EMBEDDED_VISUALS_PER_DOCUMENT:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_embedded_image",
                        locator=asset_locator,
                        code="embedded_visual_count_limit",
                        message="DOCX 内嵌图片数量超过安全上限。",
                    )
                )
                continue
            if (
                len(blob) > MAX_EMBEDDED_VISUAL_BYTES
                or total_bytes + len(blob) > MAX_TOTAL_EMBEDDED_VISUAL_BYTES
            ):
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_embedded_image",
                        locator=asset_locator,
                        code="embedded_visual_size_limit",
                        message="DOCX 内嵌图片超过单图或总字节安全上限。",
                    )
                )
                continue
            content_hash = hashlib.sha256(blob).hexdigest()
            asset_id = _stable_id(
                "docx-image",
                {
                    "source_file": relative_path,
                    "file_hash": file_hash,
                    "content_hash": content_hash,
                    "locator": asset_locator,
                },
            )
            assets.append(
                EmbeddedVisualAsset(
                    asset_id=asset_id,
                    source_file=relative_path,
                    source_kind="docx_embedded_image",
                    file_hash=file_hash,
                    content_hash=content_hash,
                    content_type=content_type,
                    extension=extension,
                    embedded_name=embedded_name,
                    locator=asset_locator,
                    alt_text=alt_text,
                    data=blob,
                )
            )
            total_bytes += len(blob)

        for chart_index, chart_element in enumerate(
            _docx_visual_elements(element, ".//c:chart"),
            start=1,
        ):
            relationship_id = chart_element.get(qn("r:id")) or ""
            occurrence = (
                "chart",
                relationship_id,
                json.dumps(locator, sort_keys=True),
            )
            if not relationship_id or occurrence in seen_occurrences:
                continue
            seen_occurrences.add(occurrence)
            chart_locator = {
                **locator,
                "relationship_id": relationship_id,
                "chart_index": chart_index,
            }
            if len(charts) >= MAX_NATIVE_CHARTS_PER_DOCUMENT:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_native_chart",
                        locator=chart_locator,
                        code="native_chart_count_limit",
                        message="DOCX 原生图表数量超过安全上限。",
                    )
                )
                continue
            part = document.part.related_parts.get(relationship_id)
            chart_xml = getattr(part, "blob", None)
            if not isinstance(chart_xml, bytes):
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_native_chart",
                        locator=chart_locator,
                        code="missing_chart_part",
                        message="DOCX 图表关系没有可读取的 XML 内容。",
                    )
                )
                continue
            try:
                charts.append(
                    _parse_chart_xml(
                        chart_xml,
                        source_file=relative_path,
                        source_kind="docx_native_chart",
                        file_hash=file_hash,
                        locator=chart_locator,
                    )
                )
            except (ET.ParseError, ValueError, TypeError) as exc:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="docx_native_chart",
                        locator=chart_locator,
                        code="invalid_chart_xml",
                        message=f"DOCX 图表 XML 无法解析：{type(exc).__name__}",
                    )
                )

    paragraph_index = 0
    table_index = 0
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            inspect_element(child, {"paragraph": paragraph_index})
            paragraph_index += 1
        elif isinstance(child, CT_Tbl):
            table = Table(child, document)
            for row_index, row in enumerate(table.rows):
                for column_index, cell in enumerate(row.cells):
                    inspect_element(
                        cell._tc,
                        {
                            "table": table_index,
                            "row": row_index,
                            "column": column_index,
                        },
                    )
            table_index += 1
    return assets, charts, issues


def _chart_title(chart: Any) -> str | None:
    title = getattr(chart, "title", None)
    if isinstance(title, str):
        return title.strip()[:500] or None
    try:
        paragraphs = title.tx.rich.p
    except AttributeError:
        return None
    values: list[str] = []
    for paragraph in paragraphs or []:
        for run in getattr(paragraph, "r", []) or []:
            value = str(getattr(run, "t", "") or "").strip()
            if value:
                values.append(value)
        for field in getattr(paragraph, "fld", []) or []:
            value = str(getattr(field, "t", "") or "").strip()
            if value:
                values.append(value)
    return " ".join(values)[:500] or None


def _reference_formula(container: Any) -> str | None:
    if container is None:
        return None
    for attribute in (
        "numRef",
        "strRef",
        "multiLvlStrRef",
    ):
        reference = getattr(container, attribute, None)
        formula = getattr(reference, "f", None)
        if isinstance(formula, str) and formula.strip():
            return formula.strip()
    return None


def _literal_reference_values(container: Any) -> list[Any]:
    if container is None:
        return []
    for attribute in ("numLit", "strLit"):
        literal = getattr(container, attribute, None)
        points = getattr(literal, "pt", None)
        if not points:
            continue
        values: list[tuple[int, Any]] = []
        for point in points[:MAX_CHART_POINTS_PER_SERIES]:
            raw = getattr(point, "v", None)
            try:
                index = int(getattr(point, "idx", len(values)))
            except (TypeError, ValueError):
                index = len(values)
            values.append((index, raw))
        values.sort(key=lambda item: item[0])
        return [value for _, value in values]
    return []


def _resolve_xlsx_formula(
    workbook: Any,
    formula: str | None,
) -> list[Any]:
    if not formula:
        return []
    from openpyxl.utils.cell import range_to_tuple

    material = formula.strip().lstrip("=")
    if "[" in material or "]" in material or "," in material:
        return []
    try:
        sheet_name, bounds = range_to_tuple(material)
        sheet = workbook[sheet_name]
    except (KeyError, TypeError, ValueError):
        return []
    min_col, min_row, max_col, max_row = bounds
    values: list[Any] = []
    for row in sheet.iter_rows(
        min_row=min_row,
        max_row=max_row,
        min_col=min_col,
        max_col=max_col,
    ):
        for cell in row:
            values.append(cell.value)
            if len(values) >= MAX_CHART_POINTS_PER_SERIES:
                return values
    return values


def _series_title(workbook: Any, series: Any, index: int) -> str:
    label = getattr(series, "tx", None)
    direct = getattr(label, "v", None)
    if direct not in (None, ""):
        return str(direct)[:500]
    formula = _reference_formula(label)
    values = _resolve_xlsx_formula(workbook, formula)
    if values and values[0] not in (None, ""):
        return str(values[0])[:500]
    return f"Series {index}"


def _chart_anchor(chart: Any) -> dict[str, Any]:
    anchor = getattr(chart, "anchor", None)
    if isinstance(anchor, str):
        return {"cell": anchor}
    marker = getattr(anchor, "_from", None)
    if marker is None:
        return {}
    result = {
        "row": int(getattr(marker, "row", 0)) + 1,
        "column": int(getattr(marker, "col", 0)) + 1,
    }
    target = getattr(anchor, "to", None)
    if target is not None:
        result["to_row"] = int(getattr(target, "row", 0)) + 1
        result["to_column"] = int(getattr(target, "col", 0)) + 1
    return result


def _xlsx_chart(
    workbook: Any,
    worksheet: Any,
    chart: Any,
    *,
    chart_index: int,
    relative_path: str,
    file_hash: str,
) -> StructuredDocumentVisual:
    title = _chart_title(chart)
    chart_type = type(chart).__name__
    series_rows: list[dict[str, Any]] = []
    for series_index, series in enumerate(
        list(getattr(chart, "ser", []) or [])[:MAX_CHART_SERIES],
        start=1,
    ):
        category_container = getattr(series, "cat", None)
        if category_container is None:
            category_container = getattr(series, "xVal", None)
        value_container = getattr(series, "val", None)
        if value_container is None:
            value_container = getattr(series, "yVal", None)
        category_formula = _reference_formula(category_container)
        value_formula = _reference_formula(value_container)
        categories = (
            _resolve_xlsx_formula(workbook, category_formula)
            or _literal_reference_values(category_container)
        )
        values = (
            _resolve_xlsx_formula(workbook, value_formula)
            or _literal_reference_values(value_container)
        )
        point_count = min(
            max(len(categories), len(values)),
            MAX_CHART_POINTS_PER_SERIES,
        )
        points = [
            {
                "category": (
                    categories[index] if index < len(categories) else index + 1
                ),
                "value": values[index] if index < len(values) else None,
            }
            for index in range(point_count)
        ]
        series_rows.append(
            {
                "index": series_index,
                "name": _series_title(workbook, series, series_index),
                "category_formula": category_formula,
                "value_formula": value_formula,
                "points": points,
            }
        )
    locator = {
        "sheet": worksheet.title,
        "chart_index": chart_index,
        "anchor": _chart_anchor(chart),
    }
    visual_id = _stable_id(
        "xlsx-chart",
        {
            "source_file": relative_path,
            "file_hash": file_hash,
            "locator": locator,
            "title": title,
        },
    )
    return StructuredDocumentVisual(
        visual_id=visual_id,
        source_file=relative_path,
        source_kind="xlsx_native_chart",
        file_hash=file_hash,
        locator=locator,
        title=title,
        structured_data={
            "chart_type": chart_type,
            "series": series_rows,
        },
        status=("structured" if any(row["points"] for row in series_rows) else "detected"),
    )


def _xlsx_image_anchor(image: Any) -> dict[str, Any]:
    anchor = getattr(image, "anchor", None)
    if isinstance(anchor, str):
        return {"cell": anchor}
    marker = getattr(anchor, "_from", None)
    if marker is None:
        return {}
    return {
        "row": int(getattr(marker, "row", 0)) + 1,
        "column": int(getattr(marker, "col", 0)) + 1,
    }


def extract_xlsx_visuals(
    workbook: Any,
    *,
    relative_path: str,
    file_hash: str,
) -> tuple[
    list[EmbeddedVisualAsset],
    list[StructuredDocumentVisual],
    list[dict[str, Any]],
]:
    assets: list[EmbeddedVisualAsset] = []
    charts: list[StructuredDocumentVisual] = []
    issues: list[dict[str, Any]] = []
    total_bytes = 0

    for worksheet in workbook.worksheets:
        for image_index, image in enumerate(
            list(getattr(worksheet, "_images", []) or []),
            start=1,
        ):
            locator = {
                "sheet": worksheet.title,
                "image_index": image_index,
                "anchor": _xlsx_image_anchor(image),
            }
            if len(assets) >= MAX_EMBEDDED_VISUALS_PER_DOCUMENT:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="xlsx_embedded_image",
                        locator=locator,
                        code="embedded_visual_count_limit",
                        message="XLSX 内嵌图片数量超过安全上限。",
                    )
                )
                continue
            try:
                blob = image._data()
            except Exception as exc:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="xlsx_embedded_image",
                        locator=locator,
                        code="image_read_failed",
                        message=f"XLSX 内嵌图片读取失败：{type(exc).__name__}",
                    )
                )
                continue
            if not isinstance(blob, bytes):
                continue
            embedded_name = str(getattr(image, "path", f"image-{image_index}"))
            extension = _safe_extension(
                filename=embedded_name,
                content_type=None,
                format_hint=getattr(image, "format", None),
            )
            if extension is None:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="xlsx_embedded_image",
                        locator=locator,
                        code="unsupported_image_format",
                        message=f"不支持的 XLSX 内嵌图片格式：{embedded_name}",
                    )
                )
                continue
            if (
                len(blob) > MAX_EMBEDDED_VISUAL_BYTES
                or total_bytes + len(blob) > MAX_TOTAL_EMBEDDED_VISUAL_BYTES
            ):
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="xlsx_embedded_image",
                        locator=locator,
                        code="embedded_visual_size_limit",
                        message="XLSX 内嵌图片超过单图或总字节安全上限。",
                    )
                )
                continue
            content_hash = hashlib.sha256(blob).hexdigest()
            asset_id = _stable_id(
                "xlsx-image",
                {
                    "source_file": relative_path,
                    "file_hash": file_hash,
                    "content_hash": content_hash,
                    "locator": locator,
                },
            )
            assets.append(
                EmbeddedVisualAsset(
                    asset_id=asset_id,
                    source_file=relative_path,
                    source_kind="xlsx_embedded_image",
                    file_hash=file_hash,
                    content_hash=content_hash,
                    content_type=None,
                    extension=extension,
                    embedded_name=embedded_name,
                    locator=locator,
                    alt_text=None,
                    data=blob,
                )
            )
            total_bytes += len(blob)

        for chart_index, chart in enumerate(
            list(getattr(worksheet, "_charts", []) or [])[
                :MAX_NATIVE_CHARTS_PER_DOCUMENT
            ],
            start=1,
        ):
            try:
                charts.append(
                    _xlsx_chart(
                        workbook,
                        worksheet,
                        chart,
                        chart_index=chart_index,
                        relative_path=relative_path,
                        file_hash=file_hash,
                    )
                )
            except Exception as exc:
                issues.append(
                    _issue(
                        source_file=relative_path,
                        source_kind="xlsx_native_chart",
                        locator={
                            "sheet": worksheet.title,
                            "chart_index": chart_index,
                        },
                        code="native_chart_parse_failed",
                        message=f"XLSX 原生图表解析失败：{type(exc).__name__}",
                    )
                )
    return assets, charts, issues
