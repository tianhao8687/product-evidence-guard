# 比赛要求对照表

最后复核：2026-08-05

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
| 真实图片完整商业演示 | **已验证（流程；录屏待用户）** | 真实图＋受控文档形成 4 candidates、1 个 strong/block、0 errors；用户确认一致标签重量、拒绝冲突物流重量，首次导出 confirmed 1/stale 0，源图哈希变化后导出 confirmed 0/stale 1；决定阶段未调用模型、未发起需要联网的操作、无新 Qoder 云端消息，本轮未做抓包 |
| 真实客户数据集评测 | **待用户操作** | 需要授权且匿名的商品资料 |
| 不承诺比赛入选或监管合规 | **已完成** | `LIMITATIONS.md` 明确排除保证 |

## 2. 本地 AI 与 OpenVINO

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 模型固定为 `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` | **已完成** | 清单、下载器和文档使用同一 ID |
| 官方来源、许可证、大小和文件树 | **已验证** | 官方模型卡和文件树已复核 |
| OpenVINO Runtime/GenAI 路线 | **已完成** | 固定版本与 API 合同已记录 |
| 严格两步视觉转录与字段映射 | **已完成** | `qwen_vl_reader.py`、`model_output_schema.py`；仅用于 `qwen_deep_fallback` |
| OpenVINO OCR + 分路安全处理 | **已验证** | 普通商品事实路由可进入一次 Qwen 紧凑复核；`observation-only` 失败关闭且不调用 Qwen；8 张真实样本完成 OCR 路由，电池误读被拦截并由本地 Qwen 修正 |
| 两步 reader 保留为深度兜底 | **已验证** | 紧凑复核 schema 有效空结果不重复 deep；只有 schema 非法或调用错误进入两步路径，原有真实单图仍可从主 engine 生成 `visual-transcription.json` 和候选 |
| 精确 8B 正式快照下载 | **已验证** | revision `f3d0bc7`；26 payload `5,462,515,610` bytes，含元数据目录 `5,462,526,140` bytes |
| 精确模型在目标硬件加载 | **已验证** | 最终离线工件：Ryzen 7 7800X3D，CPU，3.6064 s |
| 至少一张真实商品图推理 | **已验证** | 最终工件 `status=passed`，701×1097 公有领域标签图，1 个 pending 候选 |
| CPU 执行 | **已验证** | 最终内层分析 57.1824 s，外层 `analyze` 61.895 s |
| 适用 Intel GPU 执行 | **未完成** | 本机 GPU 全名为 NVIDIA RTX 5070，不作为 Intel GPU 证据 |
| 显式设备 → 适用 Intel GPU → CPU 自动选择 | **已完成** | 默认 AUTO；检查 `FULL_DEVICE_NAME`，校验显式设备并拒绝 NPU |
| NPU 支持 | **未完成** | 没有该精确模型的模型特定依据和实测 |
| 离线环境变量下本地推理 | **已验证** | `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`OPENVINO_TELEMETRY_DISABLED=1` 下成功 |
| 本地进程 TCP 状态采样 | **已验证** | 35 个约 100 ms 周期未观察到外部 TCP；不是防火墙、数据包、DNS、UDP 或 air-gap 证明 |
| 防火墙阻断或完整抓包审计 | **未完成** | 有限 TCP 状态采样不能声明零外连 |
| 无云模型/OCR 回退 | **已完成** | RapidOCR、OpenVINO 与 Qwen 均在本机；OCR 不可用时普通商品事实路由只回到本地 Qwen，`observation-only` 失败关闭且不调用 Qwen |
| confidence 不冒充校准概率 | **已完成** | 保留 `model_self_assessment` 来源 |

## 3. Skill 与 Qoder 打包

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 根目录 `SKILL.md` 与 YAML frontmatter | **已验证** | `test_submission_contract` 校验 name、description 长度、中英文触发词、唯一入口、`--continue`、离线与人工确认边界 |
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
| 中文自动触发 | **已验证** | 原任务 5 条指定原文在 5 个新会话中 5/5 自动选择 Skill，均只执行一次 `status`，退出码 0 |
| 英文自动触发 | **已验证** | 原任务 3 条指定原文在 3 个新会话中 3/3 自动选择 Skill，均只执行一次 `status`，退出码 0 |
| 手动 `/local-product-evidence-guard` | **已验证** | status 范围：手动激活并通过唯一入口返回退出码 0 |
| Qoder 账号登录 | **已验证** | 2026-08-04 CLI 登录成功；不保存认证材料 |
| Qoder 只调用 `scripts\run.ps1` | **已验证** | 匿名 sidecar 范围：status/analyze/confirm/reject/export 均观察到唯一入口 |
| Qoder 确认、拒绝、导出和 stale | **已验证** | 匿名 sidecar 范围：人工 ID/理由、安全拦截、前后两次 export 与 stale 均有脱敏证据 |
| Qoder 真实图片 Qwen 工作流 | **已验证** | 公开领域标签图冷/热/缓存三轮退出码 0；真实 Qwen 路线，候选保持 pending |
| Qoder 真实图片＋受控文档冲突 | **已验证（决定阶段走本地公共入口）** | 分析为 4 candidates、1 个 strong/block、0 errors；用户决定、两次导出和源图哈希变化后的 stale 闭环通过，随后恢复原哈希；热模型 15.141 s、文件复查 0.0241 s；决定阶段未调用模型、未发起需要联网的操作、无新 Qoder 云端消息，本轮未做抓包 |
| Qoder 模型常驻与缓存 | **已验证** | 冷分析 26.7585 s、常驻热模型重新识图 11.9164 s、未变化文件复查 0.0177 s |
| Qoder 下载/续传 | **已验证** | 公共入口范围：tiny Hub 真实 partial、exit 3、后台继续与第二次 `--continue` exit 0；更新后安装副本由 Qoder 宿主 status exit 0 |
| Qoder UTF-8 与本地视觉后端 | **已验证** | 中文路径/理由无乱码；真实图记录本地 RapidOCR/OpenVINO Qwen 后端与 CPU 设备 |

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
| Windows Named Pipe smoke | **已验证** | `tests/test.ps1` 的稳定 JSON、完整业务闭环、status 与 shutdown 通过 |
| auth 不匹配、崩溃恢复、重复启动和 timeout | **已验证** | 最终 265 项本地回归覆盖 |
| confirm/reject/export/stale 完整业务 E2E | **已验证** | `run.ps1`/Named Pipe 的 deterministic sidecar 闭环已通过；真实标签＋受控文档也完成用户决定、导出 1→0 与 stale 0→1 的本地公共入口闭环，完整 Qoder 录屏另列 |
| 模型跨多次请求复用 | **已验证** | 常驻 worker 热请求 `model_reused=true`、加载 0 s |

## 5. 环境与模型获取

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 仓库内 Python 3.11 环境 | **已验证** | Windows PowerShell 5.1 干净重建 Python `3.11.13` |
| 优先并固定 `uv` | **已验证** | `uv 0.8.4` Windows ZIP SHA-256 固定并实测 |
| 完整运行/构建依赖固定版本与分发哈希 | **已验证** | `requirements.lock` 含 35 个 hash-locked 包，正式发布/Qoder 必备 |
| 可重复安装 stamp | **已完成** | 需求哈希和 Python 版本检查 |
| `install-env.ps1` 干净安装实测 | **已验证** | `-Force` 成功；36 个已安装包（含本项目）`pip check` 兼容；二次执行快速跳过 |
| 本地 editable build 供应链边界 | **已验证** | `--no-build-isolation --no-index` 成功，未临时解析未锁定构建后端 |
| 下载进入 `.partial` | **已完成** | 单独 partial 目录 |
| `--continue` 与 pending request | **已验证** | tiny Hub 真实部分下载；首调用 0.462 s/exit 3，后台继续，第二次 exit 0 |
| 必需文件、非空和 LFS pointer 校验 | **已完成** | 结构检查覆盖配置文件 |
| 完成后才原子提升 | **已完成** | 正式目录提升逻辑存在 |
| 中断后续传实测 | **已验证** | 公共入口 partial→后台 worker→status exit 3→`--continue`→原子提升；正式 8B 前后 26/26 哈希一致 |
| revision 固定 | **已验证** | 实际快照 revision `f3d0bc7` |
| 密码学 checksum manifest | **已验证** | 26 payload SHA-256 清单已生成；尚未签名 |
| 下载前磁盘和内存预检 | **已验证** | 公共下载 worker 强制检查剩余 payload＋512 MiB 余量和 `mem_need_gb`；本机实测磁盘可用 61,420,417,024 B/需要 5,996,870,912 B，内存可用 21,016,993,792 B/需要 16,000,000,000 B |

## 6. 文件处理与证据图

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| TXT/Markdown/CSV/JSON 确定性解析 | **已完成** | 主分派存在 |
| DOCX/XLSX/文字版 PDF 确定性解析 | **已完成** | 本地可选解析器存在 |
| 相对定位与 SHA-256 | **已完成** | SourceBlock 保留 |
| OCR sidecar 兼容 | **已完成** | 证据保留图片、bbox 和 confidence |
| pypdfium2 扫描页渲染 | **已完成** | 无文字页临时渲染并重定向证据 |
| 文件、页数、像素、文字和数量上限 | **已完成** | 代码边界存在 |
| PDF 负向子矩阵 | **已验证（有限）** | 本地实际生成空白、损坏、加密和 101 页 PDF，并覆盖文件/数量/像素边界；来源 hash 不变。未扩大为完整 fuzz、真实扫描 OCR 质量或大规模压力结论 |
| PDF 临时页清理 | **已验证（代表性失败）** | visual reader、ROI 和 partial PNG 故障注入均清理临时目录且不删除来源；未声称穷尽所有失败路径 |
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
| 公共入口确认/失效完整 E2E | **已验证** | confirm/reject/export/源变更/reanalyze/stale 与旧候选拒绝均通过 |
| JSON、Markdown、HTML 报告 | **已完成** | writers 存在 |
| HTML 转义 | **已完成** | 动态值经过转义 |
| 报告完整显示确认/stale | **已验证** | E2E 校验 JSON、Markdown、HTML 与审计 JSONL |
| 真实 `visual-transcription.json` | **已验证** | 已保留成功和安全拒绝两类工件 |
| 文档内视觉与 `document-visuals.json` | **已验证** | synthetic DOCX/XLSX/PDF OCR fast、单次请求内跨文件视觉缓存、XLSX 原生图表及 Raspberry Pi/TI mixed PDF ROI/observations 均有脱敏摘要；观察不是 FactCandidate，不声称曲线逐点数字化 |
| `observation-only` 安全门 | **已验证** | 只有重量（含净重/毛重）、尺寸、数量、型号、材质、颜色可在明确标签、单值、高置信度且单位安全时生成候选；电气/容量、低置信度、未知单位、同一 OCR 行多值和 input/output 混合方向仅保留 observations，不调用 Qwen |

## 8. 测试与 CI

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 核心单元测试文件 | **已完成** | 测试文件存在 |
| Qwen Schema/输出测试文件 | **已完成** | 接受/拒绝、schema 有效空复核不重复 deep，以及非法/错误进入深度兜底路径存在 |
| 确认、下载、协议、PDF 与报告测试文件 | **已完成** | 对应测试文件存在 |
| `tests/test.ps1` | **已完成** | 文件存在；结果另行验证 |
| `tests/test-real-model.ps1` | **已完成** | opt-in 脚本存在 |
| 最终完整 Python 测试执行 | **已验证** | 2026-08-25 D 盘正式目录 265 项通过，84.114 s，0 跳过 |
| Windows PowerShell smoke | **已验证** | 265 项、中文空格、compileall、deterministic、incremental、完整确认闭环、Pipe status/shutdown 通过；无效路径和旧候选退出码 1 |
| Linux CI workflow | **已完成** | Ubuntu compile、unit、demo smoke 配置存在 |
| Windows CI workflow | **已完成** | workflow 已配置 Windows PowerShell/Python 3.11 job |
| commit `5a4fad8` 远端 GitHub Actions | **已验证** | 临时绿色 |
| 最新 GitHub Actions | **未完成** | 本地最终回归已通过；最新远端 CI 状态以 PR Actions 为准，不预先声称通过 |
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
| 真实 PDF/DOCX/XLSX 原生解析 | **已验证** | 4 份官方公开文档全部解析；1,491 块、359,063 字符、0 错误；修复后 3 条 pending、0 阻断冲突 |
| 文档内图片、原生 XLSX 图表与 mixed PDF | **已验证** | Office 内嵌图片 2/2、mixed PDF 1/1、原生图表 1/1；官方 mixed PDF 3 页登记并安全路由；单次冷/热/缓存与 ROI 摘要位于 `docs/evidence/`。窄白名单、歧义失败关闭和 schema 有效空不重复 deep 是计时后无模型回归边界，未重启模型复测 |
| 真实评测数据集 | **待用户操作** | 需要授权匿名样本 |
| Synthetic 准确率/F1/漏检/编造 | **已验证** | 原始计数已保留；不等于真实业务准确率 |
| 30 图逐图分布 | **已验证** | 中位数 75.991379 s，p90 85.350259 s |
| 重复独立冷启动 | **已验证** | 同一真实标签图安全关闭后独立运行 3 次；分析均值 26.7046 s、范围 25.5298–27.6312 s |
| 无变化增量节省 | **已验证** | 10/10 复用，节省 0.0052551 s（8.9149%） |
| 单文件变化增量 | **已验证（功能）** | 已完成只改 1 份说明书、复用另外 2 份的功能 E2E；未冻结独立性能值，不把它写成性能结论 |
| 离线环境变量结果 | **已验证** | 三个离线/遥测环境变量下完成真实本地推理 |
| 本地 TCP 状态观察 | **已验证** | 35 周期未观察到外部 TCP；严格边界见 `docs/evidence/performance-network-validation-20260805.md` |
| 防火墙/数据包/DNS/UDP 审计 | **未完成** | TCP 状态采样不能证明零外连 |
| 失败案例保留 | **已验证** | 截断拒绝、未解析单位和最终成功均保留 |

## 10. 隐私、安全、许可证与发布

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| `PRIVACY.md` | **已完成** | 本地数据流、输出和网络边界 |
| `SECURITY.md` | **已完成** | 威胁边界、注入和已知缺口 |
| `THIRD_PARTY_NOTICES.md` | **已完成** | 模型和直接依赖声明索引 |
| 项目 MIT 与第三方许可证分开 | **已完成** | 文档明确区分 |
| pypdfium2/PDFium 路线 | **已完成** | BSD-3-Clause/Apache-2.0 与 notice 义务已记录 |
| 精确 PDFium build notices | **已验证** | 从实际 `pypdfium2 5.12.1` Windows x64 wheel 收集 19 份 notices 并生成索引 |
| hash-required 完整依赖锁 | **已验证** | 35 个锁定包；不等于完整 SBOM 或许可证包 |
| CycloneDX 1.5 SBOM 与 35 包许可证元数据 | **已验证** | 35/35 distribution 已纳入机器可读工件 |
| 漏洞扫描与完整二进制许可证审计 | **未完成** | SBOM、元数据和 PDFium notices 不能替代全部传递二进制审计 |
| 离线环境变量推理 | **已验证** | 三个离线/遥测环境变量下成功 |
| 网络外连审计 | **未完成** | 已有有限 TCP 状态观察；防火墙、数据包、DNS 与 UDP 审计尚未执行 |
| 打包脚本 | **已完成** | `scripts/package-release.ps1` 存在 |
| 发布 allowlist 与链接防护合同 | **已验证** | 包含必备 lock；排除模型/数据/日志；拒绝 reparse/hardlink |
| 版本 ZIP 与 SHA-256 | **已验证** | 最终标准路径 ZIP 已生成，相邻 `.sha256` 独立重算匹配；相邻 verification.json 保存精确归档哈希与边界 |
| 发布身份与校验权威 | **已验证** | ZIP 内 `manifest.json.commit`、manifest 列出的全部 Git 来源文件和 Git object 逐项核验，mismatch 0；验证记录位于归档外避免自引用 |
| 干净目录解压 smoke | **已验证** | 最新标准路径 ZIP 在全新目录使用 Python 3.11.13 与 36 个已安装包；265 项、0 跳过，compileall、业务 E2E、Pipe status/shutdown 均通过。精确 commit、SHA-256 与耗时只写入归档相邻 verification.json，避免归档自引用 |

## 11. 文档、文章、演示与外部提交

| 要求 | 状态 | 证据或缺口 |
|---|---|---|
| 至少四张 Mermaid 架构图 | **已完成** | `ARCHITECTURE.md` 当前 5 张 |
| 中文用户指南 | **已完成** | `USER_GUIDE.md` |
| 模型、Benchmark、限制文档 | **已完成** | 对应文档 |
| Qoder 验证记录 | **已完成** | 安装、发现、登录、中英文触发、匿名 sidecar 业务闭环及真实图片性能已实测；剩余缺口已记录 |
| 3 分钟和 5 分钟演示脚本 | **已完成** | `DEMO_SCRIPT.md` |
| 中文技术文章草稿 | **已完成** | `ARTICLE_DRAFT.md`；未完成项保留占位 |
| 提交清单 | **已完成** | `SUBMISSION_CHECKLIST.md` |
| 演示视频 | **待用户操作** | 需要录制并发布 |
| Qoder 截图 | **未完成** | 已保存 Skill 发现页和脱敏 IDE 分析/安全门画面；真实图文确认、导出与 stale 完整截图组仍待录制 |
| 技术文章发布 | **待用户操作** | 需要平台账号和发布决定 |
| ModelScope Skill 发布 | **待用户操作** | 需要平台账号和发布决定 |
| 比赛标签和最终 URL | **待用户操作** | 外部发布/提交 |
| 分支 `codex/competition-ready-v1-sanitized` | **已完成** | 当前脱敏分支已检查；原始未脱敏截图历史不在此分支可达历史中 |
| Draft PR | **已验证** | [GitHub PR #2](https://github.com/tianhao8687/product-evidence-guard/pull/2) 已从最终脱敏分支创建，未合并 |
| 临时远端 CI | **已验证** | commit `5a4fad8` 绿色 |
| 最新远端 CI | **未完成** | 本地最终回归已通过；实时状态以 PR Actions 为准 |
| 比赛表单提交 | **待用户操作** | 用户拥有的外部动作 |

## 发布门槛

下列事项没有达到 **已验证** 或合理的 **待用户操作** 前，不能称为“参赛版全部
完成”：

- 真实图文决定、导出和 stale 闭环的完整 Qoder 录屏；
- 防火墙阻断或抓包网络审计；
- Qoder 完整 IDE 截图组；
- 最新 PR Actions 状态复核；
- 可追溯到提交清单的原始证据。
