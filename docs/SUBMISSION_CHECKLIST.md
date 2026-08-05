# 比赛提交清单

最后复核：2026-08-05

本文件是最终证据索引。源码工件勾选只代表“文件存在并已检查”，不代表工作流
已经通过。只有精确命令或会话真实执行并留下证据，验证项才可以勾选。

## 状态说明

- `[x]`：该项已经完成并检查；
- `[ ]`：尚未完成或尚未验证；
- `USER`：需要用户账号、授权样本、发布决定或外部提交；
- `N/A`：确实不适用，必须写明原因和复核人。

空框不能用暗示成功的文字替代。

## 1. 仓库与范围

- [x] 工作分支为 `codex/competition-ready-v1-sanitized`。
- [x] 最终分支以远端安全父提交重建，保留全部有意源码变更且不携带原始未脱敏截图历史。
- [x] 已逐个复核工作区变更及其用途。
- [x] 已确认没有修改无关仓库。
- [x] 没有直接 push 或 merge 到 `main`；最终脱敏分支仍仅在本地。
- [x] 产品名为 Product Evidence Guard。
- [x] Skill 名为 `local-product-evidence-guard`。
- [x] 范围保持为商品证据核验，不扩展为 PIM、SaaS、生图或自动发布。
- [x] 禁止云模型/OCR 回退。
- [x] AI 候选在人工确认前不是正式事实。

证据：

```text
发布源码身份：读取发布 ZIP 内 manifest.json 的 commit 字段
发布 ZIP 完整性：读取同目录 .sha256；manifest.json 不做自引用哈希
目标 base 与工作区：在提交/推送前由发布者用 Git 状态和 diff 复核
```

## 2. 根目录必需工件

- [x] `SKILL.md`
- [x] `info.json`
- [x] `meta.json`
- [x] `requirements.txt`
- [x] `requirements.lock`
- [x] `pyproject.toml`
- [x] `README.md`
- [x] `LICENSE`
- [x] `THIRD_PARTY_NOTICES.md`
- [x] `PRIVACY.md`
- [x] `SECURITY.md`
- [x] 最终检查 `SKILL.md` 的 name、description 长度、中英文触发词、公开入口、
      `--continue`、失败、离线与注入规则。
- [x] Qoder 安装器只提供当前 `.qoder\skills` 用户级/项目级范围，不支持旧
      `.lingma` 目标。
- [x] `requirements.lock` 是正式发布包和 Qoder 安装必备文件。
- [x] `info.json`、`requirements.txt`、`requirements.lock`、包元数据、文档和
      代码版本在最终 ZIP 中完全一致。
- [x] 所有发布 JSON 文件均通过 UTF-8 解析。

证据：

```text
模型校验清单：<project-root>\release\model-sha256-manifest.json
发布源码身份：ZIP 内 manifest.json.commit
最终 ZIP 版本一致性：标准路径 ZIP、相邻 SHA-256 和相邻 verification.json 已复核；
verification.json 位于归档外，避免归档身份自引用
```

## 3. 唯一入口与运行架构

- [x] `scripts/run.ps1`
- [x] `scripts/client.py`
- [x] `scripts/server.py`
- [x] `scripts/protocol.py`
- [x] `scripts/install-env.ps1`
- [x] `scripts/install-qoder-skill.ps1`
- [x] `scripts/model_download.py`
- [x] `scripts/benchmark.py`
- [x] `scripts/benchmark-document-visuals.py`
- [x] `scripts/package-release.ps1`
- [x] `SKILL.md` 和用户文档只把 `scripts/run.ps1` 作为公开入口。
- [x] Windows 上稳定 UTF-8 JSON stdout 已验证。
- [x] 运行日志与业务 JSON 已验证分离。
- [x] 退出码 `0/1/2/3` 已通过公开入口端到端验证。
- [x] Named Pipe 地址、authkey 和协议版本已验证一致。
- [x] 每个连接只处理一个请求。
- [x] `status`、`analyze`、`confirm`、`reject`、`export`、`shutdown` 已通过
      `scripts/run.ps1` → Named Pipe 确定性 sidecar E2E 实测。
- [x] `starting`、`downloading`、`loading`、`running`、`error`、`shutdown`
      状态迁移全部实测。
- [x] 重复启动、旧 PID/lock、精确进程恢复、崩溃和 timeout 已纳入最终回归。
- [x] 常驻模型 worker 在第二次同模型请求复用 pipeline。
- [x] 冷请求 `model_reused=false`，热请求 `model_reused=true` 且加载 0 秒。
- [x] 真实 4 图推理中，`run.ps1 status` 经 Named Pipe 在 0.438 秒内返回
      `running` 和 `available_operations`，响应没有 fallback 字段。

确定性 sidecar 已覆盖完整公开入口商业闭环；该证据不等同于真实 Qwen/Qoder
录屏。模型复用、推理中 Pipe 状态并发、认证失败和生命周期错误路径已经验证。

证据：

