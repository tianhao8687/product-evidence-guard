# Product Evidence Guard 演示脚本

最后复核：2026-08-05

本文提供一套确定性彩排，以及 3 分钟、5 分钟比赛录屏脚本。最终视频必须使用
真实本地模型，并保留与画面一致的原始运行证据。仓库自带 OCR sidecar 示例只适合
彩排证据流程，绝不能包装成 Qwen3-VL 真机结果。

## 录制前的诚实检查门

以下项目必须按真实证据勾选：

- [x] 精确 8B 模型正式快照完整：revision `f3d0bc7`；
- [x] OpenVINO `2026.2.1` 与 OpenVINO GenAI `2026.2.1.0` 已记录；
- [x] `Core().available_devices` 已记录为 `CPU`、`GPU`；
- [x] GPU 全名为 NVIDIA RTX 5070，因此没有冒充 Intel GPU 验证；
- [x] 实际推理设备明确为 `CPU`；
- [x] 精确模型已在录制机器 CPU 加载；
- [x] 一张 701×1097 公有领域商品标签图成功；
- [x] 两步原始输出、通过项和拒绝诊断已保存；
- [x] 两步 reader 已接入主分析路径；
- [x] 同一常驻 worker 的第二次请求记录 `model_reused=true`；
- [x] 真实 4 图推理中 `status` 经 Named Pipe 在 0.438 秒内并发返回；
- [x] 最终 D 盘正式目录回归 231 项通过（79.634 s，0 跳过），Windows 完整 JSON smoke 为
      `status=passed`；
- [x] Windows PowerShell 5.1 `-Force` 干净安装与二次快速跳过已验证；
- [x] commit `06f8360` 的 30 图、10 文档 synthetic CPU Benchmark 已完成；
- [x] Qoder CLI 1.1.8 已发现用户级 Skill 为 `Enabled`；
- [x] confirm、reject、export 和 stale 已通过 `run.ps1`/Named Pipe 使用
      deterministic sidecar 完整实测；真实 Qwen/Qoder 录屏仍需另做；
- [x] 三个离线/遥测环境变量下完成真实本地推理；
- [ ] 已通过防火墙阻断或抓包证明无外连；
- [x] Qoder 登录后已完成中英文自动/手动触发、真实图片和真实图文冲突调用；
- [ ] 画面已检查，不含客户身份、token、私有绝对路径或无关窗口。

只要上述核心证据仍缺失，视频就应标为“开发预览”或在画面上明确列出未完成项，
不能剪掉真实失败。

## 已有真实单图镜头可用数据

| 项目 | 真实值 |
|---|---:|
| 模型 | `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` |
| revision | `f3d0bc7` |
| 模型 payload / 含元数据目录 | `5,462,515,610` / `5,462,526,140` bytes |
| 设备 | CPU，AMD Ryzen 7 7800X3D |
| RAM | 31.11 GB |
| 最终工件 | `test-real-model-release-20260730`；`status=passed` |
| 最终加载/内层分析/外层 `analyze` | 3.6064 / 57.1824 / 61.895 s |
| 最终复用/候选 | `model_reused=false`；1 个 pending 候选 |
| 较早同 worker 热加载/单图 | 0 s / 30.8367 s；复用证据 |
| 峰值 Working Set | 约 10.83 GiB，来自另一成功监测轮 |
| 峰值 Private Bytes | 约 6.96 GiB，来自同一监测轮 |

真实第一步模型原文：

```text
NET WT 8.0oz (0.501b)
```

真实第二步和确定性归一：

```text
field=net_weight
raw_value=8.0oz
normalized_value=226.796185
normalized_unit=g
bbox_1000=[183,540,707,570]
position_precision=approximate
recognition_confidence=0.85
mapping_confidence=0.91
confidence_source=model_self_assessment
status=pending
```

这些值可原样展示，但必须同时说明：单样本不是准确率，confidence 是模型自评，
候选没有自动确认。

冻结 synthetic Benchmark 可展示：

