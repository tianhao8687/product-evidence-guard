"""Validated, inert field and unit configuration.

The registry is JSON data only.  It cannot import callables or execute code; an
invalid entry rejects the complete registry so extraction never silently runs
with a partial schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
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
            or set(row) != {"base_unit", "units"}
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
            unit_alias = str(alias).strip().casefold()
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
        families[name] = UnitFamily(name, row["base_unit"].strip(), units)

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
        "scope_policy", "variant_headers",
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
        ))

    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return FieldRegistry(1, tuple(definitions), families, hashlib.sha256(canonical).hexdigest())


REGISTRY = _load_registry()
FIELD_SPECS = REGISTRY.fields


def registry_fingerprint() -> str:
    return REGISTRY.fingerprint


def field_definition(name: str) -> FieldDefinition | None:
    return REGISTRY.by_name(name)
