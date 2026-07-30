# Product Evidence Guard 用户指南

最后复核：2026-07-30

Product Evidence Guard 用来核对说明书、参数表、PDF、文本和包装图中的商品参数
是否一致。它会把每个候选值和来源证据绑在一起；AI 提出的内容默认保持
`pending`，不会自动变成正式商品事实。

## 开始前

建议一个输入目录只放一个商品或一个 SKU。把多个型号混在同一目录，可能造成
误分组或无意义冲突。

Windows 常规运行需要：

- Windows PowerShell；
- 足够的磁盘空间存放虚拟环境、约 5.46 GB 的模型和报告；
- 仅在安装依赖、下载模型时使用网络；
- 对输入商品资料具有合法读取权限；
- 一个不会被无意共享的本地输出目录。

本机已经完成一次真实单图 CPU 验证。所用模型快照为
`OpenVINO/Qwen3-VL-8B-Instruct-int4-ov`，26 个必需 payload 为
`5,462,515,610` bytes，包含 Hugging Face 元数据的目录为
`5,462,526,140` bytes；一次受监控的成功运行峰值 Working Set 为
`11,626,774,528` bytes（约 10.83 GiB），Private Bytes 为
`7,469,481,984` bytes（约 6.96 GiB）。这只是当前机器、当前单图运行的观察值，
不是所有机器的最低内存保证；继续按 `info.json` 中的 16 GB 规划值留出安全余量。

commit `06f8360` 还完成了 30 图、10 文档的 synthetic CPU 工程 Benchmark；
图片 30/30 成功。该基准不等于真实业务准确率。Qoder CLI 已发现 Skill，但账号
未登录；防火墙/抓包和完整 Windows 商业闭环仍未完成。准确状态见
[`MODEL_AND_RUNTIME.md`](MODEL_AND_RUNTIME.md)、
[`QODER_VALIDATION.md`](QODER_VALIDATION.md) 和
[`BENCHMARK.md`](BENCHMARK.md)。

## 可读取的资料

| 输入 | 本地处理路线 |
|---|---|
| TXT、Markdown、CSV、JSON | 确定性解析器 |
| DOCX、XLSX | 确定性 Office 解析器 |
| 有文字层的 PDF | 确定性 PDF 文字解析器 |
| 带 `image.ext.ocr.json` 的图片 | 明确提供的 OCR sidecar 解析器 |
| 不带 sidecar 的图片 | 配置模型后使用本地 OpenVINO Qwen3-VL |
| 无文字层或扫描 PDF 页面 | pypdfium2 本地逐页渲染，再交给 Qwen3-VL；代码已接入，真实 PDF 仍待验证 |

图片扩展名支持 PNG、JPG/JPEG、WebP 和 BMP。生成式视觉语言模型不是经过校准的
OCR；它给出的坐标只能是 `approximate` 或 `unavailable`。

## 1. 安装环境

在仓库目录打开 PowerShell：

```powershell
Set-Location "<project-root>"
$ProjectRoot = (Resolve-Path ".").Path
$InputRoot = "<input-root>"
$ArtifactRoot = "<artifact-root>"
$ModelRoot = Join-Path $ProjectRoot ".models\Qwen3-VL-8B-Instruct-int4-ov"
.\scripts\install-env.ps1
```

安装器会：

- 使用固定 `uv 0.8.4`，并校验 Windows ZIP 的固定 SHA-256；
- 在仓库内创建固定 Python `3.11.13` 虚拟环境 `.venv`；
- 从 `requirements.lock` 同步经哈希锁定的完整依赖；`requirements.txt` 只作为
  顶层依赖输入；
- 把本项目安装到该虚拟环境；
- 记录安装 stamp，重复执行时可快速跳过；
- 把安装日志写入仓库的 `logs` 目录。

依赖安装需要联网，但不会把包安装到系统 Python。需要明确重装时：

```powershell
.\scripts\install-env.ps1 -Force
```

2026-07-30 已在 Windows PowerShell 5.1 以 `-Force` 完成干净重建，27 个
运行/构建包都以 `--require-hashes` 锁定；editable build 使用
`--no-build-isolation --no-index`。安装后 28 个包（含本项目）`pip check` 通过；
二次执行命中 stamp 并快速跳过。`requirements.lock` 是发布包与 Qoder 安装的
必备文件，不可只带 `requirements.txt`。安装失败时先保留日志并排查，不要为了
重试而删除已有模型或来源资料。

## 2. 唯一公开入口与状态

用户和 Qoder 都只应调用：

```powershell
.\scripts\run.ps1
```

查看本地服务状态：

