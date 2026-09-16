from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import PurePosixPath
import re
from statistics import mean
from typing import Iterable

from .models import CrossFieldRelation, FactCandidate, FactGroup
from .identity import conflict_group_id, evidence_identity, graph_identity, identity_needs_review, product_label
from .normalization import normalize_text, normalize_fact_text, values_equal, qualified_values_compatible, invalid_fact_value
from .field_registry import field_definition


FACT_STATUS_LABELS = {"verified": "已确认", "pending_confirmation": "待确认", "conflict": "冲突"}
REVIEW_REASONS = {
    "identity_ambiguous": "这条参数属于哪个商品？",
    "scope_unclear": "参数口径尚未确定。",
    "version_unclear": "需要确认哪个版本适用于当前商品。",
    "compatible_expression": "表达不同，请选择正式写法。",
    "source_changed": "来源更新了，请重新分析这条参数。",
    "unclear_value": "识别结果或单位不够清楚，请核对。",
    "invalid_value": "数值待补充或明显不合理，请填写正确参数。",
    "all_rejected": "现有值已全部拒绝，请补充正确参数。",
    "blocking_relation": "这条参数与其他参数存在未解决的问题。",
}


def _clear_recognition(item: FactCandidate) -> bool:
    review = item.provenance.get("visual_review", {})
    return (item.status == "confirmed" or item.recognition_confidence >= 0.8
            or (review.get("complete") is True and review.get("ocr_agrees") is True))


def _corroborated(item: FactCandidate, candidates: list[FactCandidate]) -> bool:
    return any(_clear_recognition(other) and other.normalized_unit == item.normalized_unit
               and values_equal(other.normalized_value, item.normalized_value) for other in candidates)


