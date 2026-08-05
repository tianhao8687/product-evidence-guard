# Benchmark 方案与结果

最后复核：2026-08-05

## 当前结论

**冻结 commit `06f8360` 的完整 synthetic CPU 工程 Benchmark 已完成。**

最终发布候选的离线真实单图功能工件位于
`<artifact-root>\test-real-model-release-20260730\`：状态 `passed`，实际设备
CPU，`model_reused=false`，模型加载 3.6064 s、内层分析 57.1824 s、外层
`analyze` 61.895 s，生成 1 个保持 `pending` 的候选。它是单次功能证据，**不是
准确率结果**，也不替代下方冻结 synthetic Benchmark。

结果目录为 `<artifact-root>\benchmark-final-06f8360-20260730\`。冻结数据集包含
30 张合成图片和 10 份合成文档；30 张图片全部成功，失败数为 0，
`document_errors=[]`，最终状态为 `completed`。数据集运行前后 SHA-256 均为
`c96e95f2817b1c8f16a99952022e03f541d3a5fe89ee6db0f1d85ea01c76dcf3`。

本次已取得 CPU 加载、首图、热图、批量、逐图分布、峰值进程内存、字段 recall、
mapping precision/recall/F1、数字与单位正确率、参数漏检、空样本编造、提示词
注入接受数、冲突分类和无变化增量复用结果。Intel GPU 不适用于本机；防火墙/抓包
网络审计仍未执行。后续另完成 3 次独立冷启动分布和一次 35 周期本地进程 TCP 状态
采样；有限采样没有观察到外部 TCP，但不是数据包、DNS、UDP 或 air-gap 证明。

**所有准确率与质量指标都来自项目确定性生成的 synthetic 数据，只能证明这套固定
工程样本在当前代码与模型下可复现通过，不等于真实客户资料或真实业务准确率。**
一张公开真实商品标签图只证明真实图片功能链成功，不进入准确率统计。

2026-07-31 又完成 1 份真实 PDF、1 份真实 DOCX 和 2 份真实 XLSX 的原生文档
测试。4 份文件全部解析成功；旧规则产生 348 条候选，其中包含大量明显误报，并
形成 11 个阻断冲突；修复后为 3 条 `pending` 电气候选、0 个阻断冲突。保留的
缓存未命中/增量运行分别为 0.6907 s/0.0272 s。该轮没有人工逐字段标注，属于
功能、精确性压力与速度测试，不进入真实准确率。完整来源、哈希、原始证据位置见
[真实 PDF、DOCX、XLSX 文档评测](REAL_DOCUMENT_EVALUATION.md)。

同日又用 Raspberry Pi 5 官方机械图纸和 TI TPS65301-Q1 官方数据手册选定页完成
混合 PDF 视觉功能测试。完整官方 PDF SHA-256 分别为
`5dd680d6c1f5e7aa9c7b020695e315d04aac862df82ff01d4e9041cd0668d7f2`
和
`21d046b6d56a969cba807b51b48a90eac09d4a3934dc25befb296faf38aff512`。
3 个模型输入页均检测为 mixed page；2 页由文字层结构化上下文直接处理并避免模型
调用。TI 第 24 页用 PDFium ROI 减少 29.84% 页面面积，保留 32 行文字层和
40 条 OCR observations，并以 `ocr_observations` 结束；最终候选为 0、Qwen 调用
为 0。这是图纸、曲线和嵌入图片的功能证据，不是曲线理解准确率。

`scripts/benchmark.py` 已在冻结 commit 上真实执行，并完成：

- 确定性生成 30 张合成图片和 10 份文档；
- 生成并核对数据集哈希；
- 拒绝明显的 mock/fake 模型路径；
- 让每张图片通过一次 `QwenVlReader`；
- 记录加载、首图、热图、批量、逐图分布和峰值进程内存；
- 汇总字段、mapping、数字、单位、漏检、编造、注入和冲突指标；
- 对 10 份文档执行无变化增量复用检查；
- 保存原始模型输出、逐样本状态和机器可读 aggregate。

## Benchmark 要回答的问题

1. 精确 8B OpenVINO 模型能否在目标机器加载？
2. 哪个 OpenVINO 设备实际执行推理？
3. 模型加载、首图、热图和完整文件夹各需多久？
4. 峰值进程内存和可观测设备内存是多少？
5. 可见数字与单位的转录准确率是多少？
6. 已接受原文映射到字段白名单的准确率是多少？
7. 模型会漏掉多少参数，又会编造多少不存在的参数？
8. 确定性代码对单位等价和冲突等级的判断是否正确？
9. 文件级增量复用节省多少时间？
10. 依赖和模型已缓存后，阻断联网能否完成同一张真实图片？

## 已记录的测试环境

未留证的环境信息不根据硬件名称推断。

| 项目 | 结果 |
|---|---|
| 测试日期/时区 | 2026-07-30 / Asia/Shanghai |
| Benchmark 冻结 commit | `06f8360` |
| 发布源码身份 | 由发布 ZIP 内 `manifest.json.commit` 记录；ZIP 由同目录 `.sha256` 校验 |
| OS 与 build | Windows 11 专业版 10.0.26200，build 26200 |
| CPU | AMD Ryzen 7 7800X3D |
| 物理/逻辑核心 | 8 / 16 |
| 安装 RAM | 33,409,253,376 bytes，约 31.11 GiB |
| Intel GPU 与驱动 | N/A；本机没有适用 Intel GPU |
| NPU 与驱动 | N/A；本次 CPU Benchmark 不声明 NPU |
| NVIDIA GPU | NVIDIA GeForce RTX 5070 (dGPU)；不得当作已验证的 OpenVINO Intel GPU |
| Python | `3.11.13` |
| OpenVINO | `2026.2.1` |
| OpenVINO GenAI | `2026.2.1.0` |
| OpenVINO Tokenizers | `2026.2.1.0` |
| pypdfium2/PDFium | pypdfium2 `5.12.1`；实际 Windows x64 wheel 的 19 份 PDFium/依赖 notices 已收集并索引 |
| Pillow / huggingface-hub / NumPy | `11.3.0` / `0.34.4` / `2.2.6` |
| `Core().available_devices` | `CPU`、`GPU` |
| `GPU` 设备全名 | NVIDIA GeForce RTX 5070 (dGPU) |
| 请求设备 | `CPU`；AUTO 策略另有代码与单元测试 |
| 实际设备 | `CPU` |
| 电源模式/是否接电 | 高性能，GUID `8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`；是否接电未留证 |

设备发现命令：

```powershell
.\.venv\Scripts\python.exe -c "from openvino import Core; print(Core().available_devices)"
```

依赖版本命令：

```powershell
.\.venv\Scripts\python.exe -m pip show `
  openvino openvino-genai openvino-tokenizers pypdfium2
```

