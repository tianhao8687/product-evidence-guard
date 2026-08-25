from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from math import isfinite
import re
from typing import Any, Callable, Generic, Mapping, Sequence, TypeVar

from .extractor import FIELD_SPECS


SCHEMA_VERSION = 1
MAX_MODEL_OUTPUT_CHARS = 100_000
MAX_TRANSCRIPTION_ITEMS = 64
MAX_MAPPING_ITEMS = 128
MAX_RAW_TEXT_CHARS = 500
MAX_RAW_VALUE_CHARS = 256
MAX_IDENTIFIER_CHARS = 64

POSITION_PRECISIONS = frozenset({"approximate", "unavailable"})
LEGIBILITY_VALUES = frozenset({"clear", "uncertain"})
MODEL_CONFIDENCE_SOURCE = "model_self_assessment"
ALLOWED_FIELDS = frozenset(spec.name for spec in FIELD_SPECS)

JsonRepairCallback = Callable[[str], str | bytes | None]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_VISUAL_KEYS = frozenset(
    {
        "id",
        "raw_text",
        "bbox_1000",
        "position_precision",
        "legibility",
        "confidence_estimate",
        "confidence_source",
    }
)
_MAPPING_KEYS = frozenset(
    {
        "transcription_id",
        "field",
        "raw_value",
        "mapping_confidence_estimate",
        "confidence_source",
    }
)
_WRAPPER_KEYS = frozenset({"schema_version", "items"})


@dataclass(frozen=True, slots=True)
class ModelOutputIssue:
    """A safe, structured error produced while parsing untrusted model output."""

    stage: str
    code: str
    message: str
    item_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class VisualTranscriptionItem:
    id: str
    raw_text: str
    bbox_1000: tuple[int, int, int, int] | None
    position_precision: str
    legibility: str
    confidence_estimate: float
    confidence_source: str = MODEL_CONFIDENCE_SOURCE

    @property
    def recognition_confidence(self) -> float:
        return self.confidence_estimate

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["bbox_1000"] = list(self.bbox_1000) if self.bbox_1000 is not None else None
        return data


@dataclass(frozen=True, slots=True)
class FieldMappingItem:
    transcription_id: str
    field: str
    raw_value: str
    mapping_confidence_estimate: float
    confidence_source: str = MODEL_CONFIDENCE_SOURCE

    @property
    def mapping_confidence(self) -> float:
        return self.mapping_confidence_estimate

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


ItemT = TypeVar("ItemT")


@dataclass(slots=True)
class ModelOutputResult(Generic[ItemT]):
    """Validated items plus non-throwing diagnostics for one model response."""

    stage: str
    raw_output: str
    items: list[ItemT] = field(default_factory=list)
    errors: list[ModelOutputIssue] = field(default_factory=list)
    json_payload: str | None = None
    repair_attempted: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        serialized_items = [
            item.to_dict() if hasattr(item, "to_dict") else item  # type: ignore[union-attr]
            for item in self.items
        ]
        return {
            "stage": self.stage,
            "ok": self.ok,
            "items": serialized_items,
            "errors": [error.to_dict() for error in self.errors],
            "json_payload": self.json_payload,
            "repair_attempted": self.repair_attempted,
        }


@dataclass(frozen=True, slots=True)
class _DecodedDocument:
    value: Any
    payload: str


def _issue(stage: str, code: str, message: str, item_index: int | None = None) -> ModelOutputIssue:
    return ModelOutputIssue(stage=stage, code=code, message=message, item_index=item_index)


def _coerce_output_text(value: Any, stage: str) -> tuple[str, ModelOutputIssue | None]:
    if isinstance(value, str):
        text = value
    elif isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return "", _issue(stage, "invalid_utf8", "模型输出不是有效的 UTF-8 文本。")
    else:
        return "", _issue(stage, "invalid_output_type", "模型输出必须是字符串或 UTF-8 字节。")

    if len(text) > MAX_MODEL_OUTPUT_CHARS:
        return "", _issue(
            stage,
            "output_too_long",
            f"模型输出超过 {MAX_MODEL_OUTPUT_CHARS} 个字符的安全上限。",
        )
    text = text.lstrip("\ufeff").strip()
    if not text:
        return "", _issue(stage, "empty_output", "模型返回了空输出。")
    return text, None


