"""Review presentation and portable, local Excel delivery; never accepts decisions."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile

from . import workflow
from .confirmation import ConfirmationRequestError
from .graph import build_fact_groups
from .models import FactCandidate


SCOPES = {"input": "输入", "output": "输出", "rated": "额定", "nominal": "标称",
          "min": "最小", "max": "最大", "typical": "典型", "net": "净重", "gross": "毛重",
          "minimum": "最小", "maximum": "最大", "unspecified": "未限定"}
STATUSES = {"pending": "待确认", "confirmed": "已确认", "rejected": "已拒绝", "stale": "确认已失效",
            "source_changed": "来源已变化"}
CLASSIFICATIONS = {"strong_conflict": "数值冲突", "converted_match": "换算一致", "exact_match": "表达一致",
    "insufficient_evidence": "证据不足", "compatible_expression": "表达待统一", "likely_version_update": "可能版本不同",
    "semantic_scope_split": "不同口径", "source_changed": "先更新资料", "decided": "已作人工决定"}


def scope_label(scope):
    return " / ".join(SCOPES.get(part.removeprefix("rating:"), part.replace("profile:", "模式：").replace("variant:", "变体："))
                      for part in (scope or "unspecified").split("|"))


def _unit(unit):
    return "件" if unit == "count" else unit or ""


def group_candidates(rows, hashes):
    """Group by semantic scope and prioritize unresolved issues without choosing a value."""
    buckets = defaultdict(list)
    for candidate in rows:
        buckets[(candidate.get("product_id"), candidate["field"], candidate.get("scope"))].append(candidate)
    groups = []
    for (product_id, field, scope), items in buckets.items():
        current = [c for c in items if hashes.get(c["source_file"]) == c["file_hash"]]
        active = [c for c in current if c["status"] not in {"rejected", "stale"}]
        pending = any(c["status"] == "pending" for c in active)
        source_count = len({c["source_file"] for c in current})
        if len(current) != len(items) or any(c["status"] == "stale" for c in items):
            kind, severity, reason = "source_changed", "block", "来源或确认已失效，请重新分析后再选择。"
        elif not active:
            kind, severity, reason = "decided", "review", "当前没有可采用的已确认参数，请补充资料。"
        else:
            graph = build_fact_groups([FactCandidate.from_dict(c) for c in active])[0]
            kind, severity, reason = graph.classification, graph.severity, graph.reason
            if not pending and severity != "block":
                kind, severity, reason = "decided", "pass", "已按用户决定保留参数，其他候选不自动采用。"
            elif source_count < 2 and severity != "block":
                kind, severity, reason = "insufficient_evidence", "review", "仅来自一份资料；同文件的多处文字不等于多来源验证。"
        groups.append({"product_id": product_id, "product_label": items[0].get("product_sku") or items[0].get("product_model") or product_id,
            "field": field, "label": items[0]["field_label"], "scope": scope,
            "scope_label": scope_label(scope), "classification": kind, "severity": severity,
            "reason": reason, "source_count": source_count, "candidate_ids": [c["candidate_id"] for c in items]})
    return sorted(groups, key=lambda g: ({"block": 0, "review": 1, "pass": 2}.get(g["severity"], 1),
                                       g.get("product_id") or "", g["field"], g["scope"] or ""))


def snapshot(output, product):
    from .conversation import _snapshot
    rows, hashes, _ = _snapshot(output, product, sorted({c["field"] for c in product["candidates"]}))
    version = workflow.digest({"session": product["run_summary"]["session_id"], "rows": rows, "hashes": hashes})
    return rows, hashes, version


def _value(value):
    if isinstance(value, list):
        return " × ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def _position(locator):
    labels = {"page": "页", "line": "行", "paragraph": "段落", "sheet": "工作表", "cell": "单元格",
              "row": "行", "column": "列", "table": "表格"}
    parts = [f"{label} {locator[key]}" for key, label in labels.items() if locator.get(key) is not None]
    if locator.get("bbox_1000"):
        parts.append("图片近似区域 " + str(locator["bbox_1000"]))
    elif "image" in locator:
        parts.append("图片文字（无精确坐标）")
    return "；".join(parts) or "见来源原文"


def export_review(output_dir, *, session_id=None):
    """Export all review candidates locally, with confirmed facts on a separate sheet."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.worksheet.table import Table, TableStyleInfo
    except ImportError as exc:
        raise ConfirmationRequestError("Excel 导出依赖未安装，请运行 scripts\\install-env.ps1。") from exc
    output, product, confirmed = workflow.context(output_dir, session_id)
    rows, hashes, version = snapshot(output, product)
    if not rows:
        raise ConfirmationRequestError("当前没有参数候选，请先分析包含商品参数的资料。")
    groups = group_candidates(rows, hashes)
    by_id = {c["candidate_id"]: c for c in rows}
    confirmed_ids = {c["candidate_id"] for c in confirmed}
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(title, subtitle, headings, records, widths):
        ws = wb.create_sheet(title)
        ws.sheet_view.showGridLines = False
        ws.append([title]); ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headings))
        ws.cell(1, 1).font = Font(name="微软雅黑", size=19, bold=True, color="183D4D")
        ws.row_dimensions[1].height = 35
        ws.append([subtitle]); ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headings))
        ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical="center")
        ws.cell(2, 1).font = Font(name="微软雅黑", size=10, color="506675")
        ws.row_dimensions[2].height = 42
        ws.append(["生成时间：" + generated]); ws.row_dimensions[3].height = 23
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=len(headings))
        ws.append(headings)
        for record in records:
            ws.append(record)
        for row in ws.iter_rows(min_row=3):
            for cell in row:
                cell.font = Font(name="微软雅黑", size=10, color="233B49")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                if isinstance(cell.value, str):
                    # Data from documents must remain literal, including =/+/@.
                    cell.data_type = "s"
            if row[0].row >= 5:
                ws.row_dimensions[row[0].row].height = 48
        for cell in ws[4]:
            cell.fill = PatternFill("solid", fgColor="183D4D")
            cell.font = Font(name="微软雅黑", color="FFFFFF", bold=True, size=10)
        ws.row_dimensions[4].height = 28
        if records:
            table = Table(displayName=f"ReviewTable{len(wb.worksheets)}", ref=f"A4:{ws.cell(ws.max_row, len(headings)).coordinate}")
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            ws.add_table(table)
        ws.freeze_panes = "C5"
        from openpyxl.utils import get_column_letter
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A3
        ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
        ws.print_title_rows = "1:4"
        ws.print_options.horizontalCentered = True
        return ws

    overview = []
    details = []
    selected = []
    for group in groups:
        items = [by_id[cid] for cid in group["candidate_ids"]]
        adopted = [c for c in items if c["candidate_id"] in confirmed_ids]
        adopted_values = {workflow.digest([c["normalized_value"], c["normalized_unit"]]) for c in adopted}
        conflicting_decisions = len(adopted_values) > 1
        issue = "已确认值互相冲突" if conflicting_decisions else CLASSIFICATIONS.get(group["classification"], group["classification"])
        if conflicting_decisions:
            advice = "请在对话中明确采用哪个已确认值；暂不列入可用参数。"
        elif group["classification"] == "source_changed":
            advice = "重新分析资料，再导出新核验表。"
        elif not adopted:
            advice = "在 Qoder 中核对候选，选择采用值并说明理由。"
        else:
            advice = "已有人工确认；其余未决定候选继续保留，不自动确认。"
        overview.append([group.get("product_label") or group.get("product_id") or "未归属商品",
                         group["label"], group["scope_label"], issue, len(items), group["source_count"],
                         len(adopted), advice])
        for c in items:
            current = hashes.get(c["source_file"]) == c["file_hash"]
            status = c["status"] if current else "source_changed"
            position = _position(c.get("locator", {}))
            details.append([c.get("product_sku") or c.get("product_model") or c.get("product_id") or "未归属商品",
                c["field_label"], scope_label(c.get("scope")), STATUSES.get(status, status),
                _value(c["normalized_value"]), _unit(c.get("normalized_unit")), c["raw_value"],
                c["source_file"], position, c["raw_text"], "" if current else "旧值仅供追溯，不可继续采用。", ""])
            if c["candidate_id"] in confirmed_ids and not conflicting_decisions and current:
                selected.append([c.get("product_sku") or c.get("product_model") or c.get("product_id") or "未归属商品",
                                 c["field_label"], scope_label(c.get("scope")), _value(c["normalized_value"]),
                                 _unit(c.get("normalized_unit")), c["source_file"], position])
    overview_sheet = sheet("核验总览", "按口径分别核验，先处理冲突。证据一致不等于已经人工确认。",
          ["商品", "参数", "口径", "核验情况", "候选数", "来源文件数", "已确认数", "下一步"], overview, [24, 16, 20, 25, 12, 14, 12, 65])
    for row in overview_sheet.iter_rows(min_row=5):
        if "冲突" in row[3].value or row[3].value == "先更新资料":
            row[3].fill = PatternFill("solid", fgColor="FBE4DE")
            row[3].font = Font(name="微软雅黑", size=10, bold=True, color="9E3429")
    detail_sheet = sheet("候选与证据", "包含待确认、拒绝或失效记录，仅供本地核对。修改 Excel 不会改变 Skill 的确认状态；最后一列可填写备注。",
          ["商品", "参数", "口径", "状态", "标准值", "单位", "原始值", "来源文件", "位置", "证据原文", "提示", "人工备注"],
          details, [24, 16, 18, 18, 20, 10, 26, 32, 30, 58, 34, 32])
    for row in detail_sheet.iter_rows(min_row=5):
        color = "FBE4DE" if row[3].value in {"来源已变化", "确认已失效"} else "FFF3D6" if row[3].value == "待确认" else "E5F1EA"
        row[3].fill = PatternFill("solid", fgColor=color)
        row[11].fill = PatternFill("solid", fgColor="F4F7FA")
    sheet("已确认参数", "只列出当前有效的已确认记录；同一口径存在矛盾的已确认值时暂不列入。资料或决定变化后需重新导出。",
          ["商品", "参数", "口径", "采用值", "单位", "来源文件", "位置"], selected, [24, 18, 22, 24, 12, 40, 48])

    path = output / "product-review.xlsx"
    if path.exists():
        workflow.safe_file(path, max_bytes=20_000_000)
    descriptor, temporary_name = tempfile.mkstemp(dir=output, prefix=".product-review-", suffix=".xlsx")
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        wb.save(temporary)
        _, current_product, _ = workflow.context(output, product["run_summary"]["session_id"])
        if snapshot(output, current_product)[2] != version:
            raise ConfirmationRequestError("资料或确认结果在导出期间改变，请重新导出。")
        os.replace(temporary, path)
    except PermissionError as exc:
        raise ConfirmationRequestError("核验表正在被 Excel 占用，请关闭该文件后重新导出。") from exc
    finally:
        wb.close()
        temporary.unlink(missing_ok=True)
    session = product["run_summary"]["session_id"]
    manifest = workflow._manifest(output, session)
    artifact_id = workflow.digest(str(path))[:24]
    manifest["deliverables"][artifact_id] = {"artifact_id": artifact_id, "kind": "review_workbook",
        "name": "商品核验表", "path": str(path), "file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bundle_id": "local-review", "bundle_version": version, "review_version": version,
        "checked_at": workflow.now(), "revision": manifest["deliverables"].get(artifact_id, {}).get("revision", 0) + 1,
        "references": [], "check": {"status": "review_snapshot", "finding_count": sum(g["severity"] == "block" for g in groups)}}
    from .state import atomic_write_json
    atomic_write_json(output / workflow.WORKFLOW_FILE, manifest)
    return {"path": str(path), "artifact_id": artifact_id, "status": "review_workbook_ready",
        "candidate_count": len(rows), "confirmed_row_count": len(selected), "needs_reanalysis": any(hashes.get(c["source_file"]) != c["file_hash"] for c in rows),
        "processing": "local", "network_sent": False, "model_called": False,
        "next_action": "open_local_review_workbook", "contains_source_evidence": True}