设备清单只说明“可见”，不能单独证明实际推理设备。公开入口默认 `AUTO`，只在
`FULL_DEVICE_NAME` 含 `Intel` 时选 GPU，否则选 CPU；显式设备先校验，NPU 会
拒绝。本次真实工件记录请求与实际设备均为 CPU，因此只验证 `CPU`。

## 模型身份

| 项目 | 结果 |
|---|---|
| Model ID | `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` |
| 上游 revision | `f3d0bc7` |
| 本地目录 | `<project-root>\.models\Qwen3-VL-8B-Instruct-int4-ov` |
| 配置的运行文件数 | `26` |
| 正式快照可加载 | 已验证 |
| 中断/partial 续传 | 不属于本次 synthetic 推理 Benchmark；产品验证状态另见模型文档 |
| 26 个必需 payload | `5,462,515,610` bytes |
| 含 Hugging Face 元数据目录 | `5,462,526,140` bytes |
| 本地模型 SHA-256 manifest | `<project-root>\release\model-sha256-manifest.json`；26 文件，尚未签名 |
| 权重压缩 | `INT4_SYM`，ratio `1.0`，group size `128` |
| 官方仓库页面大小 | 5.46 GB；仅作上游元数据 |

## 数据集合同

### 最小目标组成

| 类别 | 最少数量 | 用途 |
|---|---:|---|
| 清晰参数图 | 10 | 基线转录与映射 |
| 倾斜、反光、压缩或低清图 | 10 | 鲁棒性 |
| 中英文混合图 | 5 | 多语言转录 |
| 空白、无参数或含提示词注入文字的图 | 5 | 空结果与编造控制 |
| TXT/CSV/XLSX/PDF 等确定性资料 | 10 | Hybrid 文件夹与冲突评测 |

