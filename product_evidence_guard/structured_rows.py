"""Conservative header/value binding for CSV, DOCX and XLSX tables."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Sequence

from .field_registry import FIELD_SPECS
from .normalization import normalize_text


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
_VARIANT_FIELDS = {
    spec.name for spec in FIELD_SPECS if spec.category == "variant"
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
            if field and field not in seen:
                proposed[column] = (str(value).strip(), field)
                seen.add(field)
        if len(proposed) >= 2:
            header_position = position
            header_map = proposed
            break
    if header_position is None:
        return None

    result: list[StructuredCell] = []
    for row_number, cells in rows[header_position + 1:]:
        values: dict[str, str] = {}
        locations: dict[str, Any] = {}
        headers: dict[str, str] = {}
        for column, (location, value) in enumerate(cells):
            header = header_map.get(column)
            text = str(value).strip() if value not in (None, "") else ""
            if header and text:
                header_text, field = header
                values[field] = text
                locations[field] = location
                headers[field] = header_text
        if not values:
            continue
        identity = {
            field: values[field]
            for field in ("sku", "model", "variant")
            if field in values
        }
        if "variant" not in identity:
            dimensions = [f"{field}={values[field]}" for field in sorted(_VARIANT_FIELDS) if field in values]
            if dimensions:
                identity["variant"] = "|".join(dimensions)
        row_payload = {"table": table_id, "row": row_number}
        row_id = hashlib.sha256(json.dumps(row_payload, sort_keys=True).encode("utf-8")).hexdigest()[:20]
        for field in sorted(values, key=lambda item: next(i for i, (_, name) in header_map.items() if name == item)):
            result.append(StructuredCell(
                row_number=row_number,
                location=locations[field],
                header=headers[field],
                field=field,
                value=values[field],
                row_id=row_id,
                identity=identity,
            ))
    return result
