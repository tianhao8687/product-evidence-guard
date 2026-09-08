from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath
import re
from statistics import mean
from typing import Iterable

from .models import CrossFieldRelation, FactCandidate, FactGroup
from .identity import conflict_group_id, evidence_identity, graph_identity
from .normalization import normalize_text, values_equal


VERSION_RE = re.compile(
    r"(?:^|[_\-.\s])(?:v|ver|version|版本)\s*(\d{1,4})(?:[_\-.](\d{1,2}))?(?:[_\-.](\d{1,2}))?(?:$|[_\-.\s])",
    re.IGNORECASE,
)


def _deduplicate(candidates: Iterable[FactCandidate]) -> list[FactCandidate]:
    by_key: dict[tuple[str, str, str, str, str, str], FactCandidate] = {}
    for candidate in candidates:
        key = evidence_identity(candidate)
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


def _only_cross_scope_difference(candidates: list[FactCandidate]) -> bool:
    by_scope: dict[str, list[FactCandidate]] = defaultdict(list)
    for candidate in candidates:
        if not candidate.scope:
            return False
        by_scope[candidate.scope].append(candidate)
    return len(by_scope) > 1 and all(
        len(items) == 1 or _all_equal(items)
        for items in by_scope.values()
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
    deduplicated = _deduplicate(candidates)
    by_product_field: dict[tuple[str, str], list[FactCandidate]] = defaultdict(list)
    for candidate in deduplicated:
        product_id, field, _scope = graph_identity(candidate)
        by_product_field[(product_id, field)].append(candidate)
    grouped: dict[tuple[str, str, str], list[FactCandidate]] = defaultdict(list)
    split_scope_keys: set[tuple[str, str]] = set()
    for (product_id, field), field_items in by_product_field.items():
        explicit_scopes = {item.scope for item in field_items if item.scope}
        if len(explicit_scopes) > 1:
            split_scope_keys.add((product_id, field))
        # Missing scope is uncertainty, not a separate trustworthy semantic
        # scope. With one possible explicit scope, compare conservatively;
        # with several, keep it isolated for review instead of guessing.
        merge_unscoped = len(explicit_scopes) == 1
        only_scope = next(iter(explicit_scopes)) if merge_unscoped else None
        for candidate in field_items:
            scope_key = candidate.scope or (only_scope if merge_unscoped else "")
            grouped[(product_id, field, scope_key or "")].append(candidate)

    result: list[FactGroup] = []
    for (product_id, field, scope_key), items in sorted(grouped.items()):
        items.sort(key=lambda item: (item.source_file, str(item.locator), item.candidate_id))
        recognition = mean(item.recognition_confidence for item in items)
        mapping = mean(item.mapping_confidence for item in items)
        value_counts = Counter(_value_key(item) for item in items)
        consistency = max(value_counts.values()) / len(items)
        label = items[0].field_label

        if len(items) == 1 and (product_id, field) in split_scope_keys:
            classification = "semantic_scope_split"
            severity = "review"
            reason = (
                "该字段在同一商品下存在多个输入/输出、额定/典型/上下限、运行模式"
                "或产品变体语义口径；各口径独立成组，数值不同不互判为冲突。"
            )
            recommendation = (
                "按当前语义口径单独展示并人工确认，不要跨口径合并成一个值。"
            )
        elif len(items) == 1:
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
        elif _only_cross_scope_difference(items):
            classification = "semantic_scope_split"
            severity = "review"
            reason = (
                "候选分别属于输入/输出、额定/典型/上下限、运行模式"
                "或产品变体等不同语义口径，数值不同不应互相判为冲突。"
            )
            recommendation = (
                "按各自输入输出、额定/上下限或模式口径分别展示并人工确认，"
                "不要合并成一个值。"
            )
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
                product_id=product_id,
                product_label=(items[0].product_sku or items[0].product_model or product_id),
                scope=scope_key or None,
                group_id=conflict_group_id(product_id, field, scope_key or None),
            )
        )
    return result


def build_cross_field_relations(candidates: Iterable[FactCandidate]) -> list[CrossFieldRelation]:
    by_product_field: dict[tuple[str, str], list[FactCandidate]] = defaultdict(list)
    deduped = _deduplicate(candidates)
    for candidate in deduped:
        by_product_field[(graph_identity(candidate)[0], candidate.field)].append(candidate)

    relations: list[CrossFieldRelation] = []
    product_ids = sorted({product for product, _ in by_product_field})
    for product_id in product_ids:
        weight_fields = [field for field in ("net_weight", "gross_weight", "weight") if by_product_field.get((product_id, field))]
        if len(weight_fields) < 2:
            continue
        all_candidates = [candidate for field in weight_fields for candidate in by_product_field[(product_id, field)]]
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
                    product_id=product_id,
                )
            )
    return relations


def build_graph(candidates: Iterable[FactCandidate]) -> tuple[list[FactCandidate], list[FactGroup], list[CrossFieldRelation]]:
    deduped = _deduplicate(candidates)
    return deduped, build_fact_groups(deduped), build_cross_field_relations(deduped)
