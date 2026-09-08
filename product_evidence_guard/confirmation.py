from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat
from typing import Any, Iterable
import uuid

from . import __version__
from .models import CrossFieldRelation, FactCandidate, FactGroup
from .reports import (
    write_conflicts_markdown,
    write_html_report,
    write_product_facts,
)
from .state import atomic_write_json, atomic_write_text


CONFIRMATION_SCHEMA_VERSION = 3
CONFIRMATION_STATE_NAME = "confirmation-state.json"
CONFIRMATION_AUDIT_NAME = "confirmation-audit.jsonl"
CONFIRMED_FACTS_NAME = "confirmed-product-facts.json"
ALLOWED_ACTIONS = {"confirm", "reject"}
ACTIVE_STATUSES = {"confirmed", "rejected"}
CANDIDATE_SNAPSHOT_FIELDS = (
    "candidate_id",
    "field",
    "field_label",
    "raw_value",
    "normalized_value",
    "normalized_unit",
    "source_block_id",
    "source_file",
    "source_kind",
    "file_hash",
    "locator",
    "raw_text",
    "recognition_confidence",
    "mapping_confidence",
    "extraction_method",
    "scope",
    "notes",
    "mapping_confidence_source",
    "provenance",
    "product_id",
    "product_sku",
    "product_model",
    "product_variant",
    "product_identity_status",
    "identity_version",
)


class ConfirmationError(ValueError):
    """Raised when a confirmation command violates the session contract."""


class ConfirmationRequestError(ConfirmationError):
    """Raised for a safe, retryable user request rejection."""


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    session_id: str
    candidate_id: str
    field: str
    status: str
    confirmed_facts_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "candidate_id": self.candidate_id,
            "field": self.field,
            "status": self.status,
            "confirmed_facts_path": self.confirmed_facts_path,
        }


@dataclass(frozen=True, slots=True)
class BatchConfirmationResult:
    session_id: str
    transaction_id: str
    applied: tuple[ConfirmationResult, ...]
    confirmed_facts_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "transaction_id": self.transaction_id,
            "applied_count": len(self.applied),
            "applied": [item.to_dict() for item in self.applied],
            "confirmed_facts_path": self.confirmed_facts_path,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise ConfirmationError(f"分析结果不是安全的普通文件：{path}")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfirmationError(f"找不到分析结果：{path}") from exc
    except ConfirmationError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfirmationError(f"无法读取分析结果：{path}") from exc
    if not isinstance(data, dict):
        raise ConfirmationError(f"分析结果格式无效：{path}")
    return data


def _new_confirmation_state(session_id: str) -> dict[str, Any]:
    return {
        "schema_version": CONFIRMATION_SCHEMA_VERSION,
        "session_id": session_id,
        "decisions": {},
    }


def _stale_event(
    *,
    session_id: str,
    candidate_id: str,
    decision: dict[str, Any],
    reason: str,
    previous_session_id: str | None = None,
) -> dict[str, Any]:
    timestamp = _utc_now()
    decision["status"] = "stale"
    decision["stale_at"] = timestamp
    decision["stale_reason"] = reason
    event = {
        "timestamp": timestamp,
        "action": "stale",
        "status": "stale",
        "field": decision.get("field"),
        "scope": decision.get("scope"),
        "product_id": decision.get("product_id"),
        "candidate_id": candidate_id,
        "reason": reason,
        "file_hash": decision.get("file_hash"),
        "source_file": decision.get("source_file"),
        "session_id": session_id,
        "tool_version": __version__,
    }
    if previous_session_id:
        event["previous_session_id"] = previous_session_id
    return event


