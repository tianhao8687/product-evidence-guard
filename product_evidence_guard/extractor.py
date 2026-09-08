from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from .field_registry import FIELD_SPECS, FieldDefinition
from .models import FactCandidate, SourceBlock
from .normalization import normalize_text, normalize_value

# Compatibility name retained for integrations that imported FieldSpec.
FieldSpec = FieldDefinition

# Longest aliases first, preventing "重量" from stealing "产品净重".
_ALIAS_INDEX: list[tuple[str, FieldSpec]] = sorted(
    ((alias.casefold(), spec) for spec in FIELD_SPECS for alias in spec.aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)

_NUMERIC_FIELDS = {
    spec.name
    for spec in FIELD_SPECS
    if spec.value_type in {"number", "dimensions", "count", "capacity"}
}
_TEXT_FIELDS = {
    spec.name for spec in FIELD_SPECS if spec.value_type == "text"
}
_ELECTRICAL_LABEL_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    r"(?P<scope>input|output|输入|输出|entrada|salida|입력|출력)"
    r"\s*(?:voltage|current|power|电压|电流|功率)?\s*[:：=]\s*"
    r"(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_ELECTRICAL_TOKEN_PATTERNS = {
    "voltage": re.compile(
        r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?"
        r"(?:\s*(?:-|–|—|~|to|至|到)\s*[-+]?\d+(?:\.\d+)?)?"
        r"\s*(?:mV|V)(?:ac|dc)?(?![A-Za-z])",
        re.IGNORECASE,
    ),
    "current": re.compile(
        r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?\s*(?:mA|A)(?![A-Za-z])",
        re.IGNORECASE,
    ),
    "power": re.compile(
        r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?\s*(?:mW|kW|W)(?![A-Za-z])",
        re.IGNORECASE,
    ),
}
_MODE_CONTEXT_RE = re.compile(
    r"(?:\b(?:by|per)\s+(?:operating\s+)?"
    r"(?:mode|profile|variant)\b|"
    r"按(?:工作)?模式|(?:每种|不同)(?:工作)?模式)",
    re.IGNORECASE,
)
_PROFILE_BEFORE_MODE_RE = re.compile(
    r"(?P<label>[A-Za-z\u3400-\u9fff][A-Za-z0-9\u3400-\u9fff_-]{1,31})"
    r"\s+(?:operating\s+)?(?:mode|profile|variant)\b",
    re.IGNORECASE,
)
_PROFILE_LABEL_RE = re.compile(
    r"^[A-Za-z\u3400-\u9fff][A-Za-z0-9\u3400-\u9fff]*"
    r"(?:[\s_/-][A-Za-z0-9\u3400-\u9fff]+){0,1}$",
)
_PROFILE_LABEL_STOPWORDS = {
    "all",
    "by",
    "per",
    "mode",
    "profile",
    "variant",
    "operating",
    "power",
    "voltage",
    "current",
    "value",
    "values",
    "rated",
    "nominal",
    "typ",
    "typical",
    "min",
    "minimum",
    "max",
    "maximum",
    "is",
    "are",
    "at",
    "for",
    "with",
    "available",
    "ignore",
    "json",
    "only",
    "output",
    "return",
    "instruction",
    "instructions",
    "prompt",
    "system",
    "assistant",
    "user",
    "w",
    "mw",
    "kw",
    "v",
    "mv",
    "a",
    "ma",
}
_RATING_TOKEN_PATTERN = (
    r"(?:rated\b|nominal\b|typ(?:ical)?\b|"
    r"min(?:imum)?\b|max(?:imum)?\b|额定|典型|最小|最大)"
)
_ELECTRICAL_FIELD_TOKEN_PATTERN = (
    r"(?:voltage\b|current\b|power\b|电压|电流|功率)"
)
_OPTIONAL_DIRECTION_PREFIX_PATTERN = (
    r"(?:(?:(?:input|output|entrada|salida)\b|"
    r"输入|输出|입력|출력)\s*)?"
)
_RATING_FIELD_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    + _OPTIONAL_DIRECTION_PREFIX_PATTERN
    + r"(?:"
    + rf"(?P<label_before>{_RATING_TOKEN_PATTERN})\s*"
    + rf"(?P<field_after>{_ELECTRICAL_FIELD_TOKEN_PATTERN})"
    + r"|"
    + rf"(?P<field_before>{_ELECTRICAL_FIELD_TOKEN_PATTERN})\s*"
    + rf"(?P<label_after>{_RATING_TOKEN_PATTERN})"
    + r")",
    re.IGNORECASE,
)
_RATING_MEASUREMENT_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    + _OPTIONAL_DIRECTION_PREFIX_PATTERN
    + rf"(?P<label>{_RATING_TOKEN_PATTERN})",
    re.IGNORECASE,
)
_RATING_TOKEN_RE = re.compile(_RATING_TOKEN_PATTERN, re.IGNORECASE)
_ELECTRICAL_FIELD_TOKEN_RE = re.compile(
    _ELECTRICAL_FIELD_TOKEN_PATTERN,
    re.IGNORECASE,
)
_SCOPE_DELIMITERS_RE = re.compile(
    r"^[\s:：=|,，;；/\\\-–—()\[\]（）【】]*$"
)
_RATING_SCOPE_NAMES = {
    "rated": "rated",
    "额定": "rated",
    "nominal": "nominal",
    "typ": "typical",
    "typical": "typical",
    "典型": "typical",
    "min": "min",
    "minimum": "min",
    "最小": "min",
    "max": "max",
    "maximum": "max",
    "最大": "max",
}
_ELECTRICAL_FIELD_NAMES = {
    "voltage": "voltage",
    "电压": "voltage",
    "current": "current",
    "电流": "current",
    "power": "power",
    "功率": "power",
}
_IO_SCOPE_NAMES = {
    "input": "input",
    "输入": "input",
    "entrada": "input",
    "입력": "input",
    "output": "output",
    "输出": "output",
    "salida": "output",
    "출력": "output",
}
_IO_SCOPE_TOKEN_PATTERN = (
    r"(?:input\b|output\b|entrada\b|salida\b|输入|输出|입력|출력)"
)
_IO_SCOPE_PREFIX_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    rf"(?P<direction>{_IO_SCOPE_TOKEN_PATTERN})"
    r"(?=$|[\s:：=|/(\[（【])",
    re.IGNORECASE,
)
_FIELD_THEN_IO_SCOPE_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?"
    rf"(?P<field>{_ELECTRICAL_FIELD_TOKEN_PATTERN})\s+"
    rf"(?P<direction>{_IO_SCOPE_TOKEN_PATTERN})"
    r"(?=$|[\s:：=|/(\[（【])",
    re.IGNORECASE,
)
_IO_SCOPE_ANY_RE = re.compile(_IO_SCOPE_TOKEN_PATTERN, re.IGNORECASE)
_FREQUENCY_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?"
    r"(?:\s*(?:/|-)\s*[-+]?\d+(?:\.\d+)?)?"
    r"\s*(?:Hz|kHz|MHz)(?![A-Za-z])",
    re.IGNORECASE,
)
_ELECTRICAL_QUALIFIER_TOKEN_RE = re.compile(
    r"(?:AC|DC)(?![A-Za-z])",
    re.IGNORECASE,
)
_ELECTRICAL_UNIT_HEADER_PATTERNS = {
    "voltage": re.compile(
        r"[\(\[（【]\s*(?:mV|V)(?:ac|dc)?\s*[\)\]）】]",
        re.IGNORECASE,
    ),
    "current": re.compile(
        r"[\(\[（【]\s*(?:mA|A)\s*[\)\]）】]",
        re.IGNORECASE,
    ),
    "power": re.compile(
        r"[\(\[（【]\s*(?:mW|W|kW)\s*[\)\]）】]",
        re.IGNORECASE,
    ),
}
_SCOPE_DELIMITER_PREFIX_RE = re.compile(
    r"[\s:：=|,，;；/\\~\-–—]*"
)


