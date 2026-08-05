# Draft PR：Product Evidence Guard 竞赛发布候选

> 建议标题：`feat: deliver offline Product Evidence Guard skill for Qoder and OpenVINO`
>
> 本文是可复制到 GitHub PR #1 的草稿，不代表已经更新远端 PR。最终包的 commit、
> SHA-256 和精确净室耗时以标准路径 ZIP 旁的 `.verification.json` 为准；复制到 PR
> 时应从该机器记录填入，不能在归档内部预填自引用哈希。

## 概要

本 PR 把 Product Evidence Guard 完善为一个可由 Qoder 发现和调用的本地 AI Skill：
它从图片、PDF、DOCX、XLSX 与结构化文本提取可追溯候选，使用 OpenVINO OCR 与
Qwen3-VL 处理需要视觉理解的内容，再由确定性代码完成 schema 校验、单位归一、
证据分组、冲突判断、人工确认、正式导出和来源变化后的 stale 失效。

所有公开操作统一经过 `scripts/run.ps1`。Windows Named Pipe 常驻服务复用模型，
避免每次 Qoder 操作都重新加载 8B 模型；候选始终保持 `pending`，只有用户明确提供
candidate ID 和理由后才能确认或拒绝。

## 功能与文件变化

| 区域 | 主要文件 | 变化 |
|---|---|---|
| Skill 与元数据 | `SKILL.md`、`info.json`、`meta.json` | 中英文自动触发、唯一公开入口、退出码、模型与隐私边界 |
| 本地常驻运行时 | `scripts/run.ps1`、`client.py`、`server.py`、`protocol.py` | Named Pipe 协议、authkey、1 MiB 上限、状态查询、模型复用、关闭与错误恢复 |
| 安装与发布 | `install-env.ps1`、`install-qoder-skill.ps1`、`model_download.py`、`package-release.ps1` | Python 3.11、hash lock、模型断点续传、完整性校验、Qoder 安装、确定性安全 ZIP |
| 图片与文档理解 | `hybrid_image_reader.py`、`document_visuals.py`、`qwen_vl_reader.py`、`parsers.py` | OCR 快速路径、Qwen 紧凑复核/深度兜底、Office 内嵌图与图表、PDF mixed page/ROI/observation-only |
| 证据与业务规则 | `engine.py`、`extractor.py`、`normalization.py`、`graph.py`、`confirmation.py`、`reports.py` | 来源定位、单位换算、冲突分级、审计、confirmed-only 导出和 stale 失效 |
| 测试与供应链 | `tests/`、`requirements.lock`、`docs/evidence/` | Windows E2E、负面资源测试、发布合同、SBOM、许可证、性能/网络/Qoder/Benchmark 脱敏证据 |
| 竞赛材料 | `docs/ARTICLE_DRAFT.md`、`DEMO_SCRIPT.md`、`assets/competition/` | 长文草稿、3/5 分钟演示脚本、字幕、架构图和性能图 |

## 测试命令与冻结结果

正式源码回归命令：

```powershell
powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\tests\test.ps1
```

当前源码与最终包结果：

| 范围 | 结果 | 证据 |
|---|---|---|
| 当前 D 盘源码回归 | 263 项通过，178.084 s，0 跳过；Windows 业务 E2E 通过 | `docs/evidence/final-local-regression-20260805.json` |
| 最终精确 ZIP clean-room | Python 3.11.13、36 个已安装包；263 项通过，0 跳过；精确 commit、SHA-256、测试与墙钟耗时见相邻记录 | `release/local-product-evidence-guard-v1.0.0.verification.json` |
| 编译与业务 smoke | `compileall`、确定性 smoke、增量复用、confirm/reject/export/stale、Pipe status/shutdown 全部通过 | 同上 verification JSON |

最终发布包使用标准文件名 `local-product-evidence-guard-v1.0.0.zip`。包内模型数、
真实样本 payload、私有主机路径和凭据命中数均为 0；manifest 与 Git tree/blob
逐项匹配。精确文件数、字节数、SHA-256 和 manifest commit 由相邻 verification
记录保存，任何后续源码变更都必须重建并重新执行净室验证。

