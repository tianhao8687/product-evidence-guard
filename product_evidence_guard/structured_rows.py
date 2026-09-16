"""Conservative header/value binding for CSV, DOCX and XLSX tables."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from math import isfinite
import re
from typing import Any, Sequence

from .field_registry import FIELD_SPECS, field_for_label, split_label_unit
from .normalization import normalize_text, normalize_value


@dataclass(frozen=True, slots=True)
class StructuredCell:
    row_number: int
    location: Any
    header: str
    field: str
    value: str
    row_id: str
    identity: dict[str, str]
    parent_labels: tuple[str, ...] = ()
    whole_parameter_row: bool = False


_VARIANT_HEADERS = {
    normalize_text(header): spec.name
    for spec in FIELD_SPECS for header in spec.variant_headers
}


def identity_from_values(values: dict[str, set[str]]) -> dict[str, str]:
    """Shared row/object identity contract; conflicting aliases are ambiguous."""
    identity = {field: next(iter(items)) for field, items in values.items() if len(items) == 1}
    if any(len(items) > 1 for items in values.values()):
        identity["_ambiguous_identity"] = "true"
    return identity


def header_field(value: Any) -> str | None:
    text = normalize_text(str(value or ""))
    # Unit annotations such as "Weight (kg)" are common.
    base, _unit = split_label_unit(text)
    spec = field_for_label(base, known_only=True)
    return spec.name if spec else None


def bind_structured_rows(
    rows: Sequence[tuple[int, Sequence[tuple[Any, Any]]]], *, table_id: str
) -> list[StructuredCell] | None:
    """Return cells only when a genuine multi-field header is present."""
    # An explicit key/value heading owns the table orientation. Otherwise a
    # later text value (e.g. '材质,不锈钢') can look like an all-new header.
    for _row_number, cells in rows:
        if len(cells) == 2:
            labels = [normalize_text(str(value or "")) for _, value in cells]
            if labels[0] in {"字段", "参数", "field", "parameter"} and labels[1] in {"值", "数值", "value"}:
                return None
    header_position: int | None = None
    header_map: dict[int, tuple[str, str]] = {}
    for position, (_row_number, cells) in enumerate(rows):
        proposed: dict[int, tuple[str, str]] = {}
        seen: set[str] = set()
        for column, (_location, value) in enumerate(cells):
            field = header_field(value)
            if field:
                proposed[column] = (str(value).strip(), field)
                seen.add(field)
        if len(seen) >= 2:
            header_position = position
            header_map = proposed
            break
    if header_position is None:
        # Unknown columns need a header/value layout, not just two arbitrary
        # strings. In particular '型号,A1' is a key/value row, not a header.
        for position, (_row_number, cells) in enumerate(rows[:-1]):
            has_identity = any(header_field(value) in {"sku", "model", "variant"} for _, value in cells)
            # An all-new header is still recognizable above a row of values.
            next_cells = rows[position + 1][1]
            unknown_header = (len(cells) == len(next_cells) and
                              all(not re.search(r"\d", str(value)) for _, value in cells) and
                              not header_field(next_cells[0][1]) and
                              any(re.match(r"^\s*[-+≤≥<>]?\d", str(value)) for _, value in next_cells))
            if not has_identity and not unknown_header:
                continue
            if any(re.search(r"\d", str(value)) and not header_field(value) for _, value in cells):
                continue
            if len(cells) < 2 or not all(field_for_label(split_label_unit(str(value))[0]) for _, value in cells):
                continue
            header_position = position
            break
        if header_position is None:
            return None
    # Never discard a column just because there is no specialist rule for it.
    for column, (_location, value) in enumerate(rows[header_position][1]):
        label = str(value or "").strip()
        spec = field_for_label(split_label_unit(label)[0])
        if spec:
            header_map.setdefault(column, (label, spec.name))

    labels = [normalize_text(header[0]) for header in header_map.values()]
    if len(labels) != len(set(labels)):
        return []  # parallel subtables need region binding, not one flat row

    result: list[StructuredCell] = []
    for row_number, cells in rows[header_position + 1:]:
        if all(str(cells[column][1] or "").strip() == header[0] for column, header in header_map.items() if column < len(cells)):
            continue  # repeated page header, not another product
        bound: list[tuple[Any, str, str, str]] = []
        identity_values: dict[str, set[str]] = {}
        dimensions: dict[str, set[str]] = {}
        uncertain_variant = False
        for column, (location, value) in enumerate(cells):
            header = header_map.get(column)
            text = str(value).strip() if value not in (None, "") else ""
            if header and text:
                header_text, field = header
                bound.append((location, header_text, field, text))
                if field in {"sku", "model", "variant"}:
                    identity_values.setdefault(field, set()).add(text)
                base, unit = split_label_unit(header_text)
                if normalize_text(base) in _VARIANT_HEADERS:
                    # Normalize units so 1 L and 1000 mL are one variant.
                    normalized = normalize_value(field, text + (" " + unit if unit else ""))
                    if any(note.startswith("unparsed_") for note in normalized.notes):
                        uncertain_variant = True
                    else:
                        token = json.dumps([normalized.value, normalized.unit], ensure_ascii=False,
                                           sort_keys=True, separators=(",", ":"))
                        dimensions.setdefault(field, set()).add(token)
        if not bound:
            continue
        identity = identity_from_values(identity_values)
        if "variant" not in identity:
            if uncertain_variant or any(len(values) > 1 for values in dimensions.values()):
                identity["_ambiguous_variant"] = "true"
            elif dimensions:
                identity["variant"] = "|".join(f"{field}={next(iter(dimensions[field]))}"
                                               for field in sorted(dimensions))
        row_payload = {"table": table_id, "row": row_number}
        row_id = hashlib.sha256(json.dumps(row_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        for location, header, field, value in bound:
            result.append(StructuredCell(
                row_number=row_number,
                location=location,
                header=header,
                field=field,
                value=value,
                row_id=row_id,
                identity=identity,
            ))
    return result


def _physical_cell_key(cell: tuple[Any, Any]) -> tuple | None:
    """Only real PDF geometry can prove that expanded columns are one cell."""
    box, value = cell
    if (not isinstance(box, (tuple, list)) or len(box) != 4
            or not all(isinstance(n, (int, float)) and not isinstance(n, bool) and isfinite(n) for n in box)
            or box[0] >= box[2] or box[1] >= box[3]):
        return None
    return (*box, re.sub(r"\s+", " ", str(value)).strip() if value is not None else "")


def bind_model_matrix(rows: Sequence[tuple[int, Sequence[tuple[Any, Any]]]], *,
                      table_id: str) -> list[StructuredCell] | None:
    """Transpose only an explicit MODEL/SKU header, never inferred product names.

    Blank cells are not forward-filled. Repeated headers start a new section;
    ambiguous or differently sized rows remain unbound for coverage reporting.
    Works on the same row contract as CSV/Office/PDF, without vendor rules.
    """
    result: list[StructuredCell] = []
    identities: list[dict[str, str]] | None = None
    value_columns: list[int] = []
    aliases: dict[int, list[int]] = {}
    physical_rows: dict[tuple, set[int]] = {}
    for row_number, cells in rows:
        for cell in cells:
            key = _physical_cell_key(cell)
            if key is not None:
                physical_rows.setdefault(key, set()).add(row_number)
    context: tuple[str, ...] = ()
    found = False
    for row_number, cells in rows:
        texts = [str(v).strip() if v not in (None, "") else "" for _, v in cells]
        if not texts:
            continue
        identity_field = header_field(texts[0])
        first_value = 1
        while first_value < len(texts) and texts[first_value] in {"", texts[0]}:
            first_value += 1
        proposed_columns = [i for i in range(first_value, len(texts)) if texts[i]]
        if (identity_field in {"model", "sku", "variant"} and len(proposed_columns) >= 2
                and all(not header_field(texts[i]) and re.search(r"[A-Za-z\u3400-\u9fff]", texts[i])
                        and re.search(r"\d", texts[i]) for i in proposed_columns)):
            found = True
            # PDF grids can have extra subcolumns introduced by another row.
            # De-duplicate only aliases of the SAME physical merged header,
            # never repeated model text in distinct cells or CSV columns.
            value_columns = []
            aliases = {}
            physical_columns: dict[tuple, int] = {}
            for column in proposed_columns:
                key = _physical_cell_key(cells[column])
                if key is not None and key in physical_columns:
                    aliases[physical_columns[key]].append(column)
                else:
                    value_columns.append(column)
                    aliases[column] = []
                    if key is not None:
                        physical_columns[key] = column
            values = [texts[i] for i in value_columns]
            if len(values) < 2 or not all(values) or len(set(values)) != len(values):
                identities = None
                continue
            identities = [{identity_field: value} for value in values]
            context = ()
            continue
        if not identities:
            continue
        if texts[0] and not any(texts[value_columns[0]:]):
            context = (texts[0],)
            continue
        labels = [text for text in texts[:value_columns[0]] if text]
        label = labels[-1] if labels else ""
        spec = field_for_label(split_label_unit(label)[0])
        if not spec or len(texts) <= max(value_columns):
            continue
        label_column = max((i for i in range(value_columns[0]) if texts[i]), default=-1)
        label_key = _physical_cell_key(cells[label_column]) if label_column >= 0 else None
        if label_key is not None and len(physical_rows[label_key]) > 1:
            # One vertically merged label can describe a threshold AND its
            # recovery behavior. They are not competing values of one fact.
            # Until the region is bound as a whole, report it as unread, not
            # a user-resolvable conflict between fragments.
            continue
        # A split sub-row under a merged model header needs its own binding;
        # selecting its first value would silently discard an applicability
        # condition. Leave the entire row unbound for coverage reporting.
        if any(alias >= len(cells) or _physical_cell_key(cells[column]) is None
               or _physical_cell_key(cells[alias]) != _physical_cell_key(cells[column])
               for column, extra_columns in aliases.items() for alias in extra_columns):
            continue
        for index, column in enumerate(value_columns):
            value = texts[column]
            if not value:
                continue
            row_id = hashlib.sha256(json.dumps([table_id, row_number, index],
                                               ensure_ascii=False).encode()).hexdigest()[:20]
            result.append(StructuredCell(row_number, cells[column][0], label, spec.name,
                                         value, row_id, dict(identities[index]), tuple([*context, *labels[:-1]])))
    return result if found else None


def bind_table(rows: Sequence[tuple[int, Sequence[tuple[Any, Any]]]], *,
               table_id: str) -> list[StructuredCell] | None:
    """Shared orientation dispatch; keep the original row binder reusable."""
    if table_role(rows) == "reference":
        return []
    parameters = bind_parameter_matrix(rows, table_id=table_id)
    if parameters is not None:
        return parameters
    matrix = bind_model_matrix(rows, table_id=table_id)
    if matrix is not None:
        return matrix
    return bind_structured_rows(rows, table_id=table_id)


def _heading(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold().rstrip(".")


_PARAMETER_HEADERS = {"parameter", "parameters", "characteristic", "characteristics", "item", "参数", "特性", "项目"}
_VALUE_HEADERS = {"value", "values", "min", "typ", "max", "minimum", "typical", "maximum", "数值", "值", "最小", "典型", "最大"}


def parameter_header_columns(values):
    """Recognize structural column roles, independent of product vocabulary."""
    names = [_heading(value) for value in values]
    labels = [i for i, name in enumerate(names) if name in _PARAMETER_HEADERS]
    values = [i for i, name in enumerate(names) if name in _VALUE_HEADERS or name.endswith(" type")]
    return (names, labels, values) if labels and values else None


def table_role(rows):
    """Recognize document/reference tables by their *combined* header roles.

    These are not a whitelist of product parameters: unfamiliar specifications
    remain supported. A history/date table or a pin/name list is not one
    product with many competing dates or pin numbers.
    """
    for _, cells in rows[:3]:
        headers = {_heading(value) for _, value in cells}
        if (headers & {"date", "日期"} and headers & {"version", "revision", "版本", "changes", "变更"}) or (
            headers & {"pin", "pin no", "pin number", "引脚", "引脚编号"} and
            headers & {"name", "symbol", "function", "assignment", "signal", "名称", "功能", "信号", "description"}) or (
            headers & {"reference", "参考资料"} and headers & {"link", "链接"}):
            return "reference"
    return "parameters"


def bind_parameter_matrix(rows, *, table_id):
    """Parameter/condition/unit tables are row-oriented, not product records.

    Carry the entire row, including every min/typ/max value and condition.
    Only actual merged geometry may repeat labels or units across subrows.
    No field names or product models are needed to recognize this structure.
    """
    qualifiers = {"condition", "conditions", "test condition", "test conditions", "classification", "comments", "测试条件", "条件", "分类"}
    units = {"unit", "units", "单位"}
    header = None
    for position, (_, cells) in enumerate(rows[:6]):
        roles = parameter_header_columns([value for _, value in cells])
        if roles:
            names, labels, values = roles
            header = (position, names, labels, values)
            break
    if header is None:
        return None
    position, names, label_cols, value_cols = header
    result = []
    for rn, cells in rows[position + 1:]:
        if len(cells) != len(names):
            continue
        # Several measurements stacked in one physical cell need an inner
        # row binder. Flattening them loses which condition owns each value.
        if any(sum(bool(re.search(r"\d", line)) for line in str(cells[i][1] or "").splitlines()) > 1
               for i in value_cols):
            continue
        label_parts = list(dict.fromkeys(re.sub(r"\s+", " ", str(cells[i][1] or "")).strip() for i in label_cols))
        label = " ".join(p for p in label_parts if p)
        spec = field_for_label(split_label_unit(label)[0])
        if not spec or _heading(label) in _PARAMETER_HEADERS:
            continue
        unit = " ".join(dict.fromkeys(re.sub(r"\s+", "", str(cells[i][1] or "")) for i, name in enumerate(names) if name in units)).strip()
        conditions = [re.sub(r"\s+", " ", str(cells[i][1] or "")).strip() for i, name in enumerate(names) if name in qualifiers]
        conditions = [s for s in conditions if s and s not in {"-", "—"}]
        values = []
        locations = []
        for i in value_cols:
            value = re.sub(r"\s+", " ", str(cells[i][1] or "")).strip()
            if not value or value in {"-", "—"}:
                continue
            prefix = names[i] + ": " if len(value_cols) > 1 or names[i] not in {"value", "values", "值", "数值"} else ""
            values.append(prefix + value + (" " + unit if unit and unit not in {"-", "—"} else ""))
            locations.append(cells[i][0])
        if not values:
            continue
        value = "; ".join(values)
        if conditions:
            value += " (" + "; ".join(conditions) + ")"
        if len(value) > 512:
            continue
        # Physical PDF box includes the complete parameter+condition row, so
        # footnote markers and preview cannot lose the label-side evidence.
        boxes = [loc for loc, _ in cells if isinstance(loc, (list, tuple)) and len(loc) == 4]
        location = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)) if boxes else locations[0]
        row_id = hashlib.sha256(json.dumps([table_id, rn], ensure_ascii=False).encode()).hexdigest()[:20]
        result.append(StructuredCell(rn, location, label, spec.name, value, row_id, {}, tuple(conditions), True))
    return result
