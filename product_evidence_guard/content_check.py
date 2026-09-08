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

from .extractor import FIELD_SPECS, infer_semantic_scope
from .field_registry import REGISTRY
from .models import ClaimCandidate
from .normalization import normalize_text, normalize_value, values_equal


LABELS = {spec.name: spec.label for spec in FIELD_SPECS}
DEFAULT_SCOPES = {spec.name: spec.scope for spec in FIELD_SPECS}
TEXT_FIELDS = {spec.name for spec in FIELD_SPECS if spec.value_type == "text"}
NUMBER = r"[-+]?\d+(?:\.\d+)?"
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
    rf"(?<![A-Za-z0-9_.]){NUMBER}(?:\s*(?:[-–—~～至到]|to)\s*{NUMBER})?"
    rf"(?:\s*[×xX*]\s*{NUMBER}){{0,2}}\s*(?:{UNIT_TOKEN})(?![A-Za-z])",
    re.I,
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
STANDALONE_IP = re.compile(r"(?<![A-Za-z0-9])IP\s*\d{2}[A-Za-z]?(?![A-Za-z0-9])", re.I)
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


def _product_for_clause(clause: str, facts: list[dict[str, Any]]) -> tuple[str | None, bool]:
    products = {str(f.get("product_id")) for f in facts if f.get("product_id")}
    if len(products) <= 1:
        return (next(iter(products)) if products else None), False
    text = normalize_text(clause)
    mentioned: set[str] = set()
    for fact in facts:
        token = normalize_text(str(fact.get("value") or ""))
        if (
            fact.get("product_id")
            and fact.get("field") in {"sku", "model", "variant"}
            and token
            and token in text
        ):
            mentioned.add(str(fact["product_id"]))
    return (next(iter(mentioned)) if len(mentioned) == 1 else None), len(mentioned) != 1


def _claim_row(claim: ClaimCandidate, raw_value: str | None = None) -> dict[str, Any]:
    row = claim.to_dict()
    # v1 compatibility keys remain while consumers migrate to ClaimCandidate.
    row.update({"raw_value": raw_value, "value": claim.normalized_value,
                "unit": claim.normalized_unit, "fact_ids": list(claim.evidence_ids)})
    return row