def _normalized_profile_label(value: str) -> str | None:
    compact = re.sub(r"\s+", " ", value.strip())
    compact = re.sub(
        r"^[\s:：=|,，;；/\\\-–—()\[\]（）【】]+|"
        r"[\s:：=|,，;；/\\\-–—()\[\]（）【】]+$",
        "",
        compact,
    )
    if not compact or len(compact) > 48:
        return None
    if not _PROFILE_LABEL_RE.fullmatch(compact):
        return None
    normalized = normalize_text(compact)
    words = {
        item
        for item in re.split(r"[\s_/-]+", normalized)
        if item
    }
    if not words or words <= _PROFILE_LABEL_STOPWORDS:
        return None
    if words & _PROFILE_LABEL_STOPWORDS:
        return None
    return normalized


def _profile_gap_is_neutral(field: str, value: str) -> bool:
    if _SCOPE_DELIMITERS_RE.fullmatch(value):
        return True
    unit_header = _ELECTRICAL_UNIT_HEADER_PATTERNS.get(field)
    if unit_header is None:
        return False
    stripped = value.strip()
    return unit_header.fullmatch(stripped) is not None


def _infer_profile_scope(field: str, text: str) -> str | None:
    token_pattern = _ELECTRICAL_TOKEN_PATTERNS.get(field)
    if token_pattern is None:
        return None
    measurements = list(token_pattern.finditer(text))
    if len(measurements) != 1:
        # Without a one-to-one value/label relationship, attaching a profile
        # would guess which mode belongs to which measurement.
        return None

    measurement = measurements[0]
    context_matches = [
        item
        for item in _MODE_CONTEXT_RE.finditer(text)
        if item.start() < measurement.start()
    ]
    labels: list[str] = []
    if context_matches:
        context = context_matches[-1]
        for raw_gap in (
            text[context.end() : measurement.start()],
            text[measurement.end() :],
        ):
            if _profile_gap_is_neutral(field, raw_gap):
                continue
            label = _normalized_profile_label(raw_gap)
            if label is None:
                # A mode/profile relationship is only safe when every token
                # around the measurement is either punctuation, a bracketed
                # unit header, or the single profile label. Never skip over
                # arbitrary prose and attach a convenient trailing label.
                return None
            labels.append(label)

    for match in _PROFILE_BEFORE_MODE_RE.finditer(text):
        if match.end() <= measurement.start():
            label = _normalized_profile_label(match.group("label"))
            if label:
                if not _profile_gap_is_neutral(
                    field,
                    text[match.end() : measurement.start()],
                ):
                    return None
                suffix = text[measurement.end() :]
                if not _profile_gap_is_neutral(field, suffix):
                    suffix_label = _normalized_profile_label(suffix)
                    if suffix_label is None:
                        return None
                    labels.append(suffix_label)
                labels.append(label)

    unique_labels = list(dict.fromkeys(labels))
    if len(unique_labels) != 1:
        return None
    return f"profile:{unique_labels[0]}"


