# Qoder 中英文自动触发矩阵（脱敏记录）

日期：2026-08-05

Qoder CLI：`1.1.8`

Skill：`local-product-evidence-guard`

验证范围：只检查 `status`；不读取、枚举或搜索商品资料，不分析样本，不安装或
下载依赖/模型，不启动或关闭模型服务。

## 方法与数据边界

每条触发句均在一个全新的非交互会话中运行，并使用
`--no-session-persistence`。会话只开放 Qoder 的 `Skill` 与 `Bash` 两种工具；提示
明确要求第一步自动选择匹配 Skill，第二步仅调用该 Skill 的 `status`。禁止使用
`Glob`、`Read`、`Grep`、Web 等其他工具。

原始 `stream-json` 只暂存在操作系统临时目录，用于机器解析；它包含会话 ID、请求
ID、本机绝对路径和宿主元数据，因此不进入仓库或发布包。下表把安装根目录统一脱敏
为 `%SKILL_ROOT%`，不保存账号、令牌、宿主模型栏、PID 或绝对路径。

工具入口统一记为：

```text
powershell -NoProfile -ExecutionPolicy Bypass -File %SKILL_ROOT%\scripts\run.ps1 status
```

Qoder 在不同会话中可能使用相对路径、正斜杠绝对路径或先切换到 Skill 根目录；
脱敏后都指向同一个公开入口 `scripts/run.ps1 status`。

## 8 条独立会话结果

“Qoder 耗时”来自流式结果的 `duration_ms`；“端到端耗时”从 CLI 进程启动计时到
退出，包含宿主初始化。二者均为单次实测，不是多轮统计量。

| # | 原始触发句 | 自动选择目标 Skill | 实际调用 `scripts/run.ps1 status` | Tool 退出码 | CLI 退出码 | Qoder 耗时 | 端到端耗时 | 中文乱码 |
|---:|---|---|---|---:|---:|---:|---:|---|
| 1 | 检查这个商品资料文件夹 | 是 | 是，1 次 | 0 | 0 | 15.992 s | 18.690 s | 无 |
| 2 | 核对包装图和参数表有没有冲突 | 是 | 是，1 次 | 0 | 0 | 16.971 s | 19.829 s | 无 |
| 3 | 帮我找出商品重量到底是多少 | 是 | 是，1 次 | 0 | 0 | 9.416 s | 12.387 s | 无 |
| 4 | 生图前先确认产品参数 | 是 | 是，1 次 | 0 | 0 | 10.381 s | 13.711 s | 无 |
| 5 | 告诉我型号来自哪个文件 | 是 | 是，1 次 | 0 | 0 | 16.696 s | 19.441 s | 无 |
| 6 | Verify this product material folder. | 是 | 是，1 次 | 0 | 0 | 12.580 s | 15.758 s | 无 |
| 7 | Find conflicting product specifications. | 是 | 是，1 次 | 0 | 0 | 14.720 s | 17.946 s | 无 |
| 8 | Check the packaging image against the datasheet. | 是 | 是，1 次 | 0 | 0 | 14.881 s | 18.020 s | 无 |

汇总：8/8 会话自动选择 `local-product-evidence-guard`，8/8 只执行一次目标
`status` 入口，8/8 Tool 与 CLI 退出码均为 0。端到端平均 16.973 秒，最短
12.387 秒，最长 19.829 秒；Qoder 结果平均 13.955 秒。所有稳定 JSON 均为
`ok=true`、`operation=status`、`exit_code=0`、`status=stopped`，中文消息可正常
解码。

机器检查还确认：8 个正式会话都没有调用 `Glob`、`Read`、`Grep`、Web 或其他
未开放工具；没有商品文件路径、文件名或正文进入这些正式会话；没有出现安装、下载、
分析、启动或关闭模型服务的命令。`status=stopped` 表示服务在测试前已经停止，
`status` 本身没有加载模型。

## 预跑异常与处理

在正式矩阵前的探索性探针未限制工具面，Qoder 曾枚举用户级 Skill 安装副本中的
文件名；没有读取文件正文，也没有进入上表。发现后立即把正式会话工具面收窄为
`Skill` 与 `Bash`，并对正式流执行禁用工具机器检查。

第一轮中第 3、4、6 句还暴露了 Qoder Windows Bash 对反斜杠相对路径的转义问题：
命令目标仍是 `scripts/run.ps1 status`，但路径先被错误解释并返回 127，随后重试才
成功。上表不把这种恢复过程算成“一次通过”；三个条目均改用全新会话、正斜杠
`./scripts/run.ps1` 重新验证，最终各自只调用一次且退出码为 0。

## 结论边界

本矩阵证明 5 条中文和 3 条英文业务表达可以在 Qoder 中自动路由到已安装 Skill，
并通过唯一公开入口完成无数据 `status` 检查。它不证明商品文件分析质量、真实图片
识别准确率、断网推理或人工确认工作流；这些能力必须由各自的专项证据支持。
