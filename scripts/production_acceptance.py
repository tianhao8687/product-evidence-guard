"""Offline release-evidence gate. Never manufactures human review or blind data.

Prediction files use the normal product-facts.json contract. Gold is separate,
whole-package annotation, not extracted or normalized using production code.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.evaluation_metrics import match_rows
from product_evidence_guard.state import atomic_write_json

GOLD_KEYS = ("product_model", "product_sku", "product_variant", "field", "scope", "value", "unit", "fact_status")
MINIMUMS = {"packages": 60, "suppliers": 15, "domains": 10, "annotated_facts": 3000,
            "development_packages": 40, "holdout_packages": 20}
THRESHOLDS = {"verified_precision": .995, "fact_recall": .98, "clear_auto_confirmation": .95}


def checked_json(base: Path, descriptor: dict) -> dict:
    path = (base / descriptor["path"]).resolve()
    if not path.is_relative_to(base.resolve()) or path.is_symlink():
        raise ValueError("证据文件必须位于验收目录内。")
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("验收证据文件过大。")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
        raise ValueError("验收证据文件散列不匹配。")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("验收文件必须是 JSON 对象。")
    return data


def predictions(product: dict) -> list[dict]:
    owners = {p["product_id"]: p for p in product.get("products", [])}
    result = []
    for group in product.get("facts", []):
        if group.get("excluded"):
            continue
        owner = owners.get(group.get("product_id"), {})
        variants = owner.get("variants", [])
        value, unit = group.get("selected_value"), group.get("selected_unit")
        if group["fact_status"] != "verified":
            alternatives = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in group.get("normalized_values", [])}
            value, unit = [json.loads(item) for item in sorted(alternatives)], None
        result.append({"product_model": owner.get("model"), "product_sku": owner.get("sku"),
            "product_variant": variants[0] if len(variants) == 1 else None,
            "field": group["field"], "scope": group.get("scope"), "value": value,
            "unit": unit, "fact_status": group["fact_status"],
            "automatic": group.get("fact_status") == "verified" and not group.get("human_approved", False)})
    return result


def metrics(counts: dict) -> dict:
    return {**counts, "verified_precision": counts["verified_correct"] / counts["verified"] if counts["verified"] else None,
            "fact_recall": counts["matched"] / counts["expected"] if counts["expected"] else None,
            "clear_auto_confirmation": counts["clear_auto"] / counts["clear"] if counts["clear"] else None}


def evaluate(manifest: dict, base: Path) -> dict:
    failures = []
    cases = []
    groups = defaultdict(lambda: defaultdict(int))
    seen_ids, seen_originals = set(), set()
    suppliers, domains = set(), set()
    split_counts = defaultdict(int)
    for package in manifest.get("packages", []):
        label = str(package.get("id", "missing-id"))
        try:
            if label in seen_ids:
                raise ValueError("测试包 ID 重复。")
            seen_ids.add(label)
            if package.get("origin") != "public_original" or not package.get("source_url", "").startswith("https://"):
                raise ValueError("原件验收不能用改编/合成或无出处的资料代替。")
            gold = checked_json(base, package["gold"])
            prediction = checked_json(base, package["prediction"])
            if any(g.get("human_approved") for g in prediction.get("facts", [])) or any(
                    c.get("extraction_method") == "human_correction" or c.get("status") in {"confirmed", "rejected"}
                    for c in prediction.get("candidates", [])):
                raise ValueError("自动正确率必须使用人工改值/决定之前的预测。")
            review = gold.get("review", {})
            if (gold.get("annotation_scope") != "whole_source_package" or
                review.get("kind") != "independent_human" or not review.get("reviewer_id") or
                not review.get("annotator_id") or review["reviewer_id"] == review["annotator_id"] or
                review.get("approved") is not True):
                raise ValueError("缺少整包独立人工复核；自动标注不满足此条件。")
            expected = gold.get("facts", [])
            if not expected or any(not all(k in row for k in GOLD_KEYS) or not row.get("source_locator") for row in expected):
                raise ValueError("金标需要完整身份/口径/状态及原件定位，不能用部分匹配评分。")
            inputs = package.get("sources", [])
            if not inputs:
                raise ValueError("缺少原件散列。")
            hashes = []
            for source in inputs:
                path = (base / source["path"]).resolve()
                if not path.is_relative_to(base.resolve()) or path.stat().st_size > 100 * 1024 * 1024:
                    raise ValueError("原件路径或大小无效。")
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != source["sha256"]:
                    raise ValueError("原件散列不匹配。")
                hashes.append(actual)
            fingerprint = tuple(sorted(hashes))
            if fingerprint in seen_originals:
                raise ValueError("同一原件不能拆成多个独立测试包计数。")
            seen_originals.add(fingerprint)
            if sorted(prediction.get("run_summary", {}).get("source_snapshots", {}).values()) != sorted(hashes):
                raise ValueError("预测未绑定此批原件的完整来源快照。")
            split = package.get("split")
            if split not in {"development", "holdout"}:
                raise ValueError("缺少开发/留出分组。")
            if split == "holdout":
                if package.get("used_for_development") is not False:
                    raise ValueError("已参与调试的数据不能算新盲测。")
                frozen = datetime.fromisoformat(manifest["holdout_frozen_at"])
                generated = datetime.fromisoformat(package["prediction_generated_at"])
                if frozen >= generated:
                    raise ValueError("必须先冻结留出清单，再运行最终预测。")
            rows = predictions(prediction)
            matched, predicted_matched = match_rows(expected, rows, GOLD_KEYS)
            verified = [i for i, p in enumerate(rows) if p["fact_status"] == "verified"]
            clear = [i for i, g in enumerate(expected) if g.get("clear") is True]
            # Re-match only automatic outputs to avoid credit for later edits.
            auto_matched, _ = match_rows(expected, [p for p in rows if p["automatic"]], GOLD_KEYS)
            critical_misses = sum(i not in matched and g.get("critical") is True for i, g in enumerate(expected))
            wrong_auto = sum(p["automatic"] and i not in predicted_matched for i, p in enumerate(rows))
            counts = {"expected": len(expected), "matched": len(matched), "verified": len(verified),
                      "verified_correct": sum(i in predicted_matched for i in verified),
                      "clear": len(clear), "clear_auto": sum(i in auto_matched for i in clear),
                      "critical_misses": critical_misses, "wrong_auto": wrong_auto}
            if not package.get("domain") or not package.get("supplier") or not package.get("format"):
                raise ValueError("缺少领域、供应商或格式分层。")
            for bucket in ("overall", "split:" + split, "domain:" + package["domain"], "format:" + package["format"]):
                for key, value in counts.items():
                    groups[bucket][key] += value
            split_counts[split] += 1
            suppliers.add(package["supplier"])
            domains.add(package["domain"])
            cases.append({"id": label, "metrics": metrics(counts)})
        except (KeyError, TypeError, ValueError, OSError) as exc:
            failures.append({"package": label, "reason": str(exc)})
    summary = {"packages": len(cases), "suppliers": len(suppliers), "domains": len(domains),
               "annotated_facts": groups["overall"]["expected"],
               "development_packages": split_counts["development"], "holdout_packages": split_counts["holdout"]}
    for key, threshold in MINIMUMS.items():
        if summary[key] < threshold:
            failures.append({"gate": key, "actual": summary[key], "minimum": threshold})
    measured = {name: metrics(counts) for name, counts in groups.items() if counts.get("expected")}
    for name, values in measured.items():
        for key, threshold in THRESHOLDS.items():
            if values[key] is None or values[key] < threshold:
                failures.append({"gate": name + ":" + key, "actual": values[key], "minimum": threshold})
        if values["critical_misses"] or values["wrong_auto"]:
            failures.append({"gate": name + ":serious_errors", "critical_misses": values["critical_misses"], "wrong_auto": values["wrong_auto"]})
    for evidence in ("real_user_trial", "continuous_batch_stability", "recovery_and_security", "unified_regression"):
        descriptor = manifest.get("release_evidence", {}).get(evidence)
        try:
            report = checked_json(base, descriptor or {})
            if report.get("status") != "passed" or not report.get("reviewer_id") or not report.get("completed_at"):
                raise ValueError("证据尚未通过或未经签认。")
        except (KeyError, TypeError, ValueError, OSError) as exc:
            failures.append({"gate": evidence, "reason": str(exc)})
    return {"schema_version": 1, "status": "passed" if not failures else "blocked", "summary": summary,
            "metrics": measured, "cases": cases, "failures": failures,
            "warning": "清单声明不替代人工真实性审查；本工具不会自动批准生产使用。"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    report = evaluate(data, args.manifest.resolve().parent)
    report["manifest_sha256"] = hashlib.sha256(args.manifest.read_bytes()).hexdigest()
    atomic_write_json(args.output, report)
    print(json.dumps({"status": report["status"], "summary": report["summary"], "failures": len(report["failures"])}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
