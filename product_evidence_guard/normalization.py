from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from .field_registry import REGISTRY, field_definition


@dataclass(frozen=True, slots=True)
class NormalizedValue:
    value: Any
    unit: str | None
    notes: tuple[str, ...] = ()


_NUMBER = r"[-+]?\d+(?:\.\d+)?"


UNIT_RULES = {
    family.name: family.units
    for family in REGISTRY.unit_families.values()
}
# Compatibility view for callers that only need multiplicative factors.
UNIT_FACTORS: dict[str, dict[str, Decimal]] = {
    name: {alias: rule.scale for alias, rule in rules.items()}
    for name, rules in UNIT_RULES.items()
}
BASE_UNITS = {
    family.name: family.base_unit
    for family in REGISTRY.unit_families.values()
}


def _clean_decimal(value: Decimal) -> int | float:
    quantized = value.normalize()
    if quantized == quantized.to_integral_value():
        return int(quantized)
    return float(quantized)


def normalize_text(value: str) -> str:
    compact = re.sub(r"\s+", " ", value.strip()).casefold()
    compact = compact.replace("，", ",").replace("：", ":")
    return compact


def normalize_number_with_unit(raw: str, family: str) -> NormalizedValue | None:
    rules = UNIT_RULES.get(family)
    if rules is None:
        return None
    aliases = sorted(rules, key=len, reverse=True)
    unit_pattern = "|".join(re.escape(item) for item in aliases)
    range_pattern = re.compile(
        rf"(?P<start>{_NUMBER})\s*"
        rf"(?P<start_unit>{unit_pattern})?\s*"
        rf"(?:-|–|—|~|to|至|到)\s*"
        rf"(?P<end>{_NUMBER})\s*"
        rf"(?P<end_unit>{unit_pattern})",
        re.IGNORECASE,
    )
    range_match = range_pattern.search(raw)
    if range_match:
        start_unit_text = range_match.group("start_unit") or range_match.group(
            "end_unit"
        )
        end_unit_text = range_match.group("end_unit")
        start_rule = rules.get(start_unit_text.casefold())
        end_rule = rules.get(end_unit_text.casefold())
        if start_rule is not None and end_rule is not None:
            try:
                start = (
                    Decimal(range_match.group("start")) * start_rule.scale
                    + start_rule.offset
                )
                end = (
                    Decimal(range_match.group("end")) * end_rule.scale
                    + end_rule.offset
                )
            except InvalidOperation:
                pass
            else:
                return NormalizedValue(
                    [_clean_decimal(start), _clean_decimal(end)],
                    BASE_UNITS[family],
                    ("range_preserved",),
                )

    pattern = re.compile(
        rf"(?P<number>{_NUMBER})\s*(?P<unit>{unit_pattern})",
        re.IGNORECASE,
    )
    match = pattern.search(raw)
    if not match:
        return None
    try:
        number = Decimal(match.group("number"))
    except InvalidOperation:
        return None
    unit = match.group("unit").casefold()
    rule = rules.get(unit)
    if rule is None:
        return None
    normalized = number * rule.scale + rule.offset
    return NormalizedValue(_clean_decimal(normalized), BASE_UNITS[family])


def normalize_count(raw: str) -> NormalizedValue | None:
    match = re.search(rf"(?P<number>{_NUMBER})\s*(?:个|件|只|套|pcs?|pieces?|pack)?", raw, re.IGNORECASE)
    if not match:
        return None
    try:
        value = Decimal(match.group("number"))
    except InvalidOperation:
        return None
    if value != value.to_integral_value():
        return None
    return NormalizedValue(int(value), "count")


def normalize_dimensions(raw: str) -> NormalizedValue | None:
    pattern = re.compile(
        rf"(?P<a>{_NUMBER})\s*[x×*]\s*(?P<b>{_NUMBER})(?:\s*[x×*]\s*(?P<c>{_NUMBER}))?\s*"
        rf"(?P<unit>mm|cm|m|in|inch|毫米|厘米|米|英寸)",
        re.IGNORECASE,
    )
    match = pattern.search(raw)
    if not match:
        return None
    rule = UNIT_RULES["length"].get(match.group("unit").casefold())
    if rule is None:
        return None
    values: list[int | float] = []
    for key in ("a", "b", "c"):
        text = match.group(key)
        if text is None:
            continue
        values.append(_clean_decimal(Decimal(text) * rule.scale + rule.offset))
    return NormalizedValue(values, "mm", ("dimension_order_preserved",))


def normalize_value(field: str, raw: str) -> NormalizedValue:
    definition = field_definition(field)
    if field == "capacity":
        return (
            normalize_number_with_unit(raw, "capacity_charge")
            or normalize_number_with_unit(raw, "capacity_volume")
            or NormalizedValue(normalize_text(raw), None, ("unparsed_capacity",))
        )
    if definition and definition.value_type == "dimensions":
        return normalize_dimensions(raw) or NormalizedValue(normalize_text(raw), None, ("unparsed_dimensions",))
    if definition and definition.value_type == "count":
        return normalize_count(raw) or NormalizedValue(normalize_text(raw), None, ("unparsed_count",))
    family = definition.unit_family if definition else None
    if family:
        return normalize_number_with_unit(raw, family) or NormalizedValue(normalize_text(raw), None, ("unparsed_unit",))
    return NormalizedValue(normalize_text(raw), None)


def values_equal(a: Any, b: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tolerance
    return a == b
