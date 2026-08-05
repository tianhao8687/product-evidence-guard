from __future__ import annotations

from collections import Counter
from html import escape
import json
from pathlib import Path
from typing import Any, Iterable

from .models import CrossFieldRelation, FactCandidate, FactGroup
from .state import atomic_write_json, atomic_write_text


def _locator_text(locator: dict[str, Any]) -> str:
    return json.dumps(locator, ensure_ascii=False, sort_keys=True)


_STATUS_LABELS = {
    "pending": "待人工确认",
    "confirmed": "已人工确认",
    "rejected": "已人工拒绝",
    "stale": "已失效",
}

_CONFIDENCE_SOURCE_LABELS = {
    "deterministic": "确定性规则",
    "model_self_assessment": "模型自评（未校准）",
    "ocr_sidecar": "OCR 旁路提供",
    "unavailable": "未提供",
}


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status or "未知")


def _confidence_source_label(source: str) -> str:
    return _CONFIDENCE_SOURCE_LABELS.get(source, source or "未提供")


def _recognition_confidence_source(candidate: FactCandidate) -> str:
    source = candidate.provenance.get("recognition_confidence_source")
    if not source:
        source = candidate.locator.get("recognition_confidence_source")
    if not source and candidate.source_kind == "image_ocr":
        source = "ocr_sidecar"
    if not source:
        source = "deterministic"
    return str(source)


def _mapping_confidence_source(candidate: FactCandidate) -> str:
    source = candidate.mapping_confidence_source
    if not source:
        source = candidate.provenance.get("mapping_confidence_source")
    return str(source or "unavailable")


def _position_text(locator: dict[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(locator.get("page"), int):
        parts.append(f"第 {locator['page']} 页")
    elif locator.get("image"):
        parts.append(f"图片 {locator['image']}")
    elif locator.get("line") is not None:
        parts.append(f"第 {locator['line']} 行")
    elif locator.get("row") is not None:
        parts.append(f"第 {locator['row']} 行")

    if locator.get("sheet"):
        parts.append(f"工作表 {locator['sheet']}")
    if isinstance(locator.get("cells"), list):
        parts.append(f"单元格 {', '.join(str(item) for item in locator['cells'])}")
    elif locator.get("column") is not None:
        parts.append(f"第 {locator['column']} 列")
    if locator.get("paragraph") is not None:
        parts.append(f"段落 {locator['paragraph']}")
    if locator.get("table") is not None:
        parts.append(f"表格 {locator['table']}")
    if locator.get("json_path"):
        parts.append(f"JSON 路径 {locator['json_path']}")
    if locator.get("text_block") is not None:
        parts.append(f"文本块 {locator['text_block']}")

    bbox = locator.get("bbox_1000")
    precision = str(locator.get("position_precision", ""))
    if isinstance(bbox, list):
        if precision == "approximate":
            parts.append(f"近似坐标 bbox_1000={bbox}")
        else:
            parts.append(f"坐标 bbox_1000={bbox}")
    elif isinstance(locator.get("bbox"), list):
        parts.append(f"OCR 坐标 bbox={locator['bbox']}")
    elif precision == "unavailable":
        parts.append("图片坐标不可用")

    if not parts:
        return _locator_text(locator)
    return "；".join(parts)


def _confirmation_counts(
    candidates: Iterable[FactCandidate],
    run_summary: dict[str, Any] | None,
) -> dict[str, int]:
    if run_summary:
        stored = run_summary.get("confirmation_status_counts")
        if isinstance(stored, dict):
            return {
                status: int(stored.get(status, 0))
                for status in ("pending", "confirmed", "rejected", "stale")
            }
    counts = Counter(candidate.status for candidate in candidates)
    return {
        status: int(counts.get(status, 0))
        for status in ("pending", "confirmed", "rejected", "stale")
    }


def _group_recommendation(
    group: FactGroup,
    candidates_by_id: dict[str, FactCandidate],
) -> str:
    statuses = {
        candidates_by_id[candidate_id].status
        for candidate_id in group.candidate_ids
        if candidate_id in candidates_by_id
    }
    if statuses and statuses <= {"confirmed", "rejected"}:
        if "confirmed" in statuses:
            return (
                "该组已完成人工处理；已确认项可以进入正式导出。"
                "源文件变化后确认会自动失效。"
            )
        return "该组候选已全部人工拒绝，不会进入正式导出。"
    return group.recommendation


def write_product_facts(
    path: Path,
    *,
    candidates: Iterable[FactCandidate],
    groups: Iterable[FactGroup],
    relations: Iterable[CrossFieldRelation],
    run_summary: dict[str, Any],
) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "status": "pending_human_confirmation",
            "warning": "这些是候选事实和核验结果，不是已经确认的正式产品参数。",
            "run_summary": run_summary,
            "candidates": [candidate.to_dict() for candidate in candidates],
            "fact_groups": [group.to_dict() for group in groups],
            "cross_field_relations": [relation.to_dict() for relation in relations],
        },
    )


