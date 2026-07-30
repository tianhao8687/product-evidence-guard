# Product Evidence Guard

**本地商品事实核验 · v1.0.0**

把同一商品的说明书、参数表、文字版或扫描版 PDF、文本和包装图放进一个文件夹，Product Evidence Guard 会在本机：

- 找出型号、材质、颜色、重量、尺寸、数量、电气参数和容量等候选；
- 保留文件 SHA-256、页码、行号、单元格或图片近似区域；
- 把 `0.32kg` 与 `320g` 判断为换算一致；
- 区分净重、毛重和未说明口径的普通重量；
- 找出跨文件强冲突；
- 让用户明确确认或拒绝候选，并保留理由与审计记录；
- 在源文件变化后自动把旧确认标记为失效。

AI 只负责读取图片原文和映射字段。单位换算、冲突判断、确认与失效都由确定性代码完成；**任何 AI 候选都不会自动成为正式产品事实。**

## 隐私边界

推理使用本地 OpenVINO 和本地模型，不调用云端模型、云端 OCR 或付费 API。联网只用于安装公开依赖和下载公开模型。程序不收集遥测，不修改用户输入，也不把商品资料写入普通运行日志。

模型下载完成后，已经在 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、
`OPENVINO_TELEMETRY_DISABLED=1` 下完成本地推理。该结果证明离线开关路径可用，
但尚未做防火墙阻断或抓包审计，不能扩写成“已证明零外连”。

输出报告会包含原文证据，应当按客户资料同等保护。详见 [PRIVACY.md](PRIVACY.md) 和 [SECURITY.md](SECURITY.md)。

## 支持的资料

| 类型 | 处理方式 |
|---|---|
| TXT、Markdown、CSV、JSON | 确定性读取 |
| DOCX、XLSX、带文字层 PDF | 确定性读取并保留段落、单元格或页码 |
| JPG、JPEG、PNG、WebP、BMP | 本地 Qwen3-VL 两步读取 |
| 无文字层的扫描 PDF | 本机逐页渲染，再走同一视觉模型 |
| `*.ocr.json` | 保留的显式兼容输入；不作为隐藏 OCR 回退 |

第一版主模型固定为：

```text
OpenVINO/Qwen3-VL-8B-Instruct-int4-ov
```

模型许可证为 Apache-2.0，固定下载 revision 为 `f3d0bc7`。顶层依赖固定在
`requirements.txt`，Windows/Python 3.11.13 的 27 个锁定包及下载哈希固定在
`requirements.lock`；该锁文件是正式发布包和 Qoder 安装的必备文件。26 个模型
payload 共 `5,462,515,610` bytes，包含 Hugging Face 元数据的模型目录共
`5,462,526,140` bytes。建议至少 16 GB 内存并预留足够磁盘空间。实际设备、版本
和测试边界见
[docs/MODEL_AND_RUNTIME.md](docs/MODEL_AND_RUNTIME.md)。

发布阶段的 26 文件 SHA-256 清单位于
`<project-root>\release\model-sha256-manifest.json`。它提供逐文件校验，但尚未
签名。发布 ZIP 的源码身份以 ZIP 内 `manifest.json` 的 `commit` 字段为准，ZIP
整体以同目录 `.sha256` 文件为准；`manifest.json` 不对自身做自引用哈希。

## 1. 安装独立环境

要求 Windows 10/11。所有用户和 Qoder 命令只使用 `scripts\run.ps1`；安装器是一次性准备步骤。

```powershell
cd <product-evidence-guard>
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-env.ps1
```

安装器会：

- 使用固定版本 `uv 0.8.4`，并校验官方 Windows ZIP 的固定 SHA-256
  `817c50c80229f88de9699626ee3774c0cceed86099663e8fb00c5ffae7ea911c`；
- 在项目内准备固定的 Python `3.11.13`；
- 创建项目自己的 `.venv`；
- 从 `requirements.lock` 以 `--require-hashes` 安装完整锁定依赖；
- 校验依赖一致性并执行 `pip check`；
- 重复运行时快速跳过。

它不会把依赖安装到系统 Python。2026-07-30 已在 Windows PowerShell 5.1 用
`-Force` 完成一次干净重建：固定 Python `3.11.13`，27 个运行/构建包全部要求
哈希，editable build 使用 `--no-build-isolation --no-index`；环境盘点为 28 个
已安装包（含本项目），`pip check` 报告兼容。随后再次执行命中安装 stamp 并快速
跳过。

## 2. 第一次分析与模型下载

以下示例用占位符代替机器专属盘符；请先把它们改成自己的目录：

```powershell
$ProjectRoot = (Resolve-Path ".").Path
$InputRoot = "<input-root>"
$ArtifactRoot = "<artifact-root>"
$ModelRoot = Join-Path $ProjectRoot ".models\Qwen3-VL-8B-Instruct-int4-ov"

powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 analyze $InputRoot
```

