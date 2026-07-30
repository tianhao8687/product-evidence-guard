# 比赛要求对照表

最后复核：2026-07-30

本表严格区分“工件存在”和“真实执行”。状态列只使用以下四个值：

| 状态 | 含义 |
|---|---|
| **已完成** | 所需代码、文档或工件已经存在并检查；不自动代表运行成功 |
| **已验证** | 精确流程已经真实执行，且有保留证据支撑结论 |
| **待用户操作** | 需要用户账号、授权资料、发布决定或外部提交 |
| **未完成** | 缺失、仍在集成，或没有执行并保留证据 |

不能因为测试文件或源代码存在，就把状态从 **已完成** 提升为 **已验证**。

## 1. 场景价值与范围

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 明确的真实生产力问题与用户 | **已完成** | 面向商品运营、供应链、设计和生产前事实核验 |
| 文件夹级多来源工作流 | **已完成** | 发现、解析、分组和报告链路存在 |
| 证据优先，而不是通用总结 | **已完成** | 使用 `SourceBlock → FactCandidate → FactGroup` |
| 人工决定后才形成正式事实 | **已完成** | 确认状态与当前哈希导出代码存在 |
| 真实图片完整商业演示 | **未完成** | 单图模型已跑通，但文本+图片冲突、确认、导出、stale 全链路未留证 |
| 真实客户数据集评测 | **待用户操作** | 需要授权且匿名的商品资料 |
| 不承诺比赛入选或监管合规 | **已完成** | `LIMITATIONS.md` 明确排除保证 |

