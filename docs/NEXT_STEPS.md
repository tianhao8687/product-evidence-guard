# 下一步与发布关键路径

最后复核：2026-07-31

当前比赛路线固定：

- 模型：`OpenVINO/Qwen3-VL-8B-Instruct-int4-ov`；
- 运行时：本地 OpenVINO Runtime 与 OpenVINO GenAI；
- 视觉路线：OpenVINO OCR 快速筛查；普通商品图片异常时做一次 Qwen 紧凑复核，
  仅在复核输出无效或报错时进入原有两步 deep；图表 observation-only 路线不调用
  Qwen 补猜；
- Office 路线：确定性解析；
- 宿主：Qoder，只通过 `scripts\run.ps1`；
- 决策边界：明确人工确认后才能正式导出。

不得静默切换到其他模型大小或云 API。普通商品图片的 OCR 异常结果进入本地 Qwen
安全复核；图表 observation-only 页中的电气/容量、低置信、未知单位、同一 OCR 行
多值或输入/输出混写只保留观察，不调用 Qwen，也不能自动确认。

## 当前真实基线

| 能力 | 状态 | 已有证据 | 仍需完成 |
|---|---|---|---|
| 证据记录、单位归一、冲突图、报告、增量缓存 | **已验证** | 2026-08-05 D 盘正式目录回归 229 项通过，74.951 s，0 跳过 | 继续扩展恶意/边界语料 |
| confirm、reject、audit、当前哈希 export、stale | **已验证** | `run.ps1` → Named Pipe 的确定性 sidecar E2E 已覆盖；Qoder 真实图文分析已到人工安全门 | 用户亲自选择本轮真实图文 candidate ID/理由后补决定闭环 |
| 严格两步 Qwen 输出 | **已验证** | 主图片路径完成真实 CPU 冷/热公开入口运行 | 无文字层 PDF、恶劣图片和批量验证 |
| OCR 混合加速 | **已验证** | 8 张真实样本完成路由；清晰图 0.41 s，电池误读经 Qwen 修正 | 真实授权大样本与延迟分布 |
| 真实 PDF/DOCX/XLSX | **已验证** | 4 份官方公开文档全部解析；修复后 3 条 pending、0 阻断；缓存未命中/增量 0.6907/0.0272 s | 建立逐字段标注集、逐行产品 schema 与标题召回 |
| 常驻 server 与协议 | **已验证** | 模型复用；Pipe 完整业务 E2E、status/shutdown、认证、崩溃、重复启动和超时回归通过 | 独立拒绝服务/渗透测试 |
| 环境安装 | **已验证** | PS 5.1 `-Force` 干净重建；35 个 hash-locked 包、36 个已安装包兼容；CycloneDX 1.5 SBOM 与 35 包许可证元数据完成 | 漏洞扫描与完整二进制许可证审计 |
| 模型下载器 | **已验证** | tiny Hub 真实 partial→后台→exit 3→`--continue`→原子提升；正式 8B 前后 26/26 哈希一致 | 大模型真实断网重连不重复演练 |
| 扫描 PDF | **已完成** | 页面渲染、硬上限和 300 秒阶段心跳已接入；真实文字层 PDF 已验证 | 无文字层真实 PDF 与畸形压力测试 |
| Qoder Skill 与匿名业务闭环 | **已验证** | CLI 1.1.8，中英文自动/手动触发、sidecar 业务闭环、真实图片冷/热/缓存、真实图文强冲突、公共入口下载续传及 1 张脱敏 IDE 截图 | 本轮人工决定、完整截图组与网络审计 |
| 离线环境变量推理 | **已验证** | 三个离线/遥测环境变量下推理成功；35 周期 TCP 状态采样未观察到外部 TCP | 防火墙、数据包、DNS 与 UDP 审计 |
| Synthetic CPU Benchmark | **已验证** | commit `06f8360`；30 图、10 文档完成 | 真实授权数据与 Intel GPU 另行评测 |
| Draft PR | **已验证** | [PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1) | 本地回归已通过；最新远端 CI 以 PR Actions 为准 |
| 发布包 | **未完成** | commit `2f10f53` 的包曾完成 219 项、0 跳过 clean-room；最终新 HEAD 尚无同一精确 ZIP 的复验 | 重建标准路径 ZIP、SHA-256、clean-room，再做外部上传 |