def _infer_rating_scope(field: str, text: str) -> str | None:
    token_pattern = _ELECTRICAL_TOKEN_PATTERNS.get(field)
    if token_pattern is None:
        return None
    measurements = list(token_pattern.finditer(text))
    if len(measurements) != 1:
        return None
    measurement = measurements[0]

    pair = _RATING_FIELD_PREFIX_RE.match(text)
    label: str | None = None
    prefix_end = 0
    if pair is not None:
        field_label = pair.group("field_after") or pair.group("field_before")
        canonical_field = _ELECTRICAL_FIELD_NAMES.get(
            normalize_text(field_label)
        )
        if canonical_field != field:
            return None
        label = pair.group("label_before") or pair.group("label_after")
        prefix_end = pair.end()
    else:
        rating_only = _RATING_MEASUREMENT_PREFIX_RE.match(text)
        if rating_only is None:
            return None
        label = rating_only.group("label")
        prefix_end = rating_only.end()

    if not _SCOPE_DELIMITERS_RE.fullmatch(
        text[prefix_end : measurement.start()]
    ):
        return None
    if not _SCOPE_DELIMITERS_RE.fullmatch(text[measurement.end() :]):
        return None
    canonical_rating = _RATING_SCOPE_NAMES.get(normalize_text(label))
    if canonical_rating is None:
        return None
    return f"rating:{canonical_rating}"


def _io_directions(text: str) -> set[str]:
    return {
        _IO_SCOPE_NAMES[normalize_text(item.group(0))]
        for item in _IO_SCOPE_ANY_RE.finditer(text)
    }