```text
最终完整基线：tests/test.ps1 在 D 盘正式目录内部 unittest 263 项通过，178.084 s，
0 跳过
公开入口：scripts/run.ps1 → Named Pipe sidecar E2E 覆盖 analyze、confirm（含理由）、
reject（含理由）、export、修改来源、reanalyze 和 stale
报告/审计：JSON、Markdown、HTML 与 confirmation-audit.jsonl 均通过
退出码：0/1 来自业务 E2E，2 来自隔离 Named Pipe 不可达 E2E，3 来自真实续传路径
说明：上述是确定性 sidecar 证据，不是真实 Qwen/Qoder 证据
```

## 4. 环境与依赖安装

- [x] 使用仓库内固定 Python `3.11.13` `.venv`。
- [x] 安装器固定 `uv 0.8.4`，Windows ZIP SHA-256 已固定并验证。
- [x] 关键运行时版本已固定。
- [x] 重复安装 stamp 已实现。
- [x] 已在 Windows PowerShell 5.1 以 `-Force` 干净重建。
- [x] 重复安装命中 stamp 并安全快速跳过。
- [x] `-Force` 精确目标与 reparse point 防护已验证。
- [x] 36 个已安装包（含本项目）`pip check` 全部兼容。
- [x] 35 个运行/构建包全部 hash-locked，并使用 `--require-hashes`。
- [x] editable build 使用 `--no-build-isolation --no-index`，不临时解析未锁定后端。
- [x] 安装失败返回非零码和清晰 UTF-8 中文提示；缺 `requirements.lock` 的真实
      PowerShell 入口测试已通过。
- [x] 绝对日志路径存在，且代表性日志与服务异常日志不含商品正文。
- [x] 已盘点 35 个锁定 distribution 的许可证元数据并生成机器可读清单。
- [x] 已生成带分发哈希的正式 `requirements.lock`。

证据：

```text
安装结果：Windows PowerShell 5.1 -Force 成功；Python 3.11.13
重复安装：命中 stamp，快速跳过
依赖结果：35 个 hash-locked 包；36 个已安装包（含本项目）兼容
SBOM：CycloneDX 1.5，35/35 锁定 distribution 已纳入
许可证：35 包元数据已盘点；完整所有二进制传递依赖许可证审计仍未完成
```

## 5. 模型获取与身份

- [x] 精确 ID：`OpenVINO/Qwen3-VL-8B-Instruct-int4-ov`。
- [x] 官方来源和 Apache-2.0 许可证已记录。
- [x] 官方页面 5.46 GB 已标注为上游元数据。
- [x] 26 个运行文件清单已记录。
- [x] `.partial`、续传、结构检查和原子提升代码存在。
- [x] revision 固定。
- [x] 下载前剩余磁盘已记录。
- [x] 完整正式快照已下载。
- [x] 中断并恢复下载已真实演练。
- [x] 正式快照可由精确模型成功加载。
- [x] 26 个必需 payload 为 `5,462,515,610` bytes。
- [x] 含 Hugging Face 元数据的模型目录为 `5,462,526,140` bytes。
- [x] 已生成并保留 26 文件 SHA-256 manifest；清单尚未签名。
- [x] 已验证不完整正式目录会报错且不会被覆盖。
- [x] 通过 `run.ps1` 验证退出码 `3` 和 `--continue`。

证据：

```text
目标目录：<project-root>\.models\Qwen3-VL-8B-Instruct-int4-ov
revision：f3d0bc7
下载日志：正式快照已下载并成功加载；原始日志保留在 Git 外
续传日志：tiny Hub partial→后台 worker；首调用 0.462 s/exit 3，第二次
--continue exit 0，最终原子提升；详见 docs/evidence/download-resume-validation-20260805.md
manifest/checksum：<project-root>\release\model-sha256-manifest.json
必需 payload：5,462,515,610 bytes；含元数据目录：5,462,526,140 bytes
```

公开截图或文章应脱敏本地绝对路径。

## 6. 真实 OpenVINO 模型验证

- [x] OS 已记录为 Windows 11 专业版 10.0.26200，build 26200。
- [x] 本机 OpenVINO 可见设备的 driver/plugin 版本已记录：NVIDIA `591.86`；CPU
      不暴露独立 driver，记录 OpenVINO plugin build；NPU 不存在并标为 N/A。
- [x] CPU 为 AMD Ryzen 7 7800X3D，RAM 为 31.11 GB。
- [x] CPU 物理/逻辑核心为 8 / 16；内存精确值为 33,409,253,376 bytes。
- [x] 电源计划记录为高性能，GUID `8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c`。
- [x] Python `3.11.13` 已记录。
- [x] NVIDIA RTX 5070 已记录，未冒充 Intel GPU。
- [x] OpenVINO `2026.2.1` 已记录。
- [x] OpenVINO GenAI `2026.2.1.0` 已记录。
- [x] `Core().available_devices` 已记录为 `CPU`、`GPU`。
- [x] 公共客户端已校验显式设备，并实现“设备全名含 Intel 的 GPU → CPU”
      AUTO 回退。