## P0：完成可执行产品链

### 1. 冻结公开命令合同

正式命令：

```powershell
scripts\run.ps1 status
scripts\run.ps1 analyze "<商品资料目录>"
scripts\run.ps1 confirm --session-id "<id>" --candidate-id "<id>" --reason "<原因>"
scripts\run.ps1 reject --session-id "<id>" --candidate-id "<id>" --reason "<原因>"
scripts\run.ps1 export --session-id "<id>"
scripts\run.ps1 --continue
scripts\run.ps1 shutdown
```

完成标准：

- `run.ps1` 是唯一用户/宿主入口；
- client 输出稳定 UTF-8 JSON，运维日志与业务输出分开；
- 退出码 `0/1/2/3` 与 `info.json` 一致；
- 中文、空格和绝对 Windows 路径完整往返；
- 错误路径或不可用模型不返回 success；
- 内部脚本继续作为实现细节。

状态：**已验证**。最终 Windows JSON smoke 已覆盖中文空格路径、稳定 JSON、
确定性分析、增量复用、Named Pipe status 与 shutdown；并通过唯一公开入口完成
`analyze → confirm/reject → export → 修改来源 → reanalyze → stale` 的确定性
sidecar 业务 E2E。该记录验证的是公开命令与状态机，不是一次真实 Qwen/Qoder
会话。

### 2. 验证并修正 Client/Server 生命周期

验证 `starting → downloading → loading → running`、所有 error 路径及
`shutdown`：

- 每条 Named Pipe 连接只处理一次请求；
- authkey 匹配与不匹配；
- 已验证重复进程拒绝；
- stale PID/lock 安全恢复；
- server 异常退出；
- 只终止精确进程身份；
- 配置时间后的 idle shutdown；
- 超大消息拒绝；
- 日志不复制来源正文。

当前实现由 Named Pipe 父服务管理常驻模型 worker 子进程；worker 以模型路径和
实际设备为 key 缓存 pipeline。最终离线功能工件记录 `model_reused=false`、
加载 3.6064 秒、内层分析 57.1824 秒、外层 `analyze` 61.895 秒。更早的同模型
冷/热运行记录 `model_reused=false/true` 与热加载 0 秒，继续作为复用证据。

真实 4 图推理期间，公开 `run.ps1 status` 已通过 Named Pipe 在 0.438 秒返回
`running` 和 `available_operations`，响应没有 fallback 字段。完整协议回归
还覆盖 authkey 不匹配、异常恢复、重复启动和 timeout；独立拒绝服务/渗透测试
仍未执行。

完成标准：`tests/test.ps1` 和 protocol 测试保留 Windows 结果。

状态：**已验证**。真实模型、模型复用、推理中 Pipe 状态、生命周期与认证回归已
验证；确定性 sidecar 的确认、拒绝、导出、重分析和 stale 公开路径 E2E 也已
通过。Qoder 匿名 sidecar 业务实录、真实图片 Qwen 会话和真实图＋受控文档强冲突
分析已完成；本轮人工决定与独立安全测试仍未完成。

### 3. 验证并加固混合视觉与两步 Qwen 深度路径

代码检查确认主图片路径已经使用 `HybridImageReader`。OCR 紧凑复核输出无效或
报错时，才进入 `QwenVlReader` 的两步深度路径：

1. 视觉原文抄录；
2. 严格 transcription schema；
3. 从已接受原文做字段 mapping；
4. 严格字段白名单与原文子串关联；
5. 确定性归一和 graph；
6. 持久化 `visual-transcription.json` 诊断。

完成标准：

- 畸形输出不产生 `FactCandidate`；
- JSON 语法修复最多一次；
- `bbox_1000` 只能是 `approximate` 或 unavailable；
- 两类 confidence 都保留 `model_self_assessment` 来源；
- model ID、device、提取版本、来源哈希和定位进入最终报告；
- 一张图片失败不让其他文件崩溃。

状态：**已验证**。主图片路径已完成 CPU 真实运行并保留截断拒绝工件；无文字层
PDF、空白/恶劣图片和批量质量评测仍未完成。

### 4. 完成扫描 PDF 的边界防护

