# 真实 PDF、DOCX、XLSX 文档评测

最后复核：2026-08-05

## 结论

项目已经用 4 份从官方公开页面下载的真实文档完成原生文档路径测试，不再只有
合成文档：

- 1 份 Raspberry Pi 产品规格 PDF；
- 1 份英国政府采购技术规范 DOCX；
- 1 份英国政府采购设备清单 XLSX；
- 1 份 ENERGY STAR 历史产品 XLSX。

4 份文件全部成功解析，合计 1,491 个可追溯文本块、359,063 个字符，运行错误为
0。初轮测试还真实发现了严重误报：旧规则把 `Power Supply` 当功率、把
`All dimensions are approximate` 当尺寸、把 `country` 中的 `count` 当数量，
并可能把 `lightweight` 当重量。初轮共生成 348 条候选，其中包含大量明显误报；
修复后只剩 3 条结构化电气参数候选，阻断冲突从 11 个降为 0；3 条候选仍全部
保持 `pending`，没有自动冒充正式事实。

这是一轮真实文档的功能、精确性压力和速度测试，**不是有标注真实数据集上的准确率
评测**。

在上述原生文字评测之外，又增加 2 份官方公开 PDF 的文档内视觉评测：
Raspberry Pi 5 机械图纸和 TI TPS65301-Q1 数据手册。它们覆盖带普通文字层的
工程图、矢量特性曲线和同页嵌入的示波器图片。3 个选定页面全部被识别为
mixed visual page；这证明系统会登记这些页面的视觉上下文并选择处理路线，
不代表已经理解图形几何。

## 样本与完整性

第三方原文件只保存在 Git 忽略目录，不进入源码仓库或发布 ZIP。

