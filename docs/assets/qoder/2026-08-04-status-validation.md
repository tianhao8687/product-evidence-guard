# Qoder status 调用脱敏记录

日期：2026-08-04

Qoder CLI：1.1.8

Skill：`local-product-evidence-guard`

安装范围：用户级

数据范围：未读取商品文件、样本或报告

## 手动触发

输入以 `/local-product-evidence-guard` 开头，并明确仅执行 `status`。

Qoder 记录的实际入口：

```text
%USERPROFILE%\.qoder\skills\local-product-evidence-guard\scripts\run.ps1 status
```

结果：工具退出码 `0`，`ok=true`，`operation=status`，`status=stopped`。

## 自动路由

输入为自然语言“我准备检查商品资料文件夹，请自动选择匹配的本地离线商品事实
核验 Skill，先告诉我服务是否就绪”，并明确禁止读取文件、安装、下载和分析。

Qoder 的流式记录显示：

```text
Skill: local-product-evidence-guard
Launching skill: local-product-evidence-guard
Base directory: %USERPROFILE%\.qoder\skills\local-product-evidence-guard
Command: powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run.ps1 status
Tool exitCode: 0
```

返回 JSON 的中文没有乱码。服务状态为 `stopped`，原因是先前常驻服务达到
`idle_timeout` 后正常关闭，不是运行错误。

## 边界

本记录只证明 Qoder 的自动路由、手动触发和固定入口状态调用已经通过。它不证明
样本分析、确认、拒绝、导出、stale、英文自动触发或断网推理已经在 Qoder 会话中
通过。Qoder 宿主会使用云端 Agent；在未获得明确数据出境授权前，不向它提供本地
样本正文或报告内容。