| 项目 | 画面值 |
|---|---:|
| 运行 ID | `benchmark-final-06f8360-20260730` |
| 图片/文档 | 30/30 图片成功；10 文档无错误 |
| 加载/首图/冷总计 | 3.701264 / 75.969346 / 79.670610 s |
| 30 图批量/逐图中位数/p90 | 2221.638617 / 75.991379 / 85.350259 s |
| 峰值进程内存 | 10.861 GiB |
| 字段 recall / mapping P/R/F1 | 25/25 / 1/1/1 |
| 数字/单位/漏检/空样本编造 | 27/27 / 15/15 / 0/25 / 0/5 |
| 冲突 | TP2 FP0 FN0 TN10 |
| 10 文档增量 | 复用 10/10，节省 8.9149% |

旁白必须紧接一句：“这些全是确定性生成的 synthetic 工程样本，不是现实业务
准确率；真实商品图只证明功能成功。”

## 录制素材

准备两个目录：

```text
demo-work/
├── product-materials/
│   ├── instruction.docx 或 说明书.txt
│   ├── parameters.xlsx 或 参数表.csv
│   └── packaging-real-anonymized.jpg
└── output/
```

来源资料应故意包含：

- 一份文档写 `320g`；
- 另一份写 `0.32kg`；
- 包装图清晰印有 `300g`；
- 不同来源分别写 `2件` 和 `3件`；
- 同时出现 `不锈钢` 和 `304不锈钢`；
- 不含品牌、地址、客户名、订单号、电话或账号信息。

最终商业演示图片必须经过授权并放在仓库之外；不要把客户图片提交到
`samples/real/`。

录制前：

1. 关闭通知、聊天软件、密码管理器和无关标签页；
2. 使用能正确显示中文的终端字体；
3. 放大终端和报告文字；
4. 只清理可丢弃的 demo output，不删除来源目录；
5. 除非专门录下载恢复，否则提前下载模型；
6. 把精确命令保存到文本文件，避免现场输入错误；
7. 先完整跑一遍，成功和失败都留证；
8. 检查 HTML、JSON 和截图是否含秘密。

## 确定性彩排

使用仓库中的匿名文本、CSV 和 OCR-sidecar 示例：

```powershell
.\scripts\run.ps1 analyze ".\samples\demo" `
  --output ".\demo-output" `
  --deterministic-only
```

在核对真实生成报告后，彩排应展示：

- `320g` 与 `0.32kg` 属于 `converted_match`；
- sidecar 的 `300g` 与两份文档形成 `strong_conflict`；
- `2件` 与 `3件` 冲突；
- `不锈钢` 与 `304不锈钢` 作为 `compatible_expression` 需要复核。

旁白必须说：

> 这是确定性流程彩排。图片文字来自预先准备的 OCR sidecar，不是本地
> Qwen3-VL 真机结果。正式演示会换成真实授权图片和真实模型输出。

这句说明不能被裁掉。

## 最终录制命令卡

分析：

```powershell
.\scripts\run.ps1 analyze ".\demo-work\product-materials" `
  --output ".\demo-work\output" `
  --device CPU
```

模型未完成时：

```powershell
.\scripts\run.ps1 --continue
```

查看动态 ID：

```powershell
$summary = Get-Content ".\demo-work\output\run-summary.json" -Raw |
  ConvertFrom-Json
$facts = Get-Content ".\demo-work\output\product-facts.json" -Raw |
  ConvertFrom-Json
$summary.session_id
$facts.candidates |
  Select-Object field, raw_value, source_file, candidate_id, status |
  Format-Table -AutoSize
```

复核证据后确认：

```powershell
.\scripts\run.ps1 confirm `
  --output-dir ".\demo-work\output" `
  --session-id "<copy-current-session-id>" `
  --candidate-id "<copy-reviewed-candidate-id>" `
  --reason "已与当前供应商规格表核对"
```

拒绝旧候选：

```powershell
.\scripts\run.ps1 reject `
  --output-dir ".\demo-work\output" `
  --session-id "<copy-current-session-id>" `
  --candidate-id "<copy-reviewed-candidate-id>" `
  --reason "包装图属于旧版，当前 SKU 不适用"
```

导出：

```powershell
.\scripts\run.ps1 export `
  --output-dir ".\demo-work\output" `
  --session-id "<copy-current-session-id>"
```

结束录制后关闭服务：

```powershell
.\scripts\run.ps1 shutdown
```

不要预先写死 candidate/session ID；它们取决于当前文件哈希和会话。

## 3 分钟版本

### 0:00–0:20：业务问题

画面：

- 匿名商品文件夹：参数表、说明书和真实包装图；
- 快速放大互相矛盾的参数。

