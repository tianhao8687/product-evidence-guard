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


def _numeric_text(raw: str) -> str | None:
    # PDF/OCR typography must not turn a negative value into a positive one.
    raw = raw.translate(str.maketrans({"−": "-", "﹣": "-", "－": "-", "＋": "+", "～": "~"}))
    # Never start matching inside a comma-separated number (1,000g -> 0g).
    for token in re.findall(r"\d[\d,]*,\d[\d,]*(?:\.\d+)?", raw):
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", token):
            return None
        raw = raw.replace(token, token.replace(",", ""))
    return raw


_BOUND_PREFIX = {
    "不小于": "≥", "不大于": "≤", "不得超过": "≤",
    "no more than": "≤", "not more than": "≤", "no less than": "≥", "not less than": "≥",
    "at least": "≥", "at most": "≤", "less than": "<", "greater than": ">", "up to": "≤",
    "不超过": "≤", "不高于": "≤", "至多": "≤", "最多": "≤",
    "不少于": "≥", "不低于": "≥", "至少": "≥",
    "小于": "<", "低于": "<", "大于": ">", "高于": ">",
}
_BOUND_SUFFIX = {"以下": "≤", "以内": "≤", "以上": "≥"}


def _measurement_context_safe(raw: str, spans: list[tuple[int, int]], family: str) -> bool:
    """Never silently discard another value, tolerance or compound unit.

    Keep harmless labels/order notes, plus the standard mains-frequency suffix.
    This checks the *whole* value after recognized measurements are removed.
    """
    for _, end in spans:
        if re.match(r"\s*(?:(?:/|per\b|每|[·⋅])\s*[A-Za-z\u3400-\u9fff]|[²³^])", raw[end:], re.I):
            return False
    remainder = raw
    for start, end in reversed(spans):
        remainder = remainder[:start] + " " + remainder[end:]
    if family == "voltage":
        remainder = re.sub(rf"~?\s*{_NUMBER}(?:\s*[-~]\s*{_NUMBER})?\s*Hz\b", "", remainder, flags=re.I)
    return not re.search(r"\d|[±≤≥<>≈]|\+/-", remainder)


