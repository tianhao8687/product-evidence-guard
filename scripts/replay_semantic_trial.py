"""Replay recorded model responses through the current guard, without inference.

This is a deterministic regression check on exposed samples, never a new
model accuracy test. Keep the original result/frozen files alongside it.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.semantic_rescue_trial import prepare_case, measure, aggregate, digest
from scripts.public_document_trial import code_digest
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.openvino_adapter import OpenVinoDeviceSelection
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.state import atomic_write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trial", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to((ROOT / ".runtime").resolve()) or args.output.exists():
        raise ValueError("Use a new, dedicated .runtime output")
    frozen = json.loads((args.trial / "frozen.json").read_text("utf8"))
    original = json.loads((args.trial / "result.json").read_text("utf8"))
    if original["status"] != "complete" or original["frozen_sha256"] != digest(frozen):
        raise ValueError("A complete matching frozen experiment is required")
    recorded = {r["id"]: r for r in original["cases"]}
    result = {"kind": "saved_response_replay_not_new_inference", "source_result_sha256": digest(original),
              "code_sha256": code_digest(), "cases": [], "complete": False}
    selection = OpenVinoDeviceSelection("CPU", "CPU", ("CPU",), {"CPU": "no hardware used"}, "recorded_response_replay")
    for case in frozen["cases"]:
        inputs, _, product, _ = prepare_case(case, args.output / case["id"])
        row = {"id": case["id"], "baseline": measure(case, product)}
        for mode in ("single", "repeat"):
            attempts = recorded[case["id"]][mode + "_trace"]["attempts"]

            class Backend:
                model_id, device = "recorded-output-replay", "CPU"
                calls = 0

                def generate_semantic(self, prompt, **options):
                    if self.calls >= len(attempts):
                        raise RuntimeError("Current routing requires an unrecorded response")
                    answer = attempts[self.calls]["raw_output"]
                    self.calls += 1
                    return answer

            backend = Backend()
            output = args.output / case["id"] / mode
            summary = analyze_directory(inputs, output, preloaded_image_reader=QwenVlReader(backend),
                                        device="CPU", device_selection=selection,
                                        semantic_assist=True, semantic_repeat=mode == "repeat", preprocessing_workers=1)
            if summary["errors"] or backend.calls != len(attempts):
                raise RuntimeError("Routing changed; this case cannot be compared by response replay")
            product = json.loads((output / "product-facts.json").read_text("utf8"))
            row[mode] = measure(case, product)
            row[mode + "_trace"] = json.loads((output / "semantic-review.json").read_text("utf8"))
        result["cases"].append(row)
        atomic_write_json(args.output / "result.json", result)
    result["summaries"] = {mode: aggregate(result["cases"], mode) for mode in ("baseline", "single", "repeat")}
    result["complete"] = code_digest() == result["code_sha256"]
    atomic_write_json(args.output / "result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
