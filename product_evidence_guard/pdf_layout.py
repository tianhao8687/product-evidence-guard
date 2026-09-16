"""Geometry-preserving PDF adapter feeding the existing evidence contract.

No OCR, product-specific templates, or text-layer ordering assumptions here.
Unbound table regions are reported once and are not flattened into facts.
"""
import re
from collections import defaultdict

from .field_registry import field_for_label, split_label_unit
from .structured_rows import bind_table, header_field

MAX_PAGE_CHARACTERS = 100_000
MAX_PAGE_EDGES = 12_000


def _lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= 3:
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _text(words: list[dict]) -> str:
    return " ".join(w["text"] for w in words).strip()


def _bbox(words: list[dict]) -> tuple[float, float, float, float]:
    return (min(w["x0"] for w in words), min(w["top"] for w in words),
            max(w["x1"] for w in words), max(w["bottom"] for w in words))


def _inside(word: dict, box) -> bool:
    return box[0] <= (word["x0"] + word["x1"]) / 2 <= box[2] and box[1] <= (word["top"] + word["bottom"]) / 2 <= box[3]


def _locator(page, number, box, **extra) -> dict:
    return {"page": number, "bbox_1000": [round(box[0] / page.width * 1000, 3),
            round(box[1] / page.height * 1000, 3), round(box[2] / page.width * 1000, 3),
            round(box[3] / page.height * 1000, 3)], "position_precision": "exact", **extra}


def _orthogonal_edges(edges):
    """Printing transforms skew hairlines slightly; don't call them vertical.

    Accept at most half a point of deviation, not a page rotation heuristic.
    Preserve original endpoints as provenance via the source page/box.
    """
    horizontal, vertical = [], []
    for original in edges:
        edge = dict(original)
        width, height = edge["x1"] - edge["x0"], edge["bottom"] - edge["top"]
        if width >= 4 and height <= .5:
            y = (edge["top"] + edge["bottom"]) / 2
            edge.update(orientation="h", top=y, bottom=y, height=0)
            horizontal.append(edge)
        elif height >= 4 and width <= .75:
            x = (edge["x0"] + edge["x1"]) / 2
            edge.update(orientation="v", x0=x, x1=x, width=0)
            vertical.append(edge)
    return horizontal, vertical


def _chunks(line):
    result = [[]]
    for word in line:
        if result[-1] and word["x0"] - result[-1][-1]["x1"] > 14:
            result.append([])
        result[-1].append(word)
    return result


def _aligned_pairs(lines):
    """Find repeated two-column key/value regions, even beside body text.

    Require three adjacent aligned rows and two numeric value anchors. Only
    the final pair of columns is eligible: never assign the first value of a
    multi-product matrix to the whole document.
    """
    buckets = defaultdict(list)
    for index, line in enumerate(lines):
        chunks = _chunks(line)
        if len(chunks) < 2:
            continue
        left, right = chunks[-2:]
        label, value = _text(left), _text(right)
        if not field_for_label(split_label_unit(label)[0]) or len(label.split()) > 8 or len(value) > 512:
            continue
        key = (round(left[0]["x0"] / 3), round(right[0]["x0"] / 3))
        buckets[key].append((index, left, right))
    result = {}
    for entries in buckets.values():
        runs = [[]]
        for item in entries:
            if runs[-1] and item[1][0]["top"] - runs[-1][-1][1][0]["top"] > 30:
                runs.append([])
            runs[-1].append(item)
        for run in runs:
            numeric = sum(bool(re.match(r"^[+\-~≈<>≤≥±]?\s*\d", _text(right))) for _, _, right in run)
            if len(run) >= 3 and numeric >= 2:
                result.update({i: (left, right) for i, left, right in run})
    return result


