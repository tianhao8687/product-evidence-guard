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

本地 OpenVINO OCR 先读取清晰图片。普通商品图片中的低置信度、异常单位或冲突
结果会交给 Qwen3-VL 紧凑视觉复核；文字层充分的图表仅观察页不会调用 Qwen
补猜，只在窄字段白名单和确定性安全检查同时通过时生成候选。紧凑复核返回
schema 有效但没有安全候选的空结果时直接停止，只有输出无效或报错才进入原有
两步深度读取。单位换算、冲突判断、确认与失效都由确定性代码完成；
**任何 AI 候选都不会自动成为正式产品事实。**

## 隐私边界

推理使用本地 OpenVINO 和本地模型，不调用云端模型、云端 OCR 或付费 API。联网只用于安装公开依赖和下载公开模型。程序不收集遥测，不修改用户输入，也不把商品资料写入普通运行日志。

模型下载完成后，已经在 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、
`OPENVINO_TELEMETRY_DISABLED=1` 下完成本地推理。该结果证明离线开关路径可用，
另一次独立冷启动以约 100 ms 间隔采样本地客户端、服务及已发现子进程的 TCP 状态；
35 个观察周期没有观察到外部 TCP 连接。该有限采样不是防火墙阻断，也没有捕获
数据包、DNS 或 UDP，不能扩写成“已证明零外连”或 air-gapped。边界与逐次结果见
[冷启动分布与 TCP 采样记录](docs/evidence/performance-network-validation-20260805.md)。

输出报告会包含原文证据，应当按客户资料同等保护。详见 [PRIVACY.md](PRIVACY.md) 和 [SECURITY.md](SECURITY.md)。

## 支持的资料

| 类型 | 处理方式 |
|---|---|
| TXT、Markdown、CSV、JSON | 确定性读取 |
| DOCX、XLSX | 确定性读取文字；另提取文档内图片并保留段落、工作表和锚点 |
| XLSX 原生图表 | 不截图猜数，直接读取系列公式、类别、数值点和图表位置 |
| 带文字层 PDF | 读取文字层；检测到图纸、曲线或大图的混合页会记录视觉上下文，必要时渲染后走本地视觉路线 |
| JPG、JPEG、PNG、WebP、BMP | OpenVINO OCR 快速读取；可疑结果由本地 Qwen3-VL 复核 |
| 无文字层的扫描 PDF | 本机逐页渲染，再走同一混合视觉路径 |
| `*.ocr.json` | 保留的显式兼容输入；不作为隐藏 OCR 回退 |

主视觉模型固定为：

```text
OpenVINO/Qwen3-VL-8B-Instruct-int4-ov
```

快速路径使用 `rapidocr==3.9.1` wheel 内的 PP-OCRv6 small 模型，并强制使用
OpenVINO CPU 引擎。代码显式传入三份本地 ONNX 路径并校验 SHA-256；缺失或校验
失败时不会自动下载，也不会使用 OCR 结果。普通商品事实路由可以安全回到本地
Qwen；`observation-only` 路由则报告 OCR 不可用并失败关闭，不调用 Qwen。

模型许可证为 Apache-2.0，固定下载 revision 为 `f3d0bc7`。顶层依赖固定在
`requirements.txt`，Windows/Python 3.11.13 的 35 个锁定包及下载哈希固定在
`requirements.lock`；该锁文件是正式发布包和 Qoder 安装的必备文件。26 个模型
payload 共 `5,462,515,610` bytes，包含 Hugging Face 元数据的模型目录共
`5,462,526,140` bytes。建议至少 16 GB 内存并预留足够磁盘空间。实际设备、版本
和测试边界见
[docs/MODEL_AND_RUNTIME.md](docs/MODEL_AND_RUNTIME.md)。

发布阶段的 26 文件 SHA-256 清单位于
`<project-root>\release\model-sha256-manifest.json`。它提供逐文件校验，但尚未
签名。发布 ZIP 的源码身份以 ZIP 内 `manifest.json` 的 `commit` 字段为准；
manifest 同时列出每个 HEAD 来源文件的路径、模式、大小与 Git object。ZIP 整体
以同目录 `.sha256` 文件为准；`manifest.json` 不对自身或 ZIP 做自引用哈希。

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

它不会把依赖安装到系统 Python。2026-07-31 已在 Windows PowerShell 5.1 用
`-Force` 完成一次干净重建：固定 Python `3.11.13`，35 个运行/构建包全部要求
哈希，editable build 使用 `--no-build-isolation --no-index`；环境盘点为 36 个
已安装包（含本项目），`pip check` 报告兼容。随后再次执行命中安装 stamp 并快速
跳过。当前已生成 CycloneDX 1.5 SBOM 和 35 个锁定包的许可证元数据，并从实际
`pypdfium2 5.12.1` wheel 收集 19 份 PDFium/依赖 notices；这些工件不等于漏洞
扫描，也不等于对所有二进制传递依赖的完整许可证审计。

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
├── document-visuals.json
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