本次提交材料增量可用以下命令复核：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_submission_materials -v
.\.venv\Scripts\python.exe -m unittest tests.test_submission_contract -v
git diff --check
```

## 真实模型

| 状态 | 已验证内容 | 不能据此声称 |
|---|---|---|
| 已验证 | 精确 `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` 在 Ryzen 7 7800X3D 的 CPU 上完成公开领域真实标签图功能链；最终单图加载/内层/外层为 3.6064/57.1824/61.895 s，生成 1 个 pending 候选 | 不是总体准确率，不是 Intel GPU/NPU 结果 |
| 已验证 | 三次独立冷分析均成功，分析均值 26.7046 s，范围 25.5298–27.6312 s | 只是一台机器和一张图片的三次分布，不是延迟 SLA |
| 已验证 | 真实 PDF/DOCX/XLSX 原生解析与 mixed PDF observation-only 路线已有脱敏摘要 | 图纸/曲线 observations 不等于完整曲线数字化准确率 |

真实模型测试是 opt-in，普通 CI 不下载或运行 5.46 GB 模型。

## Qoder

| 状态 | 已验证内容 | 待完成/边界 |
|---|---|---|
| 已验证 | Qoder CLI 1.1.8；用户级 Skill 为 `Enabled`；本地更新后的安装副本与源码 6 个关键文件 SHA-256 一致；`run.ps1 status` 退出码 0 | 更新复用现有运行时，没有下载、发送 Qoder 云端消息、读取商品文件或加载模型 |
| 已验证 | 中英文自动触发、手动触发、匿名 sidecar 的 analyze/confirm/reject/export/stale，以及公开真实图片 Qwen 冷/热/缓存调用 | 确定性 sidecar 闭环不能冒充真实 Qwen 生成结果 |
| 部分完成 | 已保留 Skill 发现页与 IDE 安全门两张脱敏截图 | 真实图片＋受控文档本轮 confirm/reject/export/stale 决定和完整录屏仍需用户操作 |

## 离线与网络边界

| 状态 | 已验证内容 | 待完成/边界 |
|---|---|---|
| 已验证 | 模型与依赖已缓存后，在 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`OPENVINO_TELEMETRY_DISABLED=1` 下完成本地推理 | 环境变量不等于物理断网证明 |
| 已验证 | 一次独立冷运行的 35 个约 100 ms TCP 状态观察周期中，目标进程树未观察到外部 TCP；客户端退出码 0 | 不是防火墙阻断、数据包捕获、DNS、UDP 或 air-gap 证明 |
| 已验证 | 精确 ZIP clean-room 使用 `UV_OFFLINE=1`、`PIP_NO_INDEX=1` 和本地已校验缓存，未复制模型 | 不证明 Qoder 宿主或全部第三方组件永久无外连 |

## Benchmark

| 状态 | 已验证内容 | 边界 |
|---|---|---|
| 已验证 | commit `06f8360` 的 synthetic CPU Benchmark：30 张图片、10 份文档，图片 30/30 成功、0 失败、无文档错误 | synthetic 指标不是实际客户资料准确率 |
| 已验证 | 模型加载 3.701264 s、首图 75.969346 s、冷总计 79.670610 s、热图中位数 76.013413 s、逐图 p90 85.350259 s | `requested_device=CPU` 字段单独看不是硬件执行证明；真实设备结论还依赖运行工件 |
| 已验证 | 字段 25/25、数字 27/27、单位 15/15；空样本 0/5 编造、提示注入 0/2 接受、冲突分类 TP/FP/FN/TN 为 2/0/0/10 | 1.0 只适用于固定 deterministic synthetic 数据 |

公开机器摘要为
`docs/evidence/synthetic-benchmark-final-06f8360.json`，原始逐样本输出继续留在 Git 外。

## CI

| 状态 | 内容 |
|---|---|
| 已配置 | GitHub Actions 包含 Ubuntu/Python 3.11 的 compile、unit、demo smoke，以及 Windows/Python 3.11 的 `tests/test.ps1` |
| 远端只读快照 | Draft PR #1 的旧 head `4295ab8`：pull_request 与 push 两次运行的 Linux/Windows jobs 均成功；review/comment 均为 0 |
| 未覆盖 | 当前本地脱敏分支更新尚未推送，因此上述历史绿色不证明当前提交的 CI |

合并前必须在 PR Actions 页面确认最新 Linux 与 Windows required checks；若失败，
保留失败状态和日志结论，不能用历史绿色覆盖当前失败。

## Mermaid 审计

全部 32 份发布范围 Markdown 已做本地扫描，发现 5 个 Mermaid 块，均位于
`docs/ARCHITECTURE.md`。围栏、首行声明、引号和 `[]/{}/()` 平衡静态检查通过，
详见 `docs/evidence/mermaid-static-audit-20260805.json`。

本机没有 Node、Mermaid CLI 或本地 Mermaid JavaScript，且本次没有下载依赖；因此
没有使用真实 Mermaid 引擎渲染。Chrome/Edge 单独不能解析 Mermaid 源码。合并前应
在 GitHub PR 预览或已批准且预装的 Mermaid renderer 中逐图确认，当前状态只能写成
“静态检查通过，真实渲染待验证”。

## 已知风险与失败边界

- 任何后续源码提交都会使 ZIP、SHA-256、manifest commit 和 clean-room 记录过期，
  必须重新打包和验证。
- 真实模型对空白、模糊、遮挡、无参数和提示注入图片的完整对抗矩阵仍不足；schema
  与 mock 失败关闭测试不能代替真实模型测试。
- PDF 页数、像素、解压资源、临时目录清理和恶意文档虽有负面测试，仍不等于安全
  审计或渗透测试。
- 35 周期 TCP 观察没有发现外连，但未执行防火墙阻断、packet capture、DNS 或 UDP
  审计。
- 完整二进制传递依赖许可证审计、模型 manifest 签名与独立漏洞扫描尚未全部完成。
- 当前本地提交的 GitHub Actions、Mermaid 真实渲染、完整 Qoder 截图/视频和外部发布均需要
  外部状态或用户操作。

## 合并/投稿前的用户操作

1. 将本草稿复制到 Draft PR #1，并替换为最终 commit、ZIP SHA-256 和 clean-room
   数据。
2. 推送最终分支后检查最新 Linux/Windows Actions；记录失败或绿色结果。
3. 在 GitHub PR 预览中逐个检查 5 个 Mermaid 图，确认节点、中文、箭头和换行正确。
4. 在 Qoder 中完成真实图片＋受控文档的用户决定、导出、来源变化与 stale 录屏；
   截图前继续遮盖账号、绝对路径和样本正文。
5. 录制并检查 3/5 分钟视频，确认字幕把“已验证”“限制”“待完成”分开。
6. 用户本人登录 ModelScope，上传最终 ZIP/文章/视频链接并保存比赛提交成功页面。

## Reviewer 快速入口

- 使用与限制：`README.md`、`docs/LIMITATIONS.md`
- 架构：`docs/ARCHITECTURE.md`
- Benchmark：`docs/BENCHMARK.md` 与 `docs/evidence/synthetic-benchmark-final-06f8360.json`
- Qoder：`docs/QODER_VALIDATION.md` 与 `docs/evidence/qoder-install-integrity-20260805.json`
- 安全边界：`docs/evidence/log-privacy-contract-20260805.json` 与 `docs/evidence/named-pipe-security-boundaries-20260805.json`
- 远端旧 head：`docs/evidence/remote-pr-snapshot-20260805.json`
- 发布校验：`release/local-product-evidence-guard-v1.0.0.verification.json`
- SBOM/许可证：`docs/evidence/sbom.json`、`license-inventory.json`、`pypdfium2-notices.json`
