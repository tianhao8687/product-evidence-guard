"""Conservative header/value binding for CSV, DOCX and XLSX tables."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
            value_columns = proposed_columns
            values = [texts[i] for i in value_columns]
            if not all(values) or len(set(values)) != len(values):
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
    matrix = bind_model_matrix(rows, table_id=table_id)
    if matrix is not None:
        return matrix
    return bind_structured_rows(rows, table_id=table_id)