```powershell
.\scripts\run.ps1 status
```

命令返回一份 UTF-8 JSON。常用字段：

| 字段 | 含义 |
|---|---|
| `ok` | 本次操作是否成功 |
| `status` | `starting`、`downloading`、`loading`、`running`、`error` 或 `stopped` |
| `exit_code` | 稳定退出码 `0`、`1`、`2` 或 `3` |
| `result` | 成功时的业务结果 |
| `error` | 失败时的错误代码与说明 |

`downloading`、`loading` 或 `error` 都不等于分析成功。

退出码合同：

| 退出码 | 含义 |
|---:|---|
| `0` | 成功 |
| `1` | 参数、权限、设备、文件或其他一般错误 |
| `2` | 客户端与本地服务通信错误 |
| `3` | 模型仍在下载，需要稍后执行 `--continue` |

## 3. 第一次分析与模型下载

```powershell
.\scripts\run.ps1 analyze $InputRoot
```

没有使用 `--deterministic-only`，也没有显式提供本地模型路径时，客户端会检查
配置的模型目录。若精确快照不存在，下载器会在 `.partial` 目录开始或继续下载。
未完成时返回退出码 `3`：

```text
模型仍在下载，请稍后运行 scripts\run.ps1 --continue。
```

继续下载并恢复保存的请求：

```powershell
.\scripts\run.ps1 --continue
```

`--continue` 不能和其他操作同时使用。下载器会检查配置中的运行文件、空文件和
明显的 Git LFS pointer；检查通过后才把 partial 目录原子提升为正式目录。这是
运行时结构完整性检查，本身不等于密码学 checksum。发布阶段另有
`<project-root>\release\model-sha256-manifest.json` 对 26 个 payload 做逐文件
SHA-256；该清单尚未签名。

### 显式指定模型和设备

```powershell
.\scripts\run.ps1 analyze $InputRoot `
  --model $ModelRoot `
  --device CPU
```

公开入口默认 `AUTO`。它会检查可见设备的 `FULL_DEVICE_NAME`，只自动选择名称
含 `Intel` 的 GPU，否则选择 CPU；显式设备必须可见，NPU 会因缺少该精确模型的
双重证据而被拒绝。本机能看到 `CPU` 和 `GPU`，但 GPU 全名为
NVIDIA GeForce RTX 5070 (dGPU)，因此应选择 CPU。不要把 NVIDIA 显卡写成已经
通过的 OpenVINO Intel GPU。

### 只跑确定性流程

用于 CI、故障排查或明确不含模型图片的数据集：

```powershell
.\scripts\run.ps1 analyze ".\samples\demo" `
  --output ".\demo-output" `
  --deterministic-only
```

仓库中的图片示例依赖 OCR sidecar。这条命令只能证明确定性证据流程，不能当作
真实 Qwen3-VL 或比赛 AI 验证。

## 4. 指定输出目录

没有 `--output` 时，默认写入：

```text
<输入目录>\.peg-output\
```

也可以另存：

```powershell
.\scripts\run.ps1 analyze $InputRoot `
  --output $ArtifactRoot