冻结数据集已按上表实际生成：30 张 synthetic 图片和 10 份 synthetic 文档。

### 分区

```text
samples/
├── generated-benchmark/     # synthetic，可安全重新生成
└── real/                    # gitignored，必须授权、匿名，禁止提交原件
```

每个样本需要 manifest：

```json
{
  "sample_id": "synthetic-clear-001",
  "partition": "synthetic",
  "relative_path": "clear/001.png",
  "sha256": "<actual hash>",
  "category": "clear",
  "language": ["zh-CN"],
  "degradation": [],
  "expected_transcriptions": [
    {
      "raw_text": "净重 320g",
      "numeric_tokens": ["320"],
      "unit_tokens": ["g"]
    }
  ],
  "expected_mappings": [
    {
      "field": "net_weight",
      "raw_value": "320g"
    }
  ],
  "expected_empty": false,
  "contains_prompt_injection_text": false,
  "source_license_or_authority": "synthetic-generated-for-project"
}
```

Ground truth 必须独立于模型输出审核。不能为了提高分数而修改标签；排除项及理由
必须保留。

### 真实样本隐私

- 先取得授权；
- 非必要时移除品牌、人名、地址、电话、订单和账号信息；
- 使用不透明 sample ID；
- 原件留在 Git 之外；
- 只有在许可范围内发布脱敏裁剪；
- 授权说明保存在私有 Benchmark 记录中。

本次单图来自 FDA/Wikimedia Commons 公有领域商品标签，文件为
`wegmans-octopus-salad-label.jpg`，尺寸 701×1097，SHA-256：

```text
40C808CE56735A027CC990EE2E476A5B2538C538D1B20E0F6C2647182EABE7CF
```

## 可复现命令

只生成当前合成数据：

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark.py `
  --dataset ".\samples\generated-benchmark" `
  --generate-only
```

运行冻结模型框架：

```powershell
$ProjectRoot = (Resolve-Path ".").Path
$ModelRoot = Join-Path $ProjectRoot ".models\Qwen3-VL-8B-Instruct-int4-ov"

.\.venv\Scripts\python.exe .\scripts\benchmark.py `
  --dataset ".\samples\generated-benchmark" `
  --model $ModelRoot `
  --device CPU `
  --output "<artifact-root>\benchmark-final-06f8360-20260730"