若本项目目录下还没有完整模型，客户端会保存待继续请求并下载到：

```text
.models\Qwen3-VL-8B-Instruct-int4-ov.partial
```

下载未完成时返回退出码 `3`，临时目录绝不会被当成正式模型。随后运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 --continue
```

完整性校验通过后才会原子切换为正式模型目录，并恢复先前的分析请求。

也可以显式使用已经完整下载的模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 analyze `
  $InputRoot `
  --model $ModelRoot `
  --device CPU `
  --output $ArtifactRoot
```

`--deterministic-only` 仅供不含图片模型验证的测试或 CI 使用，不能用来声称真实视觉模型已运行。

## 3. 看懂输出

默认输出目录是资料文件夹下的 `.peg-output`：

```text
.peg-output/
├── visual-transcription.json
├── product-facts.json
├── confirmed-product-facts.json
├── conflicts.md
├── evidence-report.html
├── run-summary.json
├── confirmation-state.json
├── confirmation-audit.jsonl
└── analysis-state.json
```

先打开 `conflicts.md` 或 `evidence-report.html`。推荐阅读顺序：

1. 强冲突：不同来源无法同时成立，必须人工选择；
2. 待复核：单条证据、模糊内容或表达粒度不同；
3. 一致项：原值一致或换算后一致；
4. 证据：回到具体文件、页码、行号、单元格或图片近似区域。

`product-facts.json` 顶层始终标记：

```text
pending_human_confirmation
```

这不是“模型最终答案”。只有用户明确确认后，当前仍有效的事实才会进入 `confirmed-product-facts.json`。

## 4. 确认、拒绝与导出

从分析结果读取 `session_id` 和要处理的 `candidate_id`。确认必须包含真实理由：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 confirm `
  --output-dir $ArtifactRoot `
  --session-id "<session-id>" `
  --candidate-id "<candidate-id>" `
  --reason "已与供应商最新规格表核对"
```

拒绝：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 reject `
  --output-dir $ArtifactRoot `
  --session-id "<session-id>" `
  --candidate-id "<candidate-id>" `
  --reason "旧包装版本，已停用"
```

导出：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 export `
  --output-dir $ArtifactRoot `
  --session-id "<session-id>"
```

若源文件被修改或删除，依赖该文件哈希的旧决定会自动变为 `stale`，不会继续作为有效正式事实导出。

## 5. 服务状态

本地模型由 Windows Named Pipe 服务按需承载：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 status
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 shutdown
```

统一退出码：

| 代码 | 含义 |
|---:|---|
| 0 | 成功 |
| 1 | 参数、权限、输入、模型或操作错误 |
| 2 | 客户端/服务通信错误 |
| 3 | 模型仍在下载，需要 `--continue` |

每个客户端连接只发送一个 UTF-8 JSON 请求并接收一个 JSON 响应。业务输出与服务日志分离。

真实 4 图推理期间，另一个 `run.ps1 status` 请求已通过 Named Pipe 在 0.438 秒内
返回 `running` 和 `available_operations`，响应没有 fallback 字段。这证明父服务在
模型 worker 推理时仍能并发响应状态请求。

服务端对模型加载、每个文件和最终化阶段分别使用 300 秒心跳边界。若某一边界超时，
只终止该服务创建并精确跟踪的模型 worker，不按进程名结束其他 Python。客户端对
整个文件夹请求另设 1 小时上限。

## 6. 安装到 Qoder

用户级安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-qoder-skill.ps1
```

项目级安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-qoder-skill.ps1 `
  -Scope Project `
  -ProjectRoot "<qoder-project-root>"
```

同名目录存在时脚本不会静默覆盖；只有显式 `-Update` 才会先备份再更新。安装包
必须包含 `requirements.lock`，但不包含模型、虚拟环境、日志、输出或真实商品
样例。安装器的 allowlist、完整性、回滚及 symlink/junction/reparse point/hardlink
拒绝合同已纳入最终本地回归。

Qoder CLI 1.1.8 已隔离安装，用户级 Skill 已被 `qodercli skills list` 发现为
`Enabled`；但 `qodercli status` 显示 `Account: Not logged in`。用户登录后才可以
实际验证自动或手动调用。登录后可以说：

> 核对包装图和参数表有没有冲突。

也可以手动调用 `/local-product-evidence-guard`。实际 Qoder 验证状态与截图要求见 [docs/QODER_VALIDATION.md](docs/QODER_VALIDATION.md)。

## 7. 测试

不下载大模型的完整测试：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\test.ps1
```

Python 单元测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

最终本地回归为 **123 项通过（50.978 s）**。Windows PowerShell
`tests\test.ps1` 内部同一 123 项也通过（49.864 s），随后输出 `status=passed`
的 JSON smoke：`unit_tests`、中文空格路径、`compileall`、确定性分析、无变化
增量复用、Named Pipe `status` 和 `shutdown` 均通过；无效路径退出码为 `1`。

这些是本地最终回归结果。GitHub CI 在 commit `5a4fad8` 曾临时绿色；最终收尾
提交尚未推送，因此对应 CI 必须在推送后记录，本文不预先声称其通过。

真实模型与真实图片测试单独运行：

```powershell
$RealSampleRoot = Join-Path $ProjectRoot "samples\real"
$RealImage = Join-Path $RealSampleRoot "wegmans-octopus-salad-label.jpg"

powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\test-real-model.ps1 `
  -InputDirectory $RealSampleRoot `
  -ImagePath $RealImage `
  -ModelPath $ModelRoot
```

自动测试使用 mock 的部分不会被写成“真机模型通过”。真实运行命令、原始输出、结构化输出和性能数据分别记录在文档与机器可读结果中。

### 最终离线真机功能证据

最终保留工件为 `<artifact-root>\test-real-model-release-20260730\`，状态
`passed`。它在 CPU 上通过公开 `analyze` 路径执行，`model_reused=false`：

| 项目 | 结果 |
|---|---:|
| 模型加载 | 3.6064 s |
| 内层分析 | 57.1824 s |
| 外层 `analyze` | 61.895 s |
| 候选 | 1；保持 `pending` |
| 图片 SHA-256 | `40c808ce56735a027cc990ee2e476a5b2538c538d1b20e0f6c2647182eabe7cf` |
| 第一步原文 | `NET WT 8.0oz (0.501b)` |
| 映射与归一 | `raw_value=8.0oz`；`226.796185 g` |

该工件证明一次离线配置下的真实图功能链成功，**不是准确率评测**。防火墙阻断与
抓包仍未完成，不能把离线环境变量验证扩写成“已证明零外连”。

## 8. 冻结 synthetic Benchmark

commit `06f8360` 已完成 CPU 工程 Benchmark，结果目录为
`<artifact-root>\benchmark-final-06f8360-20260730\`：

| 项目 | 结果 |
|---|---:|
| 数据集 | 30 张 synthetic 图片 + 10 份 synthetic 文档 |
| 图片成功/失败 | 30 / 0；文档错误 `[]` |
| 加载 / 首图 / 冷总计 | 3.701264 / 75.969346 / 79.670610 s |
| 30 图批量 / 逐图中位数 / p90 | 2221.638617 / 75.991379 / 85.350259 s |
| 峰值进程内存 | 11,663,728,640 B（10.861 GiB） |
| 字段 recall / mapping P/R/F1 | 25/25 = 1 / 1/1/1 |
| 数字 / 单位 | 27/27 = 1 / 15/15 = 1 |
| 漏检 / 空样本编造 / 注入接受 | 0/25 / 0/5 / 0/2 |
| 冲突 accuracy/P/R/F1 | 1/1/1/1；TP2 FP0 FN0 TN10 |
| 10 文档无变化增量 | 节省 8.9149%，复用 10/10 |

这些是确定性生成数据上的可复现工程结果，**不等于真实业务准确率**。一张公开真实
商品标签图只用于证明真实图片两步功能成功，不进入上述准确率统计。完整原始计数、
数据集 hash 和测量边界见 [docs/BENCHMARK.md](docs/BENCHMARK.md)。

## 9. 匿名演示

仓库内 `samples\demo` 是合成演示资料，故意包含：

- `320g` 与 `0.32kg`：换算一致；
- 包装旁路样例 `300g`：与文本形成强冲突；
- `2件` 与 `3件`：强冲突；
- `不锈钢` 与 `304不锈钢`：可能兼容，需人工选择。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 analyze `
  ".\samples\demo" `
  --deterministic-only `
  --output ".\demo-output"
```

真实样例图片不进入发布 ZIP；`samples\real\README.md` 只记录允许复现的来源和许可证。

## 限制

- 生成式视觉模型的坐标只能是 `approximate` 或 `unavailable`，不能当作专业 OCR 精确框；
- 模糊、反光、极小字号、遮挡或复杂版式可能漏读；
- 没有传统 OCR 或云端回退；
- 大模型首次加载较慢，CPU 推理时间取决于机器；
- 扫描 PDF 有文件、页数、像素、文件数和文本量上限；模型加载、每文件和最终化
  各有 300 秒服务端心跳边界，整请求客户端上限为 1 小时；
- 这不是 PIM、多人审批系统或自动真值选择器；
- 项目不会自动发布到 ModelScope、文章平台或比赛系统。

更多信息：

- [架构](docs/ARCHITECTURE.md)
- [用户指南](docs/USER_GUIDE.md)
- [模型与运行时](docs/MODEL_AND_RUNTIME.md)
- [Benchmark](docs/BENCHMARK.md)
- [比赛合规](docs/COMPETITION_COMPLIANCE.md)
- [完整限制](docs/LIMITATIONS.md)

当前开发协作位于 [Draft PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1)；
PR 未合并。本地最终回归已通过；最终收尾提交 CI 待推送后记录。
