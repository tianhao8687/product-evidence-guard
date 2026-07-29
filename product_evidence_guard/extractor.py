from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable

from .models import FactCandidate, SourceBlock
from .normalization import normalize_value


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    label: str
    aliases: tuple[str, ...]
    scope: str | None = None
    mapping_confidence: float = 0.95


FIELD_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("net_weight", "净重", ("产品净重", "商品净重", "净含量", "净重", "net weight"), "net"),
    FieldSpec("gross_weight", "毛重/包装重量", ("包装重量", "整箱重量", "毛重", "gross weight", "shipping weight"), "gross"),
    FieldSpec("weight", "重量", ("单个重量", "产品重量", "商品重量", "重量", "weight"), "unspecified", 0.82),
    FieldSpec("dimensions", "尺寸", ("产品尺寸", "包装尺寸", "规格尺寸", "外形尺寸", "尺寸", "dimensions", "dimension", "size")),
    FieldSpec("length", "长度", ("总长", "长度", "length")),
    FieldSpec("width", "宽度", ("宽度", "width")),
    FieldSpec("height", "高度", ("高度", "height")),
    FieldSpec("quantity", "数量", ("套装数量", "包装数量", "件数", "数量", "quantity", "qty", "count")),
    FieldSpec("model", "型号", ("产品型号", "商品型号", "型号", "model no", "model", "sku")),
    FieldSpec("material", "材质", ("主要材质", "产品材质", "材料", "材质", "material")),
    FieldSpec("color", "颜色", ("产品颜色", "颜色", "colour", "color")),
    FieldSpec("voltage", "电压", ("额定电压", "输入电压", "输出电压", "电压", "voltage")),
    FieldSpec("current", "电流", ("额定电流", "最大电流", "输出电流", "电流", "current")),
    FieldSpec("power", "功率", ("额定功率", "最大功率", "功率", "power")),
    FieldSpec("capacity_charge", "电池容量", ("电池容量", "battery capacity")),
    FieldSpec("capacity_volume", "容积", ("净容量", "容积", "volume capacity", "volume")),
    FieldSpec("capacity", "容量", ("容量", "capacity"), mapping_confidence=0.78),
)

# Longest aliases first, preventing "重量" from stealing "产品净重".
_ALIAS_INDEX: list[tuple[str, FieldSpec]] = sorted(
    ((alias.casefold(), spec) for spec in FIELD_SPECS for alias in spec.aliases),
    key=lambda item: len(item[0]),
    reverse=True,
)

_SEPARATOR_RE = re.compile(r"\s*(?:[:：=]|\||\t|->|—|–)\s*")


def _candidate_id(block: SourceBlock, field: str, raw_value: str) -> str:
    payload = f"{block.block_id}\0{field}\0{raw_value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _extract_value_after_alias(text: str, alias: str) -> str | None:
    lowered = text.casefold()
    index = lowered.find(alias.casefold())
    if index < 0:
        return None
    remainder = text[index + len(alias) :].strip()
    remainder = re.sub(r"^[\s:：=|,，;；\-—–]+", "", remainder)
    if remainder:
        # Keep one compact logical value rather than swallowing the next sentence.
        remainder = re.split(r"[\n\r;；]", remainder, maxsplit=1)[0].strip()
        return remainder or None

    # For table-like text such as "320g | 净重", try the left-hand cell.
    parts = [part.strip() for part in _SEPARATOR_RE.split(text) if part.strip()]
    alias_cf = alias.casefold()
    for pos, part in enumerate(parts):
        if alias_cf in part.casefold() and pos > 0:
            return parts[pos - 1]
    return None


def extract_rule_candidates(block: SourceBlock) -> list[FactCandidate]:
    text = block.text.strip()
    if not text:
        return []
    lowered = text.casefold()
    candidates: list[FactCandidate] = []
    fields_seen: set[str] = set()

    for alias, spec in _ALIAS_INDEX:
        if spec.name in fields_seen or alias not in lowered:
            continue
        raw_value = _extract_value_after_alias(text, alias)
        if not raw_value:
            continue
        normalized = normalize_value(spec.name, raw_value)
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
            scope=spec.scope,
            notes=list(normalized.notes),
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
        }
        for spec in FIELD_SPECS
    ]
    return json.dumps(schema, ensure_ascii=False)