- [x] 显式 NPU 会因缺少该精确模型的双重证据而拒绝。
- [x] 请求设备与实际设备都记录为 `CPU`。
- [x] 精确 8B `VLMPipeline` 构造成功。
- [x] 一张 701×1097 公有领域商品标签图成功。
- [x] 图片 SHA-256 已记录。
- [x] 原始视觉转录输出已保留。
- [x] 原始字段映射输出已保留。
- [x] 通过和拒绝的 Schema 工件均已保留。
- [x] 早期主引擎 smoke：加载 4.1104 s、单图 54.6552 s、总计 58.7705 s。
- [x] 公开入口冷请求：加载 3.7021 s、单图 52.1827 s。
- [x] 同 worker 热请求：加载 0 s、单图 30.8367 s。
- [x] 最终离线发布候选工件状态为 `passed`。
- [x] 最终工件：`model_reused=false`，加载 3.6064 s、内层分析 57.1824 s、
      外层 `analyze` 61.895 s。
- [x] 最终工件生成 1 个保持 `pending` 的候选。
- [x] 峰值 Working Set 约 10.83 GiB、Private Bytes 约 6.96 GiB。
- [x] 设备显存标为 N/A，原因是本次使用 CPU。
- [x] 较早截断失败没有被后续成功覆盖。
- [x] 没有宣称 NPU 已支持或已验证。
- [x] 没把 NVIDIA 硬件写成已验证的 OpenVINO Intel GPU。

证据：

```text
最终模型工件：<artifact-root>\test-real-model-release-20260730\
历史成功工件：<artifact-root>\real-image-output-20260730-r3\
失败工件：<artifact-root>\real-image-output-20260730\
图片 SHA-256：40C808CE56735A027CC990EE2E476A5B2538C538D1B20E0F6C2647182EABE7CF
运行测量：见 docs/MODEL_AND_RUNTIME.md 与 docs/BENCHMARK.md
```

## 7. 视觉与扫描 PDF

- [x] 严格两步视觉 reader。
- [x] 严格模型输出 Schema。
- [x] 字段白名单。
- [x] 长度、数量、类型、bbox 和来源关联检查。
- [x] 最多一次 JSON 语法修复。
- [x] `approximate`/`unavailable` 位置合同。
- [x] 模型自评 confidence 来源。
- [x] 两步 reader 已接入 `analyze_directory`。
- [x] 主路径持久化 `visual-transcription.json`。
- [x] model ID、设备、文件 hash 和 locator 进入真实输出。
- [ ] 空白、模糊、恶意注入和无参数真实图片全部执行。
- [x] pypdfium2 无文字页检测与渲染集成。
- [x] 文件大小、PDF 页数、像素、文件数量和总文字代码边界。
- [x] 模型加载、每个文件和最终化阶段分别使用 300 秒心跳边界。
- [x] 阶段超时只终止该服务创建并精确跟踪的模型 worker。
- [x] 客户端对整个文件夹请求使用 1 小时上限。
- [x] 扫描、混合、空白、损坏、加密、101 页超限和渲染像素超限 PDF 的本地
      功能/负向子矩阵已执行；来源文件 hash 保持不变。
- [ ] PDF 全矩阵仍缺密码解密成功、更多 xref/object-stream 畸形、真实扫描 OCR
      质量和大规模压力；不得把当前子矩阵写成完整渗透测试。
- [x] 代表性视觉 reader、ROI 渲染和 partial PNG 失败路径会清理临时目录且不删除
      来源，来源 hash 已验证不变。
- [ ] “所有失败路径”尚未由故障注入穷尽，不扩大现有清理结论。

证据：

```text
单图工件：<artifact-root>\test-real-model-release-20260730\
PDF 安全子矩阵：新增 8 项纯本地负向测试，实际生成空白、损坏、加密、101 页
PDF，并覆盖文件大小/数量、渲染像素和 partial PNG 清理；真实扫描 OCR 质量、
资源炸弹与完整 fuzz/渗透矩阵仍未完成
```

## 8. 确定性证据与冲突引擎

- [x] TXT/Markdown 解析。
- [x] CSV 行/列位置。
- [x] JSON path。
- [x] DOCX 段落/表格。
- [x] XLSX sheet/cell。
- [x] 文字版 PDF page。
- [x] OCR sidecar 兼容。
- [x] 相对来源路径和 SHA-256。
- [x] 复用字段白名单和既有数据模型。
- [x] 确定性单位归一。
- [x] 净重、毛重和未指定重量分开。
- [x] exact、converted、compatible、insufficient、version、strong-conflict
      六类。
- [x] 文件级增量缓存。
- [x] 删除来源后移除证据。
- [x] 当前最终工作树的完整回归套件通过；发布源码身份由 ZIP manifest 记录。
- [x] 文件大小、文件数量、PDF 页数、渲染像素和损坏/加密 PDF 边界已用小型受控
      fixture 实测，避免生成资源炸弹本身。
