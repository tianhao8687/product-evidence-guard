"""Whole-output, independently labelled scenario checks (not a population estimate).

The JSON oracle supplies values, units, ownership, scope and status explicitly.
This scorer never calls production normalization to generate an expected value.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product_evidence_guard.engine import analyze_directory
from evaluation_metrics import match_rows


def canonical(value):
    # JSON's 1 and 1.0 represent the same measurement; booleans remain distinct.
    def numeric(item):
        if isinstance(item, float) and item.is_integer():
            return int(item)
        if isinstance(item, list):
            return [numeric(v) for v in item]
        if isinstance(item, dict):
            return {k: numeric(v) for k, v in item.items()}
        return item
    return json.dumps(numeric(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def signature(row):
    pairs = row[4]
    if row[1].startswith("custom:"):
        # Unit-adjacent whitespace is typography, not a different custom value.
        # No conversion, approximation, label guessing or production imports.
        pairs = [[re.sub(r"(?<=\d)\s+(?=[a-zA-Z%°\u3400-\u9fff])", "", v) if isinstance(v,str) else v, u]
                 for v,u in pairs]
    return canonical([*fact_key(row), row[3], sorted(canonical(pair) for pair in pairs)])


def fact_key(row):
    # Open-field identifiers are case-insensitive. Case in an English label
    # is presentation, unlike units such as mA versus MA or product ownership.
    field = row[1].casefold() if row[1].startswith("custom:") else row[1]
    return [row[0], field, row[2]]


def observed_facts(product, *, identity_mode="sku"):
    candidates = {c["candidate_id"]: c for c in product["candidates"]}
    result = []
    for group in product["facts"]:
        items = [candidates[cid] for cid in group["candidate_ids"]]
        owners = {c["product_sku"] for c in items}
        sku = next(iter(owners)) if len(owners) == 1 else "AMBIGUOUS_OWNERS"
        if identity_mode == "product_key":
            keys = {canonical({"sku": c["product_sku"]} if c["product_sku"] else
                    {"model": c["product_model"], "variant": c["product_variant"]}) for c in items}
            sku = json.loads(next(iter(keys))) if len(keys) == 1 else "AMBIGUOUS_OWNERS"
        field = "custom:" + group["field_label"] if group["field"].startswith("custom_") else group["field"]
        pairs = {canonical([c["normalized_value"], c["normalized_unit"]]):
                 [c["normalized_value"], c["normalized_unit"]] for c in items}
        result.append([sku, field, group["scope"], group["fact_status"], list(pairs.values())])
        if group["fact_status"] == "verified":
            assert canonical([group["selected_value"], group["selected_unit"]]) in pairs
    return result


def score(expected, actual):
    def matched(left, right):
        # Reuse independent one-to-one comparisons: floating conversion noise
        # is not a wrong fact; duplicate predictions still count against us.
        def mapping(row):
            key_owner, field, scope, status, encoded_pairs = json.loads(signature(row))
            return {"key":[key_owner,field,scope], "status":status,
                    "pairs":[json.loads(pair) for pair in encoded_pairs]}
        return match_rows([mapping(r) for r in left], [mapping(r) for r in right], ("key","status","pairs"))
    gold_matches, prediction_matches = matched(expected, actual)
    # Business facts and identity metadata are both checked, but not pooled to
    # inflate the main safety/coverage denominator with repeated SKU rows.
    business = lambda rows: [r for r in rows if r[1] not in {"sku", "model", "variant"}]
    exp, obs = business(expected), business(actual)
    verified_gold = [r for r in exp if r[3] == "verified"]
    verified_pred = [r for r in obs if r[3] == "verified"]
    _, verified_matches = matched(verified_gold, verified_pred)
    expected_keys = Counter(canonical(fact_key(r)) for r in exp)
    observed_keys = Counter(canonical(fact_key(r)) for r in obs)
    conflict_gold = Counter(canonical(fact_key(r)) for r in exp if r[3] == "conflict")
    conflict_pred = Counter(canonical(fact_key(r)) for r in obs if r[3] == "conflict")
    unnecessary = sum(r[3] != "verified" and canonical(fact_key(r)) in {
        canonical(fact_key(g)) for g in exp if g[3] == "verified"} for r in obs)
    return {
        "passed": len(gold_matches) == len(expected) and len(prediction_matches) == len(actual),
        "missing": [signature(r) for i,r in enumerate(expected) if i not in gold_matches],
        "unexpected": [signature(r) for i,r in enumerate(actual) if i not in prediction_matches],
        "expected_business_facts": len(exp),
        "predicted_business_facts": len(obs),
        "auto_confirmed": len(verified_pred),
        "wrong_auto_confirmed": len(verified_pred)-len(verified_matches),
        "missing_fact_keys": sum((expected_keys - observed_keys).values()),
        "missed_conflicts": sum((conflict_gold - conflict_pred).values()),
        "false_conflicts": sum((conflict_pred - conflict_gold).values()),
        "review_items": sum(r[3] != "verified" for r in obs),
        "unnecessary_review_items": unnecessary,
    }


def run_case(case, *, policy="current", native_root=None):
    with tempfile.TemporaryDirectory(prefix="peg-scenario-") as temporary:
        root, output = Path(temporary) / "input", Path(temporary) / "output"
        root.mkdir()
        files = list(case.get("files", {}).items())
        if policy == "primary_only_control":
            files = files[:1]  # Unsafe negative control, NOT a production mode.
        for name, content in files:
            path = root / name
            if path.resolve().parent != root.resolve():
                raise ValueError("Scenario filenames must be plain local filenames")
            path.write_text(content, encoding=case.get("encoding", "utf-8"))
        for source in case.get("native_files", []):
            if native_root is None:
                raise ValueError("native_root is required for original documents")
            original = (native_root / source["path"]).resolve()
            if not original.is_relative_to(native_root.resolve()):
                raise ValueError("Native input escapes the corpus root")
            if hashlib.sha256(original.read_bytes()).hexdigest() != source["sha256"]:
                raise ValueError("Native source hash changed: " + source["path"])
            target = root / original.name
            if target.exists():
                raise ValueError("Duplicate native/input filename")
            shutil.copy2(original, target)
        summary = analyze_directory(root, output, preprocessing_workers=1)
        product = json.loads((output / "product-facts.json").read_text("utf-8"))
        # Evidence integrity is separate from semantic accuracy.
        integrity = evidence_integrity(product, root)
    if summary["errors"] and not case.get("expected_error"):
        raise RuntimeError(summary["errors"])
    if policy == "strict_multisource":
        for group in product["facts"]:
            if group["fact_status"] == "verified" and group["independent_source_count"] < 2:
                group["fact_status"] = "pending_confirmation"
    actual = observed_facts(product, identity_mode=case.get("identity_mode", "sku"))
    result = {"id": case["id"], "family": case["family"], **score(case["facts"], actual), "actual": actual,
              "integrity_errors": integrity, "parse_errors": summary["errors"]}
    if case.get("expected_error"):
        result["expected_error_observed"] = case["expected_error"] in canonical(summary["errors"])
        result["passed"] &= result["expected_error_observed"]
    result["passed"] &= not integrity
    return result


def evidence_integrity(product, input_root):
    """Traceable references, without counting an evidence check as value accuracy."""
    rows = product["candidates"]
    by_id = {c["candidate_id"]: c for c in rows}
    errors = []
    if len(by_id) != len(rows):
        errors.append("duplicate_candidate_id")
    groups = product["facts"]
    if len({g["group_id"] for g in groups}) != len(groups):
        errors.append("duplicate_group_id")
    for c in rows:
        source = (input_root / c["source_file"]).resolve()
        if not source.is_relative_to(input_root.resolve()) or not source.is_file() or not c["locator"]:
            errors.append("untraceable_evidence")
        elif hashlib.sha256(source.read_bytes()).hexdigest() != c["file_hash"]:
            errors.append("wrong_source_hash")
    for g in groups:
        if not g["candidate_ids"] or any(cid not in by_id for cid in g["candidate_ids"]):
            errors.append("dangling_group_evidence")
        if g["fact_status"] == "verified" and not set(g["verified_candidate_ids"]).issubset(g["candidate_ids"]):
            errors.append("invalid_verified_evidence")
    return sorted(set(errors))


def run_suite(fixture, *, policies=("current",), rescore=None):
    payload = fixture.read_bytes()
    dataset = json.loads(payload)
    if not dataset.get("cases"):
        raise ValueError("Evaluation fixture must contain at least one case")
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
    return 0 if all(r["counts"]["passed"] == result["case_count"] for r in result["policies"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
