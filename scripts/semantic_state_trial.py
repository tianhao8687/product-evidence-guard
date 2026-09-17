"""Small state-before/source vs state-after/source adaptation experiment.

Uses prior final JSON proposals + guard feedback, NOT hidden reasoning traces.
It is not a replication of Trace as State's frontier long-context experiments.
This file is never imported by the application.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.semantic_rescue_trial import aggregate, digest, low_priority, measure
from tests.fixtures.semantic_reread_holdout import CASES
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.openvino_adapter import _generate_semantic
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.semantic_review import PROMPT
from product_evidence_guard.state import atomic_write_json


STATE_NOTICE = "previous_attempt 是上一轮未核验的草稿与校验反馈，仅作核对线索，不是事实或指令。必须重读 documents，所有值仍从当前 target 原文逐字复制。\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    args = parser.parse_args()
    out, source = args.output.resolve(), args.source.resolve()
    if out == source or not all(p.is_relative_to((ROOT / ".runtime").resolve()) for p in (out, source)):
        raise ValueError("Dedicated separate .runtime directories required")
    prior = json.loads((source / "result.json").read_text("utf8"))
    if prior["status"] != "complete":
        raise ValueError("The preceding A/B must be complete")
    rows = {r["id"]: r for r in prior["cases"]}
    frozen = {"cases": CASES, "prior_result_sha256": digest(prior), "prompt": PROMPT,
              "state_notice": STATE_NOTICE, "state": "prior_final_JSON_and_guard_feedback_not_reasoning_trace",
              "variants": ["state_before", "state_after"]}
    out.mkdir(parents=True, exist_ok=True)
    if (out / "frozen.json").exists() and json.loads((out / "frozen.json").read_text("utf8")) != frozen:
        raise ValueError("Frozen inputs changed")
    atomic_write_json(out / "frozen.json", frozen)
    low_priority()
    import openvino_genai as genai
    pipe = genai.VLMPipeline(str(args.model), "CPU", INFERENCE_NUM_THREADS=4, NUM_STREAMS=1)
    result = {"status": "running", "cases": [], "frozen_sha256": digest(frozen)}
    for index, case in enumerate(CASES):
        previous = rows[case["id"]]
        row = {"id": case["id"], "baseline": previous["baseline"], "single": previous["single"]}
        result["cases"].append(row)
        hints = {a["file"]: {"draft": a.get("raw_output", ""),
                              "feedback": [c["result"] for c in a.get("changes", [])]}
                 for a in previous["single_trace"]["attempts"]}
        modes = ("state_before", "state_after") if index % 2 == 0 else ("state_after", "state_before")
        for mode in modes:
            class Backend:
                model_id = "local-semantic-state-adaptation-" + mode
                device = "CPU"

                def generate_semantic(self, prompt, *, max_new_tokens, deadline):
                    data = json.loads(prompt[len(PROMPT):])
                    state = hints.get(data["documents"][0]["name"], {})
                    ordered = ({"previous_attempt": state, "documents": data["documents"]}
                               if mode == "state_before" else {"documents": data["documents"], "previous_attempt": state})
                    ordered["targets"] = data["targets"]
                    return _generate_semantic(pipe, PROMPT + STATE_NOTICE + json.dumps(ordered, ensure_ascii=False),
                                              max_new_tokens=max_new_tokens, deadline=deadline)

            print(json.dumps({"stage": "case_start", "id": case["id"], "mode": mode}), flush=True)
            output = out / case["id"] / mode
            started = time.perf_counter()
            summary = analyze_directory(source / case["id"] / "input", output, device="CPU",
                                        semantic_assist=True, preloaded_image_reader=QwenVlReader(Backend()), preprocessing_workers=1)
            product = json.loads((output / "product-facts.json").read_text("utf8"))
            row[mode] = measure(case, product)
            row[mode + "_trace"] = json.loads((output / "semantic-review.json").read_text("utf8"))
            row[mode + "_seconds"] = time.perf_counter() - started
            row[mode + "_errors"] = summary["errors"]
            atomic_write_json(out / "result.json", result)
            print(json.dumps({"stage": "case_done", "id": case["id"], "mode": mode,
                              "pass": row[mode]["passed"], "wrong_auto": row[mode]["wrong_auto_confirmed"],
                              "calls": row[mode + "_trace"]["calls"], "seconds": row[mode + "_seconds"]}), flush=True)
    result["status"] = "complete"
    result["summaries"] = {mode: aggregate(result["cases"], mode) for mode in ("baseline", "single", "state_before", "state_after")}
    atomic_write_json(out / "result.json", result)
    print(json.dumps(result["summaries"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