def refresh_fact_status(groups: Iterable[FactGroup], candidates: Iterable[FactCandidate],
                        relations: Iterable[CrossFieldRelation] = ()) -> None:
    """One presentation/export decision, never a synthetic human approval.

    A clear uncontested value is usable even from one file. Source counting is
    explanatory only; duplicates never vote away a contradictory value.
    """
    by_id = {item.candidate_id: item for item in candidates}
    groups = list(groups)
    explicit_scopes = defaultdict(set)
    for group in groups:
        if group.scope:
            explicit_scopes[(group.product_id, group.field)].add(group.scope)
    blocked_ids = {cid for relation in relations if relation.severity == "block" for cid in relation.candidate_ids}
    for group in groups:
        items = [by_id[cid] for cid in group.candidate_ids if cid in by_id]
        group.excluded = bool(items) and all(item.excluded_from_review for item in items)
        active = [item for item in items if item.status != "rejected"]
        valid = [item for item in active if item.source_current and item.status != "stale"]
        scoped = [item for item in valid if item.scope == group.scope]
        # A correctly scoped source can establish this value on its own.
        # Equal unscoped copies are retained as evidence, but do not certify
        # the scope and are not counted/authorized as verified sources.
        agreeing_unscoped = bool(group.scope and scoped and valid and _all_equal(valid)
                                  and all(item.scope in {None, group.scope} for item in valid))
        verified_sources = scoped if agreeing_unscoped else valid
        human = [item for item in valid if item.status == "confirmed"]
        group.fact_status = "pending_confirmation"
        group.verification_method = None
        group.verified_candidate_ids = []
        group.selected_value = None
        group.selected_unit = None
        group.human_approved = False
        group.review_reason_code = None
        group.current = len(active) == len(valid)
        # Same-byte renamed copies are not additional independent documents.
        group.independent_source_count = len({item.file_hash or item.source_file for item in verified_sources})
        spec = field_definition(group.field)
        if not group.current:
            code = "source_changed"
        elif not active:
            code = "all_rejected"
        elif any(identity_needs_review(item) for item in valid):
            code = "identity_ambiguous"
        elif (any(item.scope != group.scope for item in valid) and not agreeing_unscoped) or (
            not group.scope and ((spec and spec.scope_policy == "electrical") or
                                 explicit_scopes[(group.product_id, group.field)])
        ):
            code = "scope_unclear"
        elif blocked_ids.intersection(group.candidate_ids):
            code = "blocking_relation"
        elif any(invalid_fact_value(item.field, item.raw_value, item.normalized_value) for item in valid):
            code = "invalid_value"
        elif any((any(note.startswith("unparsed_") for note in item.notes) or
                 (spec and spec.value_type != "text" and item.normalized_unit is None) or
                 (item.status != "confirmed" and (not _corroborated(item, valid) or
                    (item.mapping_confidence_source != "deterministic" and item.mapping_confidence < 0.8))))
                 and not (item.status == "confirmed" and item.extraction_method == "human_correction")
                 for item in valid):
            code = "unclear_value"
        elif _likely_version_update(valid) and not (human and _all_equal(valid)):
            code = "version_unclear"
        elif not _all_equal(valid):
            if (group.field in {"material", "color"} and _compatible_text_values(valid)) or all(
                a.normalized_unit == b.normalized_unit and (
                    values_equal(a.normalized_value, b.normalized_value) or
                    qualified_values_compatible(a.normalized_value, b.normalized_value))
                for i, a in enumerate(valid) for b in valid[i + 1:]
            ):
                code = "compatible_expression"
            else:
                group.fact_status = "conflict"
                group.reason = "资料给出了不同的值，请选择采用哪个值，或补充说明。"
                group.recommendation = "选择采用值并明确处理其他不同值。"
                continue
        else:
            group.fact_status = "verified"
            selected_human = [item for item in verified_sources if item.status == "confirmed"]
            chosen = (selected_human or verified_sources)[0]
            group.selected_value = chosen.normalized_value
            group.selected_unit = chosen.normalized_unit
            group.verified_candidate_ids = [item.candidate_id for item in verified_sources]
            group.human_approved = bool(selected_human)
            rejected_other_value = any(item.status == "rejected" and not (
                item.normalized_unit == chosen.normalized_unit and values_equal(item.normalized_value, chosen.normalized_value)
            ) for item in items)
            group.verification_method = (
                "human_resolved_conflict" if selected_human and rejected_other_value else "human_confirmed" if selected_human
                else "single_value" if group.independent_source_count < 2
                else "cross_source_exact" if len({normalize_fact_text(item.raw_value) for item in verified_sources}) == 1
                else "cross_source_converted"
            )
            group.reason = "人工确认。" if selected_human else "参数清楚，未发现冲突。"
            group.recommendation = "无需重复确认。"
            continue
        group.review_reason_code = code
        group.reason = REVIEW_REASONS[code]
        group.recommendation = group.reason


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
    hints = []
    buckets: dict[object, list[FactCandidate]] = defaultdict(list)
    for candidate in candidates:
        context = candidate.provenance.get("document_context", {})
        body_version = context.get("version")
        hint = tuple(int(n) for n in body_version.split(".")) if body_version else _version_hint(candidate.source_file)
        if hint is None and context.get("historical"):
            hint = ("historical",)
        hints.append(hint)
        buckets[hint].append(candidate)
    usable = [hint for hint in hints if hint is not None]
    # A difference within the same applicability bucket is still a conflict.
    if any(not _all_equal(items) for items in buckets.values()):
        return False
    historical = any(c.provenance.get("document_context", {}).get("historical") for c in candidates)
    return (len(set(usable)) > 1 or (historical and len(buckets) > 1)) and not _all_equal(candidates)


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

        if any(identity_needs_review(item) for item in items):
            classification = "identity_ambiguous"
            severity = "review"
            reason = "商品归属尚未确定，需要先确认这条参数属于哪个商品；当前不同值不能直接认定为同商品冲突。"
            recommendation = "在来源资料中补充明确的 SKU、型号或变体归属并重新分析，再核对同商品、同口径的值。"
        elif len(items) == 1 and (product_id, field) in split_scope_keys:
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
            raw_signatures = {(normalize_fact_text(item.raw_value), item.normalized_unit) for item in items}
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
                product_label=product_label(items[0].product_sku, items[0].product_model, items[0].product_variant, product_id),
                scope=scope_key or None,
                group_id=conflict_group_id(product_id, field, scope_key or None),
            )
        )
    refresh_fact_status(result, deduplicated)
    return result


def build_cross_field_relations(candidates: Iterable[FactCandidate]) -> list[CrossFieldRelation]:
    by_product_field: dict[tuple[str, str], list[FactCandidate]] = defaultdict(list)
    deduped = _deduplicate(candidates)
    for candidate in deduped:
        if identity_needs_review(candidate):
            continue
        by_product_field[(graph_identity(candidate)[0], candidate.field)].append(candidate)

    relations: list[CrossFieldRelation] = []
    product_ids = sorted({product for product, _ in by_product_field})
    for product_id in product_ids:
        weight_fields = [field for field in ("net_weight", "gross_weight", "weight") if by_product_field.get((product_id, field))]
        if len(weight_fields) < 2:
            continue
        all_candidates = [candidate for field in weight_fields for candidate in by_product_field[(product_id, field)]]
        values = {
            (repr(candidate.normalized_value), candidate.normalized_unit)
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
    groups, relations = build_fact_groups(deduped), build_cross_field_relations(deduped)
    refresh_fact_status(groups, deduped, relations)
    return deduped, groups, relations