旁白：

> 同一个商品经常同时有说明书、参数表和包装图。人工核对慢，普通总结又容易
> 直接猜答案。错误重量或件数进入生图、文案和详情页后会继续污染交付。
> Product Evidence Guard 先保存证据，再让人决定正式事实。

### 0:20–0:38：Qoder 调用唯一入口

只有实际验证 Qoder 后，才展示：

- 提示词 `核对这个商品资料文件夹，找出冲突并保留来源。`；
- Skill 发现结果；
- Qoder 调用 `scripts\run.ps1`。

旁白：

> Qoder 触发本地 Skill，唯一公开入口是 run.ps1。短客户端通过 Windows
> Named Pipe 连接本地服务，得到稳定 UTF-8 JSON。

Qoder CLI 1.1.8 已完成用户级安装、`skills list`、登录、自动/手动 `status`、
匿名 sidecar 业务闭环、真实图片与真实图文冲突分析。录制时应使用脱敏终端或 IDE
画面并明确说：

> Qoder 只调用本地 Skill 入口；AI 候选保持待确认，最终决定必须由用户给出。

这一段不能把 Qoder 宿主等待剪成“18 秒实时完成”。现有真实会话的宿主外层耗时
约为 82.1–273.6 秒，高于本地引擎时间。录制前可以先完成一次预热，并预先打开
已经结束的脱敏会话；若采用时间压缩或跳切，画面必须明确标注“时间压缩”，保留
命令发起和结束两端，并同时展示稳定 JSON 中真实的 `analysis_seconds`、
`model_load_seconds` 和 `model_reused`。不得把时间轴中的 18 秒镜头长度说成实际
Qoder 或本地推理耗时。

模型跨请求复用与 Named Pipe 并发状态都已经验证。真实 4 图推理期间，另一个
`run.ps1 status` 在 0.438 秒内经 Pipe 返回 `running` 和
`available_operations`，响应没有 fallback 字段。

### 0:38–1:12：本地 Hybrid 处理

画面：

- 服务的 `loading`/`running` 状态；
- 架构图；
- 真实 `run-summary.json`；
- 真实单图的 `visual-transcription.json`；
- `document-visuals.json` 中的 Office 锚点、XLSX 原生图表和 mixed PDF 页级路由。

旁白：

> Word、Excel 和文字版 PDF 用确定性解析；包装图先走本地 OpenVINO OCR 快速
> 路线。不确定的普通商品图只做一次本地 Qwen3-VL 紧凑视觉复核；只有复核输出
> 非法或报错时，才进入“先抄原文和近似位置、再映射字段”的严格两步深度兜底。
> 模型不决定最终参数，单位换算和冲突分级由代码完成。Word/Excel 内嵌图片会
> 保留原文档位置，Excel 原生图表直接读 series 和数值点；技术图、数据图和 PDF
> mixed page 的 `observation-only` 路线只保留文字层或 OCR observations，不调用
> Qwen 补猜。这里的观察不是正式事实，也不代表曲线几何已经被理解。

画面必须先显示本轮 `image_route_counts`，明确它究竟是 OCR fast、
`qwen_ocr_review` 还是 `qwen_deep_fallback`。下面这组数值来自较早的真实单图
严格两步深度工件，只能按这个口径展示：

```text
actual device: CPU
final load/analysis/outer: 3.6064 s / 57.1824 s / 61.895 s
model_reused: false
candidates: 1 pending
earlier warm reuse evidence: 0 s / 30.8367 s, model_reused=true
peak Working Set: 10.83 GiB（另一成功监测轮）
```

同时标注“单图 smoke，不是完整 Benchmark”。

### 1:12–1:48：证据与冲突

画面：

- `evidence-report.html`；
- 净重组的 `320g`、`0.32kg` 和包装图 `300g`；
- 各自来源位置；
- 数量冲突和材质复核组。

旁白：

> 说明书的 320 克和参数表的 0.32 千克换算后一致。包装图写 300 克，因此
> 净重组被阻断，系统不会替用户猜。2 件和 3 件是强冲突；不锈钢与 304
> 不锈钢可能兼容，但正式写法仍需人工选择。每条证据都能回到文件、页码、
> 单元格、行号或图片近似位置。