当前主 engine 会对无文字层页面使用 `pypdfium2` 本地渲染，并逐页送入同一个
OCR/Qwen 混合 reader，保留 PDF 名称、页码、页面像素和近似位置。

现有代码上限：

- 单文件 `100 MiB`；
- PDF 最多 `100` 页；
- PDF 单页最多 `25,000,000` 个渲染像素；
- 直接图片最多 `40,000,000` 像素；
- 每任务最多 `100` 个文件；
- 总提取文字最多 `2,000,000` 字符；
- 模型加载、每个文件和最终化阶段的心跳边界各为 `300` 秒；
- client 整个文件夹请求上限为 `1` 小时。

前五类大小/数量检查已在代码中执行，但未通过真实/畸形语料验证。父服务在某个
300 秒心跳边界超时时，会终止它创建并精确跟踪的模型 worker，不模糊结束其他
Python。

完成标准：文字版、扫描版、混合、空白、损坏、加密和超限 PDF 都有记录结果；
二进制发布包附带精确 PDFium 依赖声明。

状态：**已完成**。渲染、证据集成和精确 worker 硬超时已实现；真实/恶意 PDF
压力 E2E 未完成。

## P1：建立真实本地 AI 证据

### 5. 完成并验证模型快照

当前任务中用户已授权下载，但文档仍必须记录实际发生的过程：

- 下载前目标目录与磁盘剩余空间；
- downloader 输出与退出码；
- 中断/续传证据；
- 完整 26 文件结构清单；
- 最终字节数；
- revision；
- 所有完整性错误；
- 成功快照后生成的 checksum 清单。

不得把 `.partial` 当成 ready，也不得覆盖不完整正式目录。

状态：**已验证**。revision `f3d0bc7` 的正式快照已经下载并成功加载；26 个
payload 共 `5,462,515,610` bytes，含 Hugging Face 元数据目录共
`5,462,526,140` bytes。逐文件 SHA-256 清单已生成于
`<project-root>\release\model-sha256-manifest.json`，但尚未签名；下载前磁盘
记录和中断/续传 E2E 仍待补齐。

### 6. 先跑通真实模型，再扩大宣传

使用一张已授权、匿名商品图，记录：

- 精确 OS、CPU、RAM、Intel GPU/NPU 与驱动；
- `Core().available_devices`；
- 请求设备与实际设备；
- OpenVINO 软件包版本；
- 模型加载成功/失败与加载时间；
- 两步原始输出；
- 通过与被拒 schema 项；
- 冷、热图片时间；
- 峰值进程内存及可获得的设备内存；
- 每次失败的完整错误。

若 8B 无法加载、内存不足、输出不可用或真实 API 与合同不一致，应停止并报告，
不能自动切到 4B。

状态：**已验证**。已在 Ryzen 7 7800X3D、CPU 设备上完成公开标签图的两步
推理；常驻 worker 的公开入口冷请求加载 3.7021 秒、单图 52.1827 秒，随后同模型
热请求加载 0 秒、单图 30.8367 秒，`model_reused=true`。另一次成功监测轮峰值
Working Set 约 10.83 GiB、Private Bytes 约 6.96 GiB。完整样本集、Intel GPU/NPU
和商业闭环仍未验证；离线环境变量推理已验证。

### 7. 证明完整商业闭环

通过唯一公开入口演示：

1. 文档证据中的 `320g` 与 `0.32kg`；
2. 本地视觉证据中的包装文字 `300g`；
3. `strong_conflict`；
4. 来源定位和哈希；
5. 带理由确认指定候选；
6. 拒绝另一个候选；
7. export 只含已确认且当前有效的事实；
8. 修改来源文件；
9. 旧决定变为 `stale`；
10. 重新分析与确认。

完成标准：保留命令 transcript 和脱敏输出工件。

状态：**已验证（确定性 sidecar）**。`tests/test.ps1` 已经由
`scripts/run.ps1` → Named Pipe 完成 `analyze → confirm/reject → export →
修改来源 → reanalyze → stale`，并验证报告、审计记录、当前事实导出以及旧候选
失败。该记录不使用真实 Qwen 输出，也不能替代 Qoder 登录后的真实业务
transcript；这两项外部实录仍需完成。

## P2：测试、测量与宿主验证

