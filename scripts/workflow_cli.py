"""CLI contracts for review and the content workflow, without cloud SDKs."""
from pathlib import Path
from product_evidence_guard.workflow import performance_summary


def add_commands(commands):
    warmup = commands.add_parser("warmup", help="提前预热千问，并保持常驻")
    warmup.add_argument("--model", dest="openvino_vlm_model", help="首次指定已有模型，之后使用记住的位置")
    warmup.add_argument("--device", default="CPU")
    warmup.add_argument("--allow-idle", action="store_true", help="预热后允许普通空闲退出；默认演示常驻")
    residency = commands.add_parser("residency", help="控制演示常驻，不重新加载模型")
    group = residency.add_mutually_exclusive_group(required=True)
    group.add_argument("--keep-alive", action="store_true")
    group.add_argument("--allow-idle", action="store_true")
    for name in ("job", "resume"):
        parser = commands.add_parser(name)
        parser.add_argument("--job-id", required=True)
    for name in ("review", "task", "authorize", "handoff", "revoke", "check-content", "deliverables", "export-table", "export-local", "export-review",
                 "allow-review", "review-summary", "decide", "revoke-review"):
        parser = commands.add_parser(name)
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--session-id")
        if name == "export-table":
            parser.add_argument("--mode", choices=("human", "verified"), default="human",
                                help="human 仅人工批准；verified 全部已确认参数（含自动核验）")
        if name == "authorize":
            selection = parser.add_mutually_exclusive_group(required=True)
            selection.add_argument("--candidate-id", action="append", dest="candidate_ids")
            selection.add_argument("--summary-id")
            parser.add_argument("--choice", action="append", dest="choices")
            parser.add_argument("--recipient", required=True)
            parser.add_argument("--purpose", required=True)
        if name in {"handoff", "revoke", "check-content"}:
            parser.add_argument("--bundle-id", required=True)
        if name in {"handoff", "check-content"}:
            parser.add_argument("--recipient", required=True)
        if name == "check-content":
            parser.add_argument("--content-file", required=True)
        if name == "export-local":
            parser.add_argument("--reason", choices=("local_only", "cloud_unavailable", "quota_exceeded", "generation_unverified"),
                                default="local_only", help="本地模板交付原因；不会调用模型或网络")
        if name in {"allow-review", "review-summary", "decide", "revoke-review"}:
            parser.add_argument("--recipient", required=True)
        if name == "allow-review":
            selection = parser.add_mutually_exclusive_group(required=True)
            selection.add_argument("--field", action="append", dest="fields")
            selection.add_argument("--all-fields", action="store_true")
            parser.add_argument("--reason", required=True)
        if name in {"review-summary", "revoke-review"}:
            parser.add_argument("--review-id", required=True)
        if name == "decide":
            parser.add_argument("--summary-id", required=True)
            parser.add_argument("--choice", required=True)
            parser.add_argument("--action", choices=("confirm", "reject"), required=True)
            parser.add_argument("--reason", required=True)


def payload(args):
    result = {k: v for k, v in vars(args).items() if k not in
              {"command", "continue_download", "allow_idle"} and v is not None}
    for key in ("output_dir", "content_file", "openvino_vlm_model"):
        if key in result:
            result[key] = str(Path(result[key]).expanduser().absolute())
    if args.command in {"warmup", "residency"}:
        result["keep_alive"] = not args.allow_idle
    return result


def brief_response(response):
    """Keep filenames, parser errors and model transcripts out of host context."""
    result = dict(response)
    if not result.get("ok"):
        result["error"] = {"code": (result.get("error") or {}).get("code", "operation_failed"),
                           "message": "任务未完成，请查询任务状态和本地错误记录。"}
        return result
    full = result.get("result", {})
    if result.get("operation") == "status":
        result["result"] = {"resident": full.get("resident", {}),
                            "available_operations": full.get("available_operations", []),
                            "processing": "local"}
        return result
    summary = full.get("summary")
    if summary is not None:
        result["result"] = {"output_dir": full.get("output_dir"),
                            "session_id": summary.get("session_id"),
                            "counts": {"files": summary.get("files_discovered", 0),
                                       "candidates": summary.get("candidate_count", 0),
                                       "errors": len(summary.get("errors", [])),
                                       **summary.get("confirmation_status_counts", {})},
                            "performance": performance_summary(summary),
                            "next_action": "inspect_task", "processing": "local"}
    return result
