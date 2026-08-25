# OCR 加速与安全回退实测

最后复核：2026-08-05

## 结论

可以加 OCR，但不能让 OCR 直接代替 Qwen 或人工确认。

当前实现采用：

```text
RapidOCR / PP-OCRv6 / OpenVINO CPU
        ↓
字段、单位、空间关系、语义口径和内部一致性安全检查
        ├─ 清晰且无歧义：确定性映射
        ├─ 文字层充分的 mixed PDF：图表仅观察门
        │       ├─ 白名单明确属性且安全检查通过：确定性 pending 候选
        │       └─ 电气/容量/低分/同行多值：只保留观察，不调用 Qwen
        └─ 可疑商品图片：Qwen 紧凑视觉复核
                    ├─ schema 有效（可以为空）：结束
                    └─ 输出无效或报错：Qwen 两步深度兜底
```

所有路线只生成 `pending` 候选。单位换算、证据图、冲突判断、确认和 stale
处理没有交给 OCR 或 Qwen。

同一路线现在也用于 DOCX/XLSX 内嵌图片和需要视觉处理的 PDF mixed page。
RapidOCR 的逐行结果作为 `ocr_observations` 写入
`visual-transcription.json`，包含原文、recognizer score 来源和近似
`bbox_1000`；`document-visuals.json` 只保存资产/页级状态和
`ocr_observation_count`。observation 不是 FactCandidate，也不能单独成为事实。

## 为什么不能只用 OCR

公开电池样本中，RapidOCR 给出了：

```text
11000mAh 3.7U
recognizer score = 0.95521
```

图片实际可见文字为：

```text
GSP 063450 1000mAh 3.7V
```

这证明较高 recognizer score 也可能伴随关键数字和单位错误。路由因此同时检查：

- 目标行最低分；
- 未知单位样式，例如 `3.7U`；
- 同一字段、同一语义口径的多个不同值；
- 有参数样式但无法被确定性规则安全映射的行；
- 文本密集但没有找到可靠参数的图片。

普通商品图片任一检查失败都不会采用 OCR 快速候选，而是进入一次 Qwen 紧凑复核。
图表仅观察页不会让 Qwen 补猜：只有重量、尺寸、数量、型号、材质或颜色等窄白名单
明确标签通过置信度、单位和单值检查时才生成候选；电压、电流、功率、频率、容量、
低置信、未知单位、同一 OCR 行多值或输入/输出混写都只保留 observations，候选和
mappings 为空。普通图片的紧凑复核若 schema 有效但没有安全候选，也会直接接受
空结果；只有无效或报错才进入两步 deep。

DOCX/XLSX 中被 OCR 分成多段的“字段标签 + 数值”会先按 RapidOCR 检测框做保守
空间合并；带明确 `by mode/profile/variant` 标题、数值和短标签的系列也会按一一
对应关系恢复。不同模式、额定值、典型值和上下限分别进入独立 semantic scope，
不会再把 `Eco 30 W / Balanced 45 W / Turbo 65 W` 误判为同一口径冲突。没有明确
标签的多个值仍保持强冲突。

## 真实样本 OCR 路由

运行环境：

- Windows 11；
- AMD Ryzen 7 7800X3D；
- OpenVINO `2026.2.1`；
- RapidOCR `3.9.1`；
- PP-OCRv6 small 检测/识别模型；
- OCR 实际设备：CPU。

8 张公开真实商品图片的一次共享 pipeline 测量：

| 样本 | OCR 秒 | OCR 行数 | 路由决定 | 主要原因 |
|---|---:|---:|---|---|
| 电脑适配器标签 | 0.8291 | 63 | Qwen 复核 | 目标样式低分；错误 `MOD0L/04A` 不得当作电流 |
| 锂电池标签 | 0.1849 | 4 | Qwen 复核 | 低分目标行、未知单位 `U` |
| 8 oz 食品标签 | 0.3172 | 22 | Qwen 复核 | 另一条低分重量行、`0.501b` 可疑 |
| Nutella 正面 | 0.1025 | 2 | 快速空结果 | 稀疏、无参数 |
| Coca-Cola 正面 | 0.1155 | 1 | 快速空结果 | 稀疏、无参数 |
| Evian 正面 | 0.1620 | 2 | 快速空结果 | 稀疏、无参数 |
| 可调电源标签 | 0.1180 | 8 | Qwen 复核 | 多个不同电压，不能擅自选一个 |
| Atari 适配器标签 | 0.4085 | 14 | OCR 快速候选 | 参数行分数高、单位明确、输入输出口径可分 |