必须展示本次真实分组，不能把预期输出录成实际结果。`product-facts.json` 应继续
显示 `pending_human_confirmation`。

### 1:48–2:22：人工确认和导出

画面：

- 复核后选择一个 candidate ID；
- 带理由执行 `confirm`；
- 对旧候选执行 `reject`；
- 执行 `export`；
- 打开 `confirmed-product-facts.json`。

旁白：

> 人必须明确选择 candidate ID 并填写理由。只有人工确认且来源哈希仍有效的
> 候选才进入正式导出。待确认、拒绝和失效项不会混进去；审计文件记录动作、
> 来源哈希、会话和工具版本。

### 2:22–2:47：来源变化与 stale

画面：

- 修改可丢弃匿名副本中的一个值；
- 重新分析；
- 在 `run-summary.json` 显示变化和复用文件；
- 显示旧决定变成 `stale` 并从导出排除。

旁白：

> 修改来源副本后，系统只重做变化文件，并让依赖旧哈希的确认自动失效。旧结论
> 不会在新版本资料中继续冒充有效事实。

### 2:47–3:00：结尾

画面：一张总结页——本地、可追溯、确定性裁决、人工确认。

旁白：

> 这不是文档总结器，而是一条本地商品事实治理链：模型负责理解，代码负责
> 裁决，人负责确认，文件版本负责让旧结论及时失效。

## 5 分钟版本

5 分钟版保留相同商业闭环，并增加工程证据。

### 0:00–0:35：问题和用户

沿用 3 分钟开场，补充商品运营、供应链、设计、文案和平台交付团队。比较人工核对
与文件夹级增量核验，不虚构行业统计。

### 0:35–1:05：Skill 与进程架构

展示：

```text
Qoder → run.ps1 → short client → Named Pipe → server
```

解释 UTF-8 JSON、下载未完成退出码 `3`、精确进程身份和空闲退出。模型加载、每个
文件和最终化阶段分别有 300 秒心跳边界，边界超时只终止该服务精确跟踪的模型
worker；客户端整请求另有 1 小时上限。展示父服务、常驻模型 worker、冷请求
`model_reused=false`、热请求 `model_reused=true`，以及 4 图推理中 0.438 秒
Named Pipe 状态响应。

### 1:05–1:50：三级视觉安全与深度两步兜底

先展示当前图片分路：OCR fast、一次 `qwen_ocr_review`、以及只有非法/报错才进入的
`qwen_deep_fallback`。技术图、数据图的 `observation-only` 分支只保存观察，不调用
Qwen。随后另行展示较早真实单图深度兜底工件的第一步原始 JSON、接受结果和第二步
mapping；不能把这份旧深度工件说成当前每张图片都会执行的路线。

旁白：

> 第一步只能抄录可见原文，坐标只能是近似或不可用。第二步只能从第一步原文
> 逐字取值，并映射到字段白名单。非 JSON、缺字段、额外字段、非法坐标、超长
> 结果和原文不存在的值都会被拒绝；最多只允许一次 JSON 语法修复。这是深度
> 兜底的安全合同，不是所有图片的固定调用次数。

展示一次真实的截断失败：

```text
max_new_tokens=900 → JSON 截断 → item_not_object → 0 candidates
```

说明安全层拒绝了不完整结果，之后成功运行没有抹掉失败记录。

如果本段包含 Qoder 现场调用，沿用 3 分钟版的等待规则：先预热；或明确标注时间
压缩/跳切并保留开始、结束和错误画面；始终展示真实 elapsed 字段，不把剪辑时长
冒充宿主、本地引擎或模型耗时。

### 1:50–2:38：证据图与确定性裁决

展示文件哈希、SourceBlock、FactCandidate、FactGroup 和人工决定链。演示单位换算
一致、净重/毛重隔离、数量冲突和兼容材质表达。

旁白：

> 模型自报分数只是 self-assessment。标准值、冲突等级和能否导出都不由模型
> 决定。

### 2:38–3:28：人工决定闭环

执行确认、拒绝和导出，打开 `confirmation-audit.jsonl`，不得显示私有绝对路径。

### 3:28–4:05：精准失效

修改可丢弃来源副本，重跑并展示变化/复用文件和 stale 排除。

### 4:05–4:35：本地实测