- [ ] 压缩炸弹、持续 CPU/RAM 耗尽、并发洪泛和全部 Office/图片敌对格式未完成。

证据：

```text
最终完整基线：D 盘正式目录 263 项通过，178.084 s，0 跳过
回归源码身份：最终 ZIP 内 manifest.json.commit；不在文档中预填
```

## 9. 确认、审计、导出与 stale

- [x] `pending`、`confirmed`、`rejected`、`stale` 代码路径。
- [x] 当前 session ID 要求。
- [x] 精确 candidate ID 要求。
- [x] 非空理由要求。
- [x] `confirmation-audit.jsonl`。
- [x] 只含当前 hash 的 `confirmed-product-facts.json`。
- [x] 来源变化后 reconciliation 为 stale。
- [x] 确定性 sidecar 公共 E2E 中强冲突未自动选择。
- [x] 通过 `run.ps1` 执行带非空理由的 confirm/reject。
- [x] export 只含已确认且当前候选。
- [x] 公共 E2E 中修改来源、reanalyze，并使旧决定 stale。
- [x] HTML/Markdown/JSON 完整显示确认和 stale 信息，审计 JSONL 已验证。

证据：

```text
商业闭环：tests/test.ps1 通过 scripts/run.ps1 → Named Pipe 完成
analyze → confirm（含理由）→ reject（含理由）→ export → 修改来源 →
reanalyze → stale；stale candidate 重试按预期 exit 1
报告/审计：JSON、Markdown、HTML、confirmation-audit.jsonl 均通过
证据边界：使用 samples/demo 确定性 sidecar，不是真实 Qwen/Qoder 录屏证据
```

## 10. 自动测试与 CI

- [x] 核心 engine 测试文件。
- [x] Qwen Schema/输出测试文件。
- [x] confirmation 测试文件。
- [x] downloader 测试文件。
- [x] protocol 测试文件。
- [x] PDF、报告、设备与 Benchmark 测试文件。
- [x] 4 份官方公开 PDF/DOCX/XLSX 完成原生解析、误报修复与增量速度测试；原文件
      保持在 Git 和发布包之外。
- [x] 可复现 DOCX/XLSX/PDF 样本覆盖 2 张 Office 内嵌图片、1 个原生 XLSX
      图表和 1 个 mixed PDF 页面；真实模型处理摘要已脱敏归档。
- [x] Raspberry Pi/TI 官方 mixed PDF 3 页完成页级检测、文字层保留与安全路由；
      TI 曲线页 ROI 面积减少 29.84%，以 OCR observations 结束；明确不宣称
      图形几何理解或曲线逐点数字化。
- [x] `docs/evidence/document-visual-acceleration-final.json` 分栏保留受控文档
      冷模型、常驻完整重算、业务缓存命中和真实 mixed PDF 摘要，并注明受控路径
      全部为 OCR fast、计时后安全加固未重启模型复测。
- [x] `observation-only` 只允许明确标签、单值、高置信度且单位安全的重量（含
      净重/毛重）、尺寸、数量、型号、材质或颜色生成候选；电气/容量、低置信度、
      未知单位、同一 OCR 行多值和 input/output 混合方向只保留 observations，
      不调用 Qwen。schema 有效空紧凑复核不重复 deep；这些都是计时后无模型回归
      边界，旧的单次速度没有重启模型复测。
- [x] `tests/test.ps1` 存在。
- [x] `tests/test-real-model.ps1` 存在且为 opt-in。
- [x] 最终 D 盘正式目录 Python 回归 263 项通过，178.084 s，0 跳过。
      脱敏机器摘要：`docs/evidence/final-local-regression-20260805.json`。
- [x] `compileall` 通过。
- [x] 确定性 demo smoke 通过。
- [x] Windows PowerShell `tests/test.ps1` 最终完整基线中，内部 unittest
      D 盘正式目录 263 项通过，178.084 s，0 跳过；完整业务 E2E JSON 通过。
- [x] 中文路径通过。
- [x] 含空格路径通过。
- [x] 缺 Python 环境、缺模型下载态、非法路径、stale 候选和 Pipe 通信错误均返回
      稳定 JSON 与对应非零码；安装缺 lock 另有真实 PowerShell 失败测试。
- [x] 下载 pending/continue 通过。
- [x] UTF-8 JSON stdout 通过；全部 stderr 失败路径审计另列。
- [x] commit `5a4fad8` 的 Linux GitHub Actions 临时通过。
- [x] commit `5a4fad8` 的 Windows GitHub Actions 临时通过。
- [x] 已只读复核远端 Draft PR #1 当前旧 head `4295ab8` 的最新 Linux/Windows
      checks 均成功；该远端 head 不包含当前本地脱敏分支，推送后的新 CI 仍待复核。
