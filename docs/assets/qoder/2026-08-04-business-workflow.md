# Qoder 匿名业务闭环脱敏记录

日期：2026-08-04

Qoder CLI：1.1.8

Skill：`local-product-evidence-guard`

样本：用户明确授权的仓库匿名 demo 副本

## 范围与边界

- Qoder 云端宿主只获得三份匿名 demo 的内容和本次生成的报告。
- 原始 `samples/demo` 保持不变；stale 使用独立测试副本。
- 所有业务操作只调用用户级安装副本的 `scripts/run.ps1`。
- 本次是 OCR sidecar/确定性业务流程验证，不是真实图片 Qwen 质量验证。
- Qoder 没有 shutdown；常驻服务由 300 秒 idle timeout 自行管理。

## 实际结果

| 步骤 | 结果 |
|---|---|
| 自动触发 | 自然语言成功选择 `local-product-evidence-guard` |
| analyze | 工具退出码 0；3 文件、13 候选、2 个 block、1 个 review |
| 单位解释 | `0.32kg` 与 `320g` 归一后一致 |
| 净重冲突 | 包装 `300g` 与两份文字资料 `320g` 判定 strong conflict |
| 数量冲突 | 包装/说明书 `2` 与参数表 `3` 判定 strong conflict |
| 安全门 | 在用户尚未亲自给出 ID/理由时，Qoder 自动确认被拦截 |
| confirm | 用户亲自选择后，320g 候选确认成功，退出码 0 |
| reject | 用户亲自选择后，300g 候选拒绝成功，退出码 0 |
| 首次 export | `confirmed_count=1`、`stale_count=0`；只导出 320g |
| 来源变化 | 测试操作者在 Skill 外把副本 `说明书.txt` 的 320g 改为 321g |
| 增量重分析 | 只重算说明书；包装 sidecar 与参数表复用；`model_reused=true` |
| stale | `confirmed=0`、`rejected=1`、`stale=1` |
| 二次 export | `confirmed_count=0`、`stale_count=1`，`facts=[]` |
| status | 工具退出码 0；服务 `running`，无 last_error |

业务 session ID 和 candidate ID 只用于本次匿名测试，不代表正式商品事实。

## 性能观察

- 初次 Qoder analyze 的模型准备时间：4.8595 秒。
- 初次确定性业务分析：0.0274 秒。
- 来源变化后的增量分析：0.0268 秒。
- 第二次模型加载：0 秒，`model_reused=true`。
- 两份未变化文件各约 0.0004 秒并命中复用。
- sidecar 没有真实图片，因此 Qwen 图片调用数为 0。

## 发现的 Qoder 用户体验问题

1. 第一轮 Qoder 省略了用户指定的 `--output` 与 `--deterministic-only`。后续定位为
   Qoder CLI query 前缺少 `--` 分隔符，导致提示词中的双横线参数未完整进入会话；
   默认输出恰好仍位于输入目录下，所以业务结果没有跑偏。
2. Qoder 在多步骤提示中多次完成当前操作后提前停止，没有自动继续 export。
3. Qoder 曾计划顺带确认未被用户点名的其他字段；安全分类器在实际执行前正确拦截。

因此 `SKILL.md` 已补充强制规则：保留用户参数、只处理用户点名 candidate、输入
只读，并在不需要新人工决定时继续完成 export/status。

## 参数保留修复复测

更新并重新安装 Skill 后，另建匿名副本复测。Qoder CLI 在 query 前增加 `--`
分隔符后，宿主实际执行命令完整保留：

```text
scripts/run.ps1 analyze <input> --output <explicit-output> --deterministic-only
```

结果：工具退出码与 JSON `exit_code` 均为 0；`result.output_dir` 精确等于指定目录；
`model_load_seconds=0.0`、`image_reader=false`，证明 deterministic 参数生效。实际本地
分析为 0.0101 秒。

Qoder 宿主在执行前仍进行了多次不必要的路径发现和源码读取，整段会话约 126.7 秒；
这不属于本地分析耗时，是宿主代理编排开销。多步骤规则修订后的全闭环尚未重复，
因此只能写“参数保留复测通过”，不能声称 Qoder 多步骤提前停止问题已经完全消失。