def _load_confirmation_state(
    output_dir: Path,
    session_id: str,
    *,
    recover_session_mismatch: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    path = output_dir / CONFIRMATION_STATE_NAME
    if not path.exists():
        return _new_confirmation_state(session_id), [], False
    data = _load_json_object(path)
    schema_version = data.get("schema_version")
    if schema_version not in {1, 2, CONFIRMATION_SCHEMA_VERSION}:
        raise ConfirmationError("确认状态版本不受支持，请重新分析。")
    if not isinstance(data.get("decisions"), dict):
        raise ConfirmationError("确认状态损坏，请保留输出目录并重新分析。")
    changed = schema_version != CONFIRMATION_SCHEMA_VERSION
    data["schema_version"] = CONFIRMATION_SCHEMA_VERSION
    previous_session_id = str(data.get("session_id", ""))
    events: list[dict[str, Any]] = []
    if previous_session_id != session_id:
        if not recover_session_mismatch:
            raise ConfirmationRequestError(
                "会话 ID 不匹配，请使用最新分析结果中的 session_id。"
            )
        reason = "分析输入根目录或恢复会话已变化，旧确认自动失效。"
        for candidate_id, decision in data["decisions"].items():
            if not isinstance(decision, dict) or decision.get("status") not in ACTIVE_STATUSES:
                continue
            events.append(
                _stale_event(
                    session_id=session_id,
                    candidate_id=str(candidate_id),
                    decision=decision,
                    reason=reason,
                    previous_session_id=previous_session_id,
                )
            )
        data["session_id"] = session_id
        changed = True
    return data, events, changed


def _assert_safe_audit_target(path: Path) -> None:
    if path.exists() or path.is_symlink():
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or path.is_symlink()
        ):
            raise ConfirmationError("确认审计文件不是安全的普通文件。")


def _append_audit_events(
    output_dir: Path,
    events: Iterable[dict[str, Any]],
) -> None:
    event_rows = list(events)
    if not event_rows:
        return
    path = output_dir / CONFIRMATION_AUDIT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_audit_target(path)
    existing = ""
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            existing += "\n"
    appended = "".join(
        json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        for event in event_rows
    )
    atomic_write_text(path, existing + appended)


def _append_audit(output_dir: Path, event: dict[str, Any]) -> None:
    _append_audit_events(output_dir, [event])


def _candidate_rows(product_facts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = product_facts.get("candidates")
    if not isinstance(rows, list):
        raise ConfirmationError("product-facts.json 缺少候选列表。")
    return [row for row in rows if isinstance(row, dict)]


def _session_id(product_facts: dict[str, Any]) -> str:
    run_summary = product_facts.get("run_summary")
    if not isinstance(run_summary, dict):
        raise ConfirmationError("product-facts.json 缺少运行摘要。")
    value = str(run_summary.get("session_id", "")).strip()
    if not value:
        raise ConfirmationError("分析结果没有 session_id，请重新分析。")
    return value


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _candidate_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _json_clone(row.get(key))
        for key in CANDIDATE_SNAPSHOT_FIELDS
    }


