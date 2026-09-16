"""Conservative header/value binding for CSV, DOCX and XLSX tables."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Sequence

from .field_registry import FIELD_SPECS, field_for_label
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


_HEADERS = {
    normalize_text(alias): spec.name
    for spec in FIELD_SPECS
    for alias in (*spec.aliases, spec.name)
}
_VARIANT_HEADERS = {
    normalize_text(header): spec.name
    for spec in FIELD_SPECS for header in spec.variant_headers
}


def header_field(value: Any) -> str | None:
    text = normalize_text(str(value or ""))
    # Unit annotations such as "Weight (kg)" are common.
    base = text.split("(", 1)[0].split("（", 1)[0].strip()
    return _HEADERS.get(text) or _HEADERS.get(base)


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
            if len(cells) < 2 or not all(field_for_label(str(value).split("(")[0].split("（")[0].strip()) for _, value in cells):
                continue
            header_position = position
            break
        if header_position is None:
            return None
    # Never discard a column just because there is no specialist rule for it.
    for column, (_location, value) in enumerate(rows[header_position][1]):
        label = str(value or "").strip()
        spec = field_for_label(label.split("(")[0].split("（")[0].strip())
        if spec:
            header_map.setdefault(column, (label, spec.name))

    result: list[StructuredCell] = []
    for row_number, cells in rows[header_position + 1:]:
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
                base = header_text.split("(", 1)[0].split("（", 1)[0].strip()
                if normalize_text(base) in _VARIANT_HEADERS:
                    # Normalize units so 1 L and 1000 mL are one variant.
                    unit = re.search(r"[（(]([^()（）]+)[)）]", header_text)
                    normalized = normalize_value(field, text + (" " + unit[1] if unit else ""))
                    if any(note.startswith("unparsed_") for note in normalized.notes):
                        uncertain_variant = True
                    else:
                        token = json.dumps([normalized.value, normalized.unit], ensure_ascii=False,
                                           sort_keys=True, separators=(",", ":"))
                        dimensions.setdefault(field, set()).add(token)
        if not bound:
            continue
        identity = {
            field: next(iter(values)) for field, values in identity_values.items() if len(values) == 1
        }
        if any(len(values) > 1 for values in identity_values.values()):
            identity["_ambiguous_identity"] = "true"
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
