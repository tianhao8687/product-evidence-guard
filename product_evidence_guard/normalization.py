from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedValue:
    value: Any
    unit: str | None
    notes: tuple[str, ...] = ()


_NUMBER = r"[-+]?\d+(?:\.\d+)?"


UNIT_FACTORS: dict[str, dict[str, Decimal]] = {
    "mass": {
        "mg": Decimal("0.001"),
        "g": Decimal("1"),
        "克": Decimal("1"),
        "kg": Decimal("1000"),
        "千克": Decimal("1000"),
        "公斤": Decimal("1000"),
        "lb": Decimal("453.59237"),
        "lbs": Decimal("453.59237"),
        "磅": Decimal("453.59237"),
    },
    "length": {
        "mm": Decimal("1"),
        "毫米": Decimal("1"),
        "cm": Decimal("10"),
        "厘米": Decimal("10"),
        "m": Decimal("1000"),
        "米": Decimal("1000"),
        "in": Decimal("25.4"),
        "inch": Decimal("25.4"),
        "英寸": Decimal("25.4"),
    },
    "current": {
        "ma": Decimal("0.001"),
        "a": Decimal("1"),
        "安": Decimal("1"),
        "安培": Decimal("1"),
    },
    "voltage": {
        "mv": Decimal("0.001"),
        "v": Decimal("1"),
        "伏": Decimal("1"),
        "伏特": Decimal("1"),
    },
    "power": {
        "mw": Decimal("0.001"),
        "w": Decimal("1"),
        "瓦": Decimal("1"),
        "kw": Decimal("1000"),
        "千瓦": Decimal("1000"),
    },
    "capacity_volume": {
        "ml": Decimal("1"),
        "毫升": Decimal("1"),
        "l": Decimal("1000"),
        "升": Decimal("1000"),
    },
    "capacity_charge": {
        "mah": Decimal("1"),
        "毫安时": Decimal("1"),
        "ah": Decimal("1000"),
        "安时": Decimal("1000"),
    },
}

BASE_UNITS = {
    "mass": "g",
    "length": "mm",
    "current": "A",
    "voltage": "V",
    "power": "W",
    "capacity_volume": "mL",
    "capacity_charge": "mAh",
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
    factors = UNIT_FACTORS[family]
    aliases = sorted(factors, key=len, reverse=True)
    pattern = re.compile(
        rf"(?P<number>{_NUMBER})\s*(?P<unit>{'|'.join(re.escape(item) for item in aliases)})",
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
    factor = factors.get(unit)
    if factor is None:
        # Chinese units are unaffected by casefold but retained as a fallback.
        factor = factors.get(match.group("unit"))
    if factor is None:
        return None
    normalized = number * factor
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
    factor = UNIT_FACTORS["length"].get(match.group("unit").casefold())
    if factor is None:
        factor = UNIT_FACTORS["length"].get(match.group("unit"))
    if factor is None:
        return None
    values: list[int | float] = []
    for key in ("a", "b", "c"):
        text = match.group(key)
        if text is None:
            continue
        values.append(_clean_decimal(Decimal(text) * factor))
    return NormalizedValue(values, "mm", ("dimension_order_preserved",))


def normalize_value(field: str, raw: str) -> NormalizedValue:
    family_by_field = {
        "weight": "mass",
        "net_weight": "mass",
        "gross_weight": "mass",
        "length": "length",
        "width": "length",
        "height": "length",
        "current": "current",
        "voltage": "voltage",
        "power": "power",
        "capacity_volume": "capacity_volume",
        "capacity_charge": "capacity_charge",
    }
    if field == "capacity":
        return (
            normalize_number_with_unit(raw, "capacity_charge")
            or normalize_number_with_unit(raw, "capacity_volume")
            or NormalizedValue(normalize_text(raw), None, ("unparsed_capacity",))
        )
    if field == "dimensions":
        return normalize_dimensions(raw) or NormalizedValue(normalize_text(raw), None, ("unparsed_dimensions",))
    if field == "quantity":
        return normalize_count(raw) or NormalizedValue(normalize_text(raw), None, ("unparsed_count",))
    family = family_by_field.get(field)
    if family:
        return normalize_number_with_unit(raw, family) or NormalizedValue(normalize_text(raw), None, ("unparsed_unit",))
    return NormalizedValue(normalize_text(raw), None)


def values_equal(a: Any, b: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tolerance
    return a == b
