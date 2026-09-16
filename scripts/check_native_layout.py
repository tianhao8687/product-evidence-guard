"""Targeted layout regression on previously exposed public originals, NOT blind accuracy."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.state import atomic_write_json
from product_evidence_guard.parsers import sha256_file

ORIGINALS = {"meanwell": "43ceb7255f0613bf50b1b749aa30c993e143ec72338d611f960c9e74269af3b7",
             "tplink": "49acbeb1d4032f92df8dd3ad67d659be9870461881d754da495b6e43cd170d98",
             "solar": "643fa07110fd0826e0612a1b0b52b1079b88cfe4eb45b3189f258779821ffaf0",
             "noctua": "15ca14b32763b7e65877cdad8e141e5318c11bd7798e112f147da643885d7c8d"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--originals", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    args = parser.parse_args()
    results = []
    for name, expected in ORIGINALS.items():
        original = args.originals / (name + ".pdf")
        if sha256_file(original) != expected:
            raise ValueError("Public original hash mismatch: " + name)
        source, output = args.output / name / "input", args.output / name / "output"
        source.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, source / original.name)
        started = time.perf_counter()
        summary = analyze_directory(source, output, openvino_vlm_model=str(args.model) if args.model else None,
                                    device="CPU", preprocessing_workers=1)
        product = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
        anchors = []
        if name == "meanwell":
            # 18 explicitly labelled cells visible in the source; these are a
            # targeted regression slice, not whole-document annotated recall.
            for voltage, current, power in ((5,7,35),(12,3,36),(15,2.4,36),(24,1.5,36),(36,1,36),(48,.8,38.4)):
                model = f"LRS-35-{voltage}"
                for field, value, scope in (("voltage",voltage,"output|signal:dc"),("current",current,"output|rating:rated"),("power",power,"output|rating:rated")):
                    matches = [g for g in product["facts"] if g["field"] == field and g.get("scope") == scope and
                               model in (g.get("product_label") or "") and g.get("selected_value") == value]
                    anchors.append({"model": model, "field": field, "value": value, "scope": scope,
                                    "passed": len(matches) == 1 and matches[0]["fact_status"] == "verified"})
        result = {"id": name, "original_sha256": expected, "seconds": round(time.perf_counter()-started, 3),
                  "candidates": summary["candidate_count"], "facts": summary["fact_count"],
                  "counts": summary["fact_status_counts"], "reading_issues": summary["reading_issues"],
                  "errors": summary["errors"], "delivery_readiness": product["delivery_readiness"], "anchors": anchors}
        results.append(result)
        atomic_write_json(args.output / "native-layout-results.json", {"kind": "exposed_original_targeted_regression_not_accuracy", "results": results})
        print(json.dumps({k:result[k] for k in ("id","seconds","candidates","facts","counts","errors")}, ensure_ascii=False), flush=True)
    return int(any(r["errors"] or any(not a["passed"] for a in r["anchors"]) for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
