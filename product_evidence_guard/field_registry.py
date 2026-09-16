"""Validated, inert field and unit configuration.

The registry is JSON data only.  It cannot import callables or execute code; an
invalid entry rejects the complete registry so extraction never silently runs
with a partial schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import hashlib
import json
import re
from importlib.resources import files
from typing import Any


class FieldRegistryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UnitRule:
    scale: Decimal
    offset: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class UnitFamily:
    name: str
    base_unit: str
    units: dict[str, UnitRule]
    word_aliases: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    name: str
    label: str
    aliases: tuple[str, ...]
    value_type: str
    unit_family: str | None = None
    default_scope: str | None = None
    category: str = "general"
    mapping_confidence: float = 0.95
    allowed_scopes: tuple[str, ...] = ()
    extraction_constraints: dict[str, Any] = field(default_factory=dict)
    scope_aliases: dict[str, str] = field(default_factory=dict)
    scope_policy: str | None = None
    variant_headers: tuple[str, ...] = ()
    review_unstructured_variants: bool = False

    @property
    def scope(self) -> str | None:
        """Compatibility alias used by the v1 extraction surface."""
        return self.default_scope


@dataclass(frozen=True, slots=True)
class FieldRegistry:
    schema_version: int
    fields: tuple[FieldDefinition, ...]
    unit_families: dict[str, UnitFamily]
    fingerprint: str

    def by_name(self, name: str) -> FieldDefinition | None:
        return next((item for item in self.fields if item.name == name), None)


_NAME_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789_")
_VALUE_TYPES = {"text", "number", "dimensions", "count", "capacity"}


def _identifier(value: Any, context: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 64 or any(char not in _NAME_CHARS for char in text):
        raise FieldRegistryError(f"{context} must be a lowercase identifier")
    return text


def _decimal(value: Any, context: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FieldRegistryError(f"{context} must be numeric") from exc
    if not result.is_finite():
        raise FieldRegistryError(f"{context} must be finite")
    return result


def _load_registry() -> FieldRegistry:
    raw = files(__package__).joinpath("field_registry.json").read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FieldRegistryError("field registry is not valid UTF-8 JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise FieldRegistryError("unsupported field registry schema_version")
    if set(data) != {"schema_version", "unit_families", "fields"}:
        raise FieldRegistryError("field registry contains unsupported top-level keys")

    families_data = data.get("unit_families")
    if not isinstance(families_data, dict):
        raise FieldRegistryError("unit_families must be an object")
    families: dict[str, UnitFamily] = {}
    for raw_name, row in families_data.items():
        name = _identifier(raw_name, "unit family name")
        if (
            not isinstance(row, dict)
            or not {"base_unit", "units"} <= set(row)
            or set(row) - {"base_unit", "units", "word_aliases"}
            or not isinstance(row.get("base_unit"), str)
            or not row["base_unit"].strip()
            or len(row["base_unit"]) > 32
        ):
            raise FieldRegistryError(f"unit family {name} is invalid")
        units_data = row.get("units")
        if not isinstance(units_data, dict) or not units_data:
            raise FieldRegistryError(f"unit family {name} has no units")
        units: dict[str, UnitRule] = {}
        for alias, rule in units_data.items():
            # SI symbols are case-sensitive: mW and MW cannot share a rule.
            # Tolerated spellings must be explicit aliases in the registry.
            unit_alias = str(alias).strip()
            if not unit_alias or len(unit_alias) > 32 or unit_alias in units:
                raise FieldRegistryError(f"unit family {name} has an invalid alias")
            if isinstance(rule, dict):
                unknown = set(rule) - {"scale", "offset"}
                if unknown or "scale" not in rule:
                    raise FieldRegistryError(f"unit {alias} has invalid conversion keys")
                scale = _decimal(rule["scale"], f"unit {alias} scale")
                offset = _decimal(rule.get("offset", 0), f"unit {alias} offset")
            else:
                scale = _decimal(rule, f"unit {alias} scale")
                offset = Decimal("0")
            if scale == 0:
                raise FieldRegistryError(f"unit {alias} scale cannot be zero")
            units[unit_alias] = UnitRule(scale=scale, offset=offset)
        # Written unit names are case-insensitive; symbols are not. Keep the
        # distinction explicit instead of guessing from token length/case.
        word_aliases = row.get("word_aliases", {})
        if not isinstance(word_aliases, dict):
            raise FieldRegistryError(f"unit family {name} has invalid word_aliases")
        for alias, symbol in word_aliases.items():
            if (not isinstance(alias, str) or not re.fullmatch(r"[a-z]{3,32}", alias)
                    or not isinstance(symbol, str) or symbol not in units
                    or symbol in word_aliases
                    or any(key.casefold() == alias for key in units)):
                raise FieldRegistryError(f"unit family {name} has invalid word alias {alias!r}")
        units.update({alias: units[symbol] for alias, symbol in word_aliases.items()})
        families[name] = UnitFamily(name, row["base_unit"].strip(), units, dict(word_aliases))

    fields_data = data.get("fields")
    if not isinstance(fields_data, list) or not fields_data:
        raise FieldRegistryError("fields must be a non-empty array")
    definitions: list[FieldDefinition] = []
    names: set[str] = set()
    aliases_seen: dict[str, str] = {}
    allowed_keys = {
        "name", "label", "aliases", "value_type", "unit_family",
        "default_scope", "category", "mapping_confidence",
        "allowed_scopes", "extraction_constraints", "scope_aliases",
        "scope_policy", "variant_headers", "review_unstructured_variants",
    }
    for index, row in enumerate(fields_data):
        if not isinstance(row, dict) or set(row) - allowed_keys:
            raise FieldRegistryError(f"field[{index}] contains unsupported keys")
        name = _identifier(row.get("name"), f"field[{index}].name")
        if name in names:
            raise FieldRegistryError(f"duplicate field name: {name}")
        names.add(name)
        label = str(row.get("label") or "").strip()
        aliases_data = row.get("aliases")
        if not label or not isinstance(aliases_data, list) or not aliases_data:
            raise FieldRegistryError(f"field {name} requires label and aliases")
        aliases: list[str] = []
        scope_aliases = row.get("scope_aliases", {})
        if not isinstance(scope_aliases, dict) or any(
            not isinstance(alias, str) or not isinstance(scope, str)
            or not scope.strip() or len(scope) > 80
            for alias, scope in scope_aliases.items()
        ):
            raise FieldRegistryError(f"field {name} has invalid scope_aliases")
        # Scoped labels are ordinary extraction/header aliases too.
        aliases_data = [*aliases_data, *(alias for alias in scope_aliases if alias not in aliases_data)]
        for alias_value in aliases_data:
            alias = str(alias_value or "").strip()
            folded = alias.casefold()
            if not alias or len(alias) > 80 or folded in aliases_seen:
                owner = aliases_seen.get(folded, name)
                raise FieldRegistryError(f"duplicate/invalid alias {alias!r}: {owner}, {name}")
            aliases_seen[folded] = name
            aliases.append(alias)
        value_type = str(row.get("value_type") or "")
        if value_type not in _VALUE_TYPES:
            raise FieldRegistryError(f"field {name} has invalid value_type")
        family = row.get("unit_family")
        if family is not None and family not in families:
            raise FieldRegistryError(f"field {name} references unknown unit family")
        if value_type == "number" and family is None:
            raise FieldRegistryError(f"numeric field {name} requires unit_family")
        confidence = float(row.get("mapping_confidence", 0.95))
        if not 0 <= confidence <= 1:
            raise FieldRegistryError(f"field {name} has invalid mapping_confidence")
        constraints = row.get("extraction_constraints", {})
        if not isinstance(constraints, dict) or set(constraints) - {
            "max_length", "max_words", "allow_whitespace_separator"
        }:
            raise FieldRegistryError(f"field {name} has invalid extraction_constraints")
        for limit_key in ("max_length", "max_words"):
            if limit_key in constraints and (
                isinstance(constraints[limit_key], bool)
                or not isinstance(constraints[limit_key], int)
                or not 1 <= constraints[limit_key] <= 10000
            ):
                raise FieldRegistryError(f"field {name} has invalid {limit_key}")
        if "allow_whitespace_separator" in constraints and not isinstance(
            constraints["allow_whitespace_separator"], bool
        ):
            raise FieldRegistryError(f"field {name} has invalid allow_whitespace_separator")
        scopes_data = row.get("allowed_scopes", [])
        if not isinstance(scopes_data, list) or any(
            not isinstance(value, str) or not value.strip() or len(value) > 80
            for value in scopes_data
        ):
            raise FieldRegistryError(f"field {name} has invalid allowed_scopes")
        default_scope = row.get("default_scope")
        if default_scope is not None and (
            not isinstance(default_scope, str)
            or not default_scope.strip()
            or len(default_scope) > 80
        ):
            raise FieldRegistryError(f"field {name} has invalid default_scope")
        if any(scope not in scopes_data for scope in scope_aliases.values()):
            raise FieldRegistryError(f"field {name} has an undeclared alias scope")
        scope_policy = row.get("scope_policy")
        if scope_policy not in (None, "electrical"):
            raise FieldRegistryError(f"field {name} has invalid scope_policy")
        variant_headers = row.get("variant_headers", [])
        if not isinstance(variant_headers, list) or any(
            not isinstance(header, str) or header not in aliases
            for header in variant_headers
        ) or len(set(variant_headers)) != len(variant_headers):
            raise FieldRegistryError(f"field {name} has invalid variant_headers")
        review_unstructured = row.get("review_unstructured_variants", False)
        if not isinstance(review_unstructured, bool) or (review_unstructured and not variant_headers):
            raise FieldRegistryError(f"field {name} has invalid review_unstructured_variants")
        definitions.append(FieldDefinition(
            name=name,
            label=label,
            aliases=tuple(aliases),
            value_type=value_type,
            unit_family=family,
            default_scope=(default_scope.strip() if isinstance(default_scope, str) else None),
            category=str(row.get("category") or "general"),
            mapping_confidence=confidence,
            allowed_scopes=tuple(value.strip() for value in scopes_data),
            extraction_constraints=dict(constraints),
            scope_aliases=dict(scope_aliases),
            scope_policy=scope_policy,
            variant_headers=tuple(variant_headers),
            review_unstructured_variants=review_unstructured,
        ))

    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return FieldRegistry(1, tuple(definitions), families, hashlib.sha256(canonical).hexdigest())


REGISTRY = _load_registry()
FIELD_SPECS = REGISTRY.fields


@lru_cache(maxsize=None)
def unit_pattern(family: str) -> str:
    """One case-safe unit grammar for extraction, scopes and normalization."""
    definition = REGISTRY.unit_families[family]
    symbols = sorted(set(definition.units) - set(definition.word_aliases), key=lambda s: (-len(s), s))
    words = sorted(definition.word_aliases, key=lambda s: (-len(s), s))
    parts = ["(?-i:" + "|".join(map(re.escape, symbols)) + ")"]
    if words:
        parts.append("(?i:" + "|".join(map(re.escape, words)) + ")")
    return "(?:" + "|".join(parts) + ")"


def unit_rule(family: str, token: str) -> UnitRule | None:
    definition = REGISTRY.unit_families[family]
    key = token.casefold() if token.casefold() in definition.word_aliases else token
    return definition.units.get(key)


def registry_fingerprint() -> str:
    return REGISTRY.fingerprint


def field_definition(name: str) -> FieldDefinition | None:
    known = REGISTRY.by_name(name)
    if known is not None:
        return known
    # Reversible, task-independent IDs: no mutable global registry and no code
    # supplied by documents. Names survive serialization/restarts unchanged.
    if isinstance(name, str) and name.startswith("custom_") and len(name) <= 391:
        try:
            label = bytes.fromhex(name[7:]).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
        spec = field_for_label(label, known_only=False)
        if spec and spec.name == name:
            return spec
    return None


def split_label_unit(label: str) -> tuple[str, str | None]:
    """Separate an actual unit annotation, never a test condition (STC, 25°C).

    Common opaque SI/industry units are allowed as header metadata without
    claiming the normalizer knows how to convert them. Unknown annotations
    remain part of the open field's identity instead of silently disappearing.
    """
    label = str(label).strip()
    match = re.fullmatch(r"(.+?)\s*(?:\[([^\[\]]{1,32})\]|[（(](.{1,32})[)）])", label)
    if not match:
        return label, None
    unit = (match[2] or match[3]).strip()
    token = unit.casefold().replace(" ", "")
    known = {alias.casefold() for family in REGISTRY.unit_families.values() for alias in family.units}
    # A unit is a unit expression, not arbitrary text between brackets.
    atom = r"(?:[numkgtµμ]?(?:m|g|s|a|v|w|pa|hz|n|j|l|ohm|ω)|min|h|rpm|db(?:\([ac]\))?|ppm|ppb|cp|pcs|%rh|%|°[cf])(?:[²³]|\^?[23])?"
    data_unit = r"(?:[kKMGTPE]i?[Bb]|[Bb])(?:/s)?"
    if (token not in known and not re.fullmatch(rf"{atom}(?:[/·*]{atom})*", token)
            and not re.fullmatch(data_unit, unit)):
        return label, None
    return match[1].strip(), unit


def scope_for_label(field: str, label: str) -> str | None:
    """Scope of an exact explicit label, independent of numeric parsing success."""
    label = split_label_unit(label)[0].casefold()
    spec = REGISTRY.by_name(field)
    if not spec:
        return None
    if label in spec.scope_aliases:
        return spec.scope_aliases[label]
    if spec.scope_policy != "electrical":
        return None
    quantity = {"voltage":("电压", "voltage"), "current":("电流", "current"), "power":("功率", "power")}[field]
    for prefix, signal in (("dc", "dc"), ("ac", "ac"), ("直流", "dc"), ("交流", "ac")):
        if any(label == prefix + (" " if prefix.isascii() else "") + name for name in quantity):
            return "signal:" + signal
    directions = {"输入":"input", "输出":"output", "input":"input", "output":"output"}
    ratings = {"额定":"rated", "标称":"nominal", "最大":"max", "最小":"min", "典型":"typical",
               "rated":"rated", "nominal":"nominal", "maximum":"max", "minimum":"min", "typical":"typical", "max":"max", "min":"min"}
    for word, scope in directions.items():
        if any(label == word + (" " if word.isascii() else "") + name for name in quantity):
            return scope
    for word, scope in ratings.items():
        if any(label == word + (" " if word.isascii() else "") + name for name in quantity):
            return "rating:" + scope
    return None


def field_for_label(label: str, *, known_only: bool = False) -> FieldDefinition | None:
    label = re.sub(r"\s+", " ", str(label).strip()).casefold()
    label = {"噪音": "噪声", "质保期": "保修期", "凈重": "净重", "淨重": "净重"}.get(label, label)
    for spec in FIELD_SPECS:
        if label in {spec.name, *(alias.casefold() for alias in spec.aliases)} or scope_for_label(spec.name, label):
            return spec
    if (known_only or not re.fullmatch(r"[a-z0-9\u3400-\u9fff][a-z0-9\u3400-\u9fff _/()（）%+°.\-]{0,63}", label)
            or not re.search(r"[a-z\u3400-\u9fff]", label)):
        return None
    # A leading measurement condition can qualify a field (25°C容量); a
    # numbered paragraph or a bare measurement (3. Tolerance, 230VAC) cannot.
    if label[0].isdigit() and not re.match(r"^\d+(?:\.\d+)?\s*(?:°[cf]|%)\s*[a-z\u3400-\u9fff]", label):
        return None
    # These are document structure, not product attributes. Their contents are
    # still retained in source blocks for later extraction/coverage reporting.
    if label in {"备注", "说明", "注", "注意", "提示", "note", "notes", "description", "参数", "数值", "单位", "字段", "值", "field", "value", "parameter", "unit",
                 "项目", "内容", "主产品", "资料版本", "文档版本", "测试意图", "示例", "样例", "example", "example only",
                 "file name", "filename", "document version", "electrical data", "mechanical data"}:
        return None
    if re.search(r"(?:^|\s)https?$", label) or re.match(r"^(?:please\s+)?(?:refer\s+to|see\s+)", label):
        return None
    if re.match(r"^(?:figure|fig\.?|table)\s+(?:\d|context\s+line\s+\d)|^[图表]\s*\d+", label):
        return None
    return FieldDefinition("custom_" + label.encode("utf-8").hex(), label, (label,), "text")
