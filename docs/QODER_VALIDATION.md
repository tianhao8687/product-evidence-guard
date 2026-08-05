# Qoder 验证

官方 Qoder 文档复核日期：2026-08-05

## 当前结论

**Qoder CLI 安装、Skill 发现、自动路由、手动触发，以及匿名 sidecar 业务闭环和
真实图片 Qwen3-VL/OpenVINO 冷启动、热启动、增量缓存已经验证。**

官方 npm Qoder CLI 1.1.8 已在隔离位置安装，用户级 Skill 安装成功；
`qodercli skills list` 实际将 `local-product-evidence-guard` 显示为
`Enabled`。2026-08-04 已完成 CLI 登录，并通过安装副本的固定入口成功执行两次
`status`：一次用 `/local-product-evidence-guard` 手动触发，一次用自然语言自动
路由。两次均返回 UTF-8 稳定 JSON 和进程退出码 `0`。用户随后明确授权匿名 demo
用于本次 Qoder 云端宿主测试，并亲自选择净重 candidate ID 与确认/拒绝理由；完整
业务闭环的每次 `run.ps1` 工具调用退出码均为 `0`。

2026-08-05 又按原任务指定原文，在 8 个全新无持久化会话中逐条验证 5 条中文和
3 条英文触发句。8/8 自动选择本 Skill，8/8 仅调用一次
`scripts/run.ps1 status`，Tool 与 CLI 退出码全部为 `0`，无乱码，也没有读取任何
商品文件或加载模型。端到端单次耗时 12.387–19.829 秒，均值 16.973 秒。
脱敏矩阵、预跑路径转义异常及严格边界见
[`assets/qoder/2026-08-05-auto-trigger-matrix.md`](assets/qoder/2026-08-05-auto-trigger-matrix.md)。

本机 Qoder 安装副本使用 `-RuntimeRoot <prepared-project-root>` 配置的本地桥接；
Qoder 仍只调用安装副本的 `scripts\run.ps1`，该入口复用已准备的 `.venv`、
模型和常驻服务状态，不复制模型，也不把绝对路径写入发布包。

2026-08-05 最终只读完整性复核还确认：CLI 为 1.1.8、目标 Skill 为 `Enabled`，
只有一个用户级安装，项目和工作区同名安装计数均为 0；运行时桥接指向当前源码根，
`SKILL.md`、`info.json`、`meta.json`、`scripts/run.ps1`、`scripts/client.py` 和
`scripts/server.py` 的源码/安装 SHA-256 全部一致。安装副本只执行 `status` 时
进程与稳定 JSON 退出码均为 0，没有读取商品文件或加载模型。脱敏、机器可读记录见
[`evidence/qoder-install-integrity-20260805.json`](evidence/qoder-install-integrity-20260805.json)。

安装器的 allowlist、必备文件、完整性、备份/回滚，以及
symlink/junction/reparse point/hardlink 失败关闭合同已进入 246 项最终本地回归。
这同时证明安装边界；真实图片、英文触发和公共入口下载续传均已补测，已有一张
脱敏 IDE 截图，完整确认、导出与 stale 截图组见下文的诚实缺口。

## 当前官方 Skill 格式

Qoder IDE 和 Qoder CLI 文档给出的路径是：

```text
用户级：
~/.qoder/skills/{skill-name}/SKILL.md

项目级：
.qoder/skills/{skill-name}/SKILL.md
```

Windows 用户级路径展开为：

```text
%USERPROFILE%\.qoder\skills\local-product-evidence-guard\SKILL.md
```

旧 `~/.lingma/skills/` 不是当前 Qoder 路径，安装器不得使用。
`~/.qoderwork/skills/` 属于独立的 QoderWork 产品，也不是 Qoder IDE/CLI 路径。

官方来源：