`document-visuals.json` 单独保存 DOCX/XLSX 内嵌图片、XLSX 原生图表和 PDF
混合视觉页的来源、哈希、定位、路由及处理状态。原生 XLSX 图表可直接保存
series、类别和值；视觉路线的详细 OCR 行级观察仍在 `visual-transcription.json`
中。OCR observations 只是识别观察，不是已接受事实，也不是曲线数据点。

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

如果当前项目已经在 D 盘完成环境和模型安装，推荐显式复用它，避免 Qoder 的轻量
Skill 副本再次创建环境或下载 5.46 GB 模型：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-qoder-skill.ps1 `
  -Scope User `
  -RuntimeRoot "<prepared-project-root>" `
  -Update
```

`-RuntimeRoot` 只在安装副本的 `.runtime\source-root.txt` 中保存本机路径；该文件
不会进入发布包。Qoder 仍然只调用安装副本的 `scripts\run.ps1`，由这个唯一入口
转交给已准备好的本地运行时。

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
`Enabled`。2026-08-04 已完成 CLI 登录；中文自然语言自动路由和
`/local-product-evidence-guard` 手动触发均通过安装副本的 `scripts\run.ps1`
成功执行无数据 `status`，退出码为 `0`。用户授权匿名 demo 并亲自选择 candidate
后，Qoder 还完成了分析、冲突解释、确认、拒绝、导出、增量重分析和 stale 闭环。
2026-08-05 又完成英文自然语言自动路由，以及一张公开领域真实标签图的冷启动、
常驻热模型和未变化文件缓存验证；本地分析分别为 26.7585、11.9164 和 0.0177 秒，
真实图候选保持 `pending`。脱敏 Qoder IDE 结果截图已保存。Windows 公共入口的真实
tiny Hub 部分下载、后台继续、退出码 `3`、`status` 和第二次 `--continue` 已通过；
完整截图组仍单独列为缺口。
可以说：

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

2026-08-05 最终 D 盘正式目录回归为 **246 项通过（85.123 s，0 跳过）**。Windows PowerShell
`tests\test.ps1` 随后输出 `status=passed`：`unit_tests`、中文空格路径、
`compileall`、确定性分析、无变化增量复用，以及经唯一入口完成的
`confirm → reject → export → 修改来源 → reanalyze → stale` 均通过；JSON、
Markdown、HTML、审计 JSONL、Named Pipe `status/shutdown` 也通过。无效路径与
重新确认旧候选的退出码均为 `1`。该闭环使用仓库 OCR sidecar 彩排数据，不等同于
真实 Qwen 或 Qoder 调用。

另一个隔离公开入口 E2E 使用精确活跃 PID 标记模拟“服务身份有效、Named Pipe
不可达、状态为 error”，确认稳定返回 `server_unreachable` 和退出码 `2`，并在
测试后恢复 runtime 快照；真实 tiny Hub 续传路径已验证退出码 `3`。因此统一
`0/1/2/3` 合同均有公共入口证据。

这些是本地最终回归结果。GitHub CI 在 commit `5a4fad8` 曾临时绿色；最新远端 CI
状态以 [Draft PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1)
的 GitHub Actions 为准，本文不预先声称其通过。

### 真实文档测试

4 份官方公开真实文档（PDF、DOCX、2 份 XLSX）已通过原生文档路径测试，共解析
1,491 个文本块、359,063 个字符且 0 运行错误。初轮测试发现旧规则产生 348 条候选，
其中包含大量明显误报，并形成 11 个阻断冲突；修复后只保留 Raspberry Pi 规格页
中的 3 条输入/输出电气候选，0 个阻断冲突，而且全部保持 `pending`。

保留的一组缓存未命中运行耗时 0.6907 s；同一批文件第二次增量复用为 0.0272 s。
另一组正式重复值分别为 0.9879 s 和 0.0125 s。详细来源、SHA-256、修复前后对照、
候选内容和限制见
[真实 PDF、DOCX、XLSX 文档评测](docs/REAL_DOCUMENT_EVALUATION.md)。

另用 Raspberry Pi 5 官方机械图纸和 TI TPS65301-Q1 官方数据手册完成了带文字层
图纸、矢量曲线和嵌入示波器图的混合 PDF 测试。3 个选定页面全部识别为 mixed
visual page：2 页直接保留文字层上下文并避免模型调用；TI 曲线页先做保守 ROI，
页面面积减少 29.84%，再保留 32 行文字层和 40 条 OCR observations。该页以
`ocr_observations` 结束，不把曲线或示波器观察冒充产品事实，也不再为“0 个安全
候选”重复调用 Qwen。三份真实 mixed PDF 在同一常驻进程中的外层耗时为
1.3537 秒、engine 分析 1.2484 秒，0 错误。

受控 DOCX/XLSX/PDF 图文样本最终生成 21 条 `pending` 候选、4 个字段组、0 个
阻断冲突。`Eco 30 W / Balanced 45 W / Turbo 65 W` 与额定 `65 W` 分属不同
semantic scope；同一次 `analyze` 内，同一 PNG 跨两个 Office 文档只识别一次，
但每份证据仍有独立
文件 hash、locator 和候选 ID。最终单次 CPU 基准为：

