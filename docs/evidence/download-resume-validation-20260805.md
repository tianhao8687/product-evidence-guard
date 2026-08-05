# 模型下载中断续传验证（2026-08-05）

## 结论

Windows 公共入口的真实网络续传已通过。短时 Host 调用在等待预算到期后返回退出码
`3`，下载由独立后台 worker 继续；再次调用 `scripts\run.ps1 --continue` 后，
`.partial` 目录通过完整性检查并原子提升为正式目录。永久错误不会伪装成下载中，
待恢复分析请求也只在分析成功后清除。

实现遵循官方 Local AI Skill 的固定公共入口、8 分钟 Host 等待预算、pending 请求、
退出码 `3` 与 `--continue` 合同。参考：

- <https://github.com/openvino-dev-samples/local-ai-skill-authoring/blob/main/SKILL.md>
- <https://github.com/openvino-dev-samples/local-ai-skill-authoring/blob/main/references/model-and-env.md>

## 隔离测试边界

- 测试目录：`benchmark-output/download-resume-integration-20260805-01`（Git 忽略）。
- 公开 tiny 仓库：`peft-internal-testing/tiny-random-RobertaModel`。
- 固定 revision：`652cd07b8ffa01cb5cd0abce7bc37da0490dc575`。
- 必需文件：`config.json`、`model.safetensors`。
- 正式 8B 模型目录未作为测试目标，未移动、重命名、删除或覆盖。
- 测试前先只下载 `config.json` 到 `.partial`，构造真实的部分下载状态。

## 公共入口结果

第一次调用把 `PRODUCT_EVIDENCE_DOWNLOAD_WAIT_TIMEOUT` 设为 `0`，用于在不等待
8 分钟的情况下验证完全相同的超时分支：

```powershell
scripts\run.ps1 --continue
```

结果：

| 项目 | 结果 |
|---|---:|
| 首次返回时间 | 0.462 s |
| 首次退出码 | 3 |
| 首次状态 | `downloading` |
| 后台 worker PID | 35708（仅为本次短期测试记录） |
| `status` 在下载期间的退出码 | 3 |
| 第二次 `--continue` 退出码 | 0 |
| 最终状态 | `ready` |
| `.partial` | 不存在 |
| 正式 tiny 目录 | 存在 |
| `config.json` | 624 bytes |
| `model.safetensors` | 348,004 bytes |
| worker 报告总文件字节 | 355,761 bytes |

worker 日志显示同一 `.partial` 目录继续获取 4 个仓库文件，完成后写入
`download-state.json: status=ready, active=false, retryable=false`。最终文件哈希：

| 文件 | SHA-256 |
|---|---|
| `config.json` | `e26d2f402cdf43447715f8ce16e69a20bc986fe669e2e7c3936a7829ad29767f` |
| `model.safetensors` | `c1f54bfa475ca13e590cd22f80073338d87bf1180d6c22edcacc33515073db66` |

## 数据安全与错误语义

- 下载状态使用独立 `download-state.json`，不会覆盖常驻服务的 PID、startup ID 或
  `server-state.json`。
- 同一个 runtime 只允许一个下载 worker；重复启动返回下载中，不并发写同一
  `.partial`。
- 网络超时、连接中断、HTTP 429/5xx 进入可恢复状态；无权限、磁盘满、无效仓库、
  无效 revision、HTTP 400/401/403/404、LFS pointer、缺失必需文件和不完整正式目录
  进入永久错误并返回退出码 `1`。
- 正式目录只要存在但校验失败就停止，下载器不会被调用，也不会覆盖目录内容。
- 不同待恢复分析请求不能互相覆盖；分析失败或通信失败时 pending payload 保留，
  只有分析退出码为 `0` 后才清除。

## 正式模型未受影响

测试前、测试后分别按 `release/model-sha256-manifest.json` 校验 26 个正式模型
payload：两次均为 `26/26` 通过、`0` 个缺失、`0` 个哈希不一致；清单总字节为
`5,462,515,610`。

## 自动回归与 Qoder 安装副本

- 当时的 `tests/test.ps1`：209 项通过，74.957 s，0 跳过；这是续传提交时的历史基线，最新回归见提交清单。
- 新增覆盖：480 秒上限分支（注入时钟、不真实等待）、后台原子提升、永久/可恢复
  错误分类、正式目录不覆盖、服务状态隔离、pending 冲突，以及仅成功后清除。
- 本地提交：`5846e0b`。
- 用户级 Qoder Skill 通过 `-Update -RuntimeRoot <prepared-project-root>` 重新安装；
  6 个关键文件的源/安装副本 SHA-256 全部一致。
- Qoder CLI 1.1.8 云端宿主随后只调用安装副本的 `scripts\run.ps1 status`，返回
  `Exit code 0, status stopped`。

本次 tiny 下载证明真实 Hub、公共入口和续传状态机兼容；它不把 tiny 模型冒充为
生产 VLM，也不替代正式 8B 模型已经完成的加载与真实图片推理证据。
