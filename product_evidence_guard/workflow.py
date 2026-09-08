"""Local approval handoff, content checks, and versioned deliverable tracking."""
from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
from html import escape
import io
import json
from pathlib import Path
import re
import stat
from typing import Any
import uuid

from .confirmation import ConfirmationRequestError, export_confirmed, _load_json_object
from .content_check import check_draft, value_key, LABELS
from .state import atomic_write_json, atomic_write_text


WORKFLOW_FILE = "content-workflow.json"
MAX_CONTENT_BYTES = 2_000_000
LOCAL_DELIVERY_REASONS = {
    "local_only": "用户选择本地处理",
    "cloud_unavailable": "云端服务暂不可用",
    "quota_exceeded": "宿主返回额度不足",
    "generation_unverified": "云端生成结果尚未通过核验",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def safe_output(output_dir: str | Path) -> Path:
    original = Path(output_dir).expanduser().absolute()
    for ancestor in (original, *original.parents):
        if ancestor.is_symlink() or (ancestor.exists() and getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400):
            raise ConfirmationRequestError("输出目录不能经过符号链接或重解析点。")
    output = original.resolve()
    if not output.is_dir():
        raise ConfirmationRequestError("请先分析商品资料，输出目录尚不存在。")
    return output


def safe_file(path: Path, *, max_bytes: int = MAX_CONTENT_BYTES) -> bytes:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (ancestor.exists() and getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400):
            raise ConfirmationRequestError("文件路径不能经过符号链接或重解析点。")
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > max_bytes:
        raise ConfirmationRequestError("文件不是受支持的普通文件，或超过大小限制。")
    return path.read_bytes()


def context(output_dir: str | Path, session_id: str | None = None) -> tuple[Path, dict, list[dict]]:
    output = safe_output(output_dir)
    product = _load_json_object(output / "product-facts.json")
    actual = product["run_summary"]["session_id"]
    if session_id and session_id != actual:
        raise ConfirmationRequestError("会话已变化，请使用当前任务摘要。")
    # Revalidates source hashes even if the user has not re-run analysis yet.
    export_confirmed(output, session_id=actual)
    product = _load_json_object(output / "product-facts.json")
    confirmed = _load_json_object(output / "confirmed-product-facts.json")["facts"]
    candidates = {c["candidate_id"]: c for c in product["candidates"]}
    for fact in confirmed:
        fact["scope"] = candidates[fact["candidate_id"]].get("scope")
    return output, product, confirmed


def _manifest(output: Path, session: str) -> dict:
    path = output / WORKFLOW_FILE
    if not path.exists():
        return {"schema_version": 1, "bundles": {}, "deliverables": {}, "session_id": session}
    data = _load_json_object(path)
    if data.get("schema_version") != 1 or not all(isinstance(data.get(k), dict) for k in ("bundles", "deliverables")):
        raise ConfirmationRequestError("内容工作流记录无效，已停止写入。")
    # Keep historical artifacts across sessions; their source refs will be stale.
    data["session_id"] = session
    return data


def _public_fact(fact: dict, session: str) -> dict:
    identity = {k: fact.get(k) for k in ("candidate_id", "file_hash", "normalized_value", "normalized_unit", "scope", "product_id")}
    return {"fact_id": digest([session, identity])[:24], "field": fact["field"],
            "field_label": fact["field_label"], "value": fact["normalized_value"],
            "unit": fact["normalized_unit"], "scope": fact.get("scope"),
            "product_id": fact.get("product_id")}


def _current_refs(confirmed: list[dict], session: str) -> dict[str, dict]:
    return {_public_fact(f, session)["fact_id"]: f for f in confirmed}


def create_handoff(output_dir: str | Path, *, candidate_ids: list[str], recipient: str,
                   purpose: str, session_id: str | None = None) -> dict:
    output, product, confirmed = context(output_dir, session_id)
    session = product["run_summary"]["session_id"]
    if not isinstance(candidate_ids, list) or not candidate_ids or any(not isinstance(i, str) for i in candidate_ids):
        raise ConfirmationRequestError("请明确选择允许使用的已确认参数。")
    recipient, purpose = recipient.strip(), purpose.strip()
    if not recipient or not purpose or len(recipient) > 120 or len(purpose) > 1000:
        raise ConfirmationRequestError("请填写接收方和本次使用目的。")
    by_id = {f["candidate_id"]: f for f in confirmed}
    if any(i not in by_id for i in candidate_ids):
        raise ConfirmationRequestError("选择中包含未确认、被拒绝或已经失效的参数。")
    selected = [by_id[i] for i in dict.fromkeys(candidate_ids)]
    seen: dict[tuple, tuple] = {}
    for fact in selected:
        key = (fact.get("product_id"), fact["field"], fact.get("scope"))
        value = value_key(fact["normalized_value"], fact["normalized_unit"])
        if key in seen and seen[key] != value:
            raise ConfirmationRequestError("同一字段仍有相互矛盾的已确认值，请先处理后再授权。")
        seen[key] = value
    bundle_id = uuid.uuid4().hex
    packet = {"schema_version": 1, "bundle_id": bundle_id, "recipient": recipient,
              "purpose": purpose, "facts": [_public_fact(f, session) for f in selected]}
    packet["version"] = digest(packet)
    manifest = _manifest(output, session)
    manifest["bundles"][bundle_id] = {"packet": packet, "status": "active", "created_at": now(),
                                      "session_id": session,
                                      "candidate_ids": [f["candidate_id"] for f in selected]}
    atomic_write_json(output / WORKFLOW_FILE, manifest)
    # Creating a packet is local; no network call happens here.
    return {"bundle_id": bundle_id, "version": packet["version"], "field_count": len(selected),
            "recipient": recipient, "status": "authorized", "network_sent": False}


def _bundle(manifest: dict, bundle_id: str) -> dict:
    bundle = manifest["bundles"].get(bundle_id)
    if not isinstance(bundle, dict):
        raise ConfirmationRequestError("找不到该字段授权，请在对话中重新授权选定参数。")
    return bundle


def get_handoff(output_dir: str | Path, *, bundle_id: str, recipient: str,
                session_id: str | None = None) -> dict:
    output, product, confirmed = context(output_dir, session_id)
    session = product["run_summary"]["session_id"]
    bundle = _bundle(_manifest(output, session), bundle_id)
    if bundle["status"] != "active" or bundle["packet"]["recipient"] != recipient:
        raise ConfirmationRequestError("授权已撤销或接收方不匹配。")
    current = _current_refs(confirmed, session)
    if bundle["session_id"] != session or any(f["fact_id"] not in current for f in bundle["packet"]["facts"]):
        raise ConfirmationRequestError("字段包已失效，请重新审核并授权当前参数。")
    packet = bundle["packet"]
    if packet.get("version") != digest({k: v for k, v in packet.items() if k != "version"}):
        raise ConfirmationRequestError("字段包完整性检查失败。")
    if any(f != _public_fact(current[f["fact_id"]], session) for f in packet["facts"]):
        raise ConfirmationRequestError("字段包与当前已确认事实不一致。")
    return bundle["packet"]


def revoke_handoff(output_dir: str | Path, *, bundle_id: str, session_id: str | None = None) -> dict:
    output, product, _ = context(output_dir, session_id)
    manifest = _manifest(output, product["run_summary"]["session_id"])
    bundle = _bundle(manifest, bundle_id)
    bundle.update(status="revoked", revoked_at=now())
    atomic_write_json(output / WORKFLOW_FILE, manifest)
    return {"bundle_id": bundle_id, "status": "revoked"}


def read_content(path: Path, raw: bytes | None = None) -> str:
    if path.suffix.lower() not in {".txt", ".md", ".csv", ".tsv"}:
        raise ConfirmationRequestError("内容回检支持 UTF-8 TXT、Markdown、CSV 和 TSV 文件。")
    raw = safe_file(path) if raw is None else raw
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ConfirmationRequestError("请将文案保存为 UTF-8 编码后再检查。") from exc
    if path.suffix.lower() in {".csv", ".tsv"}:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        if rows and rows[0][:3] == ["参数", "值", "单位"]:
            return "\n" + "\n".join(f"{row[0]}：{row[1]}{row[2]}" for row in rows[1:] if len(row) >= 3)
        if rows and len(rows[0]) > 1:
            # Preserve source row numbers for CSV feedback.
            return "\n" + "\n".join("；".join(f"{key}：{value}" for key, value in zip(rows[0], row)) for row in rows[1:])
    return text


def check_content(output_dir: str | Path, *, content_file: str, bundle_id: str,
                  recipient: str, session_id: str | None = None) -> dict:
    packet = get_handoff(output_dir, bundle_id=bundle_id, recipient=recipient, session_id=session_id)
    output, product, _ = context(output_dir, session_id)
    path = Path(content_file).expanduser().absolute()
    raw = safe_file(path)
    text = read_content(path, raw)
    if not text.strip():
        raise ConfirmationRequestError("文案为空，请先保存生成内容。")
    result = check_draft(text, packet["facts"])
    if safe_file(path) != raw:
        raise ConfirmationRequestError("文案在检查期间发生变化，请重新检查。")
    manifest = _manifest(output, product["run_summary"]["session_id"])
    artifact_id = digest(str(path))[:24]
    previous = manifest["deliverables"].get(artifact_id, {})
    revision = int(previous.get("revision", 0)) + 1
    display_name = path.name
    if path.parent == output / "drafts":
        display_name = text.splitlines()[0].strip(" #\t")[:60] or "商品介绍草稿"
    record = {"artifact_id": artifact_id, "name": display_name, "path": str(path),
              "file_hash": hashlib.sha256(raw).hexdigest(), "bundle_id": bundle_id,
              "bundle_version": packet["version"], "checked_at": now(), "revision": revision,
              "references": [{"fact_id": fid, "lines": sorted({c["line"] for c in result["claims"] if fid in c["fact_ids"]}
                                    | {f["line"] for f in result["findings"] if any(x["fact_id"] == fid for x in f.get("expected", []))})}
                             for fid in result["used_fact_ids"]], "check": result}
    manifest["deliverables"][artifact_id] = record
    atomic_write_json(output / WORKFLOW_FILE, manifest)
    return {"artifact_id": artifact_id, "bundle_id": bundle_id, "revision": revision, **result}


def deliverable_status(record: dict, current_refs: dict[str, dict], bundles: dict, *, review_version=None) -> dict:
    affected = [r for r in record["references"] if r["fact_id"] not in current_refs]
    status = record["check"]["status"]
    try:
        modified = hashlib.sha256(safe_file(Path(record["path"]),
            max_bytes=20_000_000 if record.get("kind") == "review_workbook" else MAX_CONTENT_BYTES)).hexdigest() != record["file_hash"]
    except (OSError, ValueError):
        modified = True
    if affected:
        status = "source_stale"
    elif modified:
        status = "content_changed"
    elif bundles.get(record["bundle_id"], {}).get("status") == "revoked":
        status = "authorization_revoked"
    if record.get("kind") == "review_workbook" and record.get("review_version") != review_version:
        status = "needs_refresh"
    return {"artifact_id": record["artifact_id"], "name": record["name"], "status": status,
            "checked_at": record["checked_at"], "revision": record["revision"],
            "affected_references": affected, "finding_count": record["check"]["finding_count"],
            "bundle_id": record["bundle_id"],
            "next_action": "export_review" if record.get("kind") == "review_workbook" and status != "review_snapshot"
                           else "reanalyze_and_regenerate" if status == "source_stale"
                           else "recheck_content" if status == "content_changed" else "none"}


def performance_summary(summary: dict) -> dict:
    """Timing and reuse facts only; no source names, raw text or model paths."""
    return {"analysis_seconds": summary.get("analysis_seconds"),
            "model_load_seconds": summary.get("model_load_seconds"),
            "model_reused": summary.get("local_ai", {}).get("model_reused"),
            "unchanged_files_reused": len(summary.get("unchanged_files_reused", []))}


def task_snapshot(output_dir: str | Path, *, session_id: str | None = None, detailed: bool = False) -> dict:
    output, product, confirmed = context(output_dir, session_id)
    summary = product["run_summary"]
    session = summary["session_id"]
    manifest = _manifest(output, session)
    current = _current_refs(confirmed, session)
    review_version = None
    if any(r.get("kind") == "review_workbook" for r in manifest["deliverables"].values()):
        from .review_workbook import snapshot
        review_version = snapshot(output, product)[2]
    deliverables = [deliverable_status(r, current, manifest["bundles"], review_version=review_version)
                    for r in manifest["deliverables"].values()]
    bundles = [{"bundle_id": bid, "recipient": b["packet"]["recipient"],
                "field_count": len(b["packet"]["facts"]), "created_at": b["created_at"],
                "status": b["status"] if b["status"] != "active" else
                    "active" if b["session_id"] == session and all(f["fact_id"] in current for f in b["packet"]["facts"]) else "stale"}
               for bid, b in manifest["bundles"].items()]
    counts = summary.get("confirmation_status_counts", {})
    pending = counts.get("pending", 0)
    errors = len(summary.get("errors", []))
    phase = "needs_attention" if errors or counts.get("stale", 0) else "awaiting_review" if pending else "ready_to_export"
    if not product["candidates"] and not errors and not counts.get("stale", 0):
        phase = "no_candidates"
    field_counts = Counter(c["field"] for c in product["candidates"] if c["field"] in LABELS)
    review_permissions = [{"review_id": rid, "recipient": grant["recipient"], "fields": grant["fields"]}
                          for rid, grant in manifest.get("review_access", {}).items()
                          if grant["status"] == "active" and grant["session_id"] == session]
    active_handoffs = [b for b in bundles if b["status"] == "active"]
    next_action = ("request_more_material" if phase == "no_candidates" else "handoff" if active_handoffs
                   else "review_summary" if review_permissions else "request_review_permission")
    result = {"session_id": session, "phase": phase,
              "counts": {"files": summary.get("files_discovered", 0), "candidates": len(product["candidates"]),
                         "errors": errors, **counts}, "bundle_count": len(bundles),
              "deliverable_counts": dict(Counter(d["status"] for d in deliverables)),
              "deliverable_updates": [{"artifact_id": d["artifact_id"], "status": d["status"],
                                       "next_action": d["next_action"]} for d in deliverables if d["next_action"] != "none"],
              "available_handoffs": active_handoffs,
              "review_fields": [{"field": f, "label": LABELS[f], "candidates": n} for f, n in sorted(field_counts.items())],
              "review_permissions": review_permissions,
              "next_action": next_action,
              "processing": "local", "network_sent": False,
              "performance": performance_summary(summary)}
    delivery = manifest.get("local_delivery")
    if isinstance(delivery, dict) and delivery.get("session_id") == session:
        statuses = {d["artifact_id"]: d["status"] for d in deliverables}
        ready = all(statuses.get(aid) == "covered_fields_match" for aid in delivery["artifact_ids"])
        result["local_delivery"] = {"status": "ready" if ready else "needs_refresh",
                                    "reason": delivery["reason"], "artifact_count": len(delivery["artifact_ids"]),
                                    "created_at": delivery["created_at"]}
        if ready:
            result["next_action"] = "local_delivery_ready"
    if detailed:
        result.update(product=product, confirmed=confirmed, bundles=bundles, deliverables=deliverables,
                      input_name=summary.get("input_name", "商品资料"))
    return result


def export_table(output_dir: str | Path, *, session_id: str | None = None) -> dict:
    output, product, confirmed = context(output_dir, session_id)
    if not confirmed:
        raise ConfirmationRequestError("尚无当前有效的已确认参数。")
    values = {}
    for fact in confirmed:
        key = (fact.get("product_id"), fact["field"], fact.get("scope"))
        value = value_key(fact["normalized_value"], fact.get("normalized_unit"))
        if key in values and values[key] != value:
            raise ConfirmationRequestError("同一口径存在互相矛盾的已确认值，请先明确采用哪一条；可导出核验表查看差异。")
        values[key] = value
    rows = [["商品 ID", "SKU", "型号", "变体", "参数", "值", "单位", "口径"]]
    for f in confirmed:
        rows.append([f.get("product_id") or "", f.get("product_sku") or "", f.get("product_model") or "",
                     f.get("product_variant") or "", f["field_label"], str(f["normalized_value"]),
                     f.get("normalized_unit") or "", f.get("scope") or ""])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow(["'" + x if x.startswith(("=", "+", "-", "@", "\t", "\r")) else x for x in row])
    path = output / "confirmed-parameters.csv"
    atomic_write_text(path, buffer.getvalue(), encoding="utf-8-sig")
    session = product["run_summary"]["session_id"]
    manifest = _manifest(output, session)
    artifact_id = digest(str(path))[:24]
    manifest["deliverables"][artifact_id] = {
        "artifact_id": artifact_id, "name": path.name, "path": str(path),
        "file_hash": hashlib.sha256(safe_file(path)).hexdigest(), "bundle_id": "local-export",
        "bundle_version": digest([_public_fact(f, session) for f in confirmed]), "checked_at": now(),
        "revision": manifest["deliverables"].get(artifact_id, {}).get("revision", 0) + 1,
        "references": [{"fact_id": _public_fact(f, session)["fact_id"], "lines": [i + 2]}
                       for i, f in enumerate(confirmed)],
        "check": {"status": "covered_fields_match", "claim_count": len(confirmed), "finding_count": 0,
                  "findings": [], "coverage": "本地参数表直接来自当前有效的已确认事实。"}}
    atomic_write_json(output / WORKFLOW_FILE, manifest)
    return {"path": str(path), "confirmed_count": len(confirmed), "processing": "local"}


def export_local(output_dir: str | Path, *, reason: str = "local_only", session_id: str | None = None) -> dict:
    """Deliver a table and a plain factual brief without model or network calls."""
    if reason not in LOCAL_DELIVERY_REASONS:
        raise ConfirmationRequestError("本地交付原因无效。")
    output, product, confirmed = context(output_dir, session_id)
    if not confirmed:
        raise ConfirmationRequestError("尚无有效的已确认参数，请先在对话中完成参数确认。")
    values = {}
    for fact in confirmed:
        key = (fact.get("product_id"), fact["field"], fact.get("scope"))
        value = value_key(fact["normalized_value"], fact.get("normalized_unit"))
        if key in values and values[key] != value:
            raise ConfirmationRequestError("同一口径仍存在多个矛盾的已确认值，请先明确采用哪个值。")
        values[key] = value
    session = product["run_summary"]["session_id"]
    table = export_table(output, session_id=session)
    # export_table revalidates sources. Refuse to mix facts from two source versions.
    _, current_product, current_facts = context(output, session)
    if digest(confirmed) != digest(current_facts):
        raise ConfirmationRequestError("来源在交付过程中发生变化，请重新核验后导出。")
    def literal(value):
        text = escape(str(value), quote=False).replace("\r", " ").replace("\n", " ")
        return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", text)
    lines = ["# 商品参数简报", "", "生成方式：本地模板。", "",
             "交付说明：" + LOCAL_DELIVERY_REASONS[reason] + "。", "",
             "本简报只列出人工确认且来源仍有效的参数，没有生成额外的营销卖点。", "", "## 已确认参数", ""]
    scopes = {"input": "输入", "output": "输出", "rated": "额定", "nominal": "标称",
              "min": "最小", "max": "最大", "typical": "典型", "net": "净重", "gross": "毛重", "unspecified": "未限定"}
    references = []
    for fact in current_facts:
        scope = fact.get("scope")
        label = fact["field_label"]
        if scope and scope not in {"net", "gross", "unspecified"}:
            label += "（" + " / ".join(scopes.get(part, part) for part in scope.split("|")) + "）"
        value = fact["normalized_value"]
        if isinstance(value, (list, dict)):
            value = json.dumps(value, ensure_ascii=False)
        unit = "件" if fact.get("normalized_unit") == "count" else fact.get("normalized_unit") or ""
        lines.append(f"- {literal(label)}：{literal(value)} {literal(unit)}".rstrip())
        references.append({"fact_id": _public_fact(fact, session)["fact_id"], "lines": [len(lines)]})
    lines += ["", "资料更新或确认撤销后，本简报需要重新生成。", ""]
    path = output / "local-product-brief.md"
    atomic_write_text(path, "\n".join(lines))
    manifest = _manifest(output, session)
    artifact_id = digest(str(path))[:24]
    manifest["deliverables"][artifact_id] = {
        "artifact_id": artifact_id, "name": path.name, "path": str(path),
        "file_hash": hashlib.sha256(safe_file(path)).hexdigest(), "bundle_id": "local-export",
        "bundle_version": digest([_public_fact(f, session) for f in current_facts]), "checked_at": now(),
        "revision": manifest["deliverables"].get(artifact_id, {}).get("revision", 0) + 1,
        "references": references,
        "check": {"status": "covered_fields_match", "claim_count": len(current_facts), "finding_count": 0,
                  "findings": [], "coverage": "本地模板直接列出当前有效的已确认事实，不含生成式营销表述。"}}
    manifest["local_delivery"] = {"session_id": session, "reason": reason, "created_at": now(),
                                  "artifact_ids": [digest(table["path"])[:24], artifact_id]}
    atomic_write_json(output / WORKFLOW_FILE, manifest)
    return {"status": "local_delivery_ready", "reason": reason, "processing": "local", "network_sent": False,
            "model_called": False, "confirmed_count": len(current_facts),
            "artifacts": {"parameters": table["path"], "brief": str(path)},
            "next_action": "deliver_local_files"}