### 8. 完成自动测试和 Windows 测试层

要求：

- Python unit tests：解析、归一、graph、模型 schema、确认；
- mocked protocol/server tests；
- 通过 `run.ps1` 的 Windows PowerShell E2E；
- 单独、opt-in 的真实模型 PowerShell 测试；
- compileall 和 demo smoke；
- Linux 与 Windows GitHub Actions，普通 CI 不下载 8B。

每个结果必须记录日期、revision、命令、环境、退出码和原始日志。local pass 不是
CI pass，测试文件存在也不是 pass。

状态：**已验证**。2026-08-05 最终 D 盘正式目录 Python 回归 229 项通过
（74.951 s，0 跳过）。
`tests/test.ps1` 还完成 `unit_tests`、中文空格、compileall、确定性、
incremental reuse、Named Pipe status/shutdown，以及公开入口的
`analyze → confirm/reject → export → 修改来源 → reanalyze → stale` 确定性
sidecar 业务 E2E，最终 `status=passed`；无效路径和旧候选操作退出码均为 `1`。
这不是一次真实 Qwen/Qoder 测试。Linux 与 Windows workflow 已配置，commit
`5a4fad8` 的远端 CI 曾临时绿色；最新远端 CI 状态以
[Draft PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1)
的 GitHub Actions 为准，不预先声称通过。

### 9. 验证 Qoder

只选择一个安装范围：

```text
%USERPROFILE%\.qoder\skills\local-product-evidence-guard\
```

或：

```text
<project>\.qoder\skills\local-product-evidence-guard\
```

不要依赖 `.lingma`，不要同时留下用户级和项目级同名副本。测试发现、中文/英文
自动触发、手动调用、下载中处理、完整决定/导出、UTF-8 和无云回退。

完成标准：版本化 transcript 与 `docs/assets/qoder/` 下的脱敏截图。

状态：**部分验证**。已隔离安装官方 npm Qoder CLI 1.1.8，用户级 Skill 安装成功，
`qodercli skills list` 实际显示 `Enabled`。2026-08-04 已登录并完成自然语言自动
路由、手动触发、无数据 `status` 和匿名 sidecar 完整业务闭环；真实图片、英文与
下载续传已经通过真实 tiny Hub 公共入口验证：首调用退出码 3，后台继续，第二次
`--continue` 退出码 0 并原子提升；证据见
`docs/evidence/download-resume-validation-20260805.md`。安装器 allowlist、必备 lock、
完整性、回滚和链接防护合同已通过最终本地回归；技术合同不能替代真实 Qoder
业务会话。

### 10. 补充网络审计

依赖和模型本地齐全后，已经设置：

- `HF_HUB_OFFLINE=1`；
- `TRANSFORMERS_OFFLINE=1`；
- `OPENVINO_TELEMETRY_DISABLED=1`。

真实本地推理成功。随后一次独立冷启动对本地客户端、服务和已发现子进程完成
35 个约 100 ms 的 TCP 状态采样，没有观察到外部 TCP 连接。该观察不是防火墙
阻断，也没有捕获数据包、DNS 或 UDP，不能证明零外连或 air-gapped。

状态：**已验证（有限范围）**。离线环境变量路径和 TCP 状态观察已验证；完整网络
审计未完成。证据见
[`evidence/performance-network-validation-20260805.md`](evidence/performance-network-validation-20260805.md)。

### 11. 冻结 Benchmark 与后续真实评测

按 [`BENCHMARK.md`](BENCHMARK.md) 的数据与指标合同执行。合成与真实结果分开，
所有值来自机器可读原始输出；无法采集的项用 `N/A` 并说明原因。

commit `06f8360` 已完成冻结 synthetic CPU 运行：

- 30 张图片全部成功，10 份文档无错误，前后 dataset hash 一致；
- 加载 3.701264 s，首图 75.969346 s，30 图批量 2221.638617 s；
- 逐图中位数 75.991379 s、p90 85.350259 s；
- 峰值进程内存 11,663,728,640 B（10.861 GiB）；
- 字段 recall 25/25，mapping P/R/F1 均为 1；
- 数字 27/27、单位 15/15、漏检 0/25、空样本编造 0/5、注入接受 0/2；
- 冲突 TP2/FP0/FN0/TN10，accuracy/P/R/F1 均为 1；
- 10 文档无变化复用 10/10，节省 8.9149%。

