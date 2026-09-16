"""Geometry-preserving PDF adapter feeding the existing evidence contract.

No OCR, product-specific templates, or text-layer ordering assumptions here.
Unbound table regions are reported once and are not flattened into facts.
"""
import re
from collections import defaultdict

from .field_registry import field_for_label, split_label_unit
from .structured_rows import bind_table, header_field, parameter_header_columns, table_role

MAX_PAGE_CHARACTERS = 100_000
MAX_PAGE_EDGES = 12_000


def _lines(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        # Sub/superscripts overlap the body line but have a different top.
        # Compare with the tallest glyph, not the previous small marker.
        anchor = max(lines[-1], key=lambda w: w["bottom"] - w["top"]) if lines else None
        overlap = min(word["bottom"], anchor["bottom"]) - max(word["top"], anchor["top"]) if anchor else 0
        height = min(word["bottom"] - word["top"], anchor["bottom"] - anchor["top"]) if anchor else 1
        if anchor and (abs(word["top"] - anchor["top"]) <= 3 or overlap >= .6 * height):
            lines[-1].append(word)
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def _text(words: list[dict]) -> str:
    result = ""
    previous = None
    for word in words:
        # Rejoin adjoining font runs (formulas, raised markers, punctuation),
        # but retain spaces between ordinary words and across wrapped lines.
        joined = (previous and abs(word["top"] - previous["top"]) < 8
                  and -.5 <= word["x0"] - previous["x1"] < .6)
        result += ("" if not result or joined else " ") + word["text"]
        previous = word
    return result.strip()


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
        # Rectangles used for white cell backgrounds/clipping are not printed
        # borders. Counting all their edges splits one real table into dozens
        # of fake two-column fragments. Thin painted bars are genuine rules.
        if original.get("object_type") == "rect_edge" and not original.get("stroke"):
            points = original.get("pts", [])
            color = original.get("non_stroking_color")
            if color in (1, (1, 1, 1), (0, 0, 0, 0)):
                continue
            if points and min(max(p[0] for p in points) - min(p[0] for p in points),
                              max(p[1] for p in points) - min(p[1] for p in points)) > 1.5:
                continue
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


def _label_text(words):
    return re.sub(r"^\s*[-*•●▪◦–]\s*", "", _text(words)).rstrip(":：")


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
        label, value = _label_text(left), _text(right)
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


def _wrapped_pairs(lines):
    """Extend proven key/value columns across indented continuation lines.

    A colon is an explicit boundary even when the label nearly touches its
    value. Otherwise require the existing repeated-column evidence. Stop at
    another label, a paragraph gap, or a different indent; never forward-fill
    a label into a neighboring column or the next section.
    """
    pairs = _aligned_pairs(lines)
    for index, line in enumerate(lines):
        chunks = _chunks(line)
        for chunk_index in reversed(range(len(chunks))):
            chunk = chunks[chunk_index]
            for split, word in enumerate(chunk):
                if not word["text"].endswith((":", "：")):
                    continue
                left = chunk[:split + 1]
                label = _label_text(left)
                # Inline values stop at their own text region. Taking every
                # word to the right crosses the gutter into the next column.
                right = chunk[split + 1:]
                if not right and chunk_index + 1 < len(chunks):
                    right = chunks[chunk_index + 1]
                    # A neighboring prose/list column is not the missing
                    # value of a standalone section heading ending in ':'.
                    if (index not in pairs and
                            (re.match(r"^[-*•●▪◦–]\s*[^\s\d]", _text(right)) or
                             not re.search(r"[\w\d]", _text(right)))):
                        right = []
                # A colon halfway through a flowing paragraph is punctuation,
                # not evidence of a label/value column. Keep the text available
                # for semantic extraction without auto-approving a fragment.
                prose = False
                if right and right[0]["x0"] - word["x1"] < 6:
                    for neighbor_index in (index - 1, index + 1):
                        if not 0 <= neighbor_index < len(lines) or neighbor_index in pairs:
                            continue
                        neighbor = lines[neighbor_index]
                        if (abs(neighbor[0]["x0"] - left[0]["x0"]) < 3
                                and abs(neighbor[0]["top"] - left[0]["top"]) < 2 * (word["bottom"] - word["top"])
                                and (neighbor_index > index or len(_text(neighbor)) > 70)
                                and not re.search(r"[:：]", _text(neighbor))):
                            prose = True
                if right and not prose and field_for_label(split_label_unit(label)[0]) and len(label.split()) <= 8:
                    pairs[index] = (left, right)
                    break
            if index in pairs:
                break
    results = {}
    for index, (left, right) in pairs.items():
        value_words = list(right)
        last_top = min(w["top"] for w in right)
        max_gap = max(22, 2 * max(w["bottom"] - w["top"] for w in right))
        for next_index in range(index + 1, len(lines)):
            line = lines[next_index]
            if next_index in pairs or line[0]["top"] - last_top > max_gap:
                break
            # The label column is empty on a continuation. Body copy in an
            # unrelated column to its left is deliberately not consumed.
            if any(left[0]["x0"] - 3 <= w["x0"] < right[0]["x0"] - 3 for w in line):
                break
            chunks = [c for c in _chunks(line) if abs(c[0]["x0"] - right[0]["x0"]) <= 3]
            if len(chunks) != 1 or re.search(r"[:：=]", _text(chunks[0])):
                break
            value_words.extend(chunks[0])
            if len(_text(value_words)) > 512:
                break  # the caller reports this whole value, never its prefix
            last_top = line[0]["top"]
        results[index] = (left, value_words)
    return results


def _cell_text(value):
    # A physical cell is a single logical value. Its line breaks are layout,
    # not permission to discard the conditions/variants on the second line.
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _horizontal_pairs(words, horizontal, consumed):
    """Bind borderless columns from repeated, adjoining printed rule spans.

    The shared endpoints provide a physical label/value boundary. Independent
    page columns stay independent; no global gap threshold or vendor template.
    Entire value regions are retained, including nested condition/value rows.
    """
    spans = defaultdict(list)
    for edge in horizontal:
        if edge["x1"] - edge["x0"] >= 30:
            spans[(round(edge["x0"], 1), round(edge["x1"], 1))].append(edge["top"])
    spans = {span: ys for span, ys in spans.items() if len(ys) >= 3}
    pairs = []
    for (x0, middle), ys in spans.items():
        for (other, x1), right_ys in spans.items():
            if abs(middle - other) > .5:
                continue
            boundaries = sorted({round(y, 2) for y in ys if any(abs(y - r) < .5 for r in right_ys)})
            if len(boundaries) < 3:
                continue
            # A third adjoining span proves this is a matrix, not a pair.
            if any((abs(end - x0) < .6 or abs(start - x1) < .6) and
                   sum(any(abs(y - b) < .6 for y in other_ys) for b in boundaries) >= 3
                   for (start, end), other_ys in spans.items() if (start, end) not in {(x0, middle), (other, x1)}):
                continue
            heading_words = [w for w in words if x0 - .5 <= w["x0"] and w["x1"] <= x1 + .5 and
                             boundaries[0] - 18 <= w["top"] and w["bottom"] <= boundaries[0]]
            headings = _lines(heading_words)
            section = _text(headings[-1]) if headings else ""
            if len(section) > 64 or re.search(r"\d", section):
                section = ""
            for top, bottom in zip(boundaries, boundaries[1:]):
                box = (x0, top, x1, bottom)
                if any(min(b[2], x1) - max(b[0], x0) > 1 and min(b[3], bottom) - max(b[1], top) > 1 for b in consumed):
                    continue
                region = [w for w in words if _inside(w, box)]
                region_lines = _lines(region)
                if not region:
                    continue
                if any(w["x0"] < middle - 1 and w["x1"] > middle + 1 for w in region):
                    if len(region_lines) == 1 and len(_text(region)) <= 64 and not re.search(r"\d", _text(region)):
                        section = _text(region)
                    continue  # heading/prose across the inferred boundary
                left = [w for w in region if (w["x0"] + w["x1"]) / 2 < middle]
                right = [w for w in region if w not in left]
                if not left or not right:
                    if len(region_lines) == 1 and len(_text(region)) <= 64 and not re.search(r"\d", _text(region)):
                        section = _text(region)
                    continue
                label = " ".join(_text(line) for line in _lines(left))
                value = " ".join(_text(line) for line in _lines(right))
                if field_for_label(split_label_unit(label)[0]) and len(value) <= 512:
                    pairs.append((label, value, box, left, right, section))
    return pairs


def read_layout_page(page, number: int) -> tuple[list[dict], list[dict]]:
    """Return text/locator/provenance records and grouped reading issues."""
    if len(page.chars) > MAX_PAGE_CHARACTERS:
        return [], [{"code": "page_text_limit", "locator": {"page": number},
                     "message": "本页文字结构超过安全上限，其余页面继续读取；请拆分或简化本页。"}]
    clean = page.dedupe_chars()
    words = clean.extract_words(x_tolerance=2, y_tolerance=3, extra_attrs=["size"])
    records: list[dict] = []
    issues: list[dict] = []
    consumed: list[tuple] = []
    horizontal = []
    if len(page.edges) > MAX_PAGE_EDGES:
        tables = []
        issues.append({"code": "page_geometry_limit", "locator": {"page": number},
                       "message": "本页图形过于复杂，仅提取明确文字；图表尚未完整核对，其余页不受影响。"})
    else:
        horizontal, vertical = _orthogonal_edges(clean.edges)
        tables = clean.find_tables({"horizontal_strategy": "explicit", "vertical_strategy": "explicit",
            "explicit_horizontal_lines": horizontal, "explicit_vertical_lines": vertical,
            "join_tolerance": 4}) if len(horizontal) >= 2 and len(vertical) >= 2 else []
    for index, table in enumerate(tables):
        raw = table.extract(x_tolerance=2, y_tolerance=3)
        # Reject diagrams with no meaningful parameter labels.
        if len(raw) < 2 or not any(field_for_label(split_label_unit(str(row[0] or ""))[0]) for row in raw if row):
            continue
        table_id = f"pdf:{number}:{index}"
        rows = [(i, [(box, value) for box, value in zip(table.rows[i].cells, row)]) for i, row in enumerate(raw)]
        if table_role(rows) == "reference":
            consumed.append(table.bbox)
            continue
        # Shaded headers sometimes have no printed top border. Bind a nearby
        # structural header only inside the grid's exact existing columns.
        outside_header = None
        boundaries = sorted({box[0] for box in table.cells} | {table.bbox[2]})
        if raw and len(raw[0]) >= 3 and header_field(raw[0][0]) not in {"model", "sku", "variant"}:
            above = [w for w in words if table.bbox[1] - 24 <= w["top"] and w["bottom"] <= table.bbox[1]
                     and table.bbox[0] <= (w["x0"] + w["x1"]) / 2 <= table.bbox[2]]
            for line in reversed(_lines(above)):
                values, boxes = [], []
                for left, right in zip(boundaries, boundaries[1:]):
                    matched = [w for w in line if left <= (w["x0"] + w["x1"]) / 2 < right]
                    values.append(_text(matched))
                    boxes.append(_bbox(matched) if matched else None)
                if len(values) != len(raw[0]):
                    continue
                model_header = header_field(values[0]) in {"model", "sku"} and all(values)
                reference_header = table_role([(0, list(zip(boxes, values)))]) == "reference"
                if model_header or parameter_header_columns(values) or reference_header:
                    rows.insert(0, (-1, list(zip(boxes, values))))
                    outside_header = _bbox(line)
                    break
        if table_role(rows) == "reference":
            consumed.extend([table.bbox] + ([outside_header] if outside_header else []))
            continue
        # Expand only actual merged geometry, never carry a previous value.
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
        if structured and any(not cell.identity and not cell.whole_parameter_row for cell in structured):
            # A column-oriented list needs a product/record owner. Otherwise
            # "Substance | Maximum limit" becomes many conflicting product
            # limits. Keep the original table as ONE reading issue instead.
            # Explicit parameter rows and MODEL/SKU matrices remain supported.
            structured = []
        # Vertical key/value tables use the identical explicit label contract.
        if structured is None and all(len(row) == 2 for _, row in expanded):
            for rn, cells in expanded:
                if cells[0][0] == cells[1][0]:
                    continue  # one merged heading is not its own value
                label, value = (_cell_text(v) for _, v in cells)
                if field_for_label(split_label_unit(label)[0]) and value:
                    box = cells[1][0] or table.bbox
                    records.append({"text": f"{label}: {value}", "locator": _locator(page, number, box, table=index, row=rn),
                                    "provenance": {"table_id": table_id, "atomic_parameter": True,
                                        "label_bbox": cells[0][0], "value_bbox": box}})
            consumed.append(table.bbox)
            continue
        consumed.append(table.bbox)
        if outside_header:
            consumed.append(outside_header)
        bound_rows = set()
        for cell in structured or []:
            if not cell.location or re.search(r"[\ufffd\ue000-\uf8ff]", cell.value + cell.header):
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
            text = f"{_cell_text(cell.header)}: {_cell_text(cell.value)}"
            records.append({"text": text,
                            "locator": _locator(page, number, cell.location, table=index, row=cell.row_number),
                            "provenance": {"table_id": table_id, "atomic_parameter": True, "parent_labels": list(dict.fromkeys(parents)),
                                ("condition_bbox" if cell.whole_parameter_row else "value_bbox"): cell.location,
                                "structured_row": {"row_id": cell.row_id, "identity": cell.identity,
                                                   "field": cell.field}}})
        missing = [rn for rn, cells in expanded if rn >= 0 and rn not in bound_rows and
                   any(re.search(r"\d", str(value or "")) for _, value in cells[1:]) and
                   header_field(cells[0][1]) not in {"model", "sku", "variant"}]
        if missing or not structured:
            issues.append({"code": "table_binding_unclear", "locator": _locator(page, number, table.bbox, table=index),
                           "message": "这张表部分行列或适用条件未能可靠对应，请核对原表或提供表格文件。", "unbound_rows": missing})
    # Preserve line geometry outside grids. Widely separated multi-value rows
    # are not flattened (that was the source of cross-product false facts).
    for label, value, box, left, right, section in _horizontal_pairs(words, horizontal, consumed):
        records.append({"text": f"{label}: {value}", "locator": _locator(page, number, box),
                        "provenance": {"layout_binding": "horizontal_rule_pair", "atomic_parameter": True,
                                       "label_bbox": _bbox(left), "value_bbox": _bbox(right),
                                       "parent_labels": [section] if section else []}})
        consumed.append(box)
    remainder = [w for w in words if not any(_inside(w, box) for box in consumed)]
    uncertain = []
    lines = _lines(remainder)
    aligned = _wrapped_pairs(lines)
    bound_words = {id(w) for left, right in aligned.values() for w in left + right}
    for line_number, line in enumerate(lines):
        if line_number in aligned:
            left, right = aligned[line_number]
            if len(_text(right)) > 512:
                uncertain.append(line_number)
            else:
                records.append({"text": f"{_label_text(left)}: {_text(right)}", "locator": _locator(page, number, _bbox(left + right), text_block=line_number),
                                "provenance": {"layout_binding": "wrapped_aligned_pair", "atomic_parameter": True,
                                               "label_bbox": _bbox(left), "value_bbox": _bbox(right)}})
        line = [word for word in line if id(word) not in bound_words]
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
            records.append({"text": text, "locator": _locator(page, number, _bbox(line), text_block=line_number),
                            "provenance": {"layout_binding": "unbound_text"}})
    from .pdf_conditions import raised_reference_words
    for record in records:
        meta = record["provenance"]
        if not meta.get("atomic_parameter"):
            continue
        boxes = [meta[key] for key in ("label_bbox", "value_bbox", "condition_bbox") if meta.get(key)]
        ref_words = raised_reference_words([w for w in words if any(_inside(w, box) for box in boxes)])
        label = record["text"].partition(":")[0]
        explicit_refs = re.findall(r"\*(\d{1,2})\b", label)
        if ref_words or explicit_refs:
            meta["condition_refs"] = list(dict.fromkeys([w["text"].strip("*)") for w in ref_words] + explicit_refs))
            label, colon, value = record["text"].partition(":")
            for key, old in (("label_bbox", label), ("value_bbox", value)):
                if meta.get(key):
                    region = [w for w in words if _inside(w, meta[key]) and w not in ref_words]
                    updated = " ".join(_text(line) for line in _lines(region))
                    if key == "label_bbox":
                        label = re.sub(r"^\s*[-*•●▪◦–]\s*", "", updated).rstrip(":：")
                    else:
                        value = " " + updated
            for marker in meta["condition_refs"]:
                label = re.sub(r"\s*\*?" + re.escape(marker) + r"\)?$", "", label)
            record["text"] = label + colon + value
    if uncertain:
        issues.append({"code": "unruled_table_unclear", "locator": {"page": number},
                       "message": "本页有未能可靠绑定的多列表格，请补充 CSV/XLSX 或核对原件。", "unbound_rows": uncertain})
    return records, issues


def without_running_margins(blocks):
    """Repeated page-margin labels are document furniture, not parameters.

    Preserve explicit known product identity labels. Only geometry-bound
    custom labels repeated on distinct pages at the same margin are removed.
    """
    groups = defaultdict(list)
    for block in blocks:
        meta = block.provenance
        box = block.locator.get("bbox_1000", [])
        if (meta.get("layout_binding") != "wrapped_aligned_pair" or len(box) != 4
                or not (box[3] < 70 or box[1] > 930)):
            continue
        label = block.text.partition(":")[0]
        if field_for_label(label, known_only=True):
            continue
        groups[(label.casefold(), round(box[0] / 5), round(box[1] / 5))].append(block)
    remove = {id(b) for values in groups.values() if len({b.locator.get("page") for b in values}) >= 2 for b in values}
    return [block for block in blocks if id(block) not in remove]