- [x] 当前普通 CI 不下载 8B 模型。
- [x] 临时 CI commit 与 Draft PR 已记录。

当前 `.github/workflows/tests.yml` 已配置 Linux 与 Windows job；`5a4fad8`
临时绿色不覆盖后续本地收尾改动。本地最终回归已经通过；最新远端 CI 状态以
PR Actions 为准，本文不预先声称其通过。

证据：

```text
最终完整基线：D 盘正式目录 263 项通过，178.084 s，0 跳过
Windows smoke：tests/test.ps1 status=passed；unit_tests、compileall、
deterministic_smoke、incremental_reuse、public_business_e2e、json_reports、
markdown_report、html_report、audit_jsonl、named_pipe_status、shutdown 均 passed；
confirm/reject/export/reanalyze 均 exit 0，stale candidate 与 invalid path 均 exit 1
业务证据边界：完整闭环使用确定性 sidecar，不是真实 Qwen/Qoder
临时 CI commit：5a4fad8
最新远端 CI：以 PR Actions 为准
```

## 11. Qoder 验证与用户登录

- [x] 官方 npm Qoder CLI 1.1.8 已隔离安装。
- [x] 只安装到用户级当前 `.qoder\skills` 范围。
- [x] 安装器 allowlist、必备 `requirements.lock`、完整性、备份/回滚和链接防护
      合同已通过最终回归。
- [x] Skill 出现在 `qodercli skills list`，状态为 `Enabled`。
- [x] 2026-08-04 已完成 Qoder CLI 登录；自动/手动触发与匿名 sidecar 分析、确认、拒绝、导出、stale 已通过并保存脱敏记录。
- [x] 用户已登录 Qoder；只记录登录状态，不保存账号或认证材料。
- [ ] 用户级和项目级没有同名重复项的最终截图。
- [x] 原任务指定的 5 条中文触发句已在 5 个全新会话逐条验证，5/5 自动选择 Skill，均只调用一次 `status`。
- [x] 原任务指定的 3 条英文触发句已在 3 个全新会话逐条验证，3/3 自动选择 Skill，均只调用一次 `status`。
- [x] 8 条触发矩阵脱敏记录已保存；8/8 Tool 与 CLI 退出码 0、无乱码、未读取商品文件。
- [x] 手动 `/local-product-evidence-guard` 已记录。
- [x] 现有脱敏会话中，Qoder 业务操作只调用 `scripts\run.ps1`。
- [x] downloading/`--continue` 行为已记录。
- [x] 匿名 sidecar 的 analyze/confirm/reject/export/stale 已记录；真实图文新一轮决定仍未执行。
- [x] 中文文件名、理由和输出没有 mojibake。
- [x] 运行工件证明图片 OCR/VLM 使用本地 OpenVINO 后端且没有云 OCR/VLM 回退；独立网络抓包仍未完成。
- [x] Qoder 版本、范围、解析路径、命令、退出码和脱敏 transcript 已保留。
- [x] 两张脱敏真实截图及文字证据存于 `docs/assets/qoder/`：用户级 Skill 发现页与分析/人工确认安全门；完整截图组仍未完成。
- [x] `QODER_VALIDATION.md` 已按现有证据更新，并明确列出未完成边界。

证据：

```text
Qoder 版本：CLI 1.1.8
安装范围/路径：用户级 `%USERPROFILE%\.qoder\skills\local-product-evidence-guard\`
transcript：docs/assets/qoder/2026-08-04-status-validation.md；
            docs/assets/qoder/2026-08-04-business-workflow.md；
            docs/assets/qoder/2026-08-05-english-auto-trigger.md；
            docs/assets/qoder/2026-08-05-real-image-performance.md；
            docs/assets/qoder/2026-08-05-real-image-controlled-documents.md
截图：docs/assets/qoder/01-skill-discovered.png；
      docs/assets/qoder/04-analysis-summary-redacted.png
尚缺：用户级/项目级无重复项截图，以及 confirm/export/stale 完整 IDE 截图组
```

## 12. 离线验证

- [x] 依赖和模型都已完整缓存。
- [x] 已设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、
      `OPENVINO_TELEMETRY_DISABLED=1`。
- [x] 在上述环境变量下通过公开入口分析一张真实图片。
- [x] 项目分析路径没有托管模型或云 OCR 回退。
- [x] 约 100 ms TCP 状态采样方法、目标 PID 范围和严格边界已记录。
- [x] 35 个采样周期内未观察到外部 TCP；不等于防火墙/数据包/DNS/UDP 抓取。
- [x] 环境变量、退出码与本地报告已保留。
- [x] `PRIVACY.md`、模型文档和文章已区分环境变量验证与网络审计。

证据：

```text
离线方法：三个离线/遥测环境变量
离线日志：见 <artifact-root> 下脱敏运行记录
TCP 状态观察：35 个约 100 ms 周期未观察到外部 TCP
边界：防火墙、数据包、DNS、UDP 与 air-gap 证明未完成
```

## 13. Benchmark

