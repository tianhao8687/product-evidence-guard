"""One delivery gate shared by UI, CLI and local/handoff artifacts.

Fact status describes a value; reading coverage describes the input. Neither
is a measured semantic recall claim. Keep them separate and explain both.
"""
import hashlib
import json


def reading_issue(*, file: str, file_hash: str, **issue) -> dict:
    record = {"file": file, "file_hash": file_hash, **issue}
    key = [file, file_hash, issue.get("code"), issue.get("locator", {})]
    record["issue_id"] = hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    return record


def coverage_summary(summary: dict) -> dict:
    issues = [*summary.get("errors", []), *summary.get("skipped_files", []), *summary.get("reading_issues", [])]
    unfinished = {str(item["file"]) for item in issues if isinstance(item, dict) and item.get("file")}
    pages = sum(int((summary.get(key) or {}).get("pages_skipped", 0)) for key in ("scanned_pdf", "mixed_pdf"))
    images = int((summary.get("document_visuals") or {}).get("embedded_images_skipped", 0))
    visual_issues = int((summary.get("document_visuals") or {}).get("issues", 0))
    partial = bool(issues or pages or images or visual_issues)
    total = int(summary.get("files_discovered", 0))
    message = f"已扫描 {total} 份资料。"
    if partial:
        message += f"其中 {len(unfinished)} 份尚未完整核对。" if unfinished else "部分内容尚未完整核对。"
        message += "可先导出已确认部分；不能作为完整结果交付。"
    else:
        message += "本次读取已完成；不代表资料中的所有参数都已被识别。"
    return {"status": "partial" if partial else "complete", "files": total,
            "unfinished_files": len(unfinished), "unfinished_pages": pages,
            "unfinished_images": images, "message": message, "can_retry": partial,
            "issue_count": len(issues) + (1 if pages or images or visual_issues else 0)}


def assess_delivery(product: dict, *, product_ids: list[str] | None = None) -> dict:
    selected = set(product_ids or [])
    facts = [g for g in product.get("facts", []) if not g.get("excluded") and
             (not selected or g.get("product_id") in selected)]
    blockers = []
    for status, code, message in (("conflict", "conflict", "存在尚未处理的冲突"),
                                   ("pending_confirmation", "pending", "存在尚未确认的参数")):
        count = sum(g.get("fact_status") == status for g in facts)
        if count:
            blockers.append({"code": code, "count": count, "message": message})
    if any(g.get("current") is False for g in facts):
        blockers.append({"code": "stale", "message": "部分参数来源已变化，请重新读取"})
    summary = product.get("run_summary", {})
    # Only an explicit ownership declaration can restrict an unread region.
    # Unknown ownership must never be assumed irrelevant to the selection.
    scoped = dict(summary)
    if selected:
        for key in ("reading_issues", "errors", "skipped_files"):
            scoped[key] = [i for i in summary.get(key, []) if not i.get("product_ids") or selected.intersection(i["product_ids"])]
    coverage = coverage_summary(scoped)
    if any(i.get("code") == "source_snapshot_changed" for i in scoped.get("reading_issues", [])):
        blockers.append({"code": "source_snapshot_changed", "message": "原资料已改变或移除，请重新读取"})
    if coverage["status"] == "partial":
        blockers.append({"code": "incomplete_reading", "count": coverage["issue_count"], "message": coverage["message"]})
    verified = sum(g.get("fact_status") == "verified" and g.get("current", True) for g in facts)
    if not verified:
        blockers.append({"code": "no_verified_facts", "message": "尚无可交付的已确认参数"})
    return {"status": "complete" if not blockers else "partial" if verified else "blocked",
            "complete": not blockers, "can_export_partial": bool(verified), "verified_count": verified,
            "blockers": blockers, "message": "可以完整导出本次核验结果。" if not blockers else
                "；".join(b["message"] for b in blockers),
            "coverage": coverage, "product_ids": sorted(selected),
            "qualification": "reading_and_fact_checks_only_not_accuracy_certification"}
