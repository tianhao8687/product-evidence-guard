from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath
import re
from statistics import mean
from typing import Iterable

from .models import CrossFieldRelation, FactCandidate, FactGroup
from .normalization import normalize_text, values_equal


VERSION_RE = re.compile(
    r"(?:^|[_\-.\s])(?:v|ver|version|版本)\s*(\d{1,4})(?:[_\-.](\d{1,2}))?(?:[_\-.](\d{1,2}))?(?:$|[_\-.\s])",
    re.IGNORECASE,
)


def _deduplicate(candidates: Iterable[FactCandidate]) -> list[FactCandidate]:
    by_key: dict[tuple[str, str, str, str], FactCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.source_block_id,
            candidate.field,
            normalize_text(candidate.raw_value),
            str(candidate.normalized_value),
        )
        existing = by_key.get(key)
        if existing is None or candidate.mapping_confidence > existing.mapping_confidence:
            by_key[key] = candidate
    return list(by_key.values())


def _value_key(candidate: FactCandidate) -> str:
    return f"{candidate.normalized_unit}|{candidate.normalized_value!r}"


def _all_equal(candidates: list[FactCandidate]) -> bool:
    first = candidates[0]
    return all(
        first.normalized_unit == candidate.normalized_unit
        and values_equal(first.normalized_value, candidate.normalized_value)
        for candidate in candidates[1:]
    )


def _compatible_text_values(candidates: list[FactCandidate]) -> bool:
    values = [normalize_text(str(item.normalized_value)) for item in candidates]
    if any(not value for value in values):
        return False
    shortest = min(values, key=len)
    return all(shortest in value or value in shortest for value in values)


def _version_hint(path: str) -> tuple[int, ...] | None:
    stem = PurePosixPath(path).stem
    match = VERSION_RE.search(stem)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def _likely_version_update(candidates: list[FactCandidate]) -> bool:
    hints = [_version_hint(candidate.source_file) for candidate in candidates]
    usable = [hint for hint in hints if hint is not None]
    return len(usable) >= 2 and len(set(usable)) == len(usable)


def build_fact_groups(candidates: Iterable[FactCandidate]) -> list[FactGroup]:
    grouped: dict[str, list[FactCandidate]] = defaultdict(list)
    for candidate in _deduplicate(candidates):
        grouped[candidate.field].append(candidate)

    result: list[FactGroup] = []
    for field, items in sorted(grouped.items()):
        items.sort(key=lambda item: (item.source_file, str(item.locator), item.candidate_id))
        recognition = mean(item.recognition_confidence for item in items)
        mapping = mean(item.mapping_confidence for item in items)
        value_counts = Counter(_value_key(item) for item in items)
        consistency = max(value_counts.values()) / len(items)
        label = items[0].field_label

        if len(items) == 1:
            classification = "insufficient_evidence"
            severity = "review"
            reason = "只找到一条证据，暂时无法用其他资料交叉验证。"
            recommendation = "保留为待确认事实，不自动晋升。"
        elif _all_equal(items):
            raw_signatures = {(normalize_text(item.raw_value), item.normalized_unit) for item in items}
            if len(raw_signatures) == 1:
                classification = "exact_match"
                reason = "多份资料给出了相同表达和相同标准值。"
            else:
                classification = "converted_match"
                reason = "原始单位或写法不同，但换算后的标准值一致。"
            severity = "pass"
            recommendation = "可作为高可信候选，但仍由用户最终确认。"
        elif field in {"material", "color"} and _compatible_text_values(items):
            classification = "compatible_expression"
            severity = "review"
            reason = "表达粒度不同但可能兼容，例如大类名称与更具体的材质/颜色名称。"
            recommendation = "展示差异并请用户选择正式写法。"
        elif _likely_version_update(items):
            classification = "likely_version_update"
            severity = "review"
            reason = "冲突证据来自带不同版本标识的文件，可能是资料版本更新。"
            recommendation = "不要自动选择最新版；先确认哪个版本对应当前商品。"
        else:
            classification = "strong_conflict"
            severity = "block"
            reason = "同一商品字段出现无法通过单位换算或兼容表达解释的多个值。"
            recommendation = "阻止写入正式产品档案，必须人工处理。"

        result.append(
            FactGroup(
                field=field,
                field_label=label,
                classification=classification,
                severity=severity,
                reason=reason,
                candidate_ids=[item.candidate_id for item in items],
                normalized_values=[
                    {"value": item.normalized_value, "unit": item.normalized_unit} for item in items
                ],
                recognition_confidence=round(recognition, 4),
                mapping_confidence=round(mapping, 4),
                evidence_consistency=round(consistency, 4),
                recommendation=recommendation,
            )
        )
    return result


def build_cross_field_relations(candidates: Iterable[FactCandidate]) -> list[CrossFieldRelation]:
    by_field: dict[str, list[FactCandidate]] = defaultdict(list)
    deduped = _deduplicate(candidates)
    for candidate in deduped:
        by_field[candidate.field].append(candidate)

    relations: list[CrossFieldRelation] = []
    weight_fields = [field for field in ("net_weight", "gross_weight", "weight") if by_field.get(field)]
    if len(weight_fields) >= 2:
        all_candidates = [candidate for field in weight_fields for candidate in by_field[field]]
        values = {
            (candidate.normalized_value, candidate.normalized_unit)
            for candidate in all_candidates
            if candidate.normalized_unit == "g"
        }
        if len(values) > 1:
            relations.append(
                CrossFieldRelation(
                    relation_type="semantic_scope_mismatch",
                    fields=weight_fields,
                    severity="info",
                    reason=(
                        "净重、毛重/包装重量和未说明口径的重量属于不同概念。数值不同不应直接判为冲突，"
                        "但未说明口径的“重量”需要人工确认它指什么。"
                    ),
                    candidate_ids=[candidate.candidate_id for candidate in all_candidates],
                )
            )
    return relations


def build_graph(candidates: Iterable[FactCandidate]) -> tuple[list[FactCandidate], list[FactGroup], list[CrossFieldRelation]]:
    deduped = _deduplicate(candidates)
    return deduped, build_fact_groups(deduped), build_cross_field_relations(deduped)