这些数据用于验证路由和延迟，不是 8 张图的业务准确率结论。

## 两条完整真机结果

### 清晰适配器

Atari 标签实际走 `ocr_fast`，OCR 阶段 `0.4138 s`，没有调用 Qwen，生成：

| 字段 | 原文值 | 口径 | 状态 |
|---|---|---|---|
| voltage | `120V` | input | pending |
| power | `9W` | input | pending |
| voltage | `9V` | output | pending |
| current | `500mA` | output | pending |

同一图片随后从唯一公开入口 `scripts/run.ps1` 经过 client、Named Pipe server 和
常驻 worker 实测：

| 公开入口指标 | 冷请求 | 同 worker 热请求 |
|---|---:|---:|
| 模型/pipeline 加载 | 4.9420 s | 0 s（`model_reused=true`） |
| engine 分析 | 0.4691 s | 0.4521 s |
| 路由 | `ocr_fast` | `ocr_fast` |
| 候选 | 4 条 pending | 4 条 pending |
| 错误 | 0 | 0 |

### OCR 误读电池

电池图实际走 `qwen_ocr_review`：

| 指标 | 结果 |
|---|---:|
| OCR | 0.2746 s |
| Qwen 紧凑视觉复核 | 29.7067 s |
| 混合总时间 | 30.0235 s |
| Qwen + OCR pipeline 加载 | 4.9579 s |
| 错误/Schema 诊断 | 0 |

Qwen 紧凑复核原始输出：

```json
{"schema_version":1,"lines":["GSP 063450 1000mAh 3.7V"]}
```

确定性代码随后生成 `1000mAh` 和 `3.7V` 两条 `pending` 候选。同一电池图此前
完整两步 Qwen 测量约 `71.681 s`；本次单次对比下降约 58%。这只是当前机器、
当前图片的单次结果，不是分布或其他设备保证。

## D 盘正式项目复验

提交同步到 `<project-root>` 后又从公开入口复验了一次：

| 验证项 | 结果 |
|---|---:|
| 干净重建环境 | Python 3.11.13；36 个已安装包兼容 |
| 正式目录完整回归 | 2026-08-25：265 项通过，84.114 s，0 跳过 |
| Atari 冷请求 | 模型加载 4.5971 s；engine 0.4587 s；`ocr_fast` |
| Atari 同 worker 热请求 | 模型复用；engine 0.3053 s；`ocr_fast` |
| 电池低分请求 | OCR 0.3039 s；Qwen 视觉 27.9589 s；混合分析 28.3053 s |

Atari 两次均生成 4 条 `pending` 候选且零错误；电池样本生成 `1000mAh` 与
`3.7V` 两条 `pending` 候选且零错误。随后从干净 Git HEAD 生成发布 ZIP 并独立
重算 SHA-256 匹配。最终发布包在全新目录按 lock 完成 clean-room 安装，模块来源
指向解压目录；最新精确 clean-room 为 265 项通过（0 跳过），并完成
compileall、业务 E2E、Named Pipe status 与 shutdown。

## 文档图片与 mixed page 最终实测

受控样本由同一张规格/机械图组成，分别放入 DOCX、XLSX 和 mixed PDF；XLSX 另有
可结构化原生图表。最终结果为 21 条 `pending` 候选、4 个字段组、0 个阻断冲突：

