"""Opt-in Qoder cloud round trip using synthetic, explicitly selected fields.

Does not load the local VLM. Qoder runs with no tools and no session persistence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", required=True)
    parser.add_argument("--qoder-script", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    case = root / ".runtime" / ("qoder-roundtrip-" + uuid.uuid4().hex[:10])
    source, output = case / "input", case / "output"
    source.mkdir(parents=True)
    (source / "private-synthetic-source.txt").write_text(
        "净重：320g\n型号：PRIVATE-NOT-FOR-HANDOFF\n", encoding="utf-8", newline="\n")
    transcript = []
    def call(operation, *arguments):
        process = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", args.entry, operation, *arguments],
            capture_output=True, encoding="utf-8", timeout=45,
        )
        response = json.loads(process.stdout.strip())
        transcript.append(response)
        (case / "local-transcript.json").write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        if process.returncode or not response["ok"]:
            raise RuntimeError(str(response.get("error")))
        return response["result"]

    call("analyze", str(source), "--output", str(output), "--deterministic-only", "--brief")
    common = ["--output-dir", str(output)]
    permission = call("allow-review", *common, "--recipient", "Qoder", "--field", "net_weight",
                      "--reason", "测试操作者允许将合成净重参数展示给 Qoder 并用于本次集成测试")
    summary = call("review-summary", *common, "--review-id", permission["review_id"], "--recipient", "Qoder")
    choice = summary["groups"][0]["choices"][0]["choice"]
    call("decide", *common, "--summary-id", summary["summary_id"], "--choice", choice,
         "--recipient", "Qoder", "--action", "confirm", "--reason", "测试操作者明确确认合成样例 320g")
    grant = call("authorize", *common, "--summary-id", summary["summary_id"], "--choice", choice,
                 "--recipient", "Qoder", "--purpose", "生成中文商品介绍并进行本地参数回检")
    packet = call("handoff", *common, "--bundle-id", grant["bundle_id"], "--recipient", "Qoder")
    packet_text = json.dumps(packet, ensure_ascii=False)
    assert "PRIVATE-NOT-FOR-HANDOFF" not in packet_text and "private-synthetic-source" not in packet_text
    (case / "outgoing-field-packet.json").write_text(packet_text, encoding="utf-8")
    prompt = ("你正在执行一次经授权的合成数据集成测试。根据下面的字段包生成简短中文 Markdown 商品介绍。"
              "仅使用 facts 中的商品参数，参数单独写成‘字段名称：数值单位’。不要补造型号、材质、认证或其他参数。"
              "只输出文案，不输出代码围栏，不读取文件，不调用工具。\n" + packet_text)
    attempts = []
    status = "not_verified"
    for attempt in range(1, 4):
        started = time.perf_counter()
        try:
            process = subprocess.run(
                ["node", args.qoder_script, "--model", args.model, "--tools", "", "--no-session-persistence",
                 "--max-model-request-retries", "0", "--max-output-tokens", "400", "-p", prompt],
                capture_output=True, encoding="utf-8", errors="replace", timeout=70,
            )
        except subprocess.TimeoutExpired:
            attempts.append({"attempt": attempt, "status": "timeout"})
            break
        (case / f"qoder-{attempt}-stdout.txt").write_text(process.stdout, encoding="utf-8")
        (case / f"qoder-{attempt}-stderr.txt").write_text(process.stderr, encoding="utf-8")
        record = {"attempt": attempt, "exit_code": process.returncode,
                  "seconds": round(time.perf_counter() - started, 3)}
        if process.returncode or not process.stdout.strip():
            record["status"] = "provider_or_cli_error"
            record["pricing_response"] = "pricingUrl" in process.stdout
            record["quota_exceeded"] = "credit usage limit" in (process.stdout + process.stderr).casefold()
            attempts.append(record)
            break
        draft = case / "generated.md"
        draft.write_text(process.stdout, encoding="utf-8", newline="\n")
        checked = call("check-content", *common, "--bundle-id", grant["bundle_id"], "--recipient", "Qoder",
                       "--content-file", str(draft))
        record.update(status=checked["status"], finding_count=checked["finding_count"], claim_count=checked["claim_count"])
        attempts.append(record)
        if checked["status"] == "covered_fields_match":
            call("export-table", *common)
            status = "passed"
            break
        prompt = ("请依据授权字段包修正下面文案，解决本地回检指出的问题。仍只输出简短中文 Markdown，"
                  "参数单独写成‘字段名称：数值单位’，不补造未提供的参数。\n字段包：" + packet_text
                  + "\n原文案：" + process.stdout + "\n检查结果：" + json.dumps(checked, ensure_ascii=False))
    local_delivery = None
    if status != "passed":
        reason = "quota_exceeded" if any(a.get("quota_exceeded") for a in attempts) else "cloud_unavailable"
        local_delivery = call("export-local", *common, "--reason", reason)
    result = {"status": status, "model": args.model, "attempts": attempts, "artifacts": str(case),
              "local_vlm_loaded": False, "data": "synthetic net_weight only", "browser_commands": 0,
              "qoder_tools_enabled": False, "local_operations": len(transcript), "local_delivery": local_delivery}
    (case / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