def _candidate_sources(text: str) -> list[str]:
    # The top-level scanner already finds a JSON container inside a Markdown
    # fence. Extracting fenced substrings separately would be unsafe because a
    # fence nested inside an invalid wrapper could then be promoted to a
    # synthetic top-level document.
    return [text]


def _top_level_container_offsets(source: str) -> list[int]:
    """Return JSON object/array starts that are not nested in another container.

    Model responses may contain prose or Markdown around one JSON document, so
    decoding only at offset zero would be unnecessarily brittle. Scanning every
    opening bracket is unsafe, though: an invalid or truncated wrapper could
    otherwise expose its nested ``items`` array as a second, apparently valid
    top-level response. Track container nesting lexically so only genuine
    top-level candidates are handed to ``raw_decode``.
    """

    offsets: list[int] = []
    expected_closers: list[str] = []
    in_string = False
    escaped = False

    for offset, character in enumerate(source):
        if not expected_closers:
            if character == "{":
                offsets.append(offset)
                expected_closers.append("}")
            elif character == "[":
                offsets.append(offset)
                expected_closers.append("]")
            continue

        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue

        if character == '"':
            in_string = True
        elif character == "{":
            expected_closers.append("}")
        elif character == "[":
            expected_closers.append("]")
        elif character in "}]":
            if character == expected_closers[-1]:
                expected_closers.pop()
            else:
                # Once a container is malformed, its later boundaries are
                # ambiguous. Reject the remainder instead of risking promotion
                # of a nested array after the mismatched closer.
                break

    return offsets


def _decode_documents(text: str) -> list[_DecodedDocument]:
    decoder = json.JSONDecoder()
    documents: list[_DecodedDocument] = []
    payloads_seen: set[str] = set()

    for source in _candidate_sources(text):
        for offset in _top_level_container_offsets(source):
            try:
                value, consumed = decoder.raw_decode(source[offset:])
            except (json.JSONDecodeError, RecursionError):
                continue
            if not isinstance(value, (list, dict)):
                continue
            # Only retain plausible top-level documents. When generation is
            # truncated, a permissive raw_decode scan can otherwise mistake an
            # inner bbox such as [120, 240, 560, 330] for the response array
            # and emit misleading item errors instead of invoking the single
            # JSON-syntax repair attempt.
            if isinstance(value, dict) and not (
                {"items", "schema_version"} & set(value)
            ):
                continue
            if isinstance(value, list) and value and not all(
                isinstance(item, dict) for item in value
            ):
                continue
            payload = source[offset : offset + consumed]
            if payload in payloads_seen:
                continue
            payloads_seen.add(payload)
            documents.append(_DecodedDocument(value=value, payload=payload))
    return documents


def _decode_with_optional_repair(
    raw_output: Any,
    *,
    stage: str,
    repair_callback: JsonRepairCallback | None,
) -> tuple[str, list[_DecodedDocument], list[ModelOutputIssue], bool]:
    text, text_error = _coerce_output_text(raw_output, stage)
    if text_error is not None:
        return text, [], [text_error], False

    documents = _decode_documents(text)
    if documents or repair_callback is None:
        if documents:
            return text, documents, [], False
        return text, [], [_issue(stage, "invalid_json", "模型输出中没有可解析的 JSON 对象或数组。")], False

    # A repair callback is allowed exactly once, and only for JSON syntax/extraction
    # failures. Valid JSON with a bad schema never reaches this branch.
    try:
        repaired_output = repair_callback(text)
    except Exception as exc:
        return (
            text,
            [],
            [_issue(stage, "json_repair_failed", f"JSON 格式修复失败：{type(exc).__name__}。")],
            True,
        )

    repaired_text, repaired_error = _coerce_output_text(repaired_output, stage)
    if repaired_error is not None:
        return text, [], [repaired_error], True
    repaired_documents = _decode_documents(repaired_text)
    if not repaired_documents:
        return (
            text,
            [],
            [_issue(stage, "invalid_json_after_repair", "一次 JSON 格式修复后仍无法解析模型输出。")],
            True,
        )
    return text, repaired_documents, [], True