def read_layout_page(page, number: int) -> tuple[list[dict], list[dict]]:
    """Return text/locator/provenance records and grouped reading issues."""
    if len(page.chars) > MAX_PAGE_CHARACTERS:
        return [], [{"code": "page_text_limit", "locator": {"page": number},
                     "message": "本页文字结构超过安全上限，其余页面继续读取；请拆分或简化本页。"}]
    clean = page.dedupe_chars()
    words = clean.extract_words(x_tolerance=2, y_tolerance=3)
    records: list[dict] = []
    issues: list[dict] = []
    consumed: list[tuple] = []
    if len(page.edges) > MAX_PAGE_EDGES:
        tables = []
        issues.append({"code": "page_geometry_limit", "locator": {"page": number},
                       "message": "本页图形过于复杂，仅提取明确文字；图表尚未完整核对，其余页不受影响。"})
    else:
        horizontal, vertical = _orthogonal_edges(clean.edges)
        tables = clean.find_tables({"horizontal_strategy": "explicit", "vertical_strategy": "explicit",
            "explicit_horizontal_lines": horizontal, "explicit_vertical_lines": vertical}) if len(horizontal) >= 2 and len(vertical) >= 2 else []
    for index, table in enumerate(tables):
        raw = table.extract(x_tolerance=2, y_tolerance=3)
        # Reject diagrams with no meaningful parameter labels.
        if len(raw) < 2 or not any(field_for_label(split_label_unit(str(row[0] or ""))[0]) for row in raw if row):
            continue
        table_id = f"pdf:{number}:{index}"
        rows = [(i, [(box, value) for box, value in zip(table.rows[i].cells, row)]) for i, row in enumerate(raw)]
        # Some PDFs draw the MODEL row just outside the detected grid. Bind
        # only the explicit label and values inside exact existing columns.
        outside_header = None
        if raw and len(raw[0]) >= 3 and header_field(raw[0][0]) not in {"model", "sku", "variant"}:
            above = [w for w in words if table.bbox[1] - 24 <= w["top"] and w["bottom"] <= table.bbox[1]]
            for line in _lines(above):
                if header_field(line[0]["text"]) not in {"model", "sku"}:
                    continue
                values = [line[0]["text"]]
                boxes = [table.rows[0].cells[0]]
                for box in table.rows[0].cells[1:]:
                    matched = [w for w in line[1:] if box and box[0] <= (w["x0"] + w["x1"]) / 2 <= box[2]]
                    values.append(_text(matched))
                    boxes.append(_bbox(matched) if matched else None)
                if all(values):
                    rows.insert(0, (-1, list(zip(boxes, values))))
                    outside_header = _bbox(line)
                    break
        # Expand only actual merged geometry, never carry a previous value.
        boundaries = sorted({box[0] for box in table.cells} | {table.bbox[2]})
        centers = [(left + right) / 2 for left, right in zip(boundaries, boundaries[1:])]
        row_boundaries = sorted({box[1] for box in table.cells} | {table.bbox[3]})
        row_bottoms = dict(zip(row_boundaries, row_boundaries[1:]))
        expanded = []
        for rn, cells in rows:
            if rn < 0:
                expanded.append((rn, cells))
                continue
            # A row bbox includes tall merged side labels. Its midpoint can
            # land several rows lower; use this actual horizontal grid band.
            top = table.rows[rn].bbox[1]
            y = (top + row_bottoms[top]) / 2
            resolved = []
            for column, (box, value) in enumerate(cells):
                if box is None:
                    covering = [b for b in table.cells if b[0] <= centers[column] <= b[2] and b[1] <= y <= b[3]]
                    if len(covering) == 1:
                        box = covering[0]
                        value = _text([w for w in words if _inside(w, box)])
                resolved.append((box, value))
            expanded.append((rn, resolved))
        structured = bind_table(expanded, table_id=table_id)
        # Vertical key/value tables use the identical explicit label contract.
        if structured is None and all(len(row) == 2 for _, row in expanded):
            for rn, cells in expanded:
                label, value = (str(v or "").strip() for _, v in cells)
                if field_for_label(split_label_unit(label)[0]) and value:
                    box = cells[1][0] or table.bbox
                    records.append({"text": f"{label}: {value}", "locator": _locator(page, number, box, table=index, row=rn),
                                    "provenance": {"table_id": table_id}})
            consumed.append(table.bbox)
            continue
        consumed.append(table.bbox)
        if outside_header:
            consumed.append(outside_header)
        bound_rows = set()
        for cell in structured or []:
            if not cell.location or "\ufffd" in cell.value or "\ufffd" in cell.header:
                continue
            bound_rows.add(cell.row_number)
            # Side labels are only applicable inside their drawn vertical span.
            parents = list(cell.parent_labels)
            y = (cell.location[1] + cell.location[3]) / 2
            left_words = [w for w in words if w["x1"] <= table.bbox[0] and w["x1"] >= table.bbox[0] - 70]
            for line in _lines(left_words):
                center = sum((w["x0"] + w["x1"]) / 2 for w in line) / len(line)
                edges = [e["top"] for e in horizontal if e["x0"] <= center <= e["x1"] and e["x1"] >= table.bbox[0]]
                upper = max((v for v in edges if v <= line[0]["top"]), default=-1)
                lower = min((v for v in edges if v >= line[0]["bottom"]), default=-1)
                if upper >= 0 and upper <= y <= lower and len(_text(line)) <= 64:
                    parents.append(_text(line))
            nested = re.fullmatch(r"\s*([^:：\n]{1,64})\s*[:：]\s*(.+)", cell.value, re.S)
            nested_spec = field_for_label(nested[1]) if nested else None
            if nested_spec and nested_spec.name != cell.field:
                parents.append(cell.header)
                text = cell.value
            else:
                text = f"{cell.header}: {cell.value}"
            records.append({"text": text,
                            "locator": _locator(page, number, cell.location, table=index, row=cell.row_number),
                            "provenance": {"table_id": table_id, "parent_labels": list(dict.fromkeys(parents)),
                                "structured_row": {"row_id": cell.row_id, "identity": cell.identity,
                                                   "field": nested_spec.name if nested_spec else cell.field}}})
        missing = [rn for rn, cells in expanded if rn >= 0 and rn not in bound_rows and
                   any(re.search(r"\d", str(value or "")) for _, value in cells[1:]) and
                   header_field(cells[0][1]) not in {"model", "sku", "variant"}]
        if missing or not structured:
            issues.append({"code": "table_binding_unclear", "locator": _locator(page, number, table.bbox, table=index),
                           "message": "这张表部分行列或适用条件未能可靠对应，请核对原表或提供表格文件。", "unbound_rows": missing})
    # Preserve line geometry outside grids. Widely separated multi-value rows
    # are not flattened (that was the source of cross-product false facts).
    remainder = [w for w in words if not any(_inside(w, box) for box in consumed)]
    uncertain = []
    lines = _lines(remainder)
    aligned = _aligned_pairs(lines)
    for line_number, line in enumerate(lines):
        if line_number in aligned:
            left, right = aligned[line_number]
            records.append({"text": f"{_text(left)}: {_text(right)}", "locator": _locator(page, number, _bbox(left + right), text_block=line_number),
                            "provenance": {"layout_binding": "repeated_aligned_pair"}})
            line = [word for word in line if word not in left and word not in right]
            if not line:
                continue
        chunks = _chunks(line)
        parts = [_text(chunk) for chunk in chunks]
        if len(parts) >= 3 and sum(bool(re.search(r"\d", p)) for p in parts) >= 2:
            uncertain.append(line_number)
            continue
        text = _text(line)
        if len(parts) == 2 and field_for_label(split_label_unit(parts[0])[0]) and re.match(r"^[+\-~≈<>≤≥±]?\s*\d", parts[1]):
            text = f"{parts[0]}: {parts[1]}"
        elif len(parts) == 2 and re.search(r"\d", parts[1]) and not re.search(r"[:：=]", text):
            uncertain.append(line_number)
            continue
        if len(page.edges) >= 24 and re.match(r"^[A-Za-z]\s*[:=]", text):
            uncertain.append(line_number)
            continue
        if text:
            records.append({"text": text, "locator": _locator(page, number, _bbox(line), text_block=line_number), "provenance": {}})
    if uncertain:
        issues.append({"code": "unruled_table_unclear", "locator": {"page": number},
                       "message": "本页有未能可靠绑定的多列表格，请补充 CSV/XLSX 或核对原件。", "unbound_rows": uncertain})
    return records, issues