def _infer_io_scope(field: str, text: str) -> str | None:
    directions = _io_directions(text)
    if len(directions) != 1:
        # A single OCR/text row containing both INPUT and OUTPUT does not
        # provide a safe one-to-one ownership relationship for its values.
        return None

    match = _IO_SCOPE_PREFIX_RE.match(text)
    if match is None:
        match = _FIELD_THEN_IO_SCOPE_RE.match(text)
        if match is None:
            return None
        canonical_field = _ELECTRICAL_FIELD_NAMES.get(
            normalize_text(match.group("field"))
        )
        if canonical_field != field:
            return None
    direction = _IO_SCOPE_NAMES.get(normalize_text(match.group("direction")))
    if direction is None or direction not in directions:
        return None

    token_pattern = _ELECTRICAL_TOKEN_PATTERNS.get(field)
    if token_pattern is None:
        return None
    measurement = next(token_pattern.finditer(text), None)
    if measurement is None or measurement.start() < match.end():
        return None
    gap = text[match.end() : measurement.start()]
    if not _io_gap_is_structured(field, gap):
        return None
    return direction


def _io_gap_is_structured(field: str, value: str) -> bool:
    """Accept only label/unit structure between direction and measurement."""

    position = 0
    field_consumed = False
    rating_consumed = False
    mode_context_consumed = False
    while position < len(value):
        delimiter = _SCOPE_DELIMITER_PREFIX_RE.match(value, position)
        assert delimiter is not None
        position = delimiter.end()
        if position >= len(value):
            return True

        if mode_context_consumed:
            trailing_label = _normalized_profile_label(value[position:])
            if trailing_label is not None:
                return True

        if not field_consumed:
            field_match = _ELECTRICAL_FIELD_TOKEN_RE.match(value, position)
            if field_match is not None:
                canonical_field = _ELECTRICAL_FIELD_NAMES.get(
                    normalize_text(field_match.group(0))
                )
                if canonical_field != field:
                    return False
                field_consumed = True
                position = field_match.end()
                continue

        if not rating_consumed:
            rating_match = _RATING_TOKEN_RE.match(value, position)
            if rating_match is not None:
                rating_consumed = True
                position = rating_match.end()
                continue

        if field_consumed and not mode_context_consumed:
            mode_match = _MODE_CONTEXT_RE.match(value, position)
            if mode_match is not None:
                mode_context_consumed = True
                position = mode_match.end()
                continue

        unit_header = _ELECTRICAL_UNIT_HEADER_PATTERNS[field].match(
            value,
            position,
        )
        if unit_header is not None:
            position = unit_header.end()
            continue

        structured_token = _FREQUENCY_TOKEN_RE.match(value, position)
        if structured_token is None:
            structured_token = _ELECTRICAL_QUALIFIER_TOKEN_RE.match(
                value,
                position,
            )
        if structured_token is None:
            candidates = [
                pattern.match(value, position)
                for pattern in _ELECTRICAL_TOKEN_PATTERNS.values()
            ]
            structured_token = next(
                (item for item in candidates if item is not None),
                None,
            )
        if structured_token is None:
            return False
        position = structured_token.end()
    return True


def infer_semantic_scope(field: str, text: str) -> str | None:
    spec = next((spec for spec in FIELD_SPECS if spec.name == field), None)
    if spec is None:
        return None

    # Only a unique, explicit field label establishes a configured scope.
    # Prefer the longest label so e.g. a compound label is not reinterpreted
    # using a shorter substring. Conflicting labels remain unspecified.
    matches = [(alias, scope) for alias, scope in spec.scope_aliases.items()
               if _extract_value_after_alias(text, alias) is not None]
    matches = [(alias, scope) for alias, scope in matches
               if not any(alias.casefold() in longer.casefold() and len(longer) > len(alias)
                          for longer, _ in matches)]
    scopes = {scope for _, scope in matches}
    if scopes:
        return next(iter(scopes)) if len(scopes) == 1 else None
    if spec.scope_policy != "electrical":
        return None

    scopes = [
        item
        for item in (
            _infer_io_scope(field, text),
            _infer_rating_scope(field, text),
            _infer_profile_scope(field, text),
        )
        if item
    ]
    return "|".join(scopes) or None


def _candidate_id(block: SourceBlock, field: str, raw_value: str) -> str:
    payload = f"{block.block_id}\0{field}\0{raw_value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _alias_pattern(alias: str) -> re.Pattern[str]:
    prefix = (
        r"(?<![A-Za-z0-9_])"
        if alias and alias[0].isascii() and alias[0].isalnum()
        else ""
    )
    suffix = (
        r"(?![A-Za-z0-9_])"
        if alias and alias[-1].isascii() and alias[-1].isalnum()
        else ""
    )
    return re.compile(prefix + re.escape(alias) + suffix, re.IGNORECASE)