```

必须从 commit `06f8360` 的代码和同一数据集 seed 复现，并核对 dataset hash 与
aggregate。不能把另一个 commit 的结果混入本表。

## 冻结运行协议

1. 记录系统、依赖、模型、revision、设备和数据集 manifest。
2. 确定性生成 30 张图片与 10 份文档，记录运行前数据集 SHA-256。
3. 在 CPU 上构造一次精确模型 pipeline，分开记录加载与首图时间。
4. 按固定顺序处理全部 30 张图片，逐样本保存耗时、原始输出和 Schema 诊断。
5. 汇总逐图中位数、p90、批量总时长和峰值进程内存。
6. 解析 10 份文档，不修改来源重跑，并记录复用数与节省时间。
7. 从逐样本结果计算质量原始计数和比率。
8. 再次计算数据集 SHA-256，必须与运行前一致。

本次没有把防火墙/抓包网络审计混入 Benchmark；离线环境变量验证单独记录。

### 时间边界

| 指标 | 开始 | 结束 |
|---|---|---|
| 模型加载 | 构造 `VLMPipeline` 前 | 构造返回或失败 |
| 首图 | 第一次图片生成调用前 | 第二阶段结果通过校验或终止失败 |
| 热图 | 常驻模型的后续生成调用前 | 第二阶段结果通过校验或终止失败 |
| 冷总计 | 构造 `VLMPipeline` 前 | 首图两阶段结果通过校验 |
| 30 图批量 | 第一张图片开始前 | 第 30 张结果写入后 |
| 无变化增量 | 相同输入重跑前 | 最终响应和报告落盘 |

使用单调高精度时钟，保存逐样本数据，不能只保存平均数。

### 内存边界

记录：

- server 进程峰值 Resident Set/Working Set；
- 可用时记录起始和峰值 Commit/Private Bytes；
- 加载前后物理内存；
- 只有使用可靠专用工具时才记录 Intel GPU 内存；
- 采样工具、间隔和盲区。

无法采集设备内存时写 `N/A — collection unavailable`，不能写 `0`。

## 指标定义

### 数字转录正确率

```text
被逐字正确转录的 ground-truth 数字 token
-----------------------------------------
全部 ground-truth 数字 token
```

只允许预先声明的无害排版归一，例如全角数字；不能用单位换算掩盖转录错误。

### 单位识别正确率

```text
与正确数值关联的正确单位 token
------------------------------
全部 ground-truth 单位 token
```

转录指标评价“读到了什么”；确定性单位归一另行评价。

### 字段映射 precision、recall 和 F1

把 `(sample_id, transcription_id, field, raw_value)` 当作一个 mapping item：

```text
precision = true-positive mappings / all predicted mappings
recall    = true-positive mappings / all ground-truth mappings
F1        = 2 × precision × recall / (precision + recall)
```

precision 和 recall 都为 0 时，F1 定义为 0，并保留原始计数。

### 参数漏检率

```text
没有对应已接受预测的 ground-truth mapping
-----------------------------------------
全部 ground-truth mapping
```

### 编造率

```text
没有可见/来源 ground truth 支持的已接受 mapping
---------------------------------------------
全部已接受 mapping
```

空白或无参数图片还要报告样本级假阳性率。

### 冲突分类正确率

由确定性归一后的 ground-truth 分组计算：

```text
分类正确的分组 / 全部有标签分组
```

同时发布 `exact_match`、`converted_match`、`compatible_expression`、
`insufficient_evidence`、`likely_version_update` 和 `strong_conflict` 的混淆
计数。不能把确定性图引擎的正确率算成模型准确率。

### 增量节省

```text
(完整重跑中位数 - 增量重跑中位数)
---------------------------------
完整重跑中位数
```

无变化和单文件变化分别报告；负值也是有效结果，不能隐藏。

## 原始工件布局

当前 runner 写入：

```text
<output>/
├── benchmark-results.json
├── per-sample-results.json
├── expected-manifest.snapshot.json
└── raw-model-output/
```

冻结结果目录包含机器可读汇总、逐样本结果、CSV、manifest 快照和原始模型输出：

```text
<artifact-root>/benchmark-final-06f8360-20260730/
├── benchmark-results.json
├── per-sample-results.json
├── per-sample-metrics.csv
├── expected-manifest.snapshot.json
├── raw-model-output/
└── schema-rescored-output/
```

真实数据的原始输出可能包含敏感文字，应留在 Git 外，只发布脱敏汇总。

## 已完成的真实文档实测

| 指标 | 结果 |
|---|---:|
| 官方公开文件 | PDF 1、DOCX 1、XLSX 2 |
| 解析块 / 字符 | 1,491 / 359,063 |
| 运行错误 | 0 |
| 旧规则候选 / 阻断冲突 | 348 / 11 |
| 修复后候选 / 阻断冲突 | 3 / 0 |
| 修复后候选状态 | 3 条均为 `pending` |
| 缓存未命中 / 增量复用 | 0.6907 s / 0.0272 s |

本轮使用 `--deterministic-only` 隔离原生文档路径；PDF 有文字层，不使用 OCR 或
Qwen。两份大型 XLSX 和一份叙述性 DOCX 主要用于误报压力，不是逐行标注的字段
recall 数据集。

### 官方混合 PDF 视觉补充

这组测试使用 1 页 Raspberry Pi 5 工程图、TI 数据手册第 8 页和第 24 页。它在
受控文档冷/热阶段之后继续复用同一模型 pipeline：

| 指标 | 结果 |
|---|---:|
| 外层 / engine 分析 | 1.3537 / 1.2484 s |
| 模型加载 / `model_reused` | 0 s / `true` |
| mixed 页 | 3 |
| 文字层直接处理 / OCR observations | 2 / 1 |
| Qwen 调用避免 | 1 |
| ROI 页面 / 平均面积减少 | 1 / 29.84% |
| 候选 / 错误 | 0 / 0 |

同一轮受控 DOCX/XLSX/PDF 的冷模型、常驻完整重算、业务缓存命中外层耗时为
11.1997 / 1.7050 / 0.2092 秒，严格分开了模型常驻与业务缓存。这些均为单次
CPU 工程测量，不是延迟分布。三个视觉结果都走 `ocr_fast`，所以冷/热差额主要
是模型/pipeline 首次构建成本，不是 Qwen 生成速度。计时后又补了
`observation-only` 窄白名单、低置信度/同一 OCR 行多值失败关闭、schema 有效空
复核不重复 deep，以及迟到结果拒绝、reader capability 签名、严格空间分组、
非零 CropBox 全页回退和坐标空间标签。遵守“不再重启模型”的约定没有重跑；
新增边界由无模型回归验证，因此这些数字仍是原单次测量，不是新模型进程的性能
结论。
这里的“冷”是同一 Python 进程中新建 `ResidentModelCache` 和新输出目录，
不是重启 Windows 或清空操作系统文件缓存。

29.84% 只是该页 PDF 几何裁剪面积的减少；本轮没有保存相同最终实现下的重复
全页延迟/质量对照，因此不把它宣传为 29.84% 的速度或准确率提升。

复现时使用同一个 Python 进程和同一个 `ResidentModelCache`，只在第一阶段加载
模型：

```powershell
.\.venv\Scripts\python.exe .\scripts\benchmark-document-visuals.py `
  --input "<受控 DOCX-XLSX-PDF 目录>" `
  --real-input "<真实 mixed PDF 单页目录>" `
  --model ".\.models\Qwen3-VL-8B-Instruct-int4-ov" `
  --output ".\.runtime\document-visual-benchmark" `
  --device AUTO
```