def _snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _valid_snapshot(decision: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = decision.get("candidate_snapshot")
    digest = decision.get("candidate_snapshot_sha256")
    if not isinstance(snapshot, dict) or not isinstance(digest, str):
        return None
    if _snapshot_digest(snapshot) != digest:
        return None
    return snapshot


def _snapshot_matches_row(snapshot: dict[str, Any], row: dict[str, Any]) -> bool:
    return snapshot == _candidate_snapshot(row)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_analysis_context(
    output_dir: Path,
    *,
    session_id: str,
) -> tuple[Path, dict[str, dict[str, Any]]]:
    analysis_state = _load_json_object(output_dir / "analysis-state.json")
    if str(analysis_state.get("session_id", "")) != session_id:
        raise ConfirmationError("分析状态与确认会话不匹配，请重新分析。")
    input_root_text = analysis_state.get("input_root")
    if not isinstance(input_root_text, str) or not input_root_text.strip():
        raise ConfirmationError("分析状态缺少输入根目录，请重新分析后再确认或导出。")
    try:
        input_root = Path(input_root_text).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ConfirmationError("原商品资料目录已不存在，请重新分析。") from exc
    if not input_root.is_dir():
        raise ConfirmationError("原商品资料目录已不存在，请重新分析。")

    snapshots: dict[str, dict[str, Any]] = {}
    files = analysis_state.get("files")
    if not isinstance(files, dict):
        raise ConfirmationError("分析状态文件损坏，请重新分析。")
    for entry in files.values():
        if not isinstance(entry, dict):
            continue
        candidates = entry.get("candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("candidate_id", ""))
            if candidate_id:
                snapshots[candidate_id] = _candidate_snapshot(candidate)
    return input_root, snapshots


def _source_validation_error(
    input_root: Path,
    snapshot: dict[str, Any],
    hash_cache: dict[Path, str],
) -> str | None:
    source_file = snapshot.get("source_file")
    expected_hash = snapshot.get("file_hash")
    if not isinstance(source_file, str) or not source_file.strip():
        return "候选缺少源文件路径，无法验证。"
    if not isinstance(expected_hash, str) or not expected_hash:
        return "候选缺少源文件哈希，无法验证。"
    relative = Path(source_file)
    if relative.is_absolute() or ".." in relative.parts:
        return "候选源文件路径越出分析目录。"
    try:
        source_path = (input_root / relative).resolve(strict=True)
        source_path.relative_to(input_root)
    except (OSError, RuntimeError, ValueError):
        return "候选源文件已不存在或越出分析目录。"
    if not source_path.is_file():
        return "候选源文件已不存在。"
    try:
        actual_hash = hash_cache.get(source_path)
        if actual_hash is None:
            actual_hash = _sha256_path(source_path)
            hash_cache[source_path] = actual_hash
    except OSError:
        return "候选源文件当前无法读取。"
    if actual_hash != expected_hash:
        return "源文件哈希已变化，旧确认自动失效。"
    return None


def _validate_active_decisions(
    state: dict[str, Any],
    *,
    session_id: str,
    candidate_rows: Iterable[dict[str, Any]],
    allow_snapshot_upgrade: bool,
    input_root: Path | None = None,
    analysis_snapshots: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    current = {
        str(row.get("candidate_id", "")): row
        for row in candidate_rows
        if str(row.get("candidate_id", ""))
    }
    events: list[dict[str, Any]] = []
    changed = False
    hash_cache: dict[Path, str] = {}
    for candidate_id, decision in state["decisions"].items():
        if not isinstance(decision, dict) or decision.get("status") not in ACTIVE_STATUSES:
            continue
        row = current.get(str(candidate_id))
        snapshot = _valid_snapshot(decision)
        if snapshot is None and allow_snapshot_upgrade and row is not None:
            if decision.get("file_hash") == row.get("file_hash"):
                snapshot = _candidate_snapshot(row)
                decision["candidate_snapshot"] = snapshot
                decision["candidate_snapshot_sha256"] = _snapshot_digest(snapshot)
                changed = True

        reason: str | None = None
        if row is None:
            reason = "源文件哈希或候选已变化，旧确认自动失效。"
        elif snapshot is None:
            reason = "旧确认缺少完整候选快照，无法安全导出。"
        elif not _snapshot_matches_row(snapshot, row):
            reason = "候选内容已变化，旧确认自动失效。"
        elif (
            analysis_snapshots is not None
            and analysis_snapshots.get(str(candidate_id)) != snapshot
        ):
            reason = "候选与分析状态不一致，旧确认自动失效。"
        elif input_root is not None:
            reason = _source_validation_error(input_root, snapshot, hash_cache)
        if reason is None:
            continue
        events.append(
            _stale_event(
                session_id=session_id,
                candidate_id=str(candidate_id),
                decision=decision,
                reason=reason,
            )
        )
        changed = True
    return events, changed


def _deserialize_candidates(product_facts: dict[str, Any]) -> list[FactCandidate]:
    candidates: list[FactCandidate] = []
    for row in _candidate_rows(product_facts):
        try:
            candidates.append(FactCandidate.from_dict(row))
        except (KeyError, TypeError) as exc:
            raise ConfirmationError("product-facts.json 中存在无效候选。") from exc
    return candidates


def _deserialize_rows(
    product_facts: dict[str, Any],
    key: str,
    cls: type[FactGroup] | type[CrossFieldRelation],
) -> list[FactGroup] | list[CrossFieldRelation]:
    rows = product_facts.get(key)
    if not isinstance(rows, list):
        raise ConfirmationError(f"product-facts.json 缺少 {key}。")
    allowed = {item.name for item in dataclass_fields(cls)}
    result: list[FactGroup] | list[CrossFieldRelation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ConfirmationError(f"product-facts.json 中存在无效 {key}。")
        try:
            result.append(cls(**{name: row[name] for name in allowed if name in row}))
        except (KeyError, TypeError) as exc:
            raise ConfirmationError(f"product-facts.json 中存在无效 {key}。") from exc
    return result


def _synchronize_analysis_outputs(
    output_dir: Path,
    *,
    product_facts: dict[str, Any],
    state: dict[str, Any],
) -> None:
    candidates = _deserialize_candidates(product_facts)
    decisions = state["decisions"]
    for candidate in candidates:
        candidate.status = "pending"
        decision = decisions.get(candidate.candidate_id)
        if not isinstance(decision, dict) or decision.get("status") not in ACTIVE_STATUSES:
            continue
        snapshot = _valid_snapshot(decision)
        if snapshot is not None and _snapshot_matches_row(snapshot, candidate.to_dict()):
            candidate.status = str(decision["status"])

    counts = Counter(candidate.status for candidate in candidates)
    counts["stale"] = sum(
        1
        for decision in decisions.values()
        if isinstance(decision, dict) and decision.get("status") == "stale"
    )
    confirmation_counts = {
        status: int(counts.get(status, 0))
        for status in ("pending", "confirmed", "rejected", "stale")
    }
    run_summary = product_facts.get("run_summary")
    if not isinstance(run_summary, dict):
        raise ConfirmationError("product-facts.json 缺少运行摘要。")
    run_summary = _json_clone(run_summary)
    run_summary["confirmation_status_counts"] = confirmation_counts

    groups = _deserialize_rows(product_facts, "fact_groups", FactGroup)
    relations = _deserialize_rows(
        product_facts,
        "cross_field_relations",
        CrossFieldRelation,
    )
    write_product_facts(
        output_dir / "product-facts.json",
        candidates=candidates,
        groups=groups,
        relations=relations,
        run_summary=run_summary,
    )
    write_conflicts_markdown(
        output_dir / "conflicts.md",
        candidates,
        groups,
        relations,
        run_summary=run_summary,
    )
    write_html_report(
        output_dir / "evidence-report.html",
        candidates,
        groups,
        relations,
        run_summary,
    )
    atomic_write_json(output_dir / "run-summary.json", run_summary)


def _write_confirmed_facts(
    output_dir: Path,
    *,
    product_facts: dict[str, Any],
    state: dict[str, Any],
) -> Path:
    rows = _candidate_rows(product_facts)
    decisions = state["decisions"]
    confirmed: list[dict[str, Any]] = []
    for row in rows:
        candidate_id = str(row.get("candidate_id", ""))
        decision = decisions.get(candidate_id)
        if not isinstance(decision, dict) or decision.get("status") != "confirmed":
            continue
        snapshot = _valid_snapshot(decision)
        if snapshot is None or not _snapshot_matches_row(snapshot, row):
            continue
        confirmed.append(
            {
                "field": snapshot.get("field"),
                "field_label": snapshot.get("field_label"),
                "raw_value": snapshot.get("raw_value"),
                "normalized_value": snapshot.get("normalized_value"),
                "normalized_unit": snapshot.get("normalized_unit"),
                "scope": snapshot.get("scope"),
                "product_id": snapshot.get("product_id"),
                "product_sku": snapshot.get("product_sku"),
                "product_model": snapshot.get("product_model"),
                "product_variant": snapshot.get("product_variant"),
                "product_identity_status": snapshot.get("product_identity_status"),
                "candidate_id": candidate_id,
                "source_file": snapshot.get("source_file"),
                "file_hash": snapshot.get("file_hash"),
                "locator": snapshot.get("locator"),
                "confirmation_reason": decision.get("reason"),
                "confirmed_at": decision.get("timestamp"),
            }
        )
    path = output_dir / CONFIRMED_FACTS_NAME
    atomic_write_json(
        path,
        {
            "schema_version": 2,
            "session_id": state["session_id"],
            "status": "human_confirmed",
            "warning": "只包含用户明确确认且源文件哈希仍有效的事实。",
            "facts": confirmed,
        },
    )
    return path


def apply_decision(
    output_dir: str | Path,
    *,
    session_id: str,
    candidate_id: str,
    reason: str,
    action: str,
) -> ConfirmationResult:
    output = Path(output_dir).expanduser().resolve()
    if action not in ALLOWED_ACTIONS:
        raise ConfirmationRequestError(f"不支持的确认动作：{action}")
    clean_reason = reason.strip()
    if not clean_reason:
        raise ConfirmationRequestError("确认或拒绝必须填写理由。")
    if len(clean_reason) > 1000:
        raise ConfirmationRequestError("理由过长，最多 1000 个字符。")

    product_facts = _load_json_object(output / "product-facts.json")
    current_session = _session_id(product_facts)
    if current_session != session_id:
        raise ConfirmationRequestError(
            "会话 ID 不匹配，请使用最新分析结果中的 session_id。"
        )
    rows = _candidate_rows(product_facts)
    candidate = next(
        (row for row in rows if str(row.get("candidate_id", "")) == candidate_id),
        None,
    )
    if candidate is None:
        raise ConfirmationRequestError(
            "候选 ID 不存在或已因源文件变化而失效。"
        )

    snapshot = _candidate_snapshot(candidate)
    input_root, analysis_snapshots = _load_analysis_context(
        output,
        session_id=session_id,
    )
    if analysis_snapshots.get(candidate_id) != snapshot:
        raise ConfirmationRequestError(
            "候选与分析状态不一致，请重新分析后再确认。"
        )
    source_error = _source_validation_error(input_root, snapshot, {})
    if source_error is not None:
        raise ConfirmationRequestError(f"{source_error} 请重新分析后再确认。")

    state, _, _ = _load_confirmation_state(output, session_id)
    status = "confirmed" if action == "confirm" else "rejected"
    event = {
        "timestamp": _utc_now(),
        "action": action,
        "status": status,
        "field": str(candidate.get("field", "")),
        "scope": candidate.get("scope"),
        "product_id": candidate.get("product_id"),
        "candidate_id": candidate_id,
        "reason": clean_reason,
        "file_hash": str(candidate.get("file_hash", "")),
        "source_file": str(candidate.get("source_file", "")),
        "session_id": session_id,
        "tool_version": __version__,
    }
    decision = {
        "status": status,
        "field": event["field"],
        "scope": event["scope"],
        "product_id": event["product_id"],
        "reason": clean_reason,
        "timestamp": event["timestamp"],
        "file_hash": event["file_hash"],
        "source_file": event["source_file"],
        "candidate_snapshot": snapshot,
        "candidate_snapshot_sha256": _snapshot_digest(snapshot),
    }
    # Treat the audit as a write-ahead record: if it cannot be written safely,
    # no exportable decision is persisted.
    _append_audit(output, event)
    state["decisions"][candidate_id] = decision
    atomic_write_json(output / CONFIRMATION_STATE_NAME, state)
    _synchronize_analysis_outputs(
        output,
        product_facts=product_facts,
        state=state,
    )
    confirmed_path = _write_confirmed_facts(output, product_facts=product_facts, state=state)
    return ConfirmationResult(
        session_id=session_id,
        candidate_id=candidate_id,
        field=event["field"],
        status=status,
        confirmed_facts_path=str(confirmed_path),
    )


def apply_batch_decisions(
    output_dir: str | Path,
    *,
    session_id: str,
    decisions: Iterable[dict[str, Any]],
) -> BatchConfirmationResult:
    """Validate first, then atomically publish a complete decision set.

    Every candidate receives its own audit row. The shared transaction id and
    final commit row make a prepared-only audit record distinguishable from a
    committed batch after an unexpected process interruption.
    """
    output = Path(output_dir).expanduser().resolve()
    requests = list(decisions)
    if not requests or len(requests) > 500:
        raise ConfirmationRequestError("批量决定必须包含 1 到 500 条候选。")
    seen: set[str] = set()
    clean_requests: list[tuple[str, str, str]] = []
    for request in requests:
        if not isinstance(request, dict):
            raise ConfirmationRequestError("批量决定中的每一项都必须是对象。")
        candidate_id = str(request.get("candidate_id") or "").strip()
        action = str(request.get("action") or "").strip()
        reason = str(request.get("reason") or "").strip()
        if not candidate_id or candidate_id in seen:
            raise ConfirmationRequestError("批量决定包含空或重复的候选 ID。")
        if action not in ALLOWED_ACTIONS:
            raise ConfirmationRequestError(f"不支持的确认动作：{action}")
        if not reason or len(reason) > 1000:
            raise ConfirmationRequestError("每条批量决定都需要 1 到 1000 字的真实理由。")
        seen.add(candidate_id)
        clean_requests.append((candidate_id, action, reason))

    product_facts = _load_json_object(output / "product-facts.json")
    current_session = _session_id(product_facts)
    if current_session != session_id:
        raise ConfirmationRequestError("会话 ID 不匹配，请使用最新分析结果中的 session_id。")
    rows = _candidate_rows(product_facts)
    by_id = {str(row.get("candidate_id", "")): row for row in rows}
    input_root, analysis_snapshots = _load_analysis_context(output, session_id=session_id)
    hash_cache: dict[Path, str] = {}
    prepared: list[tuple[dict[str, Any], str, str, dict[str, Any]]] = []
    for candidate_id, action, reason in clean_requests:
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise ConfirmationRequestError(f"候选 ID 不存在或已失效：{candidate_id}")
        snapshot = _candidate_snapshot(candidate)
        if analysis_snapshots.get(candidate_id) != snapshot:
            raise ConfirmationRequestError("候选与分析状态不一致，请重新分析后再批量处理。")
        source_error = _source_validation_error(input_root, snapshot, hash_cache)
        if source_error is not None:
            raise ConfirmationRequestError(f"{source_error} 请重新分析后再批量处理。")
        prepared.append((candidate, action, reason, snapshot))

    state, _, _ = _load_confirmation_state(output, session_id)
    transaction_id = uuid.uuid4().hex
    timestamp = _utc_now()
    audit_events: list[dict[str, Any]] = []
    applied: list[ConfirmationResult] = []
    for candidate, action, reason, snapshot in prepared:
        candidate_id = str(candidate["candidate_id"])
        status = "confirmed" if action == "confirm" else "rejected"
        event = {
            "timestamp": timestamp,
            "action": action,
            "status": status,
            "phase": "prepared",
            "transaction_id": transaction_id,
            "field": str(candidate.get("field", "")),
            "scope": candidate.get("scope"),
            "product_id": candidate.get("product_id"),
            "candidate_id": candidate_id,
            "reason": reason,
            "file_hash": str(candidate.get("file_hash", "")),
            "source_file": str(candidate.get("source_file", "")),
            "session_id": session_id,
            "tool_version": __version__,
        }
        audit_events.append(event)
        state["decisions"][candidate_id] = {
            "status": status,
            "field": event["field"],
            "scope": event["scope"],
            "product_id": event["product_id"],
            "reason": reason,
            "timestamp": timestamp,
            "file_hash": event["file_hash"],
            "source_file": event["source_file"],
            "transaction_id": transaction_id,
            "candidate_snapshot": snapshot,
            "candidate_snapshot_sha256": _snapshot_digest(snapshot),
        }
        applied.append(ConfirmationResult(
            session_id=session_id,
            candidate_id=candidate_id,
            field=event["field"],
            status=status,
            confirmed_facts_path=str(output / CONFIRMED_FACTS_NAME),
        ))

    # Prepared audit rows are written before the all-or-nothing state replace.
    _append_audit_events(output, audit_events)
    atomic_write_json(output / CONFIRMATION_STATE_NAME, state)
    _append_audit_events(output, [{
        "timestamp": _utc_now(),
        "action": "batch_commit",
        "status": "committed",
        "transaction_id": transaction_id,
        "candidate_ids": [item.candidate_id for item in applied],
        "session_id": session_id,
        "tool_version": __version__,
    }])
    _synchronize_analysis_outputs(output, product_facts=product_facts, state=state)
    confirmed_path = _write_confirmed_facts(output, product_facts=product_facts, state=state)
    return BatchConfirmationResult(
        session_id=session_id,
        transaction_id=transaction_id,
        applied=tuple(applied),
        confirmed_facts_path=str(confirmed_path),
    )


def resolve_conflict_group(
    output_dir: str | Path,
    *,
    session_id: str,
    group_id: str,
    selected_candidate_id: str,
    reason: str,
    reject_others: bool = False,
    expected_candidate_ids: Iterable[str] | None = None,
) -> BatchConfirmationResult:
    """Confirm one explicit choice; reject peers only when explicitly asked."""
    output = Path(output_dir).expanduser().resolve()
    product_facts = _load_json_object(output / "product-facts.json")
    if _session_id(product_facts) != session_id:
        raise ConfirmationRequestError("会话 ID 不匹配，请刷新冲突组。")
    groups = product_facts.get("fact_groups")
    group = next((item for item in groups or [] if isinstance(item, dict) and item.get("group_id") == group_id), None)
    if group is None or selected_candidate_id not in group.get("candidate_ids", []):
        raise ConfirmationRequestError("冲突组或选择已经变化，请刷新后重试。")
    if expected_candidate_ids is not None:
        expected = {str(candidate_id) for candidate_id in expected_candidate_ids}
        current = {str(candidate_id) for candidate_id in group.get("candidate_ids", [])}
        if not expected or expected != current:
            raise ConfirmationRequestError("冲突组成员已经变化，请刷新后重新明确选择。")
    decisions = [{"candidate_id": selected_candidate_id, "action": "confirm", "reason": reason}]
    if reject_others:
        decisions.extend(
            {"candidate_id": candidate_id, "action": "reject", "reason": reason}
            for candidate_id in group["candidate_ids"]
            if candidate_id != selected_candidate_id
        )
    return apply_batch_decisions(output, session_id=session_id, decisions=decisions)


def reconcile_confirmations(
    output_dir: str | Path,
    *,
    session_id: str,
    candidates: Iterable[FactCandidate],
) -> dict[str, str]:
    """Mark decisions stale when their source candidate/hash no longer exists."""

    output = Path(output_dir).expanduser().resolve()
    state_path = output / CONFIRMATION_STATE_NAME
    if not state_path.exists():
        return {}
    candidate_list = list(candidates)
    state, reset_events, changed = _load_confirmation_state(
        output,
        session_id,
        recover_session_mismatch=True,
    )
    candidate_rows = [item.to_dict() for item in candidate_list]
    validation_events, validation_changed = _validate_active_decisions(
        state,
        session_id=session_id,
        candidate_rows=candidate_rows,
        allow_snapshot_upgrade=True,
    )
    changed = changed or validation_changed
    events = reset_events + validation_events
    current = {item.candidate_id: item for item in candidate_list}
    statuses: dict[str, str] = {}
    for candidate_id, decision in state["decisions"].items():
        if not isinstance(decision, dict):
            continue
        item = current.get(candidate_id)
        status = str(decision.get("status", "pending"))
        snapshot = _valid_snapshot(decision)
        if (
            item is not None
            and status in ACTIVE_STATUSES
            and snapshot is not None
            and _snapshot_matches_row(snapshot, item.to_dict())
        ):
            statuses[candidate_id] = status
    if changed:
        atomic_write_json(state_path, state)
    _append_audit_events(output, events)
    return statuses


def export_confirmed(output_dir: str | Path, *, session_id: str) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    product_facts = _load_json_object(output / "product-facts.json")
    if _session_id(product_facts) != session_id:
        raise ConfirmationRequestError(
            "会话 ID 不匹配，请使用最新分析结果中的 session_id。"
        )
    state, _, changed = _load_confirmation_state(output, session_id)
    events: list[dict[str, Any]] = []
    try:
        input_root, analysis_snapshots = _load_analysis_context(
            output,
            session_id=session_id,
        )
    except ConfirmationError as exc:
        input_root = None
        analysis_snapshots = None
        reason = str(exc)
        for candidate_id, decision in state["decisions"].items():
            if not isinstance(decision, dict) or decision.get("status") not in ACTIVE_STATUSES:
                continue
            events.append(
                _stale_event(
                    session_id=session_id,
                    candidate_id=str(candidate_id),
                    decision=decision,
                    reason=reason,
                )
            )
            changed = True
    else:
        validation_events, validation_changed = _validate_active_decisions(
            state,
            session_id=session_id,
            candidate_rows=_candidate_rows(product_facts),
            allow_snapshot_upgrade=False,
            input_root=input_root,
            analysis_snapshots=analysis_snapshots,
        )
        events.extend(validation_events)
        changed = changed or validation_changed
    if changed:
        atomic_write_json(output / CONFIRMATION_STATE_NAME, state)
    _append_audit_events(output, events)
    _synchronize_analysis_outputs(
        output,
        product_facts=product_facts,
        state=state,
    )
    path = _write_confirmed_facts(output, product_facts=product_facts, state=state)
    data = _load_json_object(path)
    return {
        "session_id": session_id,
        "confirmed_count": len(data.get("facts", [])),
        "stale_count": sum(
            1
            for decision in state["decisions"].values()
            if isinstance(decision, dict) and decision.get("status") == "stale"
        ),
        "output": str(path),
    }


def initialize_confirmation_outputs(output_dir: str | Path, *, session_id: str) -> None:
    """Ensure every analysis has explicit pending/audit/export artifacts."""

    output = Path(output_dir).expanduser().resolve()
    state, _, state_changed = _load_confirmation_state(output, session_id)
    state_path = output / CONFIRMATION_STATE_NAME
    if not state_path.exists() or state_changed:
        atomic_write_json(state_path, state)
    audit_path = output / CONFIRMATION_AUDIT_NAME
    if not audit_path.exists() and not audit_path.is_symlink():
        atomic_write_text(audit_path, "")
    else:
        _assert_safe_audit_target(audit_path)
    product_facts = _load_json_object(output / "product-facts.json")
    _write_confirmed_facts(output, product_facts=product_facts, state=state)