- [x] `BENCHMARK.md` 已定义数据、指标、原始布局和诚实报告规则。
- [x] 已完成一组真实 CPU 冷/热单图验证。
- [x] 已记录冷加载/单图、热加载/单图、模型复用和一次内存峰值。
- [x] 已保留截断拒绝、未解析单位和最终成功记录。
- [x] `scripts/benchmark.py` 已覆盖冻结 synthetic 指标合同并真实执行。
- [x] Synthetic manifest 已生成、明确标记，运行前后 hash 一致。
- [x] 30 图、10 文档满足冻结数据集组成。
- [x] USER — 已提供授权匿名真实样本集；授权范围和样本 SHA 已保留。
- [x] 完整 synthetic CPU 结果。
- [ ] 适用 Intel GPU 结果。
- [x] 30 图逐图中位数与 p90 已记录。
- [x] 同一真实标签图安全关闭后独立冷启动 3 次；分析均值 26.7046 s，范围 25.5298–27.6312 s。
- [x] 峰值进程 RAM 已记录；GPU 内存因 CPU 推理为 N/A。
- [x] 数字/单位正确率与原始计数。
- [x] 字段 mapping precision/recall/F1。
- [x] 漏检、空样本编造和注入接受计数。
- [x] 冲突分类 TP/FP/FN/TN。
- [x] 无变化增量节省与 10/10 复用。
- [x] 单文件变化增量功能 E2E：只改 1 份说明书并复用另外 2 份；未冻结独立性能值。
- [x] Synthetic 与真实功能验证分开。
- [x] 汇总结果指向本地原始工件。
- [x] README、Benchmark 文档和文章表格与冻结值一致。
- [ ] 最终视频表格与冻结值一致。

证据：

```text
最终单图 run ID：test-real-model-release-20260730
Synthetic Benchmark run ID：benchmark-final-06f8360-20260730
冻结模型代码 commit：06f8360
原始结果目录：<artifact-root>\benchmark-final-06f8360-20260730\
dataset SHA-256：c96e95f2817b1c8f16a99952022e03f541d3a5fe89ee6db0f1d85ea01c76dcf3
```

## 14. 安全、隐私与许可证

- [x] `PRIVACY.md` 记录本地数据、输出和网络边界。
- [x] `SECURITY.md` 记录报告、注入和已知缺口。
- [x] `LIMITATIONS.md` 区分代码、官方兼容和实测。
- [x] 项目使用 MIT License。
- [x] 模型和直接依赖 notices。
- [x] 固定依赖列表中没有强 copyleft PDF renderer。
- [x] pypdfium2/PDFium notice 义务已记录。
- [x] install/server/client 普通运行日志已审计并加固：当前旧日志脱敏后正文、模型
      raw text、凭据和客户绝对路径均为 0 命中，未来写入由白名单事件和回归约束；
      业务报告、确认审计和 runtime state JSON 按其独立业务合同保留。
- [x] 已完成有限本地进程 TCP 状态采样；未观察到外部 TCP，且未扩大为零外连结论。
- [ ] 依赖漏洞扫描。
- [x] CycloneDX 1.5 SBOM 已生成；35/35 锁定 distribution 有版本、哈希和许可证元数据。
- [x] 依赖 hash-required lock 与模型 26 文件 SHA-256 manifest 已生成。
- [ ] 模型 manifest 签名、漏洞扫描和所有二进制传递依赖的完整许可证审计。
- [x] 已从实际 `pypdfium2 5.12.1` Windows x64 wheel 收集并索引 19 份 PDFium/依赖 notices。
- [x] 损坏/加密/超页/超像素 PDF、超文件大小和超文件数量的代表性负向测试通过。
- [ ] XML/ZIP bomb、持续资源耗尽、并发洪泛与完整 fuzz/渗透矩阵未执行。
- [x] 输入发现、发布和 Qoder 的 symlink/junction/reparse-point/hardlink 防护测试。
- [x] Named Pipe authkey 不匹配、崩溃、重复启动和 timeout 回归测试。
- [x] Named Pipe 已完成有限独立进程边界测试：错误 auth、`1 MiB + 1` 超限帧和
      24 路有限状态并发后服务均可用；同时确认同用户完整身份记录可伪造并公开为
      known limitation。
- [ ] Named Pipe 持续拒绝服务、完整 fuzz 或渗透测试。
- [x] 独立安全复核尚未执行，并已在限制与合规表中明确标注；此勾选不表示通过安全审计。

证据：

```text
SBOM：CycloneDX 1.5，35/35 锁定 distribution 已覆盖
notice bundle：实际 pypdfium2 wheel 的 19 份 PDFium/依赖 notices 已收集
仍未完成：漏洞扫描、所有其他二进制传递依赖的完整许可证审计、模型 manifest 签名
安全回归：最终 D 盘正式目录基线 263 项通过，178.084 s，0 跳过；
fuzz/XML bomb/渗透测试未完成
```

