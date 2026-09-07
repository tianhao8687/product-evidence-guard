"""Minimal, explicitly permitted review choices for a host conversation."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
import uuid

from .confirmation import ConfirmationRequestError, apply_decision, _load_json_object
from .extractor import FIELD_SPECS
from .state import atomic_write_json
from . import workflow


FIELD_LABELS = {spec.name: spec.label for spec in FIELD_SPECS}


def allow_review(output_dir, *, recipient: str, reason: str, fields: list[str] | None = None,
                 all_fields: bool = False, session_id: str | None = None) -> dict:
    output, product, _ = workflow.context(output_dir, session_id)
    session = product["run_summary"]["session_id"]
    if not isinstance(recipient, str) or not recipient.strip() or len(recipient) > 120:
        raise ConfirmationRequestError("请指定本次参数摘要的接收方。")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
        raise ConfirmationRequestError("请记录用户对在宿主展示这些参数摘要的明确授权。")
    if all_fields and fields:
        raise ConfirmationRequestError("字段列表与全部当前字段不能同时指定。")
    selected = sorted({c["field"] for c in product["candidates"]}) if all_fields else sorted(set(fields or []))
    if not selected or any(f not in FIELD_LABELS for f in selected):
        raise ConfirmationRequestError("请指定受支持的商品字段；没有候选时无需开启对话审核。")
    recipient = recipient.strip()
    manifest = workflow._manifest(output, session)
    grants = manifest.setdefault("review_access", {})
    for review_id, grant in grants.items():
        if (grant["session_id"] == session and grant["status"] == "active"
                and grant["recipient"] == recipient and grant["fields"] == selected):
            return {"review_id": review_id, "recipient": recipient, "fields": selected,
                    "status": "authorized", "reused": True, "network_sent": False}
    review_id = uuid.uuid4().hex
    grants[review_id] = {"session_id": session, "recipient": recipient, "fields": selected,
                        "status": "active", "reason": reason.strip(), "created_at": workflow.now()}
    atomic_write_json(output / workflow.WORKFLOW_FILE, manifest)
    return {"review_id": review_id, "recipient": recipient, "fields": selected,
            "status": "authorized", "reused": False, "network_sent": False}


def _grant(manifest, review_id, recipient, session):
    grant = manifest.get("review_access", {}).get(review_id)
    if not grant or grant["status"] != "active" or grant["recipient"] != recipient or grant["session_id"] != session:
        raise ConfirmationRequestError("没有匹配的有效对话审核授权，请先确认允许在该宿主展示的字段范围。")
    return grant


def _snapshot(output, product, fields):
    state = _load_json_object(output / "analysis-state.json")
    root = Path(state["input_root"]).resolve()
    rows = sorted((c for c in product["candidates"] if c["field"] in fields),
                  key=lambda c: (c["field"], c["source_file"], c["candidate_id"]))
    source_hashes = {}
    for c in rows:
        name = c["source_file"]
        if name in source_hashes:
            continue
        try:
            source = root / name
            if not source.resolve().is_relative_to(root):
                raise ValueError("source outside input")
            source_hashes[name] = hashlib.sha256(workflow.safe_file(source, max_bytes=100 * 1024 * 1024)).hexdigest()
        except (OSError, ValueError):
            source_hashes[name] = None
    fingerprint = workflow.digest([
        {**{k: c.get(k) for k in ("candidate_id", "field", "normalized_value", "normalized_unit", "scope", "file_hash")},
         "current_source_hash": source_hashes[c["source_file"]]} for c in rows
    ])
    return rows, source_hashes, fingerprint


def _source_type(kind):
    kind = str(kind).lower()
    if "image" in kind:
        return "图片"
    if "pdf" in kind:
        return "PDF"
    if any(s in kind for s in ("csv", "xlsx", "spreadsheet")):
        return "表格"
    if "docx" in kind:
        return "文档"
    return "文本资料"


def review_summary(output_dir, *, review_id: str, recipient: str, session_id: str | None = None) -> dict:
    output, product, _ = workflow.context(output_dir, session_id)
    session = product["run_summary"]["session_id"]
    manifest = workflow._manifest(output, session)
    grant = _grant(manifest, review_id, recipient, session)
    rows, hashes, fingerprint = _snapshot(output, product, grant["fields"])
    summary_id = workflow.digest([review_id, fingerprint])[:24]
    aliases = {name: f"来源{i + 1}" for i, name in enumerate(sorted(hashes))}
    mapping = {}
    from .review_workbook import group_candidates
    groups = group_candidates(rows, hashes)
    choices_by_id = {}
    needs_reanalysis = False
    for i, c in enumerate(rows, 1):
        choice = f"A{i}"
        current = hashes[c["source_file"]] == c["file_hash"]
        needs_reanalysis |= not current
        item = {"choice": choice, "source": aliases[c["source_file"]],
                "source_type": _source_type(c["source_kind"]),
                "status": c["status"] if current else "source_changed"}
        if current:
            item.update(value=c["normalized_value"], unit=c["normalized_unit"], scope=c.get("scope"))
        mapping[choice] = c["candidate_id"]
        choices_by_id[c["candidate_id"]] = item
    for group in groups:
        group["choices"] = [choices_by_id[cid] for cid in group.pop("candidate_ids")]
    summaries = manifest.setdefault("review_summaries", {})
    summaries[summary_id] = {"review_id": review_id, "session_id": session, "fingerprint": fingerprint,
                             "choices": mapping, "created_at": workflow.now()}
    atomic_write_json(output / workflow.WORKFLOW_FILE, manifest)
    return {"summary_id": summary_id, "review_id": review_id, "session_id": session,
            "groups": groups, "needs_reanalysis": needs_reanalysis,
            "next_action": "reanalyze" if needs_reanalysis else "ask_user_choice" if rows else "request_more_material",
            "data_scope": "仅包含获准字段的标准值、单位、口径、状态、来源别名和类型；不包含原文件名、正文或图片。"}


def resolve_choices(output_dir, *, summary_id: str, choices: list[str], recipient: str,
                    session_id: str | None = None) -> tuple[str, list[str]]:
    output, product, _ = workflow.context(output_dir, session_id)
    session = product["run_summary"]["session_id"]
    manifest = workflow._manifest(output, session)
    summary = manifest.get("review_summaries", {}).get(summary_id)
    if not summary or summary["session_id"] != session:
        raise ConfirmationRequestError("对话中的候选列表已不可用，请重新获取参数摘要。")
    grant = _grant(manifest, summary["review_id"], recipient, session)
    rows, hashes, fingerprint = _snapshot(output, product, grant["fields"])
    if fingerprint != summary["fingerprint"]:
        raise ConfirmationRequestError("候选或来源已变化，请重新分析并展示新的选项后再作决定。")
    if not choices or any(choice not in summary["choices"] for choice in choices):
        raise ConfirmationRequestError("该选项不在这次对话的候选列表中，请明确选择已展示的编号。")
    selected = [summary["choices"][choice] for choice in dict.fromkeys(choices)]
    by_id = {c["candidate_id"]: c for c in rows}
    if any(hashes[by_id[cid]["source_file"]] != by_id[cid]["file_hash"] for cid in selected):
        raise ConfirmationRequestError("选择对应的来源已经变化，请先重新分析。")
    return session, selected


def decide(output_dir, *, summary_id: str, choice: str, recipient: str, action: str,
           reason: str, session_id: str | None = None) -> dict:
    session, selected = resolve_choices(output_dir, summary_id=summary_id, choices=[choice],
                                        recipient=recipient, session_id=session_id)
    result = apply_decision(output_dir, session_id=session, candidate_id=selected[0], action=action, reason=reason)
    return {"session_id": session, "summary_id": summary_id, "choice": choice,
            "field": result.field, "status": result.status, "next_action": "continue_requested_workflow"}


def revoke_review(output_dir, *, review_id: str, recipient: str, session_id: str | None = None) -> dict:
    output, product, _ = workflow.context(output_dir, session_id)
    session = product["run_summary"]["session_id"]
    manifest = workflow._manifest(output, session)
    grant = _grant(manifest, review_id, recipient, session)
    grant.update(status="revoked", revoked_at=workflow.now())
    atomic_write_json(output / workflow.WORKFLOW_FILE, manifest)
    return {"review_id": review_id, "status": "revoked"}