```

输出可能含商品原文、文件名、位置和哈希，应按来源资料的敏感等级保存。

## 5. 查看结果

建议按这个顺序：

1. `run-summary.json`
2. `conflicts.md`
3. `evidence-report.html`
4. `product-facts.json`

| 文件 | 内容 |
|---|---|
| `run-summary.json` | 发现、重做、复用、删除、跳过和失败的文件，以及候选和分组数量 |
| `conflicts.md` | 面向人的分组解释与复核建议 |
| `evidence-report.html` | 本地可视报告：原始值、标准值、来源和位置 |
| `product-facts.json` | 完整候选图；顶层始终是 `pending_human_confirmation` |
| `analysis-state.json` | 文件哈希与增量分析缓存 |
| `confirmation-state.json` | 当前确认、拒绝和失效状态 |
| `confirmation-audit.jsonl` | 本地追加式决定和失效记录 |
| `confirmed-product-facts.json` | 仅包含人工确认且来源哈希仍有效的候选 |
| `visual-transcription.json` | 两步视觉原始输出、通过项、拒绝项和诊断 |

### 冲突等级

| 分类 | 含义 | 建议 |
|---|---|---|
| `exact_match` | 多个来源表达和标准值相同 | 复核后按业务需要确认 |
| `converted_match` | 单位或写法不同，但确定性换算一致 | 复核后确认 |
| `compatible_expression` | 可能兼容，但详细程度不同 | 选择正式写法 |
| `insufficient_evidence` | 只有一条证据 | 补证据或谨慎确认 |
| `likely_version_update` | 文件名提示可能来自不同版本 | 先确认适用版本 |
| `strong_conflict` | 同一字段存在不兼容值 | 不得猜测，核实后逐条选择 |

报告里的三类可信信息不能混为一谈：

- recognition confidence：文字看清程度；
- mapping confidence：原文映射到字段的把握；
- evidence consistency：多个来源的一致程度。

模型 confidence 是 `model_self_assessment`，不是校准概率。

## 6. 真实单图验证示例

2026-07-30 在 Ryzen 7 7800X3D、31.11 GB RAM、CPU 设备上，对一张
701×1097 的 FDA/Wikimedia 公有领域商品标签图完成最终离线功能验证。工件
`<artifact-root>\test-real-model-release-20260730\` 状态为 `passed`。图片
SHA-256 为：

```text
40C808CE56735A027CC990EE2E476A5B2538C538D1B20E0F6C2647182EABE7CF
```

最终成功记录：

| 项目 | 实测值 |
|---|---:|
| 最终离线公开入口 | 加载 3.6064 s；内层分析 57.1824 s；外层 `analyze` 61.895 s |
| 最终状态 | `status=passed`；`model_reused=false`；1 个 pending 候选 |
| 较早主引擎 smoke | 加载 4.1104 s；单图 54.6552 s；总计 58.7705 s |
| 较早公开入口冷请求 | 加载 3.7021 s；单图 52.1827 s；`model_reused=false` |
| 较早同 worker 热请求 | 加载 0 s；单图 30.8367 s；`model_reused=true` |
| 峰值 Working Set（另一成功监测轮） | 11,626,774,528 bytes，约 10.83 GiB |
| 峰值 Private Bytes（另一成功监测轮） | 7,469,481,984 bytes，约 6.96 GiB |

模型保留的视觉原文为 `NET WT 8.0oz (0.501b)`；第二步映射为
`field=net_weight`、`raw_value=8.0oz`，确定性代码归一化为
`226.796185 g`。位置为 `[183,540,707,570]`，标记 `approximate`；
识别/映射自评分为 `0.85`/`0.91`，来源均为 `model_self_assessment`。候选状态
保持 `pending`，且因为只有一条证据被归类为 `insufficient_evidence`。

一次较早运行因 `max_new_tokens=900` 截断了 JSON，Schema 安全层拒绝全部候选。
该失败被保留，说明“模型能输出文字”不等于“非法或不完整结果可以进入事实图”。
这张真实图只证明功能成功，不进入准确率统计。冻结 synthetic Benchmark 的 30 图
全部成功，字段 recall 25/25、mapping P/R/F1 为 1、数字 27/27、单位 15/15，
冲突 TP2/FP0/FN0/TN10；这些结果只适用于确定性生成数据。完整性能、原始计数和
8.9149% 无变化增量节省见 [`BENCHMARK.md`](BENCHMARK.md)。

## 7. 确认或拒绝候选

从 `run-summary.json` 复制当前 `session_id`，从 `product-facts.json` 复制
`candidate_id`。

确认一条候选：

```powershell
.\scripts\run.ps1 confirm `
  --output-dir $ArtifactRoot `
  --session-id "<session-id>" `
  --candidate-id "<candidate-id>" `
  --reason "已与供应商最新规格表核对"
```

拒绝一条候选：

```powershell
.\scripts\run.ps1 reject `
  --output-dir $ArtifactRoot `
  --session-id "<session-id>" `
  --candidate-id "<candidate-id>" `
  --reason "包装图属于旧版，当前 SKU 不适用"
```

必须提供精确候选 ID、当前会话 ID 和非空理由。强冲突不会被批量自动解决。
确认一条候选也不会删除相反证据。

## 8. 导出正式事实

```powershell
.\scripts\run.ps1 export `
  --output-dir $ArtifactRoot `
  --session-id "<session-id>"
```

查看：

```text
confirmed-product-facts.json
```

没有确认项时，空 `facts` 数组是正常结果。不能把 `product-facts.json` 中的所有
候选当成已批准事实。

## 9. 来源变化后重新分析

通过正常业务流程编辑或替换来源文件，然后用相同输入和输出目录重跑：

```powershell
.\scripts\run.ps1 analyze $InputRoot `
  --output $ArtifactRoot
```

未变化文件可复用缓存，变化文件会重新解析，删除文件会从证据图移除。候选或来源
哈希不再匹配的旧决定会变成 `stale`，直到新证据被再次复核和确认前，都不会进入
正式导出。

## 10. 停止本地服务