def _select_items_container(
    documents: Sequence[_DecodedDocument],
    *,
    stage: str,
) -> tuple[list[Any] | None, str | None, list[ModelOutputIssue]]:
    first_schema_error: tuple[str, ModelOutputIssue] | None = None

    for document in documents:
        value = document.value
        if isinstance(value, list):
            return value, document.payload, []
        if not isinstance(value, dict) or not ({"items", "schema_version"} & set(value)):
            continue

        extra_keys = set(value) - _WRAPPER_KEYS
        missing_keys = _WRAPPER_KEYS - set(value)
        if extra_keys or missing_keys:
            error = _issue(
                stage,
                "invalid_wrapper_schema",
                "顶层对象必须且只能包含 schema_version 和 items。",
            )
            first_schema_error = first_schema_error or (document.payload, error)
            continue
        if value.get("schema_version") != SCHEMA_VERSION:
            error = _issue(stage, "unsupported_schema_version", "不支持的模型输出 schema_version。")
            first_schema_error = first_schema_error or (document.payload, error)
            continue
        if not isinstance(value.get("items"), list):
            error = _issue(stage, "items_not_array", "顶层 items 必须是数组。")
            first_schema_error = first_schema_error or (document.payload, error)
            continue
        return value["items"], document.payload, []

    if first_schema_error is not None:
        payload, error = first_schema_error
        return None, payload, [error]
    return None, None, [_issue(stage, "invalid_top_level", "模型 JSON 顶层必须是数组或 schema 包装对象。")]


def _required_string(
    row: Mapping[str, Any],
    key: str,
    *,
    stage: str,
    item_index: int,
    maximum: int,
) -> tuple[str | None, ModelOutputIssue | None]:
    value = row.get(key)
    if not isinstance(value, str):
        return None, _issue(stage, "invalid_string", f"{key} 必须是字符串。", item_index)
    value = value.strip()
    if not value:
        return None, _issue(stage, "empty_string", f"{key} 不得为空。", item_index)
    if len(value) > maximum:
        return None, _issue(stage, "string_too_long", f"{key} 超过 {maximum} 个字符。", item_index)
    return value, None


def _identifier(
    row: Mapping[str, Any],
    key: str,
    *,
    stage: str,
    item_index: int,
) -> tuple[str | None, ModelOutputIssue | None]:
    value, error = _required_string(
        row,
        key,
        stage=stage,
        item_index=item_index,
        maximum=MAX_IDENTIFIER_CHARS,
    )
    if error is not None or value is None:
        return value, error
    if not _IDENTIFIER_RE.fullmatch(value):
        return None, _issue(
            stage,
            "invalid_identifier",
            f"{key} 只能包含字母、数字、点、下划线、冒号和连字符。",
            item_index,
        )
    return value, None


def _confidence(
    row: Mapping[str, Any],
    key: str,
    *,
    stage: str,
    item_index: int,
) -> tuple[float | None, ModelOutputIssue | None]:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, _issue(stage, "invalid_confidence", f"{key} 必须是 0 到 1 的有限数值。", item_index)
    number = float(value)
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        return None, _issue(stage, "invalid_confidence", f"{key} 必须是 0 到 1 的有限数值。", item_index)
    return number, None


def _validate_confidence_source(
    row: Mapping[str, Any],
    *,
    stage: str,
    item_index: int,
) -> ModelOutputIssue | None:
    if row.get("confidence_source") != MODEL_CONFIDENCE_SOURCE:
        return _issue(
            stage,
            "invalid_confidence_source",
            f"confidence_source 必须是 {MODEL_CONFIDENCE_SOURCE}。",
            item_index,
        )
    return None


def _validate_bbox(
    value: Any,
    *,
    stage: str,
    item_index: int,
) -> tuple[tuple[int, int, int, int] | None, ModelOutputIssue | None]:
    if value is None:
        return None, None
    if not isinstance(value, list) or len(value) != 4:
        return None, _issue(stage, "invalid_bbox", "bbox_1000 必须是 null 或包含四个整数的数组。", item_index)
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        return None, _issue(stage, "invalid_bbox", "bbox_1000 坐标必须是整数。", item_index)
    if any(item < 0 or item > 1000 for item in value):
        return None, _issue(stage, "invalid_bbox", "bbox_1000 坐标必须位于 0 到 1000。", item_index)
    left, top, right, bottom = value
    if left >= right or top >= bottom:
        return None, _issue(stage, "invalid_bbox_order", "bbox_1000 必须满足 left < right 且 top < bottom。", item_index)
    return (left, top, right, bottom), None


