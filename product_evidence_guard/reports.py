from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any, Iterable

from .models import CrossFieldRelation, FactCandidate, FactGroup
from .state import atomic_write_json


def _locator_text(locator: dict[str, Any]) -> str:
    return json.dumps(locator, ensure_ascii=False, sort_keys=True)


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
) -> None:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    lines = [
        "# Product Evidence Guard 核验结果",
        "",
        "> 本报告只给出候选事实和冲突证据，程序不会替用户决定哪个值是真的。",
        "",
    ]
    ordered = sorted(groups, key=lambda item: ({"block": 0, "review": 1, "pass": 2}.get(item.severity, 3), item.field))
    for group in ordered:
        lines.extend(
            [
                f"## {group.field_label} — `{group.classification}`",
                "",
                f"- 严重程度：**{group.severity}**",
                f"- 判断：{group.reason}",
                f"- 证据一致度：{group.evidence_consistency:.0%}",
                f"- 建议：{group.recommendation}",
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
            lines.append(
                f"- `{candidate.raw_value}` → `{standard}`；来源：`{candidate.source_file}`；位置："
                f"`{_locator_text(candidate.locator)}`；方法：`{candidate.extraction_method}`"
            )
        lines.append("")

    if relations:
        lines.extend(["# 跨字段提醒", ""])
        for relation in relations:
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
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_html_report(
    path: Path,
    candidates: Iterable[FactCandidate],
    groups: Iterable[FactGroup],
    relations: Iterable[CrossFieldRelation],
    run_summary: dict[str, Any],
) -> None:
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    severity_class = {"block": "danger", "review": "warn", "pass": "ok", "info": "info"}
    cards: list[str] = []
    ordered = sorted(groups, key=lambda item: ({"block": 0, "review": 1, "pass": 2}.get(item.severity, 3), item.field))
    for group in ordered:
        evidence_rows: list[str] = []
        for candidate_id in group.candidate_ids:
            candidate = by_id[candidate_id]
            standard = escape(str(candidate.normalized_value))
            if candidate.normalized_unit:
                standard += " " + escape(candidate.normalized_unit)
            evidence_rows.append(
                "<tr>"
                f"<td>{escape(candidate.raw_value)}</td>"
                f"<td>{standard}</td>"
                f"<td>{escape(candidate.source_file)}</td>"
                f"<td><code>{escape(_locator_text(candidate.locator))}</code></td>"
                f"<td>{candidate.recognition_confidence:.0%} / {candidate.mapping_confidence:.0%}</td>"
                "</tr>"
            )
        css_class = severity_class.get(group.severity, "info")
        cards.append(
            f"<section class='card {css_class}'>"
            f"<h2>{escape(group.field_label)} <small>{escape(group.classification)}</small></h2>"
            f"<p>{escape(group.reason)}</p>"
            f"<p><strong>建议：</strong>{escape(group.recommendation)}</p>"
            f"<p><strong>三层可信度：</strong>识别 {group.recognition_confidence:.0%} · "
            f"字段理解 {group.mapping_confidence:.0%} · 证据一致 {group.evidence_consistency:.0%}</p>"
            "<table><thead><tr><th>原始值</th><th>标准值</th><th>来源</th><th>位置</th><th>识别/理解</th></tr></thead>"
            f"<tbody>{''.join(evidence_rows)}</tbody></table></section>"
        )

    relation_html = ""
    if relations:
        relation_html = "<h2>跨字段提醒</h2>" + "".join(
            f"<section class='card info'><h3>{escape(item.relation_type)}</h3>"
            f"<p>{escape(item.reason)}</p></section>"
            for item in relations
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
small{{font-size:.58em;color:#667085}} table{{width:100%;border-collapse:collapse;margin-top:12px}} th,td{{text-align:left;padding:10px;border-bottom:1px solid #e6e9ef;vertical-align:top}} code{{white-space:pre-wrap;word-break:break-word}} .notice{{background:#fff4d8;border-radius:10px;padding:14px}}
</style>
</head>
<body>
<h1>Product Evidence Guard</h1>
<p class="subtitle">本地多来源商品事实核验报告</p>
<p class="notice"><strong>注意：</strong>报告中的内容仍是候选事实。强冲突必须人工处理，任何候选都不会自动成为正式产品参数。</p>
{''.join(cards)}
{relation_html}
<details><summary>本次增量分析摘要</summary><pre>{summary}</pre></details>
</body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