| 阶段 | 外层耗时 | engine 分析 | 模型加载 |
|---|---:|---:|---:|
| 冷模型 + 冷业务缓存 | 11.1997 s | 2.0912 s | 8.5615 s |
| 常驻模型 + 冷业务缓存 | 1.7050 s | 1.5536 s | 0 s |
| 常驻模型 + 业务缓存命中 | 0.2092 s | 0.0353 s | 0 s |

常驻完整重算比冷启动减少 84.78% 耗时，冷启动耗时是热重算的 6.57 倍。三个
受控视觉结果都走 `ocr_fast`，因此差额主要来自模型/pipeline 的首次构建，不是
Qwen 生成速度对比；业务缓存命中也不能宣传成模型推理速度。计时完成后又补了
观察模式窄字段白名单、低分/同一 OCR 行多值失败关闭、有效空复核不重复 deep、
迟到结果拒绝、reader capability 缓存签名和偏移 CropBox 整页回退；按“不再
重启模型”的测试约定没有重跑这组单次计时，成功 OCR 路由未变，新增边界由无模型
回归验证。机器可读摘要见
[`docs/evidence/document-visual-acceleration-final.json`](docs/evidence/document-visual-acceleration-final.json)。
这里的“冷”是同一 Python 进程中新建 `ResidentModelCache` 并使用新输出目录，
不是重启 Windows 或清空操作系统文件缓存。

另对同一张已授权公开领域标签图完成 3 次“安全关闭服务后重新启动”的独立冷启动：
分析耗时分别为 26.9529、27.6312、25.5298 秒，均值 **26.7046 秒**，范围
25.5298–27.6312 秒；模型加载均值 **4.4680 秒**。这是单机、单样本三次分布，
不代表所有图片或机器。逐次哈希和统计见
[`docs/evidence/performance-network-validation-20260805.md`](docs/evidence/performance-network-validation-20260805.md)。

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

该工件证明一次离线配置下的真实图功能链成功，**不是准确率评测**。后续 35 周期
TCP 状态采样没有观察到外部 TCP 连接，但防火墙阻断和数据包/DNS/UDP 抓取仍未
完成，不能把该观察或离线环境变量验证扩写成“已证明零外连”。

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
商品标签图和 4 份真实公开文档只用于证明对应功能与精确性压力结果，不进入上述
准确率统计。完整原始计数、数据集 hash 和测量边界见
[docs/BENCHMARK.md](docs/BENCHMARK.md)。

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

真实样例图片和文档不进入发布 ZIP；`samples\real\README.md` 只记录允许复现的
来源、校验值和使用边界。

## 限制

- Qwen 坐标只能是 `approximate` 或 `unavailable`；RapidOCR 检测框也保守标为
  `approximate`，不能当作人工校准坐标；
- 模糊、反光、极小字号、遮挡或复杂版式可能漏读；
- OCR 置信度不是正确率；普通商品图片的快速路径发现异常单位、低分或冲突时会
  转交本地 Qwen，没有云端回退；
- 文字层充分的 mixed PDF 可按 `ocr_observations` 保留图表上下文并避免 Qwen；
  这些 observation 不是 FactCandidate。只有高置信、单值、可确定映射且标签属于
  重量、尺寸、数量、型号、材质或颜色白名单的产品属性，才可取消仅观察选择并进入
  冲突审查；电压、电流、功率、频率、容量、低置信、未知单位、同一 OCR 行多值或
  输入/输出混写都只保留观察，不调用 Qwen，也不生成候选；
- 大模型首次加载较慢，CPU 推理时间取决于机器；
- 扫描 PDF 有文件、页数、像素、文件数和文本量上限；模型加载、每文件和最终化
  各有 300 秒服务端心跳边界，整请求客户端上限为 1 小时；
- PDF 曲线图当前可保留文字层、OCR 行、轴标签和图页定位，但不会把曲线完整
  数字化为每个坐标点；OCR observations 和图表上下文均不能直接当作事实；
- 这不是 PIM、多人审批系统或自动真值选择器；
- 项目不会自动发布到 ModelScope、文章平台或比赛系统。

更多信息：

- [架构](docs/ARCHITECTURE.md)
- [用户指南](docs/USER_GUIDE.md)
- [模型与运行时](docs/MODEL_AND_RUNTIME.md)
- [OCR 加速实测与安全路由](docs/OCR_ACCELERATION.md)
- [Benchmark](docs/BENCHMARK.md)
- [真实 PDF、DOCX、XLSX 文档评测](docs/REAL_DOCUMENT_EVALUATION.md)
- [真实公开样本与用户体验评测](docs/REAL_SAMPLE_EVALUATION.md)
- [比赛合规](docs/COMPETITION_COMPLIANCE.md)
- [完整限制](docs/LIMITATIONS.md)

当前开发协作位于 [Draft PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1)；
PR 未合并。本地最终回归已通过；最新远端 CI 状态以 PR Actions 为准。