## 15. 文档

- [x] `README.md` 已完成最终普通用户复核：安装、首次下载、继续、分析、报告、确认、导出、Qoder、测试与限制均有大白话说明。
- [x] `docs/ARCHITECTURE.md`，含 5 张 Mermaid。
- [x] `docs/USER_GUIDE.md`。
- [x] `docs/MODEL_AND_RUNTIME.md`。
- [x] `docs/BENCHMARK.md`。
- [x] `docs/QODER_VALIDATION.md`。
- [x] `docs/COMPETITION_COMPLIANCE.md`。
- [x] `docs/DEMO_SCRIPT.md`。
- [x] `docs/ARTICLE_DRAFT.md`。
- [x] `docs/SUBMISSION_CHECKLIST.md`。
- [x] `docs/LIMITATIONS.md`。
- [x] `docs/NEXT_STEPS.md`。
- [x] 所有本地 Markdown 链接都可解析；已由只读全库扫描复核，并纳入 `test_submission_contract`。
- [x] 32 份发布 Markdown 中的 5 个 Mermaid 块已通过围栏、声明和括号平衡静态
      审计。
- [ ] 5 个 Mermaid 块已由真实 Mermaid 引擎逐图渲染确认。
- [x] 除明确警告/历史说明外，没有旧 `.lingma` 安装指令。
- [x] 模型、Qoder、离线、Benchmark、CI 与 PR 状态已逐项链接机器证据，并明确
      区分真实实测、synthetic、远端旧 head、限制和待用户操作，没有把历史绿色或
      局部观察冒充当前完整证明。
- [x] 日期、版本、命令、输出文件名和状态表已由 JSON 解析、本地链接、提交材料
      合同及最终包相邻验证记录交叉检查；外部 URL/发布日期仍保留待填。

## 16. 视频与文章 — USER

- [ ] 3 分钟视频已录制。
- [ ] 如有需要，5 分钟视频已录制。
- [ ] 真实本地模型与 OCR-sidecar 彩排清楚区分。
- [ ] Qoder 调用和本地设备证据可见。
- [ ] 证据/冲突/确认/导出/stale 闭环可见。
- [ ] 不含秘密、客户身份或私有绝对路径。
- [x] 3/5 分钟字幕稿明确区分已实现、已验证、限制和待完成；实际录屏仍待用户执行。
- [ ] 视频链接已创建并测试权限。
- [x] 文章保留视频、发布状态等未完成占位，发布前须从原始记录替换。
- [x] 文章本地长度为 3,987 个 CJK 字符，Markdown、图片和本地链接已检查。
- [ ] ModelScope 研习社最终发布预览仍需在用户账号内确认。
- [x] 文章包含命令、架构、失败、隐私、限制和复现步骤。
- [ ] 文章通过用户账号发布。
- [ ] 文章 URL 和发布日期已记录。
- [x] 没有虚构阅读量或发布状态。

证据：

```text
视频：[待链接]
文章：[待链接]
发布日期：[待填]
```

## 17. 发布包

目标工件：

```text
release/local-product-evidence-guard-v1.0.0.zip
```

- [x] `scripts/package-release.ps1` 存在。
- [x] allowlist 合同包含源码、Skill/config、`requirements.lock`、测试、匿名 demo、
      文档、项目许可证和第三方 notices。
- [x] 合同测试验证模型文件被排除。
- [x] 合同测试验证 `.venv`、`.tools`、`.runtime` 和缓存被排除。
- [x] 合同测试验证日志和生成报告被排除。
- [x] 合同测试只允许 `samples/real` 的说明/占位文件，拒绝真实样例 payload 和
      客户资料。
- [x] 合同测试验证凭据、token、私有账号数据和私有绝对路径被排除。
- [x] 合同测试验证 pending 下载和 partial 目录被排除。
- [x] 合同测试验证未确认生成事实被排除。
- [x] allowlist 独立于 `.gitignore`，并拒绝 reparse point/hardlink 输入。
- [x] ZIP 内 `manifest.json.commit` 作为源码身份；manifest 不自引用哈希。
- [x] ZIP 同目录 `.sha256` 作为整个归档的校验权威。
- [x] ZIP 内容清单已由 `manifest.json` 核验并保留。
- [x] SHA-256 已生成并独立重算复核。
- [x] 当前 263 项源码对应的最终干净 HEAD 精确发布包完成 clean-room 解压、安装、
      完整测试和 smoke；精确身份与耗时只保存在归档相邻验证记录中。
- [ ] 所有二进制传递依赖的完整第三方 license bundle 已包含；pypdfium2/PDFium 19 份 notices 已完成。

证据：

```text
最终精确 clean-room：使用 Python 3.11.13 和 36 个已安装包，模块从标准路径 ZIP
的解压目录导入；263 项通过（0 跳过），compileall、确定性 smoke、
增量复用、Windows 业务 E2E、Named Pipe status/shutdown 均通过。标准路径 ZIP 的
相邻 `.sha256` 和 `.verification.json` 保存归档哈希、manifest commit、精确耗时与
严格边界；归档不包含这两个相邻文件，避免自引用。
```