| 阶段 | 外层耗时 | engine 分析 | 模型加载 | 说明 |
|---|---:|---:|---:|---|
| 冷模型 + 冷业务缓存 | 11.1997 s | 2.0912 s | 8.5615 s | `model_reused=false` |
| 常驻模型 + 冷业务缓存 | 1.7050 s | 1.5536 s | 0 s | 完整重新计算 |
| 常驻模型 + 业务缓存命中 | 0.2092 s | 0.0353 s | 0 s | 3/3 文件复用 |

三份视觉证据都走 `ocr_fast`。相同 PNG 跨 XLSX/DOCX 的请求级 exact-bytes cache
命中 1 次，避免 1 次重复 reader 调用；命中后仍重新绑定各自文件 hash、工作表/
段落 locator、SourceBlock ID 和 Candidate ID。冷启动与热重算相差 9.4947 秒，
常驻完整重算减少 84.78% 耗时，冷启动耗时是热重算的 6.57 倍。三个视觉结果都
走 `ocr_fast`，差额主要是模型/pipeline 首次构建成本，不是 Qwen 生成速度；
这里的“热”只表示 pipeline 常驻，不包含业务文件缓存。计时完成后新增的安全
边界按约定用无模型回归验证，没有再次重启模型重跑单次计时。

Raspberry Pi 5 官方机械图纸、TI TPS65301-Q1 数据手册第 8 页和第 24 页均被检测
为 mixed page。前两页的文字层已包含足够的尺寸/轴标签上下文，因此直接记录为
`structured_from_text_layer` 并避免模型调用。TI 第 24 页包含曲线和嵌入示波器
图，使用 PDFium 顶层大图/Form 联合区域、文字吸附和安全边距裁剪；面积保留
70.16%，即减少 29.84%。OCR 检测框随后仿射回原 PDF 页坐标。

该页已有 32 行 PDF 文字层并由 RapidOCR 保留 40 条 observations，因此走
`ocr_observations`，不再为“没有安全产品事实”连续调用 Qwen。三份真实 mixed PDF
在同一常驻模型进程内的外层耗时为 1.3537 秒、engine 分析 1.2484 秒，3/3 页面
均已处理，0 错误、0 个未经支持的产品候选。

OCR 能读到轴标签、刻度和图例，不代表已理解曲线。当前不把 PDF 曲线完整数字化
为逐点坐标；所有进入事实图的候选仍需人工确认。

机器可读的无绝对路径摘要见
[`evidence/document-visual-acceleration-final.json`](evidence/document-visual-acceleration-final.json)。

## 官方 Skill 约束

实现保持官方
[`local-ai-skill-authoring`](https://github.com/openvino-dev-samples/local-ai-skill-authoring)
的关键合同：

- 唯一公开入口仍是 `scripts/run.ps1`；
- 大模型仍由 client/server 与 Named Pipe 常驻复用；
- OCR、Qwen 和确定性处理全部在本机；
- `rapidocr==3.9.1` 固定在 hash-required lock；
- wheel 内三份 ONNX 模型在启动时逐一校验 SHA-256；
- 代码显式传入模型路径，不允许 RapidOCR 自动下载；
- 普通事实候选路线在 OCR 缺失、版本不符或哈希失败时只回到本地 Qwen；图表
  observation-only 路线则失败关闭、标记重试，不调用 Qwen；
- 退出码、`--continue`、模型 `.partial` 和 Qwen required files 合同不变。

`platform.exe` 与 `server-dog` 是官方示例中的特定宿主实现，不适合本项目的
Qoder/AMD 目标机，因此没有伪造 Intel 平台门或依赖 Marvis 生命周期工具。

## 仍需继续测试

- 至少 30 张真实授权标签的 precision、recall 和漏检率；
- 反光、倾斜、极小字、模糊和多语言矩阵；
- 快速路径假阳性率与 Qwen 复核触发率；
- 同一常驻 worker 的冷/热延迟分布；当前新结果仍是单次值；
- 纯扫描 PDF 页面的混合路径真机回归；mixed text+visual PDF 已有官方样本实测；
- Qoder 登录后的完整调用与用户体验录屏。
