# 文档视觉真机证据

这里保存可公开、无绝对路径、无用户资料正文的文档视觉运行证据：一份最终结构化
摘要，以及三份优化前的历史 `run-summary.json`。它们来自 2026-07-31 的本机
运行，用于独立核对当前结论，同时保留优化过程而不把旧结果冒充最终结果。

| 文件 | 内容 | 发布副本 SHA-256 | 原 `.runtime` 工件 SHA-256 |
|---|---|---|---|
| `document-visual-acceleration-final.json` | 最终受控 DOCX/XLSX/PDF 冷/热/业务缓存与 Raspberry Pi/TI mixed PDF 摘要 | `0614aa0905a93ed4aeee15a86c597196f382643ee7e3d86e225fe72af8a0be9e` | `8659f8e6e3948c53bcb20bbc2bdefdcc5fda2a314928004d20b2d51b756f2c1e` |
| `document-visual-controlled-run-summary.json` | **历史优化前基线**：synthetic DOCX/XLSX/PDF，每图多次调用 Qwen | `49220fb7adf5e1ec5a49267efd55ff54bdf9ad65039e62173415009ba0b3f757` | `2ac62645fd447cc1d94c83abd112a17130af212717e7eb59d53846128e800f6b` |
| `mixed-pdf-cold-run-summary.json` | **历史优化前基线**：Raspberry Pi/TI 3 页，TI 页进入 Qwen | `8dad2e7869ffac2132a063f95498ed789393ef363314113784fad0f74015ef2b` | `dc7dd52b32b422a2dc4f4bc8efa90c652ba7524f6e1738d7acf2ca991a03650c` |
| `mixed-pdf-cache-run-summary.json` | **历史优化前基线**：同一旧实现 3/3 业务缓存命中 | `eaae4061c1b1c67654d2dd616539f99cb0e00e338f13a6f038f1670d81c51753` | `eaae4061c1b1c67654d2dd616539f99cb0e00e338f13a6f038f1670d81c51753` |

原工件路径分别是：

```text
.runtime/resident-visual-benchmark-final-20260731/benchmark-results.json
.runtime/live-model-functional-controlled/run-summary.json
.runtime/live-model-v4-cold-real/run-summary.json
.runtime/live-model-v4-hot-real/run-summary.json
```

`.runtime` 本身仍被 Git 忽略，避免把模型、第三方样本、用户输出或其他运行资料放进
发布包。这里的副本只包含摘要。历史 controlled 与 cold 副本经过 Git 的 LF 文本
规范化，字节哈希因此与 Windows 原工件不同；JSON 解析结果已逐项比较为相等。

21.1892 秒的“常驻模型、业务缓存未命中”运行没有保留独立 JSON：它在同一输出
目录执行缓存复跑时被覆盖，只能作为未归档的单次服务响应记录，不能与本目录的两份
可复核历史 PDF 工件视为同等级证据。该旧实现会让 TI 曲线页进入 Qwen；最终
`document-visual-acceleration-final.json` 已用明确分栏的冷/热/业务缓存和
observation-only mixed PDF 结果取代这三份旧基线，旧记录只保留为演进历史。

## 其他发布证据

- Qoder 用户级 Skill 发现截图位于
  [`../assets/qoder/01-skill-discovered.png`](../assets/qoder/01-skill-discovered.png)：
  它来自 2026-08-05 本机 Qoder“技能与指令”设置页，直接显示
  `local-product-evidence-guard` 已被用户级 Skill 列表发现。截图不含账号、绝对路径、
  商品正文或会话内容；它只证明 UI 发现状态，不替代自动触发和业务闭环 transcript。
- [`hardware-runtime-20260805.json`](hardware-runtime-20260805.json)：记录本机
  OpenVINO 可见的 CPU/GPU、OpenVINO plugin build 和 NVIDIA `591.86` 驱动；CPU
  不暴露独立 `DRIVER_VERSION`，因此明确记为 N/A 并以 plugin build 代替。设备 UUID、
  LUID、主机路径和账号均未发布；NVIDIA GPU 没有被写成 Intel GPU 实测。
- [`synthetic-benchmark-final-06f8360.json`](synthetic-benchmark-final-06f8360.json)：
  commit `06f8360` 的 30 张 synthetic 图片、10 份 synthetic 文档 CPU 工程
  Benchmark 公开汇总。它逐项保留规模、性能、质量、冲突、增量和限制字段；仅删除
  原工件中的本机模型绝对路径，并记录原工件 SHA-256。原始模型输出和逐样本资料仍
  留在 Git 外。所有质量指标只适用于固定 synthetic 数据，不是真实业务准确率。
- `public-exit-code-contract-20260805.json`：通过公开 `scripts/run.ps1` 验证
  退出码 `0/1/2/3`。退出码 2 使用隔离的短生命 Python 子进程提供精确 PID
  启动标记，模拟服务身份有效但 Named Pipe 不可达且状态为 error；测试后恢复
  runtime 快照，不加载模型、不使用商品正文。该记录不是拒绝服务、进程冒充或
  渗透测试。
- `log-privacy-contract-20260805.json`：纯本地、无模型日志隐私合同回归。安装日志
  不再保留完整命令、参数、第三方命令输出或仓库绝对路径；server 只写有界结构化
  事件；client 不再把子进程 stdout/stderr 原样写入持久日志，并在下一次正式启动时
  重置旧格式日志。当前两份旧格式本地日志也已在 reparse/hardlink 校验后原位脱敏；
  记录包含受控正文、凭据和绝对路径哨兵的拒绝结果；不等同于独立隐私审计、渗透
  测试或对业务报告/运行状态 JSON 的删除要求。
- `qoder-install-integrity-20260805.json`：本机更新并复核 Qoder CLI 版本、Skill
  `Enabled` 状态、唯一用户级安装、项目/工作区无同名重复项、运行时桥接、6 个
  关键文件源码/安装 SHA-256 一致，以及安装副本 `run.ps1 status` 退出码 0。
  发布副本只使用 `%USERPROFILE%`、`%SKILL_ROOT%` 和
  `<prepared-project-root>` 占位符，不含主机绝对路径、账号或会话信息；该记录不
  本次更新复用既有本地运行时，没有下载、加载模型或发送 Qoder 云端消息；该记录
  也不是商品分析或网络抓包证据。
- `named-pipe-security-boundaries-20260805.json`：在随机独立 Windows Named Pipe 和
  临时运行目录中执行的有限安全边界回归。错误 auth 被拒后服务可继续响应，
  `1 MiB + 1` 的超限帧被关闭，24 路有限 `status` 并发全部成功；同时如实记录
  同一用户可写运行状态时完整身份记录可被伪造，因此固定派生 authkey 和状态文件
  不能抵御恶意同用户进程。该证据不等于完整 fuzz、持续 DoS 或渗透测试。
- `remote-pr-snapshot-20260805.json`：通过公开 GitHub REST API 只读保存 Draft PR #1
  的远端旧 head、两次成功运行中的 Linux/Windows job，以及 0 review、0 review
  comment、0 issue comment。远端 head 与本地脱敏分支不一致，因此这份历史绿色不
  证明当前本地提交的 CI，也不授权推送。