def write_conflicts_markdown(
    path: Path,
    candidates: Iterable[FactCandidate],
    groups: Iterable[FactGroup],
    relations: Iterable[CrossFieldRelation],
    *,
    run_summary: dict[str, Any] | None = None,
) -> None:
    candidate_list = list(candidates)
    group_list = list(groups)
    relation_list = list(relations)
    by_id = {candidate.candidate_id: candidate for candidate in candidate_list}
    status_counts = _confirmation_counts(candidate_list, run_summary)
    lines = [
        "# Product Evidence Guard 核验结果",
        "",
        "> 本报告只给出候选事实和冲突证据，程序不会替用户决定哪个值是真的。",
        "",
        "# 人工确认状态",
        "",
        f"- 待人工确认：{status_counts['pending']}",
        f"- 已人工确认：{status_counts['confirmed']}",
        f"- 已人工拒绝：{status_counts['rejected']}",
        f"- 已失效：{status_counts['stale']}",
        "",
    ]
    if status_counts["stale"]:
        lines.extend(
            [
                "> 有旧确认因源文件或文件哈希变化而失效，不能继续作为正式事实使用。",
                "",
            ]
        )
    document_visual_summary = (
        run_summary.get("document_visuals")
        if isinstance(run_summary, dict)
        else None
    )
    mixed_pdf_summary = (
        run_summary.get("mixed_pdf")
        if isinstance(run_summary, dict)
        else None
    )
    if isinstance(document_visual_summary, dict):
        lines.extend(
            [
                "# 文档视觉内容",
                "",
                "- 内嵌图片：发现 "
                f"{int(document_visual_summary.get('embedded_images_detected', 0))}，"
                "已处理 "
                f"{int(document_visual_summary.get('embedded_images_processed', 0))}，"
                "跳过 "
                f"{int(document_visual_summary.get('embedded_images_skipped', 0))}",
                "- 原生图表：发现 "
                f"{int(document_visual_summary.get('native_charts_detected', 0))}，"
                "已结构化 "
                f"{int(document_visual_summary.get('native_charts_structured', 0))}",
            ]
        )
        if isinstance(mixed_pdf_summary, dict):
            lines.append(
                "- PDF 正文与图形混合页：发现 "
                f"{int(mixed_pdf_summary.get('pages_detected', 0))}，"
                "文本层结构化 "
                f"{int(mixed_pdf_summary.get('pages_structured_from_text_layer', 0))}，"
                "本地图像 AI 处理 "
                f"{int(mixed_pdf_summary.get('pages_processed_with_local_image_ai', 0))}，"
                "跳过 "
                f"{int(mixed_pdf_summary.get('pages_skipped', 0))}"
            )
        lines.extend(
            [
                "- 文档视觉问题："
                f"{int(document_visual_summary.get('issues', 0))}",
                "",
                "> 图表完整数据、图片路由与位置见 `document-visuals.json`；"
                "其中内容是观察和结构化上下文，不是 FactCandidate 或正式事实。",
                "",
            ]
        )
    ordered = sorted(group_list, key=lambda item: ({"block": 0, "review": 1, "pass": 2}.get(item.severity, 3), item.field))
    for group in ordered:
        recommendation = _group_recommendation(group, by_id)
        lines.extend(
            [
                f"## {group.field_label} — `{group.classification}`",
                "",
                f"- 严重程度：**{group.severity}**",
                f"- 判断：{group.reason}",
                f"- 证据一致度：{group.evidence_consistency:.0%}",
                f"- 建议：{recommendation}",
                "",
                "证据：",
                "",
            ]
        )
        for candidate_id in group.candidate_ids:
            candidate = by_id[candidate_id]
            standard = candidate.normalized_value
            if candidate.normalized_unit:
                standard = f"{standard} {candidate.normalized_unit}"
            recognition_source = _confidence_source_label(
                _recognition_confidence_source(candidate)
            )
            mapping_source = _confidence_source_label(
                _mapping_confidence_source(candidate)
            )
            lines.extend(
                [
                    f"- `{candidate.raw_value}` → `{standard}`；状态："
                    f"**{_status_label(candidate.status)}** (`{candidate.status}`)",
                    f"  - 来源：`{candidate.source_file}`；类型：`{candidate.source_kind}`；"
                    f"方法：`{candidate.extraction_method}`",
                    f"  - 位置：{_position_text(candidate.locator)}",
                    f"  - 识别可信：{candidate.recognition_confidence:.0%}（{recognition_source}）；"
                    f"字段理解：{candidate.mapping_confidence:.0%}（{mapping_source}）",
                ]
            )
        lines.append("")

    if relation_list:
        lines.extend(["# 跨字段提醒", ""])
        for relation in relation_list:
            lines.extend(
                [
                    f"## `{relation.relation_type}`",
                    "",
                    f"- 字段：{', '.join(relation.fields)}",
                    f"- 说明：{relation.reason}",
                    "",
                ]
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def write_html_report(
    path: Path,
    candidates: Iterable[FactCandidate],
    groups: Iterable[FactGroup],
    relations: Iterable[CrossFieldRelation],
    run_summary: dict[str, Any],
) -> None:
    candidate_list = list(candidates)
    group_list = list(groups)
    relation_list = list(relations)
    by_id = {candidate.candidate_id: candidate for candidate in candidate_list}
    status_counts = _confirmation_counts(candidate_list, run_summary)
    severity_class = {"block": "danger", "review": "warn", "pass": "ok", "info": "info"}
    cards: list[str] = []
    ordered = sorted(group_list, key=lambda item: ({"block": 0, "review": 1, "pass": 2}.get(item.severity, 3), item.field))
    for group in ordered:
        recommendation = _group_recommendation(group, by_id)
        evidence_rows: list[str] = []
        for candidate_id in group.candidate_ids:
            candidate = by_id[candidate_id]
            standard = escape(str(candidate.normalized_value))
            if candidate.normalized_unit:
                standard += " " + escape(candidate.normalized_unit)
            recognition_source = escape(
                _confidence_source_label(_recognition_confidence_source(candidate))
            )
            mapping_source = escape(
                _confidence_source_label(_mapping_confidence_source(candidate))
            )
            status = escape(_status_label(candidate.status))
            status_code = escape(candidate.status or "pending")
            evidence_rows.append(
                "<tr>"
                f"<td>{escape(candidate.raw_value)}</td>"
                f"<td>{standard}</td>"
                f"<td>{escape(candidate.source_file)}</td>"
                f"<td><code>{escape(candidate.source_kind)}</code><br>"
                f"<code>{escape(candidate.extraction_method)}</code></td>"
                f"<td>{escape(_position_text(candidate.locator))}</td>"
                f"<td>识别 {candidate.recognition_confidence:.0%}"
                f"<small class='confidence-source'>{recognition_source}</small>"
                f"字段理解 {candidate.mapping_confidence:.0%}"
                f"<small class='confidence-source'>{mapping_source}</small></td>"
                f"<td><span class='status {status_code}'>{status} ({status_code})</span></td>"
                "</tr>"
            )
        css_class = severity_class.get(group.severity, "info")
        cards.append(
            f"<section class='card {css_class}'>"
            f"<h2>{escape(group.field_label)} <small>{escape(group.classification)}</small></h2>"
            f"<p>{escape(group.reason)}</p>"
            f"<p><strong>建议：</strong>{escape(recommendation)}</p>"
            f"<p><strong>三层可信度：</strong>识别 {group.recognition_confidence:.0%} · "
            f"字段理解 {group.mapping_confidence:.0%} · 证据一致 {group.evidence_consistency:.0%}</p>"
            "<div class='table-wrap'><table><thead><tr><th>原始值</th><th>标准值</th>"
            "<th>来源</th><th>来源类型 / 方法</th><th>证据位置</th>"
            "<th>识别 / 字段理解</th><th>确认状态</th></tr></thead>"
            f"<tbody>{''.join(evidence_rows)}</tbody></table></div></section>"
        )

    relation_html = ""
    if relation_list:
        relation_html = "<h2>跨字段提醒</h2>" + "".join(
            f"<section class='card info'><h3>{escape(item.relation_type)}</h3>"
            f"<p>{escape(item.reason)}</p></section>"
            for item in relation_list
        )

    stale_notice = ""
    if status_counts["stale"]:
        stale_notice = (
            "<p class='stale-notice'><strong>失效提醒：</strong>"
            "有旧确认因源文件或文件哈希变化而失效，不能继续作为正式事实使用。</p>"
        )
    confirmation_html = (
        "<section class='confirmation-summary'><h2>人工确认状态</h2>"
        f"<span class='status pending'>待确认 {status_counts['pending']}</span>"
        f"<span class='status confirmed'>已确认 {status_counts['confirmed']}</span>"
        f"<span class='status rejected'>已拒绝 {status_counts['rejected']}</span>"
        f"<span class='status stale'>已失效 {status_counts['stale']}</span>"
        f"{stale_notice}</section>"
    )
    document_visual_summary = run_summary.get("document_visuals")
    mixed_pdf_summary = run_summary.get("mixed_pdf")
    document_visual_html = ""
    if isinstance(document_visual_summary, dict):
        mixed_detected = (
            int(mixed_pdf_summary.get("pages_detected", 0))
            if isinstance(mixed_pdf_summary, dict)
            else 0
        )
        mixed_processed = (
            int(
                mixed_pdf_summary.get(
                    "pages_processed_with_local_image_ai",
                    0,
                )
            )
            if isinstance(mixed_pdf_summary, dict)
            else 0
        )
        mixed_structured = (
            int(
                mixed_pdf_summary.get(
                    "pages_structured_from_text_layer",
                    0,
                )
            )
            if isinstance(mixed_pdf_summary, dict)
            else 0
        )
        document_visual_html = (
            "<section class='card info'><h2>文档视觉内容</h2>"
            "<p>内嵌图片：发现 "
            f"{int(document_visual_summary.get('embedded_images_detected', 0))}，"
            "已处理 "
            f"{int(document_visual_summary.get('embedded_images_processed', 0))}；"
            "原生图表：发现 "
            f"{int(document_visual_summary.get('native_charts_detected', 0))}，"
            "已结构化 "
            f"{int(document_visual_summary.get('native_charts_structured', 0))}；"
            f"PDF 混合页：发现 {mixed_detected}，"
            f"文本层结构化 {mixed_structured}，"
            f"本地图像 AI 处理 {mixed_processed}。</p>"
            "<p>完整图表数据、图片路由和位置见 "
            "<code>document-visuals.json</code>；其中内容是观察和结构化上下文，"
            "不是 FactCandidate 或正式事实。</p>"
            "</section>"
        )
    summary = escape(json.dumps(run_summary, ensure_ascii=False))
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Product Evidence Guard</title>
<style>
body{{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:1180px;margin:40px auto;padding:0 20px;background:#f6f7fb;color:#172033}}
h1{{margin-bottom:6px}} .subtitle{{color:#5b6475;margin-top:0}}
.card{{background:white;border-left:6px solid #8b95a7;border-radius:12px;padding:18px 20px;margin:18px 0;box-shadow:0 4px 18px rgba(25,34,54,.07)}}
.card.danger{{border-color:#d83a52}} .card.warn{{border-color:#e79b22}} .card.ok{{border-color:#27a36a}} .card.info{{border-color:#477bd8}}
small{{font-size:.58em;color:#667085}} .table-wrap{{overflow-x:auto}} table{{width:100%;border-collapse:collapse;margin-top:12px;min-width:980px}} th,td{{text-align:left;padding:10px;border-bottom:1px solid #e6e9ef;vertical-align:top}} code{{white-space:pre-wrap;word-break:break-word}} .notice{{background:#fff4d8;border-radius:10px;padding:14px}}
.confirmation-summary{{background:white;border-radius:12px;padding:14px 18px;margin:18px 0}} .confirmation-summary h2{{margin-top:0}}
.status{{display:inline-block;border-radius:999px;padding:4px 9px;margin:2px 5px 2px 0;font-size:.86rem;background:#edf0f5;color:#344054;white-space:nowrap}}
.status.confirmed{{background:#dff6e9;color:#176b43}} .status.rejected{{background:#fde7ea;color:#9f2438}} .status.stale{{background:#fff0d6;color:#8a5510}}
.confidence-source{{display:block;font-size:.78rem;margin:2px 0 7px;color:#667085}} .stale-notice{{background:#fff0d6;border-radius:8px;padding:10px}}
</style>
</head>
<body>
<h1>Product Evidence Guard</h1>
<p class="subtitle">本地多来源商品事实核验报告</p>
<p class="notice"><strong>注意：</strong>报告中的内容仍是候选事实。强冲突必须人工处理，任何候选都不会自动成为正式产品参数。模型自评分数未经校准。</p>
{confirmation_html}
{document_visual_html}
{''.join(cards)}
{relation_html}
<details><summary>本次增量分析摘要</summary><pre>{summary}</pre></details>
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, html)
