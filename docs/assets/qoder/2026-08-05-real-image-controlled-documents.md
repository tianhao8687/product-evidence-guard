# Qoder 真实图片＋受控文档冲突验证（2026-08-05）

## 边界与授权

用户明确授权将 `samples/real` 中的匿名真实测试样本提供给 Qoder 云端宿主，仅用于
真实图片测试。本轮只使用一张已在先前单图验证中记录的公开领域标签图，以及两份
由项目生成、明确标为“受控合成”的文档；没有客户身份、账号信息、访问令牌或真实
客户正文。

真实图片 SHA-256：
`40c808ce56735a027cc990ee2e476a5b2538c538d1b20e0f6c2647182eabe7cf`。

受控文档值：

- 说明书：净重 `226.8 g`，用于与图片的 `8 oz` 做单位换算一致性验证；
- 物流表：净重 `250 g`，故意制造强冲突以验证安全拦截。

## Qoder 调用结果

Qoder CLI 1.1.8 使用已安装的 `local-product-evidence-guard` Skill，只通过安装副本
的 `scripts\run.ps1` 调用本地运行时。命令退出码为 `0`，没有执行 confirm 或
reject。

会话 ID：`faab93445b084bf388b9f07ab5034a17`。

| candidate ID | 来源 | 原始值 | 归一值 | 状态 |
|---|---|---|---|---|
| `07bc468b14a37827b4f6` | 真实标签图 | `8OZ` | `226.796185 g` | pending |
| `c0d253df364b24ee3dcf` | 真实标签图 | `8.0oz (0.501b)` | `226.796185 g` | pending |
| `78509c86c3adc2c26e2a` | 受控物流表 | `250 g` | `250 g` | pending |
| `83d74e7a084fc3fcae5f` | 受控说明书 | `226.8 g` | `226.8 g` | pending |

事实组结果：

- 字段：`net_weight`；
- 分类：`strong_conflict`；
- 严重度：`block`；
- blocking conflict：1；
- 运行错误：0；
- 推荐：阻止写入正式产品档案，必须人工处理。

图片路线为 `openvino_ocr_with_qwen_visual_fallback`，实际发生一次
`qwen_ocr_review`。RapidOCR/OpenVINO 与 Qwen3-VL/OpenVINO 均在本机 CPU 运行；
Qoder 宿主没有被授权用云端 OCR/VLM 替代本地推理。

## 用户体验与常驻模型

| 场景 | 外层时间 | 引擎分析 | 模型加载 | 结果 |
|---|---:|---:|---:|---|
| Qoder 首次组合分析 | 82.1 s | 38.5149 s | 6.978 s | 4 candidates、1 block、0 errors |
| 同一常驻模型、新输出目录 | 15.7437 s | 15.1410 s | 0 s | `model_reused=true` |
| 同一输入与同一输出复查 | 0.5118 s | 0.0241 s | 0 s | 3/3 文件复用，无模型调用 |

热模型引擎时间比首次组合分析约快 `2.54×`。Qoder 首轮外层时间包含 Agent 选择
Skill、发起命令和整理结果的宿主开销，不能把 82.1 秒全部归因于本地模型。

## 当前人工安全门

本轮故意停在 `pending_human_confirmation`。建议用户亲自决定：

- 确认 `c0d253df364b24ee3dcf`：真实标签 `8.0 oz` 与受控说明书 `226.8 g`
  换算一致；
- 拒绝 `78509c86c3adc2c26e2a`：`250 g` 与真实标签和说明书两份一致证据冲突。

在用户给出 candidate ID 和理由前，不执行决定、导出或 stale 演练。这不是功能
缺失，而是项目“AI 不得代替用户确认正式事实”的核心安全合同。