## 18. GitHub PR 与远端检查

- [x] 最终本地变更已按比赛交付范围有意地 stage 并提交；审计时工作树干净。
- [x] 本轮提交历史按安全/发布合同/材料收口拆分；最终 Git tree 与 ZIP 均不含模型、
      真实样本 payload、客户输出、凭据或运行日志。
- [ ] 最终本地分支已 push 至当前 Draft PR；当前 PR head 仍落后本地最终变更。
- [x] 对正确 base 创建 Draft PR。
- [x] PR 未 merge。
- [ ] PR 摘要列出功能和文件变化。
- [ ] PR 列出精确本地测试命令与结果。
- [ ] PR 分开记录真实模型、Qoder、离线、Benchmark 和 CI。
- [ ] PR 列出失败、风险和用户外部操作。
- [x] 2026-08-05 公开只读快照中 review、review comment、issue comment 均为 0；
      当前没有可处理意见，推送新 head 后仍需再次复核。
- [x] commit `5a4fad8` 的临时 checks 绿色。
- [ ] PR Actions 中最新所需 checks 全部通过，或失败被明确报告。

证据：

```text
Branch：codex/competition-ready-v1-sanitized
临时 CI commit：5a4fad8
最终 Commit：不在文档预填；以发布 ZIP 内 manifest.json.commit 为准
Draft PR：https://github.com/tianhao8687/product-evidence-guard/pull/1
最新远端 CI：本地最终回归已通过；实时状态以 PR Actions 为准
```

## 19. ModelScope 与比赛提交 — USER

- [ ] 登录目标 ModelScope 账号。
- [ ] 确认代码、文章、截图、视频、demo、模型引用和第三方素材发布权。
- [ ] 发布不含模型权重和客户资料的 Skill 包。
- [ ] 从干净位置验证已发布包可安装。
- [ ] 应用准确比赛标签和分类。
- [ ] 添加 README、文章、视频、源码、许可证和模型链接。
- [ ] 所有公开链接不依赖私有会话 token。
- [ ] 完成官方比赛表单。
- [ ] 提交前逐项复核 checkbox 和声明。
- [ ] 保存提交确认和时间。
- [ ] 未经用户明确要求，不执行社交媒体发布。

最终链接：

| 工件 | URL |
|---|---|
| 源码仓库 | [tianhao8687/product-evidence-guard](https://github.com/tianhao8687/product-evidence-guard) |
| Draft PR | [PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1) |
| Release ZIP/checksum | `待链接或附件` |
| ModelScope Skill | `待链接` |
| 技术文章 | `待链接` |
| 演示视频 | `待链接` |
| Benchmark 证据 | `<artifact-root>\benchmark-final-06f8360-20260730\` |
| Qoder 证据 | `docs/assets/qoder/`（脱敏 transcript、真实图片性能、Skill 发现页与 IDE 安全门截图） |
| 比赛提交确认 | `待记录` |

## 最终签署

发布负责人必须逐项回答：

| 问题 | 答案 |
|---|---|
| 精确 8B 是否在声明设备运行？ | 是：CPU 真实图功能验证与 synthetic Benchmark |
| 真实公开图片是否完成两步模型调用？ | 是：一张 |
| Windows 公共入口是否完成 analyze/confirm/export/stale？ | 是：确定性 sidecar 已经由 `scripts/run.ps1` → Named Pipe 完成 analyze、confirm、reject、export、来源变更、reanalyze 与 stale；真实 Qwen/Qoder 录屏仍待完成 |
| 离线环境变量下推理是否完成？ | 是；防火墙阻断/抓包网络审计未完成 |
| Qoder 是否发现并执行 Skill？ | 是；CLI 已登录，中英文自动触发与手动触发成功，匿名业务闭环和真实图片调用均经安装副本固定入口完成 |
| Benchmark 汇总是否链接原始记录？ | 是：synthetic run ID 与本地结果目录已记录 |
| 本地与远端测试是否链接最终 commit？ | D 盘正式目录基线为 263 项/178.084 s，0 跳过，JSON 业务 E2E 通过；远端旧 head 的 Linux/Windows 已通过，当前本地分支推送后的 CI 仍待复核 |
| 发布包是否不含模型、数据、日志、秘密和输出？ | 是：最终标准路径 ZIP 经 allowlist、内容清单、Git blob、秘密/路径扫描与 exact clean-room 复核；相邻验证记录保存边界 |
| 是否包含精确第三方 notices？ | pypdfium2/PDFium 的 19 份精确 notices 已包含并逐项校验；其他二进制传递依赖的完整许可证审计仍未完成 |
| 所有剩余缺口是否对外可见？ | 是；以当前清单和合规表为准 |

只有这些问题得到真实回答，并完成用户外部操作后，才能称为“比赛提交完成”。
