"""Run untouched public originals against a pre-frozen checkpoint manifest.

No scorer derives gold from production code. The output needs source-side
adjudication: reading completion is not measured semantic recall.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product_evidence_guard.state import atomic_write_json
from product_evidence_guard.runtime_resources import lower_worker_priority


def code_digest():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "product_evidence_guard").glob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".txt"}:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--case")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text("utf8"))
    if not args.output.resolve().is_relative_to((ROOT / ".runtime").resolve()):
        raise ValueError("Use a dedicated .runtime output")
    lower_worker_priority()
    if args.case:
        from product_evidence_guard.engine import analyze_directory
        record = next(s for s in manifest["sources"] if s["case"] == args.case)
        source = args.inputs / args.case / "source.pdf"
        if hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError("Source changed since annotation")
        summary = analyze_directory(source.parent, args.output / args.case, device="CPU", preprocessing_workers=1,
                                    semantic_assist=bool(args.model),
                                    openvino_vlm_model=str(args.model) if args.model else None)
        print(json.dumps({"case": args.case, "counts": summary["fact_status_counts"],
                          "errors": summary["errors"], "local_ai": summary["local_ai"]["semantic_assist"],
                          "model_unavailable": summary["local_ai"]["unavailable_reason"]}), flush=True)
        return
    if (args.output / "runner.json").exists():
        raise ValueError("Do not overwrite observed results; use a fresh output")
    result = {"manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(),
              "code_sha256": code_digest(), "mode": "local_ai" if args.model else "rules", "results": [], "complete": False}
    for source in manifest["sources"]:
        if code_digest() != result["code_sha256"]:
            raise RuntimeError("Code changed during experiment; keep partial evidence and use a new output")
        started = time.perf_counter()
        command = [sys.executable, "-X", "utf8", str(Path(__file__)), "--manifest", str(args.manifest),
                   "--inputs", str(args.inputs), "--output", str(args.output), "--case", source["case"]]
        if args.model:
            command.extend(["--model", str(args.model)])
        try:
            run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf8", timeout=300,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            row = {"case": source["case"], "exit_code": run.returncode, "stdout": run.stdout[-6000:], "stderr": run.stderr[-2000:]}
        except subprocess.TimeoutExpired:
            row = {"case": source["case"], "error": "timeout_300_seconds"}
        row["seconds"] = round(time.perf_counter() - started, 3)
        if code_digest() != result["code_sha256"]:
            row["invalid_accuracy_result"] = "code_changed_during_case"
        result["results"].append(row)
        atomic_write_json(args.output / "runner.json", result)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    result["complete"] = all(row.get("exit_code") == 0 and not row.get("invalid_accuracy_result") for row in result["results"])
    atomic_write_json(args.output / "runner.json", result)


if __name__ == "__main__":
    main()
