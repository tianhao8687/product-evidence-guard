# Qoder 验证

官方 Qoder 文档复核日期：2026-07-30

## 当前结论

**Qoder CLI 安装、Skill 发现和安装器技术合同已经验证，真实调用尚未验证。**

官方 npm Qoder CLI 1.1.8 已在隔离位置安装，用户级 Skill 安装成功；
`qodercli skills list` 实际将 `local-product-evidence-guard` 显示为
`Enabled`。`qodercli status` 同时显示 `Account: Not logged in`，因此不能继续
证明自动触发、手动调用、UTF-8、确认/导出、离线或无云回退。用户登录后仍须完成
这些会话并保留 transcript/截图。

安装器的 allowlist、必备文件、完整性、备份/回滚，以及
symlink/junction/reparse point/hardlink 失败关闭合同已进入 123 项最终本地回归。
这证明安装边界，不证明 Qoder 已经触发或执行业务流程。

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
| `qodercli status` | `Account: Not logged in` |
| 安装器合同 | allowlist、完整性、备份/回滚、链接防护已通过本地回归 |
| 自动/手动调用 | 未执行；需要用户登录 |

“Enabled”只证明 CLI 找到并启用了 Skill 元数据，不证明它已经触发、调用
`run.ps1` 或正确解释业务结果。账号未登录是当前真实阻塞，不能绕过或描述成
“Qoder 调用通过”。

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
├── 04-analysis-summary.png
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
| 安装器技术合同 | **已验证** | 必备 lock、完整性、回滚与链接防护进入 123 项回归 |
| Skill 发现 | **已验证** | `skills list` 显示 `Enabled` |
| CLI 账号登录 | **待用户操作** | `status` 显示 `Account: Not logged in` |
| 中文自动触发 | **待用户操作** | 登录后执行并保留 transcript |
| 英文自动触发 | **待用户操作** | 登录后执行并保留 transcript |
| 手动 `/local-product-evidence-guard` | **待用户操作** | 登录后执行并保留 transcript |
| 唯一公开入口行为 | **待用户操作** | 需在 Qoder 会话中观察 |
| 下载/续传行为 | **待用户操作** | 需在 Qoder 会话中观察 |
| 分析/确认/拒绝/导出 | **待用户操作** | 需在 Qoder 会话中执行 |
| UTF-8 与中文路径 | **待用户操作** | 需在 Qoder 会话中执行 |
| 无云端回退 | **待用户操作** | 需结合 Qoder 会话和网络观察 |
| 截图与脱敏 transcript | **待用户操作** | 业务调用证据尚未创建 |

当前只能表述为“Qoder CLI 1.1.8 已发现用户级 Skill”。只有用户登录后完成实际
会话并保留证据，才能表述“Qoder 已调用并验证该 Skill”。
