"""Rules vs local AI vs repeated input, through the real analysis workflow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.semantic_rescue_trial import prepare_case, measure, aggregate, digest, low_priority
from tests.fixtures.semantic_rescue_pilot import CASES as DEVELOPMENT
from tests.fixtures.semantic_reread_holdout import CASES as HOLDOUT
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.openvino_adapter import _generate_semantic
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.semantic_review import PROMPT, SEMANTIC_REVIEW_REVISION
from product_evidence_guard.state import atomic_write_json
from product_evidence_guard.runtime_resources import check_model_memory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--threads", default=4, type=int)
    args = parser.parse_args()
    out = args.output.resolve()
    if not out.is_relative_to((ROOT / ".runtime").resolve()):
        raise ValueError("Only a dedicated .runtime experiment directory is allowed")
    if not args.model.is_dir():
        raise ValueError("Existing model directory required; no downloads")
    cases = DEVELOPMENT + HOLDOUT
    frozen = {"cases": cases, "prompt": PROMPT, "revision": SEMANTIC_REVIEW_REVISION,
              "development_ids": [c["id"] for c in DEVELOPMENT], "holdout_ids": [c["id"] for c in HOLDOUT],
              "acceptance": "no new wrong confirmations or lost correct cases/conflicts; fewer pending items",
              "source": "handwritten_synthetic_not_business_blind_test"}
    out.mkdir(parents=True, exist_ok=True)
    frozen_path = out / "frozen.json"
    if frozen_path.exists() and json.loads(frozen_path.read_text("utf8")) != frozen:
        raise ValueError("Frozen inputs changed; use a new directory")
    atomic_write_json(frozen_path, frozen)
    result = {"status": "baseline", "started_at": datetime.now(timezone.utc).isoformat(),
              "frozen_sha256": digest(frozen), "cases": []}
    prepared = []
    low_priority()
    for case in cases:
        inputs, _records, product, seconds = prepare_case(case, out / case["id"])
        prepared.append((case, inputs))
        result["cases"].append({"id": case["id"], "cohort": "holdout" if case in HOLDOUT else "development",
                                "baseline": measure(case, product), "baseline_seconds": seconds})
    atomic_write_json(out / "result.json", result)
    print(json.dumps({"stage": "baseline", **aggregate(result["cases"], "baseline")}), flush=True)
    check_model_memory(args.model)
    import openvino_genai as genai
    start = time.perf_counter()

    class Backend:
        model_id = str(args.model)
        device = "CPU"

        def __init__(self):
            self.pipe = genai.VLMPipeline(str(args.model), "CPU", INFERENCE_NUM_THREADS=args.threads, NUM_STREAMS=1)

        def generate_semantic(self, prompt, *, max_new_tokens, deadline):
            return _generate_semantic(self.pipe, prompt, max_new_tokens=max_new_tokens, deadline=deadline)

    backend = Backend()
    reader = QwenVlReader(backend)
    result["runtime"] = {"model": str(args.model), "device": "CPU", "threads": args.threads,
                         "priority": "below_normal", "genai": genai.__version__,
                         "load_seconds": time.perf_counter() - start}
    print(json.dumps({"stage": "loaded", **result["runtime"]}), flush=True)
    result["status"] = "running"
    for index, (case, inputs) in enumerate(prepared):
        row = result["cases"][index]
        # Alternate order to avoid systematically assigning warm/cold effects.
        modes = ("single", "repeat") if index % 2 == 0 else ("repeat", "single")
        for mode in modes:
            print(json.dumps({"stage": "case_start", "id": case["id"], "mode": mode}), flush=True)
            output = out / case["id"] / mode
            started = time.perf_counter()
            try:
                summary = analyze_directory(inputs, output, device="CPU", preloaded_image_reader=reader,
                                            semantic_assist=True, semantic_repeat=mode == "repeat", preprocessing_workers=1)
                if summary["errors"]:
                    raise RuntimeError("Analysis failed; not an accuracy result: " + str(summary["errors"]))
            except Exception as exc:
                result.update(status="interrupted", interruption={"case": case["id"], "mode": mode,
                              "reason": type(exc).__name__ + ": " + str(exc)})
                atomic_write_json(out / "result.json", result)
                raise
            product = json.loads((output / "product-facts.json").read_text("utf8"))
            trace = json.loads((output / "semantic-review.json").read_text("utf8"))
            row[mode] = measure(case, product)
            row[mode + "_trace"] = trace
            row[mode + "_seconds"] = time.perf_counter() - started
            row[mode + "_errors"] = summary["errors"]
            atomic_write_json(out / "result.json", result)
            print(json.dumps({"stage": "case_done", "id": case["id"], "mode": mode,
                              "pass": row[mode]["passed"], "wrong_auto": row[mode]["wrong_auto_confirmed"],
                              "calls": trace["calls"], "accepted": trace["accepted"],
                              "seconds": row[mode + "_seconds"]}), flush=True)
    result["status"] = "complete"
    result["summaries"] = {cohort: {mode: aggregate([r for r in result["cases"] if cohort == "all" or r["cohort"] == cohort], mode)
                                    for mode in ("baseline", "single", "repeat")}
                           for cohort in ("all", "development", "holdout")}
    result["regressions_vs_rules"] = {mode: [r["id"] for r in result["cases"] if r["baseline"]["passed"] and not r[mode]["passed"]]
                                       for mode in ("single", "repeat")}
    result["repetition_wins"] = [r["id"] for r in result["cases"] if not r["single"]["passed"] and r["repeat"]["passed"]]
    result["repetition_losses"] = [r["id"] for r in result["cases"] if r["single"]["passed"] and not r["repeat"]["passed"]]
    atomic_write_json(out / "result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
