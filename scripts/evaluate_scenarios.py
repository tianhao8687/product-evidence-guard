"""Whole-output, independently labelled scenario checks (not a population estimate).

The JSON oracle supplies values, units, ownership, scope and status explicitly.
This scorer never calls production normalization to generate an expected value.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product_evidence_guard.engine import analyze_directory


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signature(row):
    return canonical([*fact_key(row), row[3], sorted(canonical(pair) for pair in row[4])])


def fact_key(row):
    # Open-field identifiers are case-insensitive. Case in an English label
    # is presentation, unlike units such as mA versus MA or product ownership.
    field = row[1].casefold() if row[1].startswith("custom:") else row[1]
    return [row[0], field, row[2]]


def observed_facts(product):
    candidates = {c["candidate_id"]: c for c in product["candidates"]}
    result = []
    for group in product["facts"]:
        items = [candidates[cid] for cid in group["candidate_ids"]]
        owners = {c["product_sku"] for c in items}
        sku = next(iter(owners)) if len(owners) == 1 else "AMBIGUOUS_OWNERS"
        field = "custom:" + group["field_label"] if group["field"].startswith("custom_") else group["field"]
        pairs = {canonical([c["normalized_value"], c["normalized_unit"]]):
                 [c["normalized_value"], c["normalized_unit"]] for c in items}
        result.append([sku, field, group["scope"], group["fact_status"], list(pairs.values())])
        if group["fact_status"] == "verified":
            assert canonical([group["selected_value"], group["selected_unit"]]) in pairs
    return result


def score(expected, actual):
    gold, predicted = Counter(map(signature, expected)), Counter(map(signature, actual))
    # Business facts and identity metadata are both checked, but not pooled to
    # inflate the main safety/coverage denominator with repeated SKU rows.
    business = lambda rows: [r for r in rows if r[1] not in {"sku", "model", "variant"}]
    exp, obs = business(expected), business(actual)
    verified_gold = Counter(signature(r) for r in exp if r[3] == "verified")
    verified_pred = Counter(signature(r) for r in obs if r[3] == "verified")
    expected_keys = Counter(canonical(fact_key(r)) for r in exp)
    observed_keys = Counter(canonical(fact_key(r)) for r in obs)
    conflict_gold = Counter(canonical(fact_key(r)) for r in exp if r[3] == "conflict")
    conflict_pred = Counter(canonical(fact_key(r)) for r in obs if r[3] == "conflict")
    unnecessary = sum(r[3] != "verified" and canonical(fact_key(r)) in {
        canonical(fact_key(g)) for g in exp if g[3] == "verified"} for r in obs)
    return {
        "passed": gold == predicted,
        "missing": list((gold - predicted).elements()),
        "unexpected": list((predicted - gold).elements()),
        "expected_business_facts": len(exp),
        "predicted_business_facts": len(obs),
        "auto_confirmed": sum(verified_pred.values()),
        "wrong_auto_confirmed": sum((verified_pred - verified_gold).values()),
        "missing_fact_keys": sum((expected_keys - observed_keys).values()),
        "missed_conflicts": sum((conflict_gold - conflict_pred).values()),
        "false_conflicts": sum((conflict_pred - conflict_gold).values()),
        "review_items": sum(r[3] != "verified" for r in obs),
        "unnecessary_review_items": unnecessary,
    }


def run_case(case, *, policy="current"):
    with tempfile.TemporaryDirectory(prefix="peg-scenario-") as temporary:
        root, output = Path(temporary) / "input", Path(temporary) / "output"
        root.mkdir()
        files = list(case["files"].items())
        if policy == "primary_only_control":
            files = files[:1]  # Unsafe negative control, NOT a production mode.
        for name, content in files:
            path = root / name
            if path.resolve().parent != root.resolve():
                raise ValueError("Scenario filenames must be plain local filenames")
            path.write_text(content, encoding=case.get("encoding", "utf-8"))
        summary = analyze_directory(root, output, preprocessing_workers=1)
        product = json.loads((output / "product-facts.json").read_text("utf-8"))
    if summary["errors"]:
        raise RuntimeError(summary["errors"])
    if policy == "strict_multisource":
        for group in product["facts"]:
            if group["fact_status"] == "verified" and group["independent_source_count"] < 2:
                group["fact_status"] = "pending_confirmation"
    actual = observed_facts(product)
    return {"id": case["id"], "family": case["family"], **score(case["facts"], actual), "actual": actual}


def run_suite(fixture, *, policies=("current",), rescore=None):
    payload = fixture.read_bytes()
    dataset = json.loads(payload)
    output = {"fixture_sha256": hashlib.sha256(payload).hexdigest(),
              "protocol": dataset["protocol"], "sample_unit": "scenario; correlated, not independent business packages",
              "case_count": len(dataset["cases"]), "policies": {}}
    saved = json.loads(rescore.read_text("utf-8")) if rescore else None
    if saved and saved["fixture_sha256"] != output["fixture_sha256"]:
        raise ValueError("Cannot rescore changed gold without a new explicitly labelled evaluation")
    if saved:
        output["rescored_from"] = rescore.name
    for policy in policies:
        started = time.perf_counter()
        if saved:
            predictions = {r["id"]: r["actual"] for r in saved["policies"][policy]["cases"]}
            rows = [{"id": c["id"], "family": c["family"], **score(c["facts"], predictions[c["id"]]),
                     "actual": predictions[c["id"]]} for c in dataset["cases"]]
        else:
            rows = [run_case(c, policy=policy) for c in dataset["cases"]]
        counts = {k: sum(int(r[k]) for r in rows) for k in rows[0]
                  if isinstance(rows[0][k], (int, bool))}
        output["policies"][policy] = {"counts": counts, "elapsed_seconds": time.perf_counter()-started, "cases": rows}
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/generalization_cases.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--rescore", type=Path, help="Regrade saved outputs only; never rerun the engine or change gold")
    args = parser.parse_args()
    policies = ("current", "strict_multisource", "primary_only_control") if args.compare else ("current",)
    result = run_suite(args.fixture, policies=policies, rescore=args.rescore)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({p: r["counts"] for p, r in result["policies"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