输出目录必须为空，避免旧 `analysis-state.json` 污染冷/热口径。

`document-visuals.json` 保存页级检测原因、文字层上下文、实际视觉路由和
`ocr_observation_count`；完整逐行 OCR observations 位于
`visual-transcription.json`。这些观察可能包括曲线刻度、轴标题和图例，但不是
已接受事实，也不表示已把 PDF 曲线完整数字化为数值点。所有可用事实候选仍必须
人工确认。

机器可读脱敏摘要已随源码放在
[`docs/evidence/document-visual-acceleration-final.json`](evidence/document-visual-acceleration-final.json)；
第三方样本本体不进入仓库或发布包。

## 已完成的单图实测

成功工件位于本机：

```text
<artifact-root>\test-real-model-release-20260730\
<artifact-root>\real-image-output-20260730-r3\
<artifact-root>\public-worker2-cold-20260730\
<artifact-root>\public-worker-cold-20260730\
```

本地绝对路径只用于当前验证记录，公开文章或截图应脱敏。

| 指标 | CPU 单图结果 | 解释 |
|---|---:|---|
| 26 个模型 payload | 5,462,515,610 bytes | revision `f3d0bc7`；含元数据目录为 5,462,526,140 bytes |
| 最终工件状态 | `passed` | `<artifact-root>\test-real-model-release-20260730\` |
| 最终模型加载 | 3.6064 s | `model_reused=false` |
| 最终内层分析 | 57.1824 s | 两步视觉处理与确定性候选生成 |
| 最终外层 `analyze` | 61.895 s | 公开测试脚本外层计时 |
| 最终候选 | 1，`pending` | 单一来源，没有自动确认 |
| 初始主引擎加载 | 4.1104 s | 早期单图 smoke |
| 初始主引擎单图 | 54.6552 s | 视觉抄录 + 字段映射 |
| 初始主引擎总计 | 58.7705 s | 含加载和报告 |
| 公开入口冷加载 | 3.7021 s | `model_reused=false` |
| 公开入口冷单图 | 52.1827 s | 常驻 worker 首个请求 |
| 同 worker 热加载 | 0 s | `model_reused=true` |
| 同 worker 热单图 | 30.8367 s | 单次热值，不是分布 |
| 峰值 Working Set | 11,626,774,528 bytes，约 10.83 GiB | 另一成功监测轮 |
| 峰值 Private Bytes | 7,469,481,984 bytes，约 6.96 GiB | 同一监测轮 |
| 设备显存 | N/A | 本次使用 CPU |
| 冻结 synthetic 热图中位数 | 76.013413 s | 30 图批量中的热调用 |
| 冻结 synthetic 逐图 p90 | 85.350259 s | 只适用于固定合成数据 |

第一步模型原文：

```text
NET WT 8.0oz (0.501b)
```

通过 Schema 的关键字段：

```json
{
  "field": "net_weight",
  "raw_value": "8.0oz",
  "normalized_value": 226.796185,
  "normalized_unit": "g",
  "bbox_1000": [183, 540, 707, 570],
  "position_precision": "approximate",
  "recognition_confidence": 0.85,
  "mapping_confidence": 0.91,
  "confidence_source": "model_self_assessment",
  "status": "pending"
}
```

报告把单一来源归为 `insufficient_evidence`，没有自动确认。注意：
`NET WT 8.0oz (0.501b)` 是模型原样输出，不能在报告中悄悄改成看起来更合理的
文字；自评 `0.85`/`0.91` 也不是准确率。

## 失败记录

失败不能被后续成功覆盖：

| 运行 | 阶段 | 设备 | 实际错误 | 处理 |
|---|---|---|---|---|
| `real-image-output-20260730` | 视觉转录 | CPU | `max_new_tokens=900` 时 JSON 被截断；提取到错误数组片段并触发 `item_not_object` | Schema 拒绝全部候选，候选数 0；保留原始输出 |
| `real-image-output-20260730-r2` | 单位解析 | CPU | mapping 返回 `NET WT 8.0oz`，包含前缀，导致 `unparsed_unit` | 保留结果；后续提示约束为原文中的值片段 |
| `real-image-output-20260730-r3` | 成功 | CPU | 无运行错误 | 保留原始输出、候选和耗时 |
| `test-real-model-release-20260730` | 最终发布候选功能验证 | CPU | 无运行错误，`status=passed` | 1 个 pending 候选；单次功能证据 |

## 冻结 synthetic Benchmark 结果

- 运行 ID：`benchmark-final-06f8360-20260730`
- 冻结模型代码 commit：`06f8360`
- 状态：`completed`

| 完整性项目 | 结果 |
|---|---|
| Synthetic 图片 | 30 |
| Synthetic 文档 | 10 |
| 图片成功/失败 | 30 / 0 |
| 文档错误 | `[]` |
| 运行前 dataset SHA-256 | `c96e95f2817b1c8f16a99952022e03f541d3a5fe89ee6db0f1d85ea01c76dcf3` |
| 运行后 dataset SHA-256 | `c96e95f2817b1c8f16a99952022e03f541d3a5fe89ee6db0f1d85ea01c76dcf3` |
| 数据集是否稳定 | 是 |

### 运行性能

| 指标 | CPU 结果 | 说明 |
|---|---:|---|
| 模型目录大小 | 5,462,526,140 B | revision `f3d0bc7`；26 payload 为 5,462,515,610 B |
| 模型加载 | 3.701264 s | 冻结运行的一次 pipeline 构造 |
| 首图 | 75.969346 s | 两步视觉调用 |
| 冷总计 | 79.670610 s | 加载 + 首图 |
| 热图中位数 | 76.013413 s | 冻结批量热调用 |
| 30 图批量总时长 | 2221.638617 s | 成功 30，失败 0 |
| 全部图片逐图中位数 | 75.991379 s | 固定 synthetic 数据 |
| 全部图片逐图 p90 | 85.350259 s | 固定 synthetic 数据 |
| 峰值进程内存 | 11,663,728,640 B（10.861 GiB） | Working Set |
| 峰值 GPU 内存 | N/A | 本次使用 CPU |

Intel GPU 和 NPU 不列入结果，因为本机没有适用 Intel GPU，且没有该精确模型的
NPU 真机证据。

### 质量

| 指标 | Synthetic 结果 | 原始计数 |
|---|---:|---:|
| 字段 recall | 1.0 | 25 / 25 |
| 字段映射 precision | 1.0 | aggregate 与逐样本记录 |
| 字段映射 recall | 1.0 | aggregate 与逐样本记录 |
| 字段映射 F1 | 1.0 | aggregate 与逐样本记录 |
| 数字转录正确率 | 1.0 | 27 / 27 |
| 单位识别正确率 | 1.0 | 15 / 15 |
| 参数漏检率 | 0.0 | 0 / 25 |
| 空样本编造率 | 0.0 | 0 / 5 |
| 注入样本接受事实数 | 0 | 0 / 2 |
| 冲突分类 accuracy | 1.0 | TP 2、FP 0、FN 0、TN 10；universe 12 |
| 冲突分类 precision | 1.0 | TP 2、FP 0 |
| 冲突分类 recall | 1.0 | TP 2、FN 0 |
| 冲突分类 F1 | 1.0 | TP 2、FP 0、FN 0 |

真实授权数据没有进入本次质量统计。上表不能写成“真实商品准确率 100%”。

### 增量行为

| 情况 | 首次 | 无变化重跑 | 节省 | 复用 |
|---|---:|---:|---:|---:|
| 10 份 synthetic 文档 | 0.0589473 s | 0.0536922 s | 0.0052551 s（8.9149%） | 10 / 10 |

### 离线验证

| 项目 | 结果 |
|---|---|
| 离线环境变量 | `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`OPENVINO_TELEMETRY_DISABLED=1` |
| 依赖和模型已缓存 | 是 |
| 真实图片分析 | 已完成 |
| 分析结果 | 成功；本地候选保持 `pending` |
| 防火墙阻断或抓包 | 未完成；不在本次证据内 |
| 后续 TCP 状态采样 | 35 个约 100 ms 周期没有观察到外部 TCP 连接 |
| TCP 采样边界 | 不是防火墙/数据包/DNS/UDP 抓取，不能声称零外连或 air-gapped |
| 原始证据路径 | `<artifact-root>\test-real-model-release-20260730\` |

### 独立冷启动补充

同一张已授权公开领域标签图在每次安全关闭本地服务后重新运行 3 次：

| 次数 | 分析耗时 | 模型加载 | 外层耗时 |
|---|---:|---:|---:|
| 1 | 26.9529 s | 4.8985 s | 32.8509 s |
| 2 | 27.6312 s | 4.2162 s | 32.8441 s |
| 3 | 25.5298 s | 4.2892 s | 30.8354 s |

分析耗时均值 **26.7046 s**，范围 **25.5298–27.6312 s**；模型加载均值
**4.4680 s**。这些值只代表单机、单样本三次分布。逐次哈希、采样方法和边界见
[`evidence/performance-network-validation-20260805.md`](evidence/performance-network-validation-20260805.md)。

## 报告规则

- 率必须同时给出原始计数。
- 报告中位数和分布，不能只挑最快一次。
- CPU 与适用 Intel GPU 分开。
- Synthetic 与真实授权分区分开。
- 跳过、超时和失败样本都要计入。
- 提示词注入和空白样本失败不能从分母删除。
- 模型自评 confidence 不能叫“准确率”。
- 官方兼容性不能变成目标机性能。
- 单张公开标签图不能扩写成生产质量保证。
- 每个汇总结果必须链接原始工件和 Git revision。

当前对外准确表述是：

> 最终离线真实单图工件状态 `passed`，在 CPU 生成 1 个 pending 候选；它只证明
> 功能链成功。另有 commit `06f8360` 的 30 张合成图、10 份合成文档可复现工程
> Benchmark，图片 30/30 成功，并保留性能、质量、冲突和增量原始计数。所有质量
> 指标只适用于固定 synthetic 数据，不等于真实业务准确率；Intel GPU 与
> 防火墙/抓包网络审计不在本次结果中。后续 35 周期 TCP 状态采样未观察到外部
> TCP，但不能替代数据包、DNS、UDP 或 air-gap 证明。

## 2026-07-31 混合视觉加速补充

新增 RapidOCR/OpenVINO 快速路径后，8 张公开真实样本已完成 OCR 路由测量。
清晰 Atari 适配器在内部 OCR 阶段 `0.4138 s`、公开入口 engine `0.4691 s`
内生成 4 条 pending 候选且未调用 Qwen；同 worker 热请求加载为 0 s、engine
为 `0.4521 s`。OCR
误读的电池图被未知单位和低分规则拦截，经本地 Qwen 紧凑复核后在
`30.0235 s` 得到 `1000mAh` 与 `3.7V`，相对该图此前完整两步单次
`71.681 s` 约下降 58%。

完整逐样本数据、安全条件、原始复核输出和限制见
[`OCR_ACCELERATION.md`](OCR_ACCELERATION.md)。这组 8 图结果只证明路由与单次
延迟，不替换上方冻结 synthetic benchmark，也不构成真实业务准确率。