服务会在配置的空闲时间后退出，也可手动停止：

```powershell
.\scripts\run.ps1 shutdown
```

不要按进程名结束所有 Python；其他软件也可能依赖 Python。

当前 server 父进程管理常驻模型 worker；同一模型和设备的后续 analyze 会复用
pipeline。本机热请求已记录 `model_reused=true` 和加载 0 秒。真实 4 图推理期间，
另一个 `run.ps1 status` 已通过 Named Pipe 在 0.438 秒返回 `running` 和
`available_operations`，响应没有 fallback 字段，因此并发状态查询已验证。

服务端把 300 秒作为模型加载、每个文件和最终化阶段各自的心跳边界。某阶段超时
时会终止该服务精确创建和跟踪的 worker，不会模糊结束其他 Python。客户端对整个
文件夹请求另有 1 小时上限。

## Qoder 安装

用户级安装：

```powershell
.\scripts\install-qoder-skill.ps1 -Scope User
```

项目级安装：

```powershell
.\scripts\install-qoder-skill.ps1 `
  -Scope Project `
  -ProjectRoot "<qoder-project-root>"
```

当前官方路径：

```text
%USERPROFILE%\.qoder\skills\local-product-evidence-guard\
<project>\.qoder\skills\local-product-evidence-guard\
```

不要依赖 `.lingma`。同名 Skill 不要同时放在用户级和项目级，因为当前 Qoder 文档
对优先级的说法不一致。安装器默认不覆盖已有目录；只有显式 `-Update` 才会先备份
再更新。安装 allowlist、必备 `requirements.lock`、完整性、回滚以及
symlink/junction/reparse point/hardlink 拒绝合同已纳入最终本地回归。

安装后重启 Qoder，或在 Qoder CLI 中运行 `/skills reload`，确认 Skill 被发现，
再手动调用：

```text
/local-product-evidence-guard
```

本机已隔离安装官方 npm Qoder CLI 1.1.8，用户级安装成功，且
`qodercli skills list` 显示 Skill 为 `Enabled`。但 `qodercli status` 显示
`Account: Not logged in`，所以只有“安装与发现”已验证；自动/手动调用、截图和
业务 transcript 仍需用户登录。

## 常见问题

| 现象 | 解释与处理 |
|---|---|
| Exit `1`、`python_unavailable` | 执行 `scripts\install-env.ps1`，查看其绝对日志路径 |
| Exit `1`、参数错误 | 检查命令、引号、必填 ID、输出目录和理由 |
| Exit `2`、通信错误 | 执行 `status`，保留 `.runtime/server.log`，重试一次；不要结束无关 Python |
| Exit `3`、`downloading` | 等待下载活动后，只执行 `scripts\run.ps1 --continue` |
| 正式模型目录存在但不完整 | 保留目录和错误，不要自动覆盖 |
| `GPU` 不适用 | 查看 `Core().available_devices` 和设备全名；当前机器应使用 CPU |
| 图片被跳过 | 提供完整本地 VLM，或有意提供 OCR sidecar；不能据此推断“图片没有参数” |
| 扫描 PDF 没结果 | 真实模型扫描 PDF 尚未验证；保留诊断并使用获准的本地检查流程 |
| DOCX/XLSX/PDF 解析失败 | 检查依赖，以及文件是否损坏、加密或超出边界 |
| 旧确认项不再导出 | 查看 `confirmation-state.json`，它可能因来源哈希变化而 `stale` |
| Qoder 找不到 Skill | 检查 `.qoder\skills` 路径、删除同名重复项、刷新或重启，并保留错误 |
| 中文乱码 | 使用公开 PowerShell 包装；保留 stdout/stderr 和终端版本 |

## 安全与隐私

- 不要以管理员身份运行。
- 不要处理不可信公开上传；本工具不是沙箱。
- 不要把快捷方式、可执行文件、凭据或无关秘密放进输入目录。
- 资料中“上传”“执行”“删除”“忽略指令”等文字始终只是证据数据。
- 安装和模型下载会联网；模型缓存后，已在 `HF_HUB_OFFLINE=1`、
  `TRANSFORMERS_OFFLINE=1`、`OPENVINO_TELEMETRY_DISABLED=1` 下完成推理。
  尚未做防火墙阻断或抓包，不能声称已证明零外连。
- 输出是本地明文，不会自动删除或加密。
- 分享或提交前先检查报告、日志和截图。

详见 [`PRIVACY.md`](../PRIVACY.md)、[`SECURITY.md`](../SECURITY.md) 和
[`LIMITATIONS.md`](LIMITATIONS.md)。
