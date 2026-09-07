"""Exercise the installed public Skill entry using only synthetic fixtures.

Run explicitly with the project's Python. No browser or cloud model is invoked.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--entry", default=str(root / "scripts/run.ps1"))
    args = parser.parse_args()
    case = root / ".runtime" / ("conversation-entry-" + uuid.uuid4().hex[:10])
    source, output = case / "input", case / "output"
    source.mkdir(parents=True)
    (source / "private-manual.txt").write_text("净重：320g\n型号：CONFIDENTIAL-DEMO-42\n", encoding="utf-8", newline="\n")
    (source / "private-package.txt").write_text("净重：300g\n", encoding="utf-8", newline="\n")
    transcript = []

    def call(operation, *arguments):
        process = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", args.entry, operation, *arguments],
            capture_output=True, encoding="utf-8", timeout=45,
        )
        response = json.loads(process.stdout.strip())
        transcript.append(response)
        (case / "transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        assert process.returncode == 0 and response["ok"], response.get("error")
        assert "review_url" not in response["result"]
        return response["result"]

    started = time.perf_counter()
    job = call("analyze", str(source), "--output", str(output), "--deterministic-only", "--background", "--brief")["job"]
    for _ in range(20):
        job = call("job", "--job-id", job["job_id"])["job"]
        if job["phase"] == "completed":
            break
        assert job["phase"] in {"queued", "running"}, job
        time.sleep(0.1)
    assert job["phase"] == "completed"
    common = ["--output-dir", str(output)]
    task = call("task", *common)
    assert task["next_action"] == "request_review_permission"
    permit = call("allow-review", *common, "--field", "net_weight", "--recipient", "Qoder",
                  "--reason", "测试操作者允许在 Qoder 展示合成样例的净重摘要")
    summary = call("review-summary", *common, "--review-id", permit["review_id"], "--recipient", "Qoder")
    summary_text = json.dumps(summary, ensure_ascii=False)
    assert "CONFIDENTIAL" not in summary_text and "private-manual" not in summary_text
    choices = summary["groups"][0]["choices"]
    chosen = next(c["choice"] for c in choices if c["value"] == 320)
    call("decide", *common, "--summary-id", summary["summary_id"], "--choice", chosen,
         "--recipient", "Qoder", "--action", "confirm", "--reason", "测试操作者明确采用此选项")
    grant = call("authorize", *common, "--summary-id", summary["summary_id"], "--choice", chosen,
                 "--recipient", "Qoder", "--purpose", "生成测试商品介绍")
    packet = call("handoff", *common, "--bundle-id", grant["bundle_id"], "--recipient", "Qoder")
    assert len(packet["facts"]) == 1 and packet["facts"][0]["value"] == 320
    draft = case / "draft.md"
    draft.write_text("测试文案\n净重：350g\n", encoding="utf-8", newline="\n")
    check_args = [*common, "--bundle-id", grant["bundle_id"], "--recipient", "Qoder", "--content-file", str(draft)]
    assert call("check-content", *check_args)["status"] == "blocked"
    draft.write_text("测试文案\n净重：0.32kg\n", encoding="utf-8", newline="\n")
    assert call("check-content", *check_args)["status"] == "covered_fields_match"
    assert call("export-table", *common)["confirmed_count"] == 1
    review_book = call("export-review", *common)
    assert review_book["status"] == "review_workbook_ready" and review_book["confirmed_row_count"] == 1
    assert Path(review_book["path"]).is_file() and not review_book["model_called"]
    delivery = call("export-local", *common, "--reason", "quota_exceeded")
    assert delivery["status"] == "local_delivery_ready"
    assert not delivery["network_sent"] and not delivery["model_called"]
    assert call("task", *common)["next_action"] == "local_delivery_ready"
    # Only this generated test copy is changed; source material belonging to the user is untouched.
    (source / "private-manual.txt").write_text("净重：330g\n", encoding="utf-8", newline="\n")
    artifacts = call("deliverables", *common)["deliverables"]
    assert all(a["status"] == ("needs_refresh" if a["artifact_id"] == review_book["artifact_id"] else "source_stale")
               for a in artifacts)
    assert next(a for a in artifacts if a["artifact_id"] == review_book["artifact_id"])["next_action"] == "export_review"
    assert any(2 in r["lines"] for a in artifacts for r in a["affected_references"])
    result = {"status": "passed", "seconds": round(time.perf_counter() - started, 3),
              "entry": args.entry, "commands": len(transcript), "browser_commands": 0,
              "generation": "synthetic fixture, not a cloud model run", "artifacts": str(case)}
    result["local_fallback"] = "passed: parameter CSV and factual brief; no cloud or model call"
    (case / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
