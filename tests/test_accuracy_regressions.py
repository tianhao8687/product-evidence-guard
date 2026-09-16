"""Frozen independently annotated cases from the September accuracy audit."""
from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.engine import analyze_directory


CASES = json.loads((Path(__file__).parent / "fixtures/accuracy_cases.json").read_text("utf-8"))["cases"]


def stable(value):
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [stable(item) for item in value]
    return value


def key(value):
    return json.dumps(stable(value), ensure_ascii=False, sort_keys=True)


class AccuracyRegressionTests(unittest.TestCase):
    def check_case(self, case):
        with tempfile.TemporaryDirectory() as directory:
            root, output = Path(directory) / "inputs", Path(directory) / "output"
            root.mkdir()
            for name, text in case["files"].items():
                (root / name).write_text(text, encoding=case.get("encoding", "utf-8"))
            summary = analyze_directory(root, output, preprocessing_workers=1)
            product = json.loads((output / "product-facts.json").read_text("utf-8"))
        self.assertEqual(summary["errors"], [], case["name"])
        by_id = {c["candidate_id"]: c for c in product["candidates"]}
        observed = []
        for group in product["facts"]:
            if group["field"] in {"sku", "model", "variant"}:
                continue
            field = "custom:" + group["field_label"] if group["field"].startswith("custom_") else group["field"]
            field = case.get("semantic_aliases", {}).get(field, field)
            if case.get("focus") and field not in case["focus"]:
                continue
            values = {key(by_id[c]["normalized_value"]): by_id[c]["normalized_value"] for c in group["candidate_ids"]}
            units = {by_id[c].get("normalized_unit") for c in group["candidate_ids"]}
            observed.append([field, group["scope"], group["fact_status"], list(values.values()),
                             next(iter(units)) if len(units) == 1 else sorted(str(u) for u in units)])
        def signatures(rows):
            return Counter(key([field, scope, state] + ([] if case.get("ignore_values") else
                [sorted(key(value) for value in values), unit])) for field, scope, state, values, unit in rows)
        self.assertEqual(signatures(observed), signatures(case["groups"]), (case["name"], observed))
        for value, sku in case.get("ownership", {}).items():
            self.assertTrue(any(str(c["normalized_value"]) == value and c["product_sku"] == sku
                                for c in product["candidates"] if c["field"] == "net_weight"))


for case in CASES:
    def run(self, case=case):
        self.check_case(case)
    setattr(AccuracyRegressionTests, "test_case_" + case["id"], run)


if __name__ == "__main__":
    unittest.main()