def _table_cell_matches_alias(cell: str, alias: str) -> bool:
    compact = re.sub(r"\s+", " ", cell.strip())
    pattern = _alias_pattern(alias)
    match = pattern.fullmatch(compact)
    if match:
        return True
    # Unit annotations are common in real spreadsheets: "Weight (kg)".
    return bool(
        re.fullmatch(
            rf"{pattern.pattern}\s*[\(\（][^()\n\r]{{1,32}}[\)\）]",
            compact,
            re.IGNORECASE,
        )
    )


def _extract_value_after_alias(text: str, alias: str) -> tuple[str, str] | None:
    # Prefer explicit table cells. Merely mentioning "colour coded" or
    # "power supply" in a prose cell must not turn the rest of the row into a
    # product fact.
    if "|" in text or "\t" in text:
        parts = [
            part.strip()
            for part in re.split(r"\s*(?:\||\t)\s*", text)
            if part.strip()
        ]
        for pos, part in enumerate(parts):
            if not _table_cell_matches_alias(part, alias):
                continue
            if pos + 1 < len(parts):
                return parts[pos + 1], "table"
            if pos > 0:
                return parts[pos - 1], "table"

    pattern = _alias_pattern(alias)
    leading = re.match(r"^\s*(?:[-*•]\s*)?", text)
    start = leading.end() if leading else 0
    match = pattern.match(text, start)
    if not match:
        return None

    tail = text[match.end() :]
    unit_annotation = re.match(r"^\s*[（(]([^()（）\n\r]{1,32})[)）]\s*", tail)
    unit_suffix = ""
    if unit_annotation:
        unit_suffix = " " + unit_annotation[1]
        tail = tail[unit_annotation.end():]
    explicit = re.match(r"^\s*(?:[:：=]|->)\s*(?P<value>.+?)\s*$", tail)
    if explicit:
        return explicit.group("value") + unit_suffix, "explicit"

    whitespace = re.match(r"^\s+(?P<value>.+?)\s*$", tail)
    if whitespace:
        return whitespace.group("value") + unit_suffix, "whitespace"
    return None


def _value_is_plausible(
    spec: FieldSpec,
    raw_value: str,
    normalized_value: object,
    normalized_unit: str | None,
    notes: tuple[str, ...],
    structure: str,
) -> bool:
    compact = re.sub(r"\s+", " ", raw_value.strip())
    if not compact or len(compact) > 256 or "|" in compact:
        return False

    if spec.value_type in {"number", "dimensions", "count", "capacity"}:
        if normalized_unit is None:
            return False
        if any(note.startswith("unparsed_") for note in notes):
            return False
        return isinstance(normalized_value, (int, float, list))

    if spec.value_type != "text":
        return False
    constraints = spec.extraction_constraints
    if (
        structure == "whitespace"
        and not constraints.get("allow_whitespace_separator", False)
    ):
        return False
    if len(compact) > int(constraints.get("max_length", 80)):
        return False
    if len(compact.split()) > int(constraints.get("max_words", 6)):
        return False
    if re.search(r"[\r\n;；]", compact):
        return False
    return bool(re.search(r"[A-Za-z0-9\u3400-\u9fff]", compact))


def _electrical_scope(text: str) -> str:
    lowered = text.casefold()
    if lowered in {"input", "输入", "entrada", "입력"}:
        return "input"
    return "output"