def check_draft(text: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    used: set[str] = set()
    references: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        if isinstance(fact, dict) and isinstance(fact.get("field"), str):
            references.setdefault(fact["field"], []).append(fact)

    for line_number, line in enumerate(text.splitlines(), 1):
        offset = 0
        for clause in re.split(r"[，,；;。！？!?\n]|(?<!\d)\.(?!\d)", line):
            clause_start = line.find(clause, offset)
            offset = max(offset, clause_start + len(clause))
            if not clause.strip():
                continue
            product_id, product_ambiguous = _product_for_clause(clause, facts)
            aliases = list(ALIAS_PATTERN.finditer(clause))
            consumed: list[tuple[int, int]] = []
            clause_fields: set[str] = set()
            for index, match in enumerate(aliases):
                field = ALIAS_FIELDS[match.group().casefold()]
                if field in clause_fields:
                    continue
                clause_fields.add(field)
                end = aliases[index + 1].start() if index + 1 < len(aliases) else len(clause)
                tail = clause[match.end():end].strip(" \t:：=|*`是为约")
                if not tail:
                    continue
                if field in TEXT_FIELDS:
                    raw = re.split(r"[|\t。；;]", tail, maxsplit=1)[0].strip(" *`\"'。")
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
                    raw = measurement.group()
                    span = measurement.span()
                normalized = normalize_value(field, raw)
                if field == "quantity" and not re.fullmatch(r"[-+]?\d+\s*(?:个|件|只|套|pcs?|pieces?|pack)?", raw, re.I):
                    findings.append({"kind": "unverified", "line": line_number, "field": field,
                                     "observed": raw, "message": "数量使用了不受支持的单位，需要人工核对。"})
                scope = infer_semantic_scope(field, clause[:end]) or DEFAULT_SCOPES.get(field)
                if field in {"voltage", "current", "power"}:
                    directions = set(re.findall(r"输入|输出|\binput\b|\boutput\b", clause[:end], re.I))
                    directions = {"input" if value.casefold() in {"输入", "input"} else "output" for value in directions}
                    if len(directions) == 1 and not ({"input", "output"} & set((scope or "").split("|"))):
                        scope = "|".join([next(iter(directions)), *([scope] if scope else [])])
                    elif len(directions) > 1:
                        scope = "ambiguous"
                consumed.append(span)
                candidates = references.get(field, [])
                if product_id:
                    candidates = [f for f in candidates if f.get("product_id") in {None, product_id}]
                scoped = [f for f in candidates if _scope_matches(scope, f.get("scope"))]
                exact = [f for f in scoped if values_equal(normalized.value, f.get("value"))
                         and (normalized.unit or "") == (f.get("unit") or "")]
                if product_ambiguous:
                    claim_status, kind = "needs_review", "product_ambiguous"
                    reason = "资料包包含多个商品，但声明没有可验证的商品身份。"
                    expected = candidates
                elif exact:
                    claim_status, kind = "supported", ""
                    reason = "与同商品、同字段、同口径的已确认事实一致。"
                    expected = exact
                else:
                    kind = "scope_ambiguous" if candidates and not scoped else "conflict" if scoped else "unsupported"
                    claim_status = "needs_review" if kind == "scope_ambiguous" else kind
                    reason = {"conflict": "与授权的已确认参数不一致。",
                              "unsupported": "该字段没有授权的已确认依据。",
                              "scope_ambiguous": "输入/输出或参数口径不明确，需要人工核对。"}[kind]
                    expected = scoped or candidates
                evidence_ids = [str(f.get("fact_id")) for f in expected if f.get("fact_id")]
                used.update(evidence_ids)
                claim = ClaimCandidate(
                    claim_id=_claim_id(line=line_number, clause=clause, field=field,
                                       value=normalized.value, unit=normalized.unit,
                                       scope=scope, product_id=product_id),
                    raw_text=clause.strip(), line=line_number, field=field,
                    field_label=LABELS.get(field), normalized_value=normalized.value,
                    normalized_unit=normalized.unit, scope=scope, product_id=product_id,
                    status=claim_status, evidence_ids=evidence_ids, reason=reason,
                    provenance={"extraction_method": "deterministic_claim_extraction"},
                )
                claims.append(_claim_row(claim, raw))
                if kind:
                    findings.append({"kind": kind, "line": line_number, "field": field,
                                     "field_label": LABELS.get(field), "observed": raw,
                                     "expected": [{k: f.get(k) for k in ("fact_id", "product_id", "value", "unit", "scope")}
                                                  for f in expected], "message": reason})

            if "ip_rating" not in clause_fields:
                for ip in STANDALONE_IP.finditer(clause):
                    raw = re.sub(r"\s+", "", ip.group()).upper()
                    normalized = normalize_value("ip_rating", raw)
                    candidates = references.get("ip_rating", [])
                    if product_id:
                        candidates = [f for f in candidates if f.get("product_id") in {None, product_id}]
                    exact = [f for f in candidates if values_equal(normalized.value, f.get("value"))]
                    status = "supported" if exact and not product_ambiguous else "needs_review" if product_ambiguous else "unsupported"
                    reason = "与已确认防护等级一致。" if status == "supported" else "防护等级没有同商品的已确认依据。"
                    evidence_ids = [str(f.get("fact_id")) for f in exact if f.get("fact_id")]
                    used.update(evidence_ids)
                    claim = ClaimCandidate(
                        claim_id=_claim_id(line=line_number, clause=clause, field="ip_rating",
                                           value=normalized.value, unit=None, scope=None, product_id=product_id),
                        raw_text=clause.strip(), line=line_number, field="ip_rating", field_label=LABELS["ip_rating"],
                        normalized_value=normalized.value, product_id=product_id, status=status,
                        evidence_ids=evidence_ids, reason=reason,
                        provenance={"extraction_method": "deterministic_standalone_ip"},
                    )
                    claims.append(_claim_row(claim, raw))
                    consumed.append(ip.span())
                    if status != "supported":
                        findings.append({"kind": "unsupported" if not product_ambiguous else "product_ambiguous",
                                         "line": line_number, "field": "ip_rating", "observed": raw,
                                         "message": reason, "expected": []})

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