- [Qoder IDE Skills](https://docs.qoder.com/extensions/skills)
- [Qoder CLI Skills](https://docs.qoder.com/en/cli/Skills)
- [QoderWork Skills](https://docs.qoder.com/qoderwork/skills)

每个 Qoder Skill 需要一个带 YAML frontmatter 与 Markdown 指令的 `SKILL.md`：

```markdown
---
name: local-product-evidence-guard
description: 描述 Skill 做什么，以及 Qoder 应在何时使用。
---

# Local Product Evidence Guard

Instructions...
```

官方 frontmatter 约束：

- `name` 必填，只能用小写字母、数字和连字符，最多 64 个字符；
- `description` 必填，最多 1024 个字符；
- 可选资源可以包括 references、examples、scripts 和 templates；
- Skill 可以自动触发，也可以用 `/skill-name` 手动触发。

当前官方 Qoder 页面对于同名优先级存在矛盾：IDE 页面称项目级优先，CLI 完整
页面称用户级优先。因此，安装器应检测并警告同名重复安装，而不能依赖优先级。

## 项目打包与 Qoder 要求的区别

本项目中的文件含义如下：

| 文件或约定 | 含义 |
|---|---|
| `SKILL.md` | Qoder 要求的 Skill 入口 |
| `scripts/run.ps1` | Product Evidence Guard 计划中的稳定用户命令 |
| `info.json` | 项目/比赛的运行时与模型清单 |
| `meta.json` | 项目/比赛的显示元数据 |
| `requirements.lock` | 发布包与技术安装必备的 hash-required 完整依赖锁 |
| Client/Server 与 Named Pipe | 为大型本地模型保活的项目架构 |

`info.json`、`meta.json`、`run.ps1`、Client/Server 和 Named Pipe 是有用的项目/
比赛打包选择，但不是 Qoder 官方 Skill 格式的强制文件或字段，不能描述成 Qoder
自身要求。

## 安装检查计划

用户级和项目级应分别测试，不要同时保留两个同名副本。

### 用户级

```text
%USERPROFILE%\.qoder\skills\local-product-evidence-guard\
├── SKILL.md
├── requirements.lock
├── scripts\
└── SKILL.md 引用的其他项目资源
```

### 项目级

```text
<project>\.qoder\skills\local-product-evidence-guard\
├── SKILL.md
├── requirements.lock
├── scripts\
└── SKILL.md 引用的其他项目资源
```

安装器不得：

- 未经显式更新选择就覆盖现有同名目录；
- 缺少 `requirements.lock` 或清单必备文件时继续安装；
- 复制 `.venv`、模型权重、客户资料、日志、报告、缓存或凭据；
- 安装到 `.lingma`；
- 接受源、目标、祖先或 staging 中的 symlink/junction/reparse point/hardlink；
- 在 Qoder 尚未发现 Skill 时声称安装成功。

Qoder CLI 文档提供 `/skills reload` 刷新 Skills，并可用 `/skills` 查看发现列表。
Qoder IDE 文档建议重启 IDE，然后输入 `/` 检查已加载 Skills。

## 已执行的 CLI 安装与发现

2026-07-30 的本机记录：

| 项目 | 实际结果 |
|---|---|
| CLI 来源 | 官方 npm Qoder CLI |
| CLI 版本 | `1.1.8` |
| CLI 安装方式 | 隔离安装；未改写项目 Python 环境 |
| Skill 范围 | 用户级 |
| Skill 路径 | `%USERPROFILE%\.qoder\skills\local-product-evidence-guard\SKILL.md` |
| `qodercli skills list` | Skill 已发现，状态 `Enabled` |
| `qodercli status` | 2026-08-04 已登录；不保存账号详情或认证材料 |
| 最终安装完整性 | 6 个关键文件源码/安装 SHA-256 一致；用户级 1 个、项目/工作区 0 个同名安装；安装副本 `status` 退出码 0 |
| 安装器合同 | allowlist、完整性、备份/回滚、链接防护已通过本地回归 |
| 自动/手动调用 | 无数据 `status` 已分别通过；匿名 sidecar 业务闭环也已通过 |

## 2026-08-04 Qoder 实际调用记录

| 项目 | 实际结果 |
|---|---|
| CLI 版本 | `1.1.8` |
| CLI 登录 | 已登录；报告中不保存邮箱、token 或认证材料 |
| Skill 发现 | `local-product-evidence-guard [Enabled]` |
| 手动触发 | `/local-product-evidence-guard` 成功激活 Skill |
| 自动路由 | “我准备检查商品资料文件夹……”成功选择 `local-product-evidence-guard` |
| Skill 基础目录 | `%USERPROFILE%\.qoder\skills\local-product-evidence-guard` |
| 唯一公开入口 | 两次均调用安装副本的 `scripts\run.ps1 status` |
| 运行时桥接 | 入口复用已准备的项目运行时；原 `python_unavailable` 已消失 |
| 退出码与编码 | 两次工具退出码均为 `0`；中文稳定 JSON 无乱码 |
| 返回状态 | `ok=true`、`operation=status`、`status=stopped`；上次服务因 `idle_timeout` 关闭 |
| 本地资料读取 | 未读取任何商品文件、样本或报告 |
| 云端 OCR/VLM | 未调用；本次只做无数据状态检查 |
| 样本分析 | 用户明确授权后已完成匿名 sidecar 业务闭环 |

脱敏文字证据见
[`assets/qoder/2026-08-04-status-validation.md`](assets/qoder/2026-08-04-status-validation.md)
和
[`assets/qoder/2026-08-04-business-workflow.md`](assets/qoder/2026-08-04-business-workflow.md)，
真实图片与性能证据见
[`assets/qoder/2026-08-05-real-image-performance.md`](assets/qoder/2026-08-05-real-image-performance.md)，
英文自动路由证据见
[`assets/qoder/2026-08-05-english-auto-trigger.md`](assets/qoder/2026-08-05-english-auto-trigger.md)。
真实图片与受控文档强冲突证据见
[`assets/qoder/2026-08-05-real-image-controlled-documents.md`](assets/qoder/2026-08-05-real-image-controlled-documents.md)。
首张已脱敏的 Qoder IDE 分析与人工确认安全门画面见
[`assets/qoder/04-analysis-summary-redacted.png`](assets/qoder/04-analysis-summary-redacted.png)；截图已裁掉
账号侧栏。该图展示的是匿名 demo sidecar 与宿主安全门，不是真实图片 Qwen3-VL
结果；画面底部的宿主模型名称也不能当成本地 OCR/VLM 设备证据。完整确认、导出和
stale 截图仍需补录。

“Enabled”只证明 CLI 找到并启用了 Skill 元数据。本机已经另外保留实际工具调用、
人工决策、安全拦截、导出与 stale 证据，不能用 `Enabled` 代替这些业务结果。

## 必须执行的触发测试

每个提示词都应在全新或明确记录的会话中执行。

中文自动触发：

```text
检查这个商品资料文件夹。
核对包装图和参数表有没有冲突。
帮我找出商品重量到底是多少。
生图前先确认产品参数。
告诉我型号来自哪个文件。
```

英文自动触发：

```text
Verify this product material folder.
Find conflicting product specifications.
Check the packaging image against the datasheet.
```

手动触发：

```text
/local-product-evidence-guard
```

每次应记录：

- Qoder 版本及 IDE/CLI；
- 安装范围与解析后的完整 Skill 路径；
- Skill 是否出现在发现列表；
- 自动还是手动调用；
- Qoder 是否只调用唯一公开入口；
- 命令、退出码、stdout/stderr 编码；
- 来源文字是否始终只被当作数据；
- 是否发生云模型/OCR 回退；
- 报告路径和候选/确认状态；
- 任何批准对话框或失败。

## 必须验证的完整工作流

Qoder 完整测试必须覆盖：

1. 发现已安装 Skill；
2. 用代表性中英文表述自动触发；
3. 手动触发；
4. 模型缺失或只下载一部分时不报告成功；
5. 当项目工作流支持时恢复 pending 下载；
6. 分析同时包含文本证据和图片的目录；
7. 用大白话解释单位换算一致与强冲突；
8. 保持 `pending_human_confirmation`；
9. 带理由确认一个指定候选；
10. 拒绝另一个候选；
11. 只导出已确认且未 stale 的事实；
12. 修改来源文件，并显示旧决定变为 `stale`；
13. 正确显示中文文件名和输出，无 mojibake；
14. 证明没有使用云推理回退。

## 证据目录

实际验证后保留：

```text
docs/assets/qoder/
├── 01-skill-discovered.png
├── 02-auto-trigger.png
├── 03-manual-trigger.png
├── 04-analysis-summary-redacted.png
├── 05-conflict-explanation.png
├── 06-confirm-and-export.png
└── 07-stale-after-source-change.png
```

同时保留带时间和精确命令的脱敏文字 transcript。截图不得包含客户秘密、访问
token、个人账号细节或未脱敏的客户绝对路径。

## 状态矩阵

| 项目 | 状态 | 证据或缺口 |
|---|---|---|
| 当前官方路径 | **已验证** | 已从 Qoder 官方文档核实 |
| 当前 `SKILL.md` 格式 | **已验证** | 已从 Qoder 官方文档核实 |
| Qoder 要求与项目打包区分 | **已完成** | 本文已明确区分 |
| 官方 npm CLI 1.1.8 隔离安装 | **已验证** | 本机命令已执行 |
| 用户级安装 | **已验证** | 当前官方用户级目录 |
| 项目级安装 | **未完成** | 未执行；不是用户级发现证据的必要条件 |
| 最终安装完整性 | **已验证** | 脱敏 JSON 记录唯一安装、无重复项、运行时桥接、6 个关键文件哈希一致及安装副本 `status` 退出码 0 |
| 安装器技术合同 | **已验证** | 必备 lock、完整性、运行时桥接、回滚与链接防护进入 246 项回归 |
| Skill 发现 | **已验证** | `skills list` 显示 `Enabled`；Qoder“用户级 → 技能”页也直接显示 `local-product-evidence-guard`，脱敏截图见 `docs/assets/qoder/01-skill-discovered.png` |
| CLI 账号登录 | **已验证** | 2026-08-04 CLI 登录成功；不保存认证材料 |
| 中文自动触发 | **已验证** | 无数据自然语言请求自动选择 Skill 并执行 `status` |
| 英文自动触发 | **已验证** | 全新会话中未点名 Skill；Qoder 自动选择并只执行 `status`，退出码 0 |
| 手动 `/local-product-evidence-guard` | **已验证** | 手动激活并执行 `status` |
| 唯一公开入口行为 | **已验证** | Qoder 的 status/analyze/confirm/reject/export 均调用安装副本 `scripts\run.ps1` |
| 下载/续传行为 | **已验证（公共入口）** | tiny Hub 真实 partial；首调用 exit 3、后台继续、status exit 3、第二次 `--continue` exit 0；更新后 Qoder 宿主 status exit 0 |
| 分析/确认/拒绝/导出 | **已验证（匿名 sidecar）** | 用户亲自选择 ID/理由；导出 1 条，来源变化后导出 0 条、stale 1 条 |
| 增量与 stale | **已验证（匿名 sidecar）** | 只重算 `说明书.txt`，复用另外两份文件；confirmed 1→0、stale 0→1 |
| UTF-8 与中文路径 | **已验证（匿名 sidecar）** | 中文文件名、理由、报告和稳定 JSON 无乱码 |
| Qoder 真实图片 Qwen 工作流 | **已验证** | 真实公开领域标签图；冷/热/缓存三轮退出码 0，候选保持 pending |
| Qoder 真实图片＋受控文档冲突 | **待用户操作** | 分析已验证：4 candidates、1 个 strong/block、0 errors；用户尚未对本轮 ID 作决定 |
| 模型常驻与增量缓存 | **已验证** | 26.7585 s 冷分析、11.9164 s 热模型重识图、0.0177 s 文件复用 |
| 无云端 OCR/VLM 回退 | **部分验证** | 本地后端与路由证据完整；仍缺独立网络抓包/断网观察 |
| 脱敏 transcript | **已验证** | status 与业务闭环文字证据已保存 |
| 截图 | **部分验证** | 已保存 2 张无账号、无样本正文、无绝对路径的真实 Qoder 截图：用户级 Skill 发现页和 IDE 分析/人工确认安全门；完整确认、导出与 stale 截图组仍待录制 |

当前可以表述为“Qoder CLI 1.1.8 已通过中英文自动触发和手动触发本 Skill，匿名
sidecar 业务闭环、真实图片 Qwen3-VL/OpenVINO 冷/热/缓存、真实图＋受控文档强
冲突分析和公共入口下载续传验证通过，并已有一张脱敏 Qoder IDE 结果截图”。本轮
真实图文决定闭环仍等待用户，不能扩大成“完整 IDE 截图组或独立断网审计已经通过”。

补充复测：Qoder CLI query 必须放在 `--` 分隔符后，避免提示词内的
`--output`/`--deterministic-only` 被 CLI 参数解析吞掉。采用该语法后，显式输出
目录与 deterministic 参数均真实生效，退出码 0、模型加载 0 秒；但宿主额外路径
探索带来约 126.7 秒代理开销，仍需优化演示体验。