def _extract_labeled_electrical_candidates(block: SourceBlock) -> list[FactCandidate]:
    match = _ELECTRICAL_LABEL_RE.match(block.text)
    if not match:
        return []
    if len(_io_directions(block.text)) != 1:
        return []

    value_text = match.group("value")
    scope = _electrical_scope(match.group("scope"))
    result: list[FactCandidate] = []
    specs = {spec.name: spec for spec in FIELD_SPECS}
    for field, token_pattern in _ELECTRICAL_TOKEN_PATTERNS.items():
        tokens = [item.group(0).strip() for item in token_pattern.finditer(value_text)]
        if not tokens:
            continue
        normalized_rows = [normalize_value(field, token) for token in tokens]
        valid_rows = [
            (token, normalized)
            for token, normalized in zip(tokens, normalized_rows, strict=True)
            if normalized.unit is not None
            and not any(note.startswith("unparsed_") for note in normalized.notes)
        ]
        if not valid_rows:
            continue

        raw_values: list[str] = []
        raw_values_casefolded: set[str] = set()
        normalized_values: list[object] = []
        notes: list[str] = []
        for token, normalized in valid_rows:
            token_casefolded = token.casefold()
            if token_casefolded in raw_values_casefolded:
                continue
            raw_values.append(token)
            raw_values_casefolded.add(token_casefolded)
            normalized_values.append(normalized.value)
            for note in normalized.notes:
                if note not in notes:
                    notes.append(note)

        if not raw_values:
            continue
        raw_value = "; ".join(raw_values)
        normalized_value: object = normalized_values[0]
        if len(normalized_values) > 1:
            normalized_value = normalized_values
            notes.append("alternative_values_preserved")
        spec = specs[field]
        result.append(
            FactCandidate(
                candidate_id=_candidate_id(block, field, raw_value),
                field=field,
                field_label=spec.label,
                raw_value=raw_value,
                normalized_value=normalized_value,
                normalized_unit=valid_rows[0][1].unit,
                source_block_id=block.block_id,
                source_file=block.source_file,
                source_kind=block.source_kind,
                file_hash=block.file_hash,
                locator=block.locator,
                raw_text=block.text,
                recognition_confidence=block.recognition_confidence,
                mapping_confidence=0.97,
                extraction_method="deterministic_labeled_unit_mapping",
                scope=scope,
                notes=notes,
                provenance=dict(block.provenance),
            )
        )
    return result


def extract_rule_candidates(block: SourceBlock) -> list[FactCandidate]:
    text = block.text.strip()
    if not text:
        return []
    if (
        len(_io_directions(text)) > 1
        and (
            _IO_SCOPE_PREFIX_RE.match(text) is not None
            or _FIELD_THEN_IO_SCOPE_RE.match(text) is not None
        )
    ):
        # A combined INPUT/OUTPUT row needs segmentation before ownership can
        # be assigned. Returning no deterministic candidates is safer than
        # labeling every value with whichever direction appeared first.
        return []
    lowered = text.casefold()
    candidates = _extract_labeled_electrical_candidates(block)
    fields_seen = {candidate.field for candidate in candidates}

    for alias, spec in _ALIAS_INDEX:
        if spec.name in fields_seen or alias not in lowered:
            continue
        extracted = _extract_value_after_alias(text, alias)
        if not extracted:
            continue
        raw_value, structure = extracted
        # Keep one compact logical value rather than swallowing the next
        # sentence from a paragraph-style source block.
        raw_value = re.split(r"[\n\r;；]", raw_value, maxsplit=1)[0].strip()
        if not raw_value:
            continue
        normalized = normalize_value(spec.name, raw_value)
        if not _value_is_plausible(
            spec,
            raw_value,
            normalized.value,
            normalized.unit,
            normalized.notes,
            structure,
        ):
            continue
        candidate = FactCandidate(
            candidate_id=_candidate_id(block, spec.name, raw_value),
            field=spec.name,
            field_label=spec.label,
            raw_value=raw_value,
            normalized_value=normalized.value,
            normalized_unit=normalized.unit,
            source_block_id=block.block_id,
            source_file=block.source_file,
            source_kind=block.source_kind,
            file_hash=block.file_hash,
            locator=block.locator,
            raw_text=block.text,
            recognition_confidence=block.recognition_confidence,
            mapping_confidence=spec.mapping_confidence,
            extraction_method="deterministic_rule",
            scope=infer_semantic_scope(spec.name, block.text) or spec.scope,
            notes=list(normalized.notes),
            provenance=dict(block.provenance),
        )
        candidates.append(candidate)
        fields_seen.add(spec.name)
    return candidates


def extract_candidates(blocks: Iterable[SourceBlock]) -> list[FactCandidate]:
    result: list[FactCandidate] = []
    for block in blocks:
        result.extend(extract_rule_candidates(block))
    return result


def field_schema_for_prompt() -> str:
    schema = [
        {
            "field": spec.name,
            "label": spec.label,
            "aliases": list(spec.aliases),
            "scope": spec.scope,
            "value_type": spec.value_type,
            "unit_family": spec.unit_family,
            "category": spec.category,
            "allowed_scopes": list(spec.allowed_scopes),
            "scope_aliases": dict(spec.scope_aliases),
            "scope_policy": spec.scope_policy,
            "variant_headers": list(spec.variant_headers),
            "extraction_constraints": dict(spec.extraction_constraints),
        }
        for spec in FIELD_SPECS
    ]
    return json.dumps(schema, ensure_ascii=False)