def normalize_number_with_unit(raw: str, family: str) -> NormalizedValue | None:
    raw = _numeric_text(raw)
    if raw is None:
        return None
    rules = UNIT_RULES.get(family)
    if rules is None:
        return None
    aliases = sorted(rules, key=len, reverse=True)
    unit_pattern = "|".join(re.escape(item) for item in aliases)
    for word, operator in _BOUND_PREFIX.items():
        raw = re.sub(re.escape(word) + r"\s*(?=[+\-]?\d)", operator, raw, flags=re.I)
    for word, operator in _BOUND_SUFFIX.items():
        raw = re.sub(rf"({_NUMBER}\s*(?:{unit_pattern}))\s*{word}", operator + r"\1", raw, flags=re.I)
    # A tolerance may share its unit: 300±5g, or use mixed units: 0.3kg±5g.
    tolerance_pattern = re.compile(
        rf"(?<![A-Za-z0-9_.,])(?P<center>{_NUMBER})\s*(?P<unit>{unit_pattern})?\s*"
        rf"(?:[(（]\s*)?(?:±|\+/-)\s*(?P<delta>{_NUMBER})\s*(?P<delta_unit>{unit_pattern})?(?![A-Za-z0-9_.%])\s*[)）]?",
        re.I,
    )
    tolerance = tolerance_pattern.search(raw)
    if tolerance:
        center_unit = tolerance["unit"] or tolerance["delta_unit"]
        if not center_unit or not _measurement_context_safe(raw, [tolerance.span()], family):
            return None
        if not tolerance["delta_unit"] and re.match(r"\s*[A-Za-z]", raw[tolerance.end():]):
            return None
        center_rule = rules[center_unit.casefold()]
        delta_rule = rules[(tolerance["delta_unit"] or center_unit).casefold()]
        center = Decimal(tolerance["center"]) * center_rule.scale + center_rule.offset
        delta = Decimal(tolerance["delta"]) * delta_rule.scale
        if delta < 0 or "%" in raw[tolerance.end():]:
            return None
        return NormalizedValue(f"{_clean_decimal(center)}±{_clean_decimal(delta)}", BASE_UNITS[family], ("qualifier_preserved",))
    range_pattern = re.compile(
        rf"(?<![A-Za-z0-9_.,])(?P<start>{_NUMBER})\s*"
        rf"(?P<start_unit>{unit_pattern})?\s*"
        rf"(?:-|–|—|~|to|至|到)\s*"
        rf"(?P<end>{_NUMBER})\s*"
        rf"(?P<end_unit>{unit_pattern})" + (r"(?:ac|dc)?" if family in {"voltage", "current"} else "") + r"(?![A-Za-z])",
        re.IGNORECASE,
    )
    range_match = range_pattern.search(raw)
    if range_match:
        all_ranges = list(range_pattern.finditer(raw))
        if not _measurement_context_safe(raw, [m.span() for m in all_ranges], family):
            return None
        converted_ranges = [
            [Decimal(m["start"]) * rules[(m["start_unit"] or m["end_unit"]).casefold()].scale
             + rules[(m["start_unit"] or m["end_unit"]).casefold()].offset,
             Decimal(m["end"]) * rules[m["end_unit"].casefold()].scale + rules[m["end_unit"].casefold()].offset]
            for m in all_ranges
        ]
        if any(any(abs(a - b) > Decimal("0.000001") for a, b in zip(converted_ranges[0], pair))
               for pair in converted_ranges[1:]):
            return None
        if re.search(r"[≤≥<>±≈]|约|\+/-", raw):
            return None
        return NormalizedValue(
            [_clean_decimal(number) for number in converted_ranges[0]],
            BASE_UNITS[family], ("range_preserved",),
        )

    pattern = re.compile(
        rf"(?<![A-Za-z0-9_.,])(?P<number>{_NUMBER})\s*(?P<unit>{unit_pattern})"
        + (r"(?:ac|dc)?" if family in {"voltage", "current"} else "") + r"(?![A-Za-z])",
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
    prefix = raw[:match.start()].strip()
    qualifier = re.search(r"(<=|>=|≤|≥|<|>|约|大约|≈|~|about|approx\.?)\s*$", prefix, re.I)
    if qualifier and re.search(r"(?:不|非|not)\s*$", prefix[:qualifier.start()], re.I):
        return None
    context_start = qualifier.start() if qualifier else match.start()
    if not _measurement_context_safe(raw, [(context_start, match.end())], family):
        return None
    if len(list(pattern.finditer(raw))) > 1:
        return None  # alternatives must not silently become the first value
    if qualifier:
        operator = {"<=": "≤", ">=": "≥", "约": "≈", "大约": "≈", "~": "≈", "about": "≈", "approx": "≈", "approx.": "≈"}.get(qualifier[1].lower(), qualifier[1])
        return NormalizedValue(f"{operator}{_clean_decimal(normalized)}", BASE_UNITS[family], ("qualifier_preserved",))
    return NormalizedValue(_clean_decimal(normalized), BASE_UNITS[family])


def normalize_count(raw: str) -> NormalizedValue | None:
    raw = _numeric_text(raw)
    if raw is None:
        return None
    match = re.fullmatch(rf"(?P<number>{_NUMBER})\s*(?:个|件|只|套|pcs?|pieces?|pack)?", raw.strip(), re.IGNORECASE)
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
    raw = _numeric_text(raw)
    if raw is None or re.search(r"[≤≥<>±≈]|约", raw):
        return None  # preserve unsupported dimensional constraints as text
    pattern = re.compile(
        rf"(?P<a>{_NUMBER})\s*[x×*]\s*(?P<b>{_NUMBER})(?:\s*[x×*]\s*(?P<c>{_NUMBER}))?\s*"
        rf"(?P<unit>mm|cm|m|in|inch|毫米|厘米|米|英寸)",
        re.IGNORECASE,
    )
    match = pattern.search(raw)
    if not match:
        return None
    matches = list(pattern.finditer(raw))
    if not _measurement_context_safe(raw, [item.span() for item in matches], "length"):
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
    for alternative in matches[1:]:
        other_rule = UNIT_RULES["length"][alternative["unit"].casefold()]
        other = [_clean_decimal(Decimal(alternative[key]) * other_rule.scale + other_rule.offset)
                 for key in ("a", "b", "c") if alternative[key] is not None]
        if len(values) != len(other) or any(abs(a - b) > 1e-6 for a, b in zip(values, other)):
            return None
    return NormalizedValue(values, "mm", ("dimension_order_preserved",))


def normalize_value(field: str, raw: str) -> NormalizedValue:
    definition = field_definition(field)
    if field.startswith("custom_"):
        text = normalize_text(raw)
        # Spacing is cosmetic; unfamiliar units are retained, never converted.
        text = re.sub(r"(?<=\d)\s+(?=[a-z%°\u3400-\u9fff])", "", text)
        # Calendar periods are not fixed numbers of seconds. Only exact,
        # whole-year/month expressions are interchangeable here.
        period = re.fullmatch(r"(\d+)\s*(年|years?|个月|月|months?)", text)
        if period:
            months = int(period[1]) * (12 if period[2] in {"年", "year", "years"} else 1)
            text = f"{months // 12}年" if months % 12 == 0 else f"{months}个月"
        return NormalizedValue(text, None)
    if field == "color":
        text = normalize_text(raw)
        colors = {"black": "黑色", "white": "白色", "red": "红色", "blue": "蓝色",
                  "green": "绿色", "yellow": "黄色", "orange": "橙色", "grey": "灰色", "gray": "灰色"}
        return NormalizedValue(colors.get(text, text), None)
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


def invalid_fact_value(field: str, raw: str, value: Any) -> bool:
    """Reject missing/physically impossible values, not unusual valid specs."""
    if normalize_text(raw) in {"待定", "待确认", "待补充", "未知", "未提供", "tbd", "tbc", "n/a", "-", "--", "暂无"}:
        return True
    spec = field_definition(field)
    nonnegative = field == "quantity" or bool(spec and spec.unit_family in {
        "mass", "length", "capacity_volume", "capacity_charge", "duration"})
    values = value if isinstance(value, list) else [value]
    if spec and spec.value_type != "dimensions" and isinstance(value, list) and len(value) == 2:
        is_range = re.search(r"\d(?:\s*[A-Za-z°℃℉]+)?\s*(?:[-~–—]|to|至|到)\s*[+\-]?\d", _numeric_text(raw) or "", re.I)
        if is_range and all(isinstance(v, (int, float)) for v in value) and value[0] > value[1]:
            return True  # an inverted measurement range cannot be silently approved
    return nonnegative and any(isinstance(v, (int, float)) and v < 0 for v in values)


def values_equal(a: Any, b: Any, tolerance: float = 1e-6) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tolerance
    return a == b


def qualified_values_compatible(a: Any, b: Any) -> bool:
    """Detect non-contradictory bounds without claiming exact equivalence."""
    def bounds(value):
        if isinstance(value, (int, float)):
            return float(value), float(value)
        text = str(value)
        match = re.fullmatch(rf"([≤≥<>≈])({_NUMBER})", text)
        if match:
            op, number = match[1], float(match[2])
            if op == "≈":
                return None  # no invented tolerance for 'approximately'
            if op in {"≤", "≥"}:
                return (-float("inf"), number) if op == "≤" else (number, float("inf"))
        match = re.fullmatch(rf"({_NUMBER})±({_NUMBER})", text)
        if match:
            return float(match[1]) - float(match[2]), float(match[1]) + float(match[2])
        return None
    left, right = bounds(a), bounds(b)
    return bool(left and right and max(left[0], right[0]) <= min(left[1], right[1]))