完成标准：

- dataset manifest 与哈希；
- model/revision/device 元数据；
- 准确率与编造原始计数；
- 冷热时间与峰值内存；
- 增量比较；
- 失败日志；
- 不虚构目标达成。

状态：**已验证**。这是可复现 synthetic 工程基准，不是授权真实商品数据准确率。
真实业务评测需用户提供授权匿名资料；另已完成 3 次独立冷启动，分析均值
26.7046 s、范围 25.5298–27.6312 s。单文件变化增量和 Intel GPU 仍不在冻结结果中。

## P3：发布与比赛外部操作

### 12. 完成安全发布包

ZIP 必须包含源码、Skill 元数据、脚本、测试、匿名 demo、文档、许可证和第三方
声明；必须排除模型、`.venv`、`.runtime`、缓存、日志、客户资料、真实样本、生成
报告、凭据、pending 下载和未确认导出。

实际 `pypdfium2 5.12.1` Windows x64 wheel 的 19 份 PDFium/依赖 notices 已
收集并索引；CycloneDX 1.5 SBOM 与 35 个锁定包许可证元数据也已生成。发布前仍需
完成漏洞扫描及所有其他二进制传递依赖的完整许可证审计。

完成标准：

- 可重复 packaging 命令；
- `release/local-product-evidence-guard-v1.0.0.zip`；
- SHA-256；
- clean-room 解压与 smoke test；
- 保留归档内容检查结果。

发布 ZIP 内 `manifest.json.commit` 是源码身份权威，同目录 `.sha256` 校验 ZIP；
manifest 不包含对自身或 ZIP 的自引用哈希。当前脚本在内存中生成确定性
`manifest.json`，列出干净 HEAD 的路径、mode、size 和 Git object；allowlist、
必备 `requirements.lock`、排除项与
symlink/junction/reparse point/hardlink 拒绝合同均已通过本地回归。历史 commit
`2f10f53` 的候选包已完成 clean-room 解压，使用 Python 3.11.13 按 lock 安装
36 个包；219 项通过（77.233 s，0 跳过）并完成业务 E2E。之后的新 HEAD 含文档
与供应链证据变化，不能把旧包测试扩大成最终精确 ZIP 的证明。

状态：**未完成**。必须从最终干净 HEAD 重建标准路径 ZIP、重算 SHA-256 并对同一
精确归档重新 clean-room；之后才进入用户决定的外部上传和发布。

### 13. 完成外部比赛步骤

所有实测证据齐全后：

- 更新 Benchmark、Qoder、模型、离线、CI 与限制文档；
- 同时记录失败与成功；
- 录制 3 分钟和 5 分钟演示；
- 用真实数据替换文章占位；
- 在现有 [Draft PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1)
  继续提交；本地最终回归已通过，远端状态以 PR Actions 为准；
- 只有用户明确登录和发布时，才向 ModelScope/文章平台发布；
- 收集最终 URL 并提交比赛表单。

状态：**待用户操作**。Draft PR 已创建，commit `5a4fad8` 的 CI 曾临时绿色；
发布、账号登录与比赛提交需要用户操作；最新远端 CI 状态以 PR Actions 为准。

## 必须停止并报告的情况

出现以下情况应停止相关阶段并保留原始错误：

- 官方模型或固定 revision 不可用；
- 磁盘或内存不足；
- OpenVINO 无法加载精确 8B 工件；
- 模型输出质量不适合安全工作流；
- 在没有官方和实测证据时依赖 NPU；
- Qoder 无法发现或调用 Skill；
- 需要收费服务、用户登录、保护分支修改或破坏性操作；
- 必须把客户图片或其他非匿名资料提交进仓库。

报告应写清：执行了什么、原始错误、已排除什么、最小下一步和由谁执行。

## 明确不做

本版本不扩张为：

- 完整 PIM 或多人审批；
- 云部署、SaaS 或同步；
- 生图或图片编辑；
- 电商平台自动发布；
- 模型训练或新量化；
- 隐藏的云端/传统 OCR 回退；
- 冲突中的自动真值选择；
- 掩盖核心未验证状态的 UI。