| 项目 | 画面值 |
|---|---|
| 模型/revision | `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` / `f3d0bc7` |
| OpenVINO/GenAI | `2026.2.1` / `2026.2.1.0` |
| 可见设备 | `CPU`、`GPU`；GPU 全名 NVIDIA RTX 5070 |
| 实际设备 | CPU |
| 最终真实图功能工件 | `passed`；加载/内层分析/外层 3.6064/57.1824/61.895 s；1 个 pending 候选 |
| 较早模型复用证据 | 热加载/单图 0/30.8367 s；`model_reused=true` |
| Synthetic 加载/首图/冷总计 | 3.701264/75.969346/79.670610 s |
| Synthetic 30 图批量 | 2221.638617 s；30/30 成功 |
| Synthetic 逐图分布 | 中位数 75.991379 s；p90 85.350259 s |
| Synthetic 峰值内存 | Working Set 10.861 GiB；GPU N/A |
| Synthetic 质量 | mapping P/R/F1=1；冲突 TP2/FP0/FN0/TN10 |
| 文档视觉真机 | synthetic Office 图片 2/2、mixed PDF 1/1、原生 XLSX 图表 1/1；官方 Raspberry Pi/TI mixed PDF 3 页安全路由 |
| 文档视觉速度 | 受控 3 文档外层冷/常驻重算/业务缓存为 11.1997/1.7050/0.2092 s；真实 mixed PDF 3 页为 1.3537 s |
| 离线结果 | 三个离线/遥测环境变量下已验证；防火墙/抓包未完成 |

口播必须补充：受控三文档均走 OCR fast，差值主要是 pipeline 首次构建，不是
Qwen 生成速度；计时后新增的 `observation-only` 窄白名单、低置信度/同一 OCR
行多值失败关闭、schema 有效空复核不重复 deep 等安全加固没有重新启动模型复测，
只完成无模型回归。这些速度仍是原单次测量，不是新模型进程的性能结论。

### 4:35–5:00：限制与价值

旁白：

> 当前模型不是校准 OCR，图片坐标只是近似；纯扫描 PDF、资源边界和恶意文件仍需
> 严格测试。PDF mixed 检测只登记页级视觉上下文，不等于图形几何理解或曲线逐点
> 数字化。工具不替代授权来源、供应商确认或企业审批。它的价值是把散乱资料变成
> 可追溯、可阻断、可确认、会随版本失效的事实链。

本地最终 231 项回归可以展示；最新远端 CI 状态以 PR Actions 为准，不能在文档
中预先显示为已通过。
Draft PR #1 可以展示；文章、ModelScope、视频和比赛链接只有真实发布后才能展示。

## 镜头清单

| 镜头 | 画面 | 必须保留的证据 |
|---:|---|---|
| 1 | 匿名来源文件夹 | 数据集 manifest 和授权说明 |
| 2 | Qoder 发现/触发 | 版本、安装路径、截图和 transcript |
| 3 | `run.ps1` 响应 | 命令、退出码、UTF-8 stdout/stderr |
| 4 | 设备/模型状态 | 依赖版本、revision、可见/实际设备 |
| 5 | 两步原始结果 | 脱敏 raw、accepted 和 rejected JSON |
| 6 | HTML 证据报告 | 输出 hash 和来源位置 |
| 7 | confirm/reject/export | 审计和正式导出 |
| 8 | 来源修改和重跑 | 修改前后 hash 与 stale 事件 |
| 9 | 离线/Benchmark | synthetic 原始工件、环境变量记录与防火墙/抓包待办 |
| 10 | 限制和结束页 | 确实存在的链接 |

## 最终剪辑清单

- [ ] 不含客户或个人信息；
- [ ] 不含凭据和账号 token；
- [ ] 没有用跳剪隐藏失败命令；
- [ ] 没把 OCR sidecar 说成真实 Qwen 输出；
- [ ] 没把 synthetic 指标说成真实业务准确率；
- [ ] 没把官方兼容性说成目标机器验证；
- [ ] 没把 NVIDIA GPU 说成已验证的 OpenVINO Intel GPU；
- [ ] 没有无依据的 NPU 声称；
- [ ] 数字与原始输出一致，没有美化；
- [ ] 字幕区分已实现、已验证和待完成；
- [ ] 所有可见链接都能打开；
- [ ] 视频时长符合所选版本；
- [ ] 来源图和生成媒体允许发布。