| 文件 | 官方来源 | 大小（bytes） | SHA-256 | 解析块 / 字符 |
|---|---|---:|---|---:|
| `raspberry-pi-45w-power-supply-brief.pdf` | [Raspberry Pi 产品资料页](https://pip.raspberrypi.com/categories/1171-raspberry-pi-45w-usb-c-power-supply) / [PDF](https://pip-assets.raspberrypi.com/categories/1171-raspberry-pi-45w-usb-c-power-supply/documents/RP-008438-DS-2-Raspberry%20Pi%2045W%20USB-C%20Power%20Supply%20Product%20Brief.pdf) | 520,517 | `7f8af5c509f01dea450f9cbcb5d10fbbe44ea1af8996433205aee9e2bae90eec` | 185 / 4,520 |
| `national-highways-technical-specification.docx` | [UK Contracts Finder 公告及附件](https://www.contractsfinder.service.gov.uk/notice/ff804aa0-b0a3-458e-a7f8-e1d030277c84) | 33,500 | `368a252c4f12a047beeafde0d0f5f52f38de16571f360766e896f9e3f5ee96f9` | 98 / 13,356 |
| `national-highways-equipment-list.xlsx` | [UK Contracts Finder 公告及附件](https://www.contractsfinder.service.gov.uk/notice/ff804aa0-b0a3-458e-a7f8-e1d030277c84) | 85,233 | `7514744665a16a70b16e7a208f16f76e0e2db5531767cbf18ede9fde99eba959` | 366 / 135,486 |
| `energy-star-dishwashers-archive.xlsx` | [ENERGY STAR 洗碗机合作伙伴资源页](https://www.energystar.gov/products/dishwashers/partners) / [XLSX](https://www.energystar.gov/sites/default/files/asset/document/Dishwashers_V6_Archived%20%282%29.xlsx) | 142,299 | `af601a65fd0e354a6c88517bb0c902855e96b5763768e4c2a3bf07edd8266624` | 842 / 205,701 |

Raspberry Pi PDF 共 11 页。使用 Poppler 把第 3 页渲染为图片后又做了人工视觉复核，
可清楚看到：

- 输入：`100–240Vac`；
- 输出：`5.1V/5A`、`9V/5A`、`12V/3.75A`、`15V/3A`、
  `20V/2.25A`。

## 官方混合视觉 PDF 样本

第三方原文件和从 TI 原文件抽取的测试页都位于 Git 忽略目录，不进入发布 ZIP。
抽取单页只为把真实模型调用控制在明确范围内；来源身份仍由完整官方 PDF 的哈希
和官方 URL 记录。

| 文件 | 官方来源 | 大小（bytes） | SHA-256 | 测试用途 |
|---|---|---:|---|---|
| `raspberry-pi-5-mechanical-drawing.pdf` | [Raspberry Pi 官方机械图纸 PDF](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf) | 174,949 | `5dd680d6c1f5e7aa9c7b020695e315d04aac862df82ff01d4e9041cd0668d7f2` | 1 页工程图；523 个可提取文字字符，含密集矢量线条和尺寸标注 |
| `ti-tps65301-q1-datasheet.pdf` | [Texas Instruments 官方数据手册 PDF](https://www.ti.com/lit/ds/symlink/tps65301-q1.pdf) | 1,336,657 | `21d046b6d56a969cba807b51b48a90eac09d4a3934dc25befb296faf38aff512` | 33 页数据手册；选取特性曲线页和曲线加示波器图混合页 |
| `ti-page-08-characteristics.pdf` | 从上述 TI PDF 抽取第 8 页 | 51,727 | `39377f257e1a1ebd34d29937e146062ae6d4ef53477b34e16a5eae4919a0a31e` | 5 组原生/矢量特性曲线，含轴、单位、图例和刻度 |
| `ti-page-24-mixed-curves.pdf` | 从上述 TI PDF 抽取第 24 页 | 53,954 | `31fb09f069908ce1d09e747dfe398a849c37fd0d78d7b0794eb9f08d4ae3c70f` | 降额曲线、效率曲线和嵌入示波器截图同页 |

所有 3 个模型输入页都先由 Poppler 以 150 DPI 渲染并人工查看：页面完整、未裁切，
关键轴标签与尺寸标注明显可见。TI 个别字体产生替代字体警告，但选定页面仍可读。

### 处理结果

| 页面 | mixed 检测依据 | 实际路线 | 结果 |
|---|---|---|---|
| Raspberry Pi 5 第 1 页 | `drawing` 关键词 + 5,800 个矢量操作 | `structured_from_text_layer` | 保留 27 行、494 字符的页级上下文；避免模型调用 |
| TI 第 8 页 | `figure` 关键词 + 矢量操作和带单位数值 | `structured_from_text_layer` | 保留 70 行、911 字符的页级上下文；避免模型调用 |
| TI 第 24 页 | 1 个大嵌入图片 + 曲线文字层 | PDFium ROI + `ocr_observations` | 保留 32 行文字层、40 条 OCR observations；ROI 面积减少 29.84%；候选 0；避免 Qwen |

`OCR observations` 是 RapidOCR 的逐行观察，包含原文、recognizer score 来源和
`bbox_1000` 近似位置。它们保存在 `visual-transcription.json`；对应
`document-visuals.json` 只记录 `ocr_observation_count`、路由、页码、检测原因、
渲染尺寸和处理状态。observations 不是 FactCandidate，不是正确率，也不能因为
识别到坐标轴刻度就声称已数字化曲线。

这轮真实 PDF 没有生成事实候选，符合安全边界：当前系统能登记工程图/曲线页的
页级视觉上下文，并保留轴标签、图例、文字层和 OCR observations，但**不理解或
保存完整图形几何，也不做 PDF 曲线的完整数值点数字化**，不会从折线位置反推出
每个 `(x, y)` 点。即使未来产生候选，也仍保持 `pending`，必须人工核对后才能确认。

### 冷、热与缓存速度

最终基准先在同一进程内完成冷/热受控文档阶段，再用仍常驻的 pipeline 处理上表
3 个真实单页文件。真实 mixed PDF 阶段结果为：

| 外层耗时 | engine 分析 | 模型加载 | 模型复用 | 业务文件缓存 |
|---:|---:|---:|---|---|
| 1.3537 s | 1.2484 s | 0 s | `true` | 未命中 |

3 页均处理完成：2 页 `structured_from_text_layer`，TI 第 24 页
`ocr_observations`；0 个错误、0 个未经支持的产品候选。TI 页的
`qwen_calls_avoided_by_text_layer_context=1`，因此该数字不能宣传成 Qwen
推理速度。

同一轮受控 DOCX/XLSX/PDF 的冷模型、常驻模型完整重算和业务缓存命中外层耗时
分别为 11.1997 / 1.7050 / 0.2092 秒；对应 engine 分析为
2.0912 / 1.5536 / 0.0353 秒。冷模型加载 8.5615 秒。常驻完整重算减少
84.78% 耗时，冷启动耗时是热重算的 6.57 倍。

受控三文档全部走 `ocr_fast`，因此这组差值主要反映 pipeline 首次构建，不是
Qwen 生成速度。计时后追加了 `observation-only` 窄白名单、低置信度/同一 OCR
行多值失败关闭、schema 有效空复核不重复 deep，以及迟到结果、严格空间分组和
CropBox 等安全加固；没有重新启动模型复测，只由无模型回归覆盖。机器可读摘要
显式记录此边界，因此这些数字仍是原单次测量，不是新模型进程的性能结论。

这些仍是当前 CPU、当前样本的单次工程延迟，不是分布。外层耗时、engine 分析和
模型加载按列记录，不能互相重复相加；业务缓存命中也不能冒充模型推理。

可随源码核对的脱敏原始摘要位于
[`docs/evidence/document-visual-acceleration-final.json`](evidence/document-visual-acceleration-final.json)。
受控 DOCX/XLSX/PDF 的 2/2 内嵌图片、1/1 mixed page 与 1/1 原生图表结果均在
该摘要中；第三方 PDF 本体仍不随包分发。

## 方法

最初 4 文档测试刻意使用 `--deterministic-only`，只测 PDF 文字层、DOCX 和
XLSX 的原生解析、候选提取、冲突判断、缓存和报告，不把 OCR 或 Qwen 的耗时混进
该组结果。新增混合视觉测试则显式配置本地模型，并把模型冷启动、常驻模型热调用
和业务缓存复用分开记录。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run.ps1 analyze `
  ".\samples\real\evaluation-documents-20260731\input" `
  --deterministic-only `
  --output ".\.runtime\real-documents-evaluation-final-20260731"
```

保留的本机原始证据：

```text
<project-root>\.runtime\real-documents-evaluation-final-20260731\
├── run-summary-cold.json
├── run-summary-hot.json
├── product-facts-cold.json
├── product-facts-hot.json
├── conflicts.md
├── evidence-report.html
└── confirmation-audit.jsonl
```

这里的 `cold` 指“新输出目录、没有项目分析缓存”，不是重启 Windows 后清空操作系统
文件缓存；`hot` 指同一输入和同一输出目录的第二次增量运行。

## 修复前后结果

| 指标 | 初轮诊断（旧提取规则） | 修复后缓存未命中 | 修复后增量复用 |
|---|---:|---:|---:|
| 文件数 | 4 | 4 | 4 |
| 运行错误 | 0 | 0 | 0 |
| 候选数 | 348 | 3 | 3 |
| 事实组 | 14 | 2 | 2 |
| 阻断冲突 | 11 | 0 | 0 |
| 待人工复核组 | 3 | 2 | 2 |
| `analysis_seconds` | 0.9683 s | 0.6907 s | 0.0272 s |
| 文件缓存复用 | 0 / 4 | 0 / 4 | 4 / 4 |

修复后另一次缓存未命中正式运行记录为 0.9879 s；另一次增量运行为 0.0125 s。
因此当前机器上这批真实文档首次分析约为 0.69–0.99 s，重复分析约为
0.013–0.027 s。这里只报告实测值，不用单次最低值冒充稳定延迟分布。

修复包括：

- 英文字段别名使用词边界，避免 `count` 命中 `country`、`weight` 命中
  `lightweight`；
- 普通叙述句不再只因包含字段单词就晋升为候选，要求标签或表格等结构上下文；
- 数字字段必须成功解析为受支持单位，`unparsed_*` 不再进入候选；
- 文本字段限制结构、长度和词数；
- 对 `Input:` / `Output:` 电气规格保留语义范围，并保留多个 USB PD 档位。

这些规则另有 4 项专门回归测试，当时完整本地测试总数由 145 增至 149；加入本轮
最新 2026-08-05 D 盘最终基线为 231 项通过（79.634 s，0 跳过）。

## 最终候选

| 字段 | 语义范围 | 原文值 | 归一结果 | 位置 | 状态 |
|---|---|---|---|---|---|
| 电压 | `input` | `100–240Vac` | `[100, 240] V` | PDF 第 3 页，文本块 1 | `pending` |
| 电压 | `output` | `5.1V; 9V; 12V; 15V; 20V` | `[5.1, 9, 12, 15, 20] V` | PDF 第 3 页，文本块 2 | `pending` |
| 电流 | `output` | `5A; 3.75A; 3A; 2.25A` | `[5, 3.75, 3, 2.25] A` | PDF 第 3 页，文本块 2 | `pending` |

输入电压与输出电压数值不同，但属于不同语义范围，报告正确标记为
`semantic_scope_split` 的人工复核项，而不是强冲突。输出电流只有一个来源，标记为
`insufficient_evidence`。程序没有自动确认任何一条。

## 用户体验判断

- 首次把 4 份文档拖入同一目录后，核心分析在当前机器上不到 1 秒；
- 文档不变时再次打开，4 个文件全部复用，核心分析约 13–27 毫秒；
- 报告能直接显示文件名、页码、文本块、输入/输出范围、原文和归一值；
- 没有因为无关的大型表格制造数百条待办，用户只需检查 3 条真正相关的候选；
- 结果仍要求人工确认，适合“核验助手”，不伪装成自动真值系统。

## 仍然不能声称的内容

- 4 份公开文档没有逐字段人工标注，不能据此计算真实 precision、recall 或 F1；
- 两份大型 XLSX 是多产品/设备目录，不是“一个商品一个资料夹”的标准输入；当前
  精确优先规则安全地产生 0 条候选，但还没有通用的逐行产品 schema 映射；
- DOCX 叙述性技术规范在本轮主要充当复杂负样本，不能据此声称任意 DOCX 的字段
  recall；
- PDF 标题中的 `45W` 没有被当作结构化功率字段，说明精确优先仍有标题召回缺口；
- 本轮 PDF 有文字层。无文字层扫描 PDF 的渲染、OCR/Qwen 路径和真实图片测试应看
  `OCR_ACCELERATION.md` 与 `REAL_SAMPLE_EVALUATION.md`，不能混为一项结果；
- 新增 mixed PDF 真实测试证明图纸/曲线页会被发现并保留上下文，但没有逐曲线
  人工标注，不能计算图表理解 precision/recall；当前也不提供曲线完整数值点数字化；
- 未执行密码文档、损坏 Office、公式语义、超大表格或恶意压缩包压力测试。