## 2. 本地 AI 与 OpenVINO

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 模型固定为 `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` | **已完成** | 清单、下载器和文档使用同一 ID |
| 官方来源、许可证、大小和文件树 | **已验证** | 官方模型卡和文件树已复核 |
| OpenVINO Runtime/GenAI 路线 | **已完成** | 固定版本与 API 合同已记录 |
| 严格两步视觉转录与字段映射 | **已完成** | `qwen_vl_reader.py`、`model_output_schema.py` |
| 两步 reader 接入主图片流程 | **已验证** | 真实单图从主 engine 生成 `visual-transcription.json` 和候选 |
| 精确 8B 正式快照下载 | **已验证** | revision `f3d0bc7`；26 payload `5,462,515,610` bytes，含元数据目录 `5,462,526,140` bytes |
| 精确模型在目标硬件加载 | **已验证** | 最终离线工件：Ryzen 7 7800X3D，CPU，3.6064 s |
| 至少一张真实商品图推理 | **已验证** | 最终工件 `status=passed`，701×1097 公有领域标签图，1 个 pending 候选 |
| CPU 执行 | **已验证** | 最终内层分析 57.1824 s，外层 `analyze` 61.895 s |
| 适用 Intel GPU 执行 | **未完成** | 本机 GPU 全名为 NVIDIA RTX 5070，不作为 Intel GPU 证据 |
| 显式设备 → 适用 Intel GPU → CPU 自动选择 | **已完成** | 默认 AUTO；检查 `FULL_DEVICE_NAME`，校验显式设备并拒绝 NPU |
| NPU 支持 | **未完成** | 没有该精确模型的模型特定依据和实测 |
| 离线环境变量下本地推理 | **已验证** | `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`OPENVINO_TELEMETRY_DISABLED=1` 下成功 |
| 防火墙阻断或抓包审计 | **未完成** | 没有网络阻断或流量观察记录，不能声明零外连 |
| 无云模型/OCR 回退 | **已完成** | 项目设计只有本地解析器和本地模型路线 |
| confidence 不冒充校准概率 | **已完成** | 保留 `model_self_assessment` 来源 |

## 3. Skill 与 Qoder 打包

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 根目录 `SKILL.md` 与 YAML frontmatter | **已完成** | 文件存在；最终发布前仍需一致性检查 |
| `info.json` 环境、模型、退出码和文件清单 | **已完成** | 根清单存在 |
| `meta.json` 展示信息与 use cases | **已完成** | 根元数据存在 |
| 用户级 Qoder 官方路径 | **已验证** | 官方文档为 `~/.qoder/skills/{name}/SKILL.md` |
| 项目级 Qoder 官方路径 | **已验证** | 官方文档为 `.qoder/skills/{name}/SKILL.md` |
| 项目打包与 Qoder 必选格式分开说明 | **已完成** | `QODER_VALIDATION.md` 已说明 |
| 安装器不静默覆盖 | **已完成** | 更新需显式选择，更新前备份 |
| 安装器不支持旧 `.lingma` 目标 | **已完成** | 只有 User/Project `.qoder\skills` |
| 安装器排除模型、日志、输出和客户资料 | **已验证** | allowlist 与实际运行合同测试通过；`requirements.lock` 为必备文件 |
| 官方 npm Qoder CLI 1.1.8 隔离安装 | **已验证** | 本机安装并执行版本对应 CLI |
| 用户级 Qoder Skill 安装 | **已验证** | 安装到当前官方用户级路径 |
| Qoder 发现 Skill | **已验证** | `qodercli skills list` 显示 `Enabled` |
| 中文自动触发 | **待用户操作** | 无 transcript/截图 |
| 英文自动触发 | **待用户操作** | 无 transcript/截图 |
| 手动 `/local-product-evidence-guard` | **待用户操作** | 无 transcript/截图 |
| Qoder 账号登录 | **待用户操作** | `qodercli status` 显示 `Account: Not logged in` |
| Qoder 只调用 `scripts\run.ps1` | **待用户操作** | 需要观察宿主调用 |
| Qoder 下载、确认、导出和 stale | **待用户操作** | 需要完整真实会话 |
| Qoder UTF-8 与无云回退 | **待用户操作** | 需要中文路径和网络观察证据 |

## 4. 唯一入口与 Client/Server

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 固定公开入口 `scripts\run.ps1` | **已完成** | 包装脚本存在 |
| 短时 `scripts/client.py` | **已完成** | 参数、模型准备、启动和一次响应代码存在 |
| 常驻 `scripts/server.py` | **已完成** | dispatcher 与生命周期代码存在 |
| Windows Named Pipe 与一致 authkey | **已完成** | 使用 `AF_PIPE` 和共享配置 |
| UTF-8 JSON 协议与 1 MiB 上限 | **已完成** | 序列化与长度校验存在 |
| `status/analyze/confirm/reject/export/shutdown` | **已完成** | dispatcher 暴露操作 |
| 退出码 `0/1/2/3` | **已完成** | 协议、client 和 `info.json` 一致 |
| PID、启动标记、重复启动和 stale 恢复 | **已完成** | 精确进程身份代码存在 |
| 约 300 秒空闲退出 | **已完成** | 配置和服务状态包含 timeout |
| Windows 公开入口真实模型 analyze | **已验证** | 冷/热请求均成功并生成候选 |
| 推理中 Named Pipe 并发响应 | **已验证** | 真实 4 图推理期间，`run.ps1 status` 经 Pipe 在 0.438 s 返回 `running` 和 `available_operations`，无 fallback 字段 |
| Windows Named Pipe smoke | **已验证** | `tests/test.ps1` 的稳定 JSON、status 与 shutdown 通过 |
| auth 不匹配、崩溃恢复、重复启动和 timeout | **已验证** | 最终 123 项本地回归覆盖 |
| confirm/reject/export/stale 完整业务 E2E | **未完成** | 尚无最终公开入口 transcript |
| 模型跨多次请求复用 | **已验证** | 常驻 worker 热请求 `model_reused=true`、加载 0 s |

## 5. 环境与模型获取

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 仓库内 Python 3.11 环境 | **已验证** | Windows PowerShell 5.1 干净重建 Python `3.11.13` |
| 优先并固定 `uv` | **已验证** | `uv 0.8.4` Windows ZIP SHA-256 固定并实测 |
| 完整运行/构建依赖固定版本与分发哈希 | **已验证** | `requirements.lock` 含 27 个 hash-locked 包，正式发布/Qoder 必备 |
| 可重复安装 stamp | **已完成** | 需求哈希和 Python 版本检查 |
| `install-env.ps1` 干净安装实测 | **已验证** | `-Force` 成功；28 个已安装包（含本项目）`pip check` 兼容；二次执行快速跳过 |
| 本地 editable build 供应链边界 | **已验证** | `--no-build-isolation --no-index` 成功，未临时解析未锁定构建后端 |
| 下载进入 `.partial` | **已完成** | 单独 partial 目录 |
| `--continue` 与 pending request | **已完成** | client/downloader 状态合同存在 |
| 必需文件、非空和 LFS pointer 校验 | **已完成** | 结构检查覆盖配置文件 |
| 完成后才原子提升 | **已完成** | 正式目录提升逻辑存在 |
| 中断后续传实测 | **未完成** | 无完整 Windows 结果 |
| revision 固定 | **已验证** | 实际快照 revision `f3d0bc7` |
| 密码学 checksum manifest | **已验证** | 26 payload SHA-256 清单已生成；尚未签名 |
| 下载前磁盘和内存预检 | **未完成** | 仍需在公共流程强制并留证 |

## 6. 文件处理与证据图

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| TXT/Markdown/CSV/JSON 确定性解析 | **已完成** | 主分派存在 |
| DOCX/XLSX/文字版 PDF 确定性解析 | **已完成** | 本地可选解析器存在 |
| 相对定位与 SHA-256 | **已完成** | SourceBlock 保留 |
| OCR sidecar 兼容 | **已完成** | 证据保留图片、bbox 和 confidence |
| pypdfium2 扫描页渲染 | **已完成** | 无文字页临时渲染并重定向证据 |
| 文件、页数、像素、文字和数量上限 | **已完成** | 代码边界存在 |
| 分阶段硬超时与精确 worker 终止 | **已验证** | 模型加载、每个文件、最终化各有 300 s 心跳边界；超时只终止该服务创建并跟踪的 worker |
| 客户端整请求上限 | **已完成** | 整个文件夹请求最长 1 h，不与每阶段 300 s 边界混用 |
| 单位归一与净重/毛重隔离 | **已完成** | 确定性 normalization |
| 六类冲突等级 | **已完成** | graph 实现 |
| 注入文字只作数据 | **已完成** | Prompt/Schema 禁止命令和额外字段 |
| 真实模型对抗注入测试 | **未完成** | mock/schema 不能证明真实模型 |
| 文件级增量缓存 | **已完成** | engine signature 与文件哈希控制 |
| 来源变化/删除后证据失效 | **已完成** | 重建图排除旧候选 |

## 7. 确认、审计与报告

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| `pending/confirmed/rejected/stale` | **已完成** | 状态与 reconciliation 代码存在 |
| confirm/reject 需要候选、会话和理由 | **已完成** | 命令校验存在 |
| `confirmation-audit.jsonl` | **已完成** | 决定与 stale 事件追加写入 |
| 只导出当前哈希的已确认候选 | **已完成** | export 过滤存在 |
| 来源变化使旧决定 stale | **已完成** | reconciliation 实现 |
| 公共入口确认/失效完整 E2E | **未完成** | 无最终 Windows transcript |
| JSON、Markdown、HTML 报告 | **已完成** | writers 存在 |
| HTML 转义 | **已完成** | 动态值经过转义 |
| 报告完整显示确认/stale | **未完成** | 需要最终目视和字段检查 |
| 真实 `visual-transcription.json` | **已验证** | 已保留成功和安全拒绝两类工件 |

## 8. 测试与 CI

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 核心单元测试文件 | **已完成** | 测试文件存在 |
| Qwen Schema/输出测试文件 | **已完成** | 接受/拒绝路径存在 |
| 确认、下载、协议、PDF 与报告测试文件 | **已完成** | 对应测试文件存在 |
| `tests/test.ps1` | **已完成** | 文件存在；结果另行验证 |
| `tests/test-real-model.ps1` | **已完成** | opt-in 脚本存在 |
| 最终完整 Python 测试执行 | **已验证** | 123 项通过，50.978 s |
| Windows PowerShell smoke | **已验证** | 内部 123 项 49.864 s；中文空格、compileall、deterministic、incremental、Pipe status/shutdown JSON smoke 通过，无效路径退出码 1 |
| Linux CI workflow | **已完成** | Ubuntu compile、unit、demo smoke 配置存在 |
| Windows CI workflow | **已完成** | workflow 已配置 Windows PowerShell/Python 3.11 job |
| commit `5a4fad8` 远端 GitHub Actions | **已验证** | 临时绿色 |
| 最终收尾提交的 GitHub Actions | **未完成** | 本地最终回归已通过；提交尚未推送，CI 待推送后记录 |
| 普通 CI 不下载 8B | **已完成** | 当前 workflow 不下载模型 |
| 独立安全测试或 fuzzing | **未完成** | 未执行 |

## 9. Benchmark 与验证证据

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 可复现 Benchmark 规范 | **已完成** | `BENCHMARK.md` 定义数据、指标和原始布局 |
| Benchmark runner | **已验证** | commit `06f8360` 已真实执行，状态 `completed` |
| 最终离线真实图功能工件 | **已验证** | `test-real-model-release-20260730` 状态 `passed`；单次功能证据，不是准确率 |
| 真实 CPU 冷/热公开入口 | **已验证** | 冷 3.7021/52.1827 s；热 0/30.8367 s；模型复用 |
| 峰值进程内存单次记录 | **已验证** | Working Set 约 10.83 GiB，Private Bytes 约 6.96 GiB |
| 完整 synthetic 数据集运行 | **已验证** | 30 图成功、10 文档无错误，前后 dataset hash 一致 |
| 真实评测数据集 | **待用户操作** | 需要授权匿名样本 |
| Synthetic 准确率/F1/漏检/编造 | **已验证** | 原始计数已保留；不等于真实业务准确率 |
| 30 图逐图分布 | **已验证** | 中位数 75.991379 s，p90 85.350259 s |
| 重复独立冷启动 | **未完成** | 冻结运行只记录一次加载与冷总计 |
| 无变化增量节省 | **已验证** | 10/10 复用，节省 0.0052551 s（8.9149%） |
| 单文件变化增量 | **未完成** | 不在冻结结果中 |
| 离线环境变量结果 | **已验证** | 三个离线/遥测环境变量下完成真实本地推理 |
| 防火墙/抓包网络审计 | **未完成** | 未阻断网络或观察流量 |
| 失败案例保留 | **已验证** | 截断拒绝、未解析单位和最终成功均保留 |

## 10. 隐私、安全、许可证与发布

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| `PRIVACY.md` | **已完成** | 本地数据流、输出和网络边界 |
| `SECURITY.md` | **已完成** | 威胁边界、注入和已知缺口 |
| `THIRD_PARTY_NOTICES.md` | **已完成** | 模型和直接依赖声明索引 |
| 项目 MIT 与第三方许可证分开 | **已完成** | 文档明确区分 |
| pypdfium2/PDFium 路线 | **已完成** | BSD-3-Clause/Apache-2.0 与 notice 义务已记录 |
| 精确 PDFium build notices | **未完成** | 发布前从实际 wheel 收集 |
| hash-required 完整依赖锁 | **已验证** | 27 个锁定包；不等于完整 SBOM 或许可证包 |
| SBOM、漏洞扫描和二进制许可证包 | **未完成** | 发布者仍需生成与盘点 |
| 离线环境变量推理 | **已验证** | 三个离线/遥测环境变量下成功 |
| 网络外连审计 | **未完成** | 防火墙阻断与抓包尚未执行 |
| 打包脚本 | **已完成** | `scripts/package-release.ps1` 存在 |
| 发布 allowlist 与链接防护合同 | **已验证** | 包含必备 lock；排除模型/数据/日志；拒绝 reparse/hardlink |
| 版本 ZIP 与 SHA-256 | **未完成** | 当前无 release 工件 |
| 发布身份与校验权威 | **已完成** | ZIP 内 `manifest.json.commit` + 同目录 `.sha256`；manifest 不自引用哈希 |
| 干净目录解压 smoke | **未完成** | 尚未执行 |

## 11. 文档、文章、演示与外部提交

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 至少四张 Mermaid 架构图 | **已完成** | `ARCHITECTURE.md` 当前 5 张 |
| 中文用户指南 | **已完成** | `USER_GUIDE.md` |
| 模型、Benchmark、限制文档 | **已完成** | 对应文档 |
| Qoder 验证记录 | **已完成** | CLI 安装与发现已实测；账号未登录和业务调用缺口已记录 |
| 3 分钟和 5 分钟演示脚本 | **已完成** | `DEMO_SCRIPT.md` |
| 中文技术文章草稿 | **已完成** | `ARTICLE_DRAFT.md`；未完成项保留占位 |
| 提交清单 | **已完成** | `SUBMISSION_CHECKLIST.md` |
| 演示视频 | **待用户操作** | 需要录制并发布 |
| Qoder 截图 | **待用户操作** | 需要目标 Qoder 会话 |
| 技术文章发布 | **待用户操作** | 需要平台账号和发布决定 |
| ModelScope Skill 发布 | **待用户操作** | 需要平台账号和发布决定 |
| 比赛标签和最终 URL | **待用户操作** | 外部发布/提交 |
| 分支 `codex/competition-ready-v1` | **已完成** | 当前分支已检查 |
| Draft PR | **已验证** | [GitHub PR #1](https://github.com/tianhao8687/product-evidence-guard/pull/1) 已创建，未合并 |
| 临时远端 CI | **已验证** | commit `5a4fad8` 绿色 |
| 最终收尾 CI | **未完成** | 本地最终回归已通过；待提交推送后记录 |
| 比赛表单提交 | **待用户操作** | 用户拥有的外部动作 |

## 发布门槛

下列事项没有达到 **已验证** 或合理的 **待用户操作** 前，不能称为“参赛版全部
完成”：

- 确认、导出和 stale 商业闭环；
- 防火墙阻断或抓包网络审计；
- 登录后的 Qoder 自动/手动触发和完整业务调用；
- 最终收尾提交的远端 CI；
- 发布 ZIP、SHA-256 和干净目录检查；
- 可追溯到提交清单的原始证据。