def parse_visual_transcription_output(
    raw_output: Any,
    *,
    repair_callback: JsonRepairCallback | None = None,
) -> ModelOutputResult[VisualTranscriptionItem]:
    stage = "visual_transcription"
    text, documents, decode_errors, repair_attempted = _decode_with_optional_repair(
        raw_output,
        stage=stage,
        repair_callback=repair_callback,
    )
    result: ModelOutputResult[VisualTranscriptionItem] = ModelOutputResult(
        stage=stage,
        raw_output=text,
        errors=decode_errors,
        repair_attempted=repair_attempted,
    )
    if decode_errors:
        return result

    rows, payload, container_errors = _select_items_container(documents, stage=stage)
    result.json_payload = payload
    result.errors.extend(container_errors)
    if rows is None:
        return result
    if len(rows) > MAX_TRANSCRIPTION_ITEMS:
        result.errors.append(
            _issue(
                stage,
                "too_many_items",
                f"视觉原文条目超过 {MAX_TRANSCRIPTION_ITEMS} 条的安全上限。",
            )
        )
        return result

    identifiers_seen: set[str] = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            result.errors.append(_issue(stage, "item_not_object", "每个视觉原文条目必须是对象。", index))
            continue
        if set(raw_row) != _VISUAL_KEYS:
            result.errors.append(
                _issue(stage, "invalid_item_schema", "视觉原文条目的字段缺失或包含额外字段。", index)
            )
            continue

        identifier, identifier_error = _identifier(raw_row, "id", stage=stage, item_index=index)
        raw_text, raw_text_error = _required_string(
            raw_row,
            "raw_text",
            stage=stage,
            item_index=index,
            maximum=MAX_RAW_TEXT_CHARS,
        )
        bbox, bbox_error = _validate_bbox(raw_row.get("bbox_1000"), stage=stage, item_index=index)
        confidence, confidence_error = _confidence(
            raw_row,
            "confidence_estimate",
            stage=stage,
            item_index=index,
        )
        confidence_source_error = _validate_confidence_source(raw_row, stage=stage, item_index=index)

        position_precision = raw_row.get("position_precision")
        precision_error = None
        if position_precision not in POSITION_PRECISIONS:
            precision_error = _issue(
                stage,
                "invalid_position_precision",
                "position_precision 只能是 approximate 或 unavailable。",
                index,
            )
        elif bbox is None and position_precision != "unavailable":
            precision_error = _issue(
                stage,
                "bbox_precision_mismatch",
                "bbox_1000 为 null 时 position_precision 必须是 unavailable。",
                index,
            )
        elif bbox is not None and position_precision != "approximate":
            precision_error = _issue(
                stage,
                "bbox_precision_mismatch",
                "生成式模型提供 bbox_1000 时只能标记为 approximate。",
                index,
            )

        legibility = raw_row.get("legibility")
        legibility_error = None
        if legibility not in LEGIBILITY_VALUES:
            legibility_error = _issue(
                stage,
                "invalid_legibility",
                "legibility 只能是 clear 或 uncertain。",
                index,
            )

        errors = (
            identifier_error,
            raw_text_error,
            bbox_error,
            confidence_error,
            confidence_source_error,
            precision_error,
            legibility_error,
        )
        row_errors = [error for error in errors if error is not None]
        if row_errors:
            result.errors.extend(row_errors)
            continue
        assert identifier is not None
        assert raw_text is not None
        assert confidence is not None
        assert isinstance(position_precision, str)
        assert isinstance(legibility, str)

        if identifier in identifiers_seen:
            result.errors.append(_issue(stage, "duplicate_id", "视觉原文 id 不得重复。", index))
            continue
        identifiers_seen.add(identifier)
        result.items.append(
            VisualTranscriptionItem(
                id=identifier,
                raw_text=raw_text,
                bbox_1000=bbox,
                position_precision=position_precision,
                legibility=legibility,
                confidence_estimate=confidence,
            )
        )
    return result


