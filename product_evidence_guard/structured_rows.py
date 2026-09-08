"""Conservative header/value binding for CSV, DOCX and XLSX tables."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Sequence

from .field_registry import FIELD_SPECS
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
    header_position: int | None = None
    header_map: dict[int, tuple[str, str]] = {}
    for position, (_row_number, cells) in enumerate(rows[:10]):
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
        return None

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
