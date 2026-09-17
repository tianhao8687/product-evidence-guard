"""Deterministic ClaimCandidate extraction and verification.

Generated content never enters the evidence graph. A claim is supported only
by an approved fact with the same product, field, scope, value and unit;
cross-field inference (for example capacity -> runtime) is forbidden.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from typing import Any

from .extractor import FIELD_SPECS, infer_semantic_scope, explicit_parameter, parameter_segments, strip_product_prefix
from .field_registry import REGISTRY, field_definition, field_for_label
from .models import ClaimCandidate
from .normalization import normalize_text, normalize_value, values_equal


LABELS = {spec.name: spec.label for spec in FIELD_SPECS}
DEFAULT_SCOPES = {spec.name: spec.scope for spec in FIELD_SPECS}
SCOPE_POLICIES = {spec.name: spec.scope_policy for spec in FIELD_SPECS}
TEXT_FIELDS = {spec.name for spec in FIELD_SPECS if spec.value_type == "text"}
NUMBER = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
UNIT_TOKEN = "|".join(
    re.escape(unit)
    for unit in sorted(
        {unit for family in REGISTRY.unit_families.values() for unit in family.units}
        | {"个", "件", "只", "套", "pc", "pcs", "piece", "pieces", "pack"},
        key=len,
        reverse=True,
    )
)
MEASUREMENT = re.compile(
    rf"(?<![A-Za-z0-9_.,])(?:<=|>=|[≤≥<>≈~]|约|大约|about\s+)?{NUMBER}(?:\s*(?:[-–—~～至到]|to)\s*{NUMBER})?"
    rf"(?:\s*[×xX*]\s*{NUMBER}){{0,2}}\s*(?:{UNIT_TOKEN})(?![A-Za-z])"
    rf"(?:\s*(?:±|\+/-)\s*{NUMBER}\s*(?:{UNIT_TOKEN})?)?",
    re.I,
)
UNFAMILIAR_MEASUREMENT = re.compile(
    rf"(?<![A-Za-z0-9_.,]){NUMBER}\s*(?:[A-Za-z%°][A-Za-z0-9%°/.-]*|[\u3400-\u9fff]{{1,8}})", re.I
)
ALIASES = sorted(
    {(alias.casefold(), spec.name) for spec in FIELD_SPECS for alias in spec.aliases},
    key=lambda item: -len(item[0]),
)
def _bounded_alias(alias: str) -> str:
    prefix = r"(?<![A-Za-z0-9_])" if alias and alias[0].isascii() and alias[0].isalnum() else ""
    suffix = r"(?![A-Za-z0-9_])" if alias and alias[-1].isascii() and alias[-1].isalnum() else ""
    return prefix + re.escape(alias) + suffix


ALIAS_PATTERN = re.compile("|".join(f"(?:{_bounded_alias(alias)})" for alias, _ in ALIASES), re.I)
ALIAS_FIELDS = dict(ALIASES)
UNLABELED_MODEL = re.compile(
    r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]+\b"
)
STANDALONE_IP = re.compile(r"(?<![A-Za-z0-9])IP\s*[0-6X][0-9X][A-Za-z]?(?![A-Za-z0-9])", re.I)
INFERENTIAL_ASSERTION = re.compile(
    r"适合户外|户外使用|长期浸水|效率提升\s*\d+(?:\.\d+)?%|"
    r"supports?\s+[^,.;，。；]{1,80}|兼容[^，。；;]{1,80}",
    re.I,
)


def value_key(value: Any, unit: Any, scope: Any = None) -> tuple[str, str, str]:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True), str(unit or ""), str(scope or ""))


def _scope_matches(claim: str | None, reference: str | None) -> bool:
    return (claim or "") == (reference or "")


def _claim_id(*, line: int, clause: str, field: str | None, value: Any,
              unit: str | None, scope: str | None, product_id: str | None) -> str:
    payload = json.dumps(
        {"line": line, "clause": normalize_text(clause), "field": field,
         "value": value, "unit": unit, "scope": scope, "product": product_id},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "claim_" + hashlib.sha256(payload).hexdigest()[:20]


def _claim_product_prefix(clause: str, facts: list[dict[str, Any]]) -> tuple[str, str | None]:
    """A declared owner is a constraint, not a hint to fall back from.

    Source extraction intentionally accepts fewer unlabeled SKU forms. Here an
    explicit `owner: parameter: value` must also be checked against the grant.
    Do not invent owners from open labels such as MTBF1 Ground Benign.
    """
    body, token = strip_product_prefix(clause)
    if token:
        return body, token
    match = re.match(r"^\s*(?:[-*•]\s*)?([A-Za-z0-9][A-Za-z0-9_.+/-]*)(\s*[:：]\s*|\s+)(.+)$", clause)
    if not match or field_for_label(match[1], known_only=True):
        return clause, None
    token, separator, body = match.groups()
    known = any(f.get("field") in {"sku", "model", "variant"}
                and normalize_text(str(f.get("value", ""))) == normalize_text(token) for f in facts)
    parameter = ALIAS_PATTERN.match(body)
    if not parameter and not STANDALONE_IP.match(body) and not (known and explicit_parameter(body)):
        return clause, None
    if parameter:
        field = ALIAS_FIELDS[parameter.group().casefold()]
        # An electrical direction preceding a label is a scope, not a SKU.
        if (SCOPE_POLICIES.get(field) == "electrical" and
                infer_semantic_scope(field, clause) != infer_semantic_scope(field, body)):
            return clause, None
    if ":" in separator or "：" in separator or known or re.search(r"[\d_.-]", token):
        return body, token
    return clause, None


def _product_for_clause(clause: str, facts: list[dict[str, Any]],
                        explicit_token: str | None = None) -> tuple[str | None, bool]:
    if any(f.get("product_identity_status") in {"ambiguous", "unresolved"} for f in facts):
        return None, True
    if explicit_token:
        owners = {f["product_id"] for f in facts if f.get("product_id")
                  and f.get("field") in {"sku", "model", "variant"}
                  and normalize_text(str(f.get("value", ""))) == normalize_text(explicit_token)}
        return (next(iter(owners)), False) if len(owners) == 1 else (None, True)
    products = {str(f.get("product_id")) for f in facts if f.get("product_id")}
    text = normalize_text(clause)
    mentioned: set[str] = set()
    for fact in facts:
        token = normalize_text(str(fact.get("value") or ""))
        if (
            fact.get("product_id")
            and fact.get("field") in {"sku", "model", "variant"}
            and token
            and re.search(_bounded_alias(token), text)
        ):
            mentioned.add(str(fact["product_id"]))
    if not mentioned and len(products) <= 1:
        return (next(iter(products)) if products else None), False
    return (next(iter(mentioned)) if len(mentioned) == 1 else None), len(mentioned) != 1


def _evaluate_claim(field: str, value: Any, unit: str | None, scope: str | None,
                    product_id: str | None, product_ambiguous: bool,
                    references: dict[str, list[dict[str, Any]]]) -> tuple[str, str, str, list[dict[str, Any]]]:
    """All spellings share the same product/field/scope/value contract."""
    candidates = references.get(field, [])
    if product_id:
        candidates = [f for f in candidates if f.get("product_id") in {None, product_id}]
    scoped = [f for f in candidates if _scope_matches(scope, f.get("scope"))]
    exact = [f for f in scoped if values_equal(value, f.get("value"))
             and (unit or "") == (f.get("unit") or "")]
    if product_ambiguous:
        return "needs_review", "product_ambiguous", "声明中的商品未获得明确授权，或无法确定所属商品。", candidates
    if exact:
        return "supported", "", "与同商品、同字段、同口径的已确认事实一致。", exact
    kind = "scope_ambiguous" if candidates and not scoped else "conflict" if scoped else "unsupported"
    reason = {"conflict": "与授权的已确认参数不一致。",
              "unsupported": "该字段没有授权的已确认依据。",
              "scope_ambiguous": "参数口径或适用条件不明确，需要人工核对。"}[kind]
    return "needs_review" if kind == "scope_ambiguous" else kind, kind, reason, scoped or candidates


def _claim_row(claim: ClaimCandidate, raw_value: str | None = None) -> dict[str, Any]:
    row = claim.to_dict()
    # v1 compatibility keys remain while consumers migrate to ClaimCandidate.
    row.update({"raw_value": raw_value, "value": claim.normalized_value,
                "unit": claim.normalized_unit, "fact_ids": list(claim.evidence_ids)})
    return row


def _claim_prefix_bound(prefix: str, field: str, scope: str | None) -> bool:
    """A value match cannot certify unexplained words before its label."""
    prefix = prefix.strip(" \t-*•`_:：")
    if not prefix or re.fullmatch(r"(?:该|本)?产品", prefix):
        return True
    direction = {"input": "input", "output": "output", "输入": "input", "输出": "output"}.get(prefix.casefold())
    return bool(SCOPE_POLICIES.get(field) == "electrical" and direction
                and direction in (scope or "").split("|"))


def check_draft(text: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    used: set[str] = set()
    references: dict[str, list[dict[str, Any]]] = {}
    aliases_by_name = dict(ALIASES)
    labels = dict(LABELS)
    text_fields = set(TEXT_FIELDS)
    for fact in facts:
        if isinstance(fact, dict) and isinstance(fact.get("field"), str):
            references.setdefault(fact["field"], []).append(fact)
            spec = field_definition(fact["field"])
            if spec and spec.name.startswith("custom_"):
                aliases_by_name[spec.label] = spec.name
                labels[spec.name] = spec.label
                text_fields.add(spec.name)

    for line_number, line in enumerate(text.splitlines(), 1):
        offset = 0
        for clause in (part for segment in parameter_segments(line)
                       for part in re.split(r"[。！？!?]|(?<!\d)\.(?!\d)", segment)):
            clause_start = line.find(clause, offset)
            offset = max(offset, clause_start + len(clause))
            if not clause.strip():
                continue
            parameter_text, product_token = _claim_product_prefix(clause, facts)
            product_id, product_ambiguous = _product_for_clause(clause, facts, product_token)
            if product_token:
                clause = parameter_text
            explicit = explicit_parameter(clause)
            if explicit and explicit[0].name.startswith("custom_"):
                spec, _raw = explicit
                aliases_by_name[spec.label] = spec.name
                labels[spec.name] = spec.label
                text_fields.add(spec.name)
            alias_pattern = re.compile("|".join(f"(?:{_bounded_alias(alias)})" for alias in sorted(aliases_by_name, key=len, reverse=True)), re.I)
            aliases = list(alias_pattern.finditer(clause))
            consumed: list[tuple[int, int]] = []
            clause_fields: set[str] = set()
            for index, match in enumerate(aliases):
                field = aliases_by_name[match.group().casefold()]
                clause_fields.add(field)
                end = aliases[index + 1].start() if index + 1 < len(aliases) else len(clause)
                tail = clause[match.end():end].strip(" \t:：=|*`是为")
                if not tail:
                    continue
                if field in text_fields:
                    raw = re.split(r"[|\t。]", tail, maxsplit=1)[0].strip(" *`\"'。")
                    if field in {"model", "sku"}:
                        identity_match = re.match(r"[A-Za-z0-9][A-Za-z0-9._/+-]*", raw)
                        raw = identity_match.group() if identity_match else raw
                    span = (match.end(), end)
                else:
                    measurement = MEASUREMENT.search(clause, match.end(), end)
                    if measurement is None and field == "quantity":
                        measurement = re.compile(NUMBER).search(clause, match.end(), end)
                    if measurement is None:
                        if re.search(r"\d", tail):
                            findings.append({"kind": "unverified", "line": line_number,
                                             "field": field, "message": "参数单位或表达无法可靠核对。"})
                        continue
                    # The whole labeled value is the claim. Taking only a
                    # regex measurement loses 'not', bounds and conditions.
                    raw = tail.strip(" ,，;；*`")
                    span = (match.end(), end)
                normalized = normalize_value(field, raw)
                if field == "capacity" and normalized.unit == "mAh" and references.get("capacity_charge"):
                    field = "capacity_charge"
                if field == "quantity" and not re.fullmatch(rf"{NUMBER}\s*(?:个|件|只|套|pcs?|pieces?|pack)?", raw, re.I):
                    findings.append({"kind": "unverified", "line": line_number, "field": field,
                                     "observed": raw, "message": "数量使用了不受支持的单位，需要人工核对。"})
                    consumed.append(span)
                    continue
                scope_text = clause[:end] if SCOPE_POLICIES.get(field) == "electrical" else clause[match.start():end]
                # Narrative glue such as '仅需' is not part of the scope label.
                scope_text = re.sub(r"(?<=时间)(?:仅需|需要)|(?<=压力)(?:可达|达到)", ":", scope_text)
                scope = infer_semantic_scope(field, scope_text) or DEFAULT_SCOPES.get(field)
                if field in {"voltage", "current", "power"}:
                    directions = set(re.findall(r"输入|输出|\binput\b|\boutput\b", clause[:end], re.I))
                    directions = {"input" if value.casefold() in {"输入", "input"} else "output" for value in directions}
                    if len(directions) == 1 and not ({"input", "output"} & set((scope or "").split("|"))):
                        scope = "|".join([next(iter(directions)), *([scope] if scope else [])])
                    elif len(directions) > 1:
                        scope = "ambiguous"
                consumed.append(span)
                claim_status, kind, reason, expected = _evaluate_claim(
                    field, normalized.value, normalized.unit, scope, product_id, product_ambiguous, references)
                if claim_status == "supported" and not _claim_prefix_bound(clause[:aliases[0].start()], field, scope):
                    claim_status, kind, reason, expected = ("needs_review", "unverified",
                        "字段前还有未核对的限定或条件，不能仅凭数值一致判定通过。", [])
                evidence_ids = [str(f.get("fact_id")) for f in expected if f.get("fact_id")]
                used.update(evidence_ids)
                claim = ClaimCandidate(
                    claim_id=_claim_id(line=line_number, clause=clause, field=field,
                                       value=normalized.value, unit=normalized.unit,
                                       scope=scope, product_id=product_id),
                    raw_text=clause.strip(), line=line_number, field=field,
                    field_label=labels.get(field), normalized_value=normalized.value,
                    normalized_unit=normalized.unit, scope=scope, product_id=product_id,
                    status=claim_status, evidence_ids=evidence_ids, reason=reason,
                    provenance={"extraction_method": "deterministic_claim_extraction"},
                )
                claims.append(_claim_row(claim, raw))
                if kind:
                    findings.append({"kind": kind, "line": line_number, "field": field,
                                     "field_label": labels.get(field), "observed": raw,
                                     "expected": [{k: f.get(k) for k in ("fact_id", "product_id", "value", "unit", "scope")}
                                                  for f in expected], "message": reason})

            if "ip_rating" not in clause_fields:
                for ip in STANDALONE_IP.finditer(clause):
                    raw = re.sub(r"\s+", "", ip.group()).upper()
                    normalized = normalize_value("ip_rating", raw)
                    scope = infer_semantic_scope("ip_rating", clause) or DEFAULT_SCOPES.get("ip_rating")
                    status, kind, reason, expected = _evaluate_claim(
                        "ip_rating", normalized.value, normalized.unit, scope, product_id, product_ambiguous, references)
                    if status == "supported" and (not _claim_prefix_bound(clause[:ip.start()], "ip_rating", scope)
                            or clause[ip.end():].strip(" \t,，;；*`")):
                        status, kind, reason, expected = ("needs_review", "unverified",
                            "防护等级前后还有未核对的限定或条件，请明确完整写法。", [])
                    evidence_ids = [str(f.get("fact_id")) for f in expected if f.get("fact_id")]
                    used.update(evidence_ids)
                    claim = ClaimCandidate(
                        claim_id=_claim_id(line=line_number, clause=clause, field="ip_rating",
                                           value=normalized.value, unit=normalized.unit, scope=scope, product_id=product_id),
                        raw_text=clause.strip(), line=line_number, field="ip_rating", field_label=LABELS["ip_rating"],
                        normalized_value=normalized.value, normalized_unit=normalized.unit, scope=scope,
                        product_id=product_id, status=status,
                        evidence_ids=evidence_ids, reason=reason,
                        provenance={"extraction_method": "deterministic_standalone_ip"},
                    )
                    claims.append(_claim_row(claim, raw))
                    consumed.append(ip.span())
                    if kind:
                        findings.append({"kind": kind,
                                         "line": line_number, "field": "ip_rating", "observed": raw,
                                         "message": reason, "expected": [
                                             {k: f.get(k) for k in ("fact_id", "product_id", "value", "unit", "scope")}
                                             for f in expected]})

            for assertion in INFERENTIAL_ASSERTION.finditer(clause):
                if any(start <= assertion.start() and assertion.end() <= end for start, end in consumed):
                    continue
                raw = assertion.group().strip()
                claim = ClaimCandidate(
                    claim_id=_claim_id(line=line_number, clause=clause, field=None, value=raw,
                                       unit=None, scope=None, product_id=product_id),
                    raw_text=raw, line=line_number, field=None, field_label="自然语言声明",
                    product_id=product_id, status="unsupported",
                    reason="该声明不能由参数跨字段推导，必须提供直接证据。",
                    provenance={"extraction_method": "deterministic_assertion_detection"},
                )
                claims.append(_claim_row(claim, raw))
                findings.append({"kind": "unsupported", "line": line_number, "observed": raw,
                                 "message": claim.reason})

            for measurement in MEASUREMENT.finditer(clause):
                if not any(start <= measurement.start() and measurement.end() <= end for start, end in consumed):
                    findings.append({"kind": "unverified", "line": line_number,
                                     "observed": measurement.group(),
                                     "message": "检测到未明确对应字段的数值，请补充字段名称后复核。"})
                    consumed.append(measurement.span())
            for measurement in UNFAMILIAR_MEASUREMENT.finditer(clause):
                if not any(start <= measurement.start() and measurement.end() <= end for start, end in consumed):
                    findings.append({"kind": "unverified", "line": line_number,
                                     "observed": measurement.group(),
                                     "message": "这处参数尚未完成核对，不能算作已通过。"})
            if not any(c["line"] == line_number and c["field"] == "model" for c in claims):
                for model in UNLABELED_MODEL.finditer(clause):
                    findings.append({"kind": "unverified", "line": line_number,
                                     "observed": model.group(), "message": "疑似型号缺少明确字段标签，请人工核对。"})

    counts = Counter(item["kind"] for item in findings)
    status = "blocked" if any(counts[k] for k in ("conflict", "unsupported")) else (
        "needs_review" if findings or not claims else "covered_fields_match"
    )
    return {"schema_version": 2, "status": status, "claims": claims, "findings": findings,
            "used_fact_ids": sorted(used), "claim_count": len(claims),
            "finding_count": len(findings), "counts": dict(counts),
            "coverage": "仅有同 Product、Field、Scope 的明确声明可被已确认事实支持；不做跨字段推理。"}