def _transcription_text_by_id(
    transcriptions: Sequence[VisualTranscriptionItem] | Mapping[str, str],
) -> dict[str, str]:
    if isinstance(transcriptions, Mapping):
        return {
            str(identifier): text
            for identifier, text in transcriptions.items()
            if isinstance(identifier, str) and isinstance(text, str)
        }
    return {item.id: item.raw_text for item in transcriptions}


def parse_field_mapping_output(
    raw_output: Any,
    *,
    transcriptions: Sequence[VisualTranscriptionItem] | Mapping[str, str],
    repair_callback: JsonRepairCallback | None = None,
) -> ModelOutputResult[FieldMappingItem]:
    stage = "field_mapping"
    text, documents, decode_errors, repair_attempted = _decode_with_optional_repair(
        raw_output,
        stage=stage,
        repair_callback=repair_callback,
    )
    result: ModelOutputResult[FieldMappingItem] = ModelOutputResult(
        stage=stage,
        raw_output=text,
        errors=decode_errors,
        repair_attempted=repair_attempted,
    )
    if decode_errors:
        return result

    rows, payload, container_errors = _select_items_container(documents, stage=stage)
    result.json_payload = payload
    result.errors.extend(container_errors)
    if rows is None:
        return result
    if len(rows) > MAX_MAPPING_ITEMS:
        result.errors.append(
            _issue(
                stage,
                "too_many_items",
                f"字段映射条目超过 {MAX_MAPPING_ITEMS} 条的安全上限。",
            )
        )
        return result

    source_texts = _transcription_text_by_id(transcriptions)
    mappings_seen: set[tuple[str, str, str]] = set()
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            result.errors.append(_issue(stage, "item_not_object", "每个字段映射条目必须是对象。", index))
            continue
        if set(raw_row) != _MAPPING_KEYS:
            result.errors.append(
                _issue(stage, "invalid_item_schema", "字段映射条目的字段缺失或包含额外字段。", index)
            )
            continue

        transcription_id, identifier_error = _identifier(
            raw_row,
            "transcription_id",
            stage=stage,
            item_index=index,
        )
        raw_value, raw_value_error = _required_string(
            raw_row,
            "raw_value",
            stage=stage,
            item_index=index,
            maximum=MAX_RAW_VALUE_CHARS,
        )
        confidence, confidence_error = _confidence(
            raw_row,
            "mapping_confidence_estimate",
            stage=stage,
            item_index=index,
        )
        confidence_source_error = _validate_confidence_source(raw_row, stage=stage, item_index=index)

        field_name = raw_row.get("field")
        field_error = None
        if not isinstance(field_name, str) or field_name not in ALLOWED_FIELDS:
            field_error = _issue(stage, "field_not_allowed", "field 不在商品字段白名单内。", index)

        association_error = None
        if transcription_id is not None:
            source_text = source_texts.get(transcription_id)
            if source_text is None:
                association_error = _issue(
                    stage,
                    "unknown_transcription_id",
                    "transcription_id 未关联到第一步的合法原文。",
                    index,
                )
            elif raw_value is not None and raw_value not in source_text:
                association_error = _issue(
                    stage,
                    "raw_value_not_in_source",
                    "raw_value 必须逐字出现在关联的第一步原文中。",
                    index,
                )

        errors = (
            identifier_error,
            raw_value_error,
            confidence_error,
            confidence_source_error,
            field_error,
            association_error,
        )
        row_errors = [error for error in errors if error is not None]
        if row_errors:
            result.errors.extend(row_errors)
            continue
        assert transcription_id is not None
        assert isinstance(field_name, str)
        assert raw_value is not None
        assert confidence is not None

        signature = (transcription_id, field_name, raw_value)
        if signature in mappings_seen:
            result.errors.append(_issue(stage, "duplicate_mapping", "重复的字段映射不会进入事实候选。", index))
            continue
        mappings_seen.add(signature)
        result.items.append(
            FieldMappingItem(
                transcription_id=transcription_id,
                field=field_name,
                raw_value=raw_value,
                mapping_confidence_estimate=confidence,
            )
        )
    return result
