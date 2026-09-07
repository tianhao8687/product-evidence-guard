"""Deterministic checks of draft claims against an explicitly approved fact packet.

Drafts are never added to the evidence graph. Unsupported prose remains reviewable;
a successful check means only that the covered product fields match the packet.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any

from .extractor import FIELD_SPECS, infer_semantic_scope
from .normalization import normalize_value, values_equal


LABELS = {spec.name: spec.label for spec in FIELD_SPECS}
DEFAULT_SCOPES = {spec.name: spec.scope for spec in FIELD_SPECS}
TEXT_FIELDS = {"model", "material", "color"}
NUMBER = r"[-+]?\d+(?:\.\d+)?"
MEASUREMENT = re.compile(
    rf"(?<![A-Za-z0-9_.]){NUMBER}(?:\s*(?:[-–—~～至到]|to)\s*{NUMBER})?"
    rf"(?:\s*[×xX*]\s*{NUMBER}){{0,2}}\s*"
    r"(?:千克|公斤|毫安时|毫升|毫米|厘米|千瓦|kg|mg|lbs?|oz|mAh|mA|mV|kW|"
    r"cm|mm|mL|Ah|克|磅|盎司|米|英寸|升|安培|安|伏特|伏|瓦|件|个|只|套|"
    r"g|V|A|W|L|m)(?![A-Za-z])", re.I,
)
ALIASES = sorted(
    {(alias.casefold(), spec.name) for spec in FIELD_SPECS for alias in spec.aliases},
    key=lambda item: -len(item[0]),
)
ALIAS_PATTERN = re.compile(
    "|".join(re.escape(alias) for alias, _ in ALIASES), re.I,
)
ALIAS_FIELDS = dict(ALIASES)
UNLABELED_MODEL = re.compile(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]+\b")


def value_key(value: Any, unit: Any, scope: Any = None) -> tuple[str, str, str]:
    import json
    return (json.dumps(value, ensure_ascii=False, sort_keys=True), str(unit or ""), str(scope or ""))


def _scope_matches(claim: str | None, reference: str | None) -> bool:
    # Never collapse input/output, operating modes, or nominal/rated values.
    return (claim or "") == (reference or "")


def check_draft(text: str, facts: list[dict[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    claims: list[dict[str, Any]] = []
    used: set[str] = set()
    references: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        references.setdefault(fact["field"], []).append(fact)

    for line_number, line in enumerate(text.splitlines(), 1):
        # Break prose at sentence/clause boundaries, but preserve decimals.
        offset = 0
        for clause in re.split(r"[，,；;。！？!?\n]|(?<!\d)\.(?!\d)", line):
            clause_start = line.find(clause, offset)
            offset = clause_start + len(clause)
            aliases = list(ALIAS_PATTERN.finditer(clause))
            consumed: list[tuple[int, int]] = []
            for index, match in enumerate(aliases):
                field = ALIAS_FIELDS[match.group().casefold()]
                end = aliases[index + 1].start() if index + 1 < len(aliases) else len(clause)
                tail = clause[match.end():end].strip(" \t:：=|*`是为约")
                if not tail:
                    continue
                if field in TEXT_FIELDS:
                    raw = re.split(r"[|\t。；;]", tail, maxsplit=1)[0].strip(" *`\"'。")
                    if field == "model":
                        model_match = re.match(r"[A-Za-z0-9][A-Za-z0-9._/+-]*", raw)
                        raw = model_match.group() if model_match else raw
                    span = (match.end(), end)
                else:
                    measurement = MEASUREMENT.search(clause, match.end(), end)
                    if measurement is None and field == "quantity":
                        measurement = re.compile(NUMBER).search(clause, match.end(), end)
                    if measurement is None:
                        # A declared parameter with no readable unit/value needs review.
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
                    directions = {"input" if d.casefold() in {"输入", "input"} else "output" for d in directions}
                    if len(directions) == 1 and not ({"input", "output"} & set((scope or "").split("|"))):
                        scope = "|".join([next(iter(directions)), *([scope] if scope else [])])
                    elif len(directions) > 1:
                        scope = "ambiguous"
                claim = {"line": line_number, "field": field, "field_label": LABELS[field],
                         "raw_value": raw, "value": normalized.value, "unit": normalized.unit,
                         "scope": scope, "fact_ids": []}
                consumed.append(span)
                candidates = references.get(field, [])
                scoped = [f for f in candidates if _scope_matches(scope, f.get("scope"))]
                exact = [f for f in scoped if values_equal(normalized.value, f["value"])
                         and (normalized.unit or "") == (f.get("unit") or "")]
                if exact:
                    claim["status"] = "matched"
                    claim["fact_ids"] = [f["fact_id"] for f in exact]
                    used.update(claim["fact_ids"])
                else:
                    kind = "scope_ambiguous" if candidates and not scoped else "conflict" if scoped else "unsupported"
                    claim["status"] = kind
                    expected = scoped or candidates
                    used.update(f["fact_id"] for f in expected)
                    findings.append({"kind": kind, "line": line_number, "field": field,
                                     "field_label": LABELS[field], "observed": raw,
                                     "expected": [{k: f.get(k) for k in ("fact_id", "value", "unit", "scope")}
                                                  for f in expected],
                                     "message": {"conflict": "与授权的已确认参数不一致。",
                                                 "unsupported": "该字段没有授权的已确认依据。",
                                                 "scope_ambiguous": "输入/输出或参数口径不明确，需要人工核对。"}[kind]})
                claims.append(claim)

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
    return {"status": status, "claims": claims, "findings": findings,
            "used_fact_ids": sorted(used), "claim_count": len(claims),
            "finding_count": len(findings), "counts": dict(counts),
            "coverage": "仅核对已识别的商品字段；营销表述、遗漏参数及未识别的自然语言仍需人工审核。"}
