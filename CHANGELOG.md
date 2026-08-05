# Changelog

## Unreleased — 2026-08-05

- 增加 RapidOCR/PP-OCRv6/OpenVINO 本地快速路径；普通商品图片中的低分、异常
  单位、冲突和无法安全映射结果交给一次 Qwen 紧凑复核，只有复核输出无效或报错
  才进入原有两步 Qwen 深度路径；schema 有效的空复核结果不会重复 deep；
- RapidOCR wheel 和三份内置 ONNX 模型均做版本/哈希约束，禁止运行时自动下载；
- 增加混合路由、错误升级、模型后缀误识别和稀疏负样本测试；
- 修复 Windows 安装器受损的用户级 uv cache 会阻断干净安装的问题，改用仓库内
  `.runtime\uv-cache`；
- 记录 8 张公开真实样本 OCR 路由，以及清晰适配器和电池误读两条真机性能结果；
- 增加 4 份官方公开 PDF/DOCX/XLSX 的真实文档评测；修复英文别名子串、叙述句和
  未解析数值造成的误报，并保留输入/输出及多档电气规格。
- 增加 DOCX/XLSX 内嵌图片、XLSX 原生图表结构化和带文字层 PDF 混合视觉页处理；
  新增 `document-visuals.json`，保留 Office 锚点、图表系列、PDF 页级检测和处理
  状态，视觉路线的行级 OCR observations 继续写入 `visual-transcription.json`；
- 使用 Raspberry Pi 5 官方机械图纸与 TI TPS65301-Q1 官方数据手册验证真实页面
  的 mixed 检测、文字层保留与本地视觉路由；明确这不等于理解图形几何，当前也
  不做 PDF 曲线完整数值点数字化，所有候选仍需人工确认。
- 增加空间 OCR 合并、通用 mode/profile/rating semantic scope、同图 exact-bytes
  请求级缓存、PDFium 保守 ROI 与原页坐标回映；文字层充分的图表页只保留
  `ocr_observations`，不把轴、图例或曲线数字晋升为产品事实；
- 观察模式在 OCR 不可用、越界、异常或超时时失败关闭，普通 Qwen reader 不会被
  静默调用；迟到结果整批丢弃并要求重试，reader/OCR/Qwen capability identity
  进入持久缓存签名；只有高置信、单值、明确标签且属于重量/尺寸/数量/型号/材质/
  颜色白名单的属性可取消仅观察选择。电气/容量、低置信、未知单位、同一 OCR 行
  多值或输入/输出混写只保留观察，不调用 Qwen，也不生成候选；
- 增加同进程文档视觉冷/热/业务缓存基准脚本；单次 CPU 外层耗时分别为
  11.1997/1.7050/0.2092 秒，常驻完整重算减少 84.78% 耗时，冷启动耗时是
  热重算的 6.57 倍；三份视觉均走 OCR fast，该差异不代表 Qwen 生成速度。
- 2026-07-31 阶段性基线为 197 项、0 跳过；此数字仅为历史记录。
- 最终 D 盘正式目录回归扩展为 231 项、0 跳过，并完成 Windows 业务 E2E、
  Named Pipe status/shutdown、JSON/Markdown/HTML 与审计输出验证；
- 增加 3 次独立冷启动分布、有限 TCP 状态采样、CycloneDX 1.5 SBOM、35 个锁定包
  许可证元数据，以及 pypdfium2/PDFium 19 份精确 notices；
- Qoder 1.1.8 的 5 条中文与 3 条英文自然语言触发全部自动选择本 Skill，只调用
  `scripts/run.ps1 status`；安装副本保持 `Enabled` 且关键运行文件与源码哈希一致；
- 最终脱敏分支的标准路径 ZIP、相邻 SHA-256 与归档外 verification 记录已建立；
  精确 clean-room 231 项、0 跳过，并完成业务 E2E 与 Pipe 生命周期验证。

## 1.0.0 — 2026-07-30

- 接入官方 `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov`，通过同一模型的视觉原文与字段映射两阶段生成待确认候选；
- 增加严格顶层 JSON/Schema/白名单/原文关联校验、单次格式修复、数量长度边界和提示词注入防护；
- 增加 Windows Named Pipe 短客户端、常驻父服务与可复用模型 worker，支持状态、分析、确认、拒绝、导出和安全关闭；
- 增加 Python 3.11 独立环境、可续传 `.partial` 模型下载、`--continue`、设备选择与 300 秒任务安全期限；
- 增加扫描 PDF 本地逐页处理、输入资源上限、证据来源、置信度来源和图片近似位置；
- 增加人工确认、拒绝、审计、正式事实导出及源文件变化后的 `stale` 失效；
- 增加 30 张图片与 10 份文档的固定种子 benchmark、真实模型测试、Windows E2E、Linux/Windows CI；
- 增加 Qoder 安装器、比赛合规文档、技术文章草稿、演示脚本、隐私/安全说明和确定性发布包。

## 0.1.0

- 建立可追溯 SourceBlock 与 FactCandidate 数据合同；
- 支持文本、CSV、JSON 与可选 DOCX/XLSX/PDF 解析；
- 支持图片 OCR sidecar 和可选 OpenVINO VLM；
- 支持规则优先、OpenVINO GenAI LLM 补充提取；
- 增加单位归一化、冲突分级和跨字段口径提醒；
- 增加三层可信度；
- 增加按文件哈希的增量失效与缓存；
- 生成 JSON、Markdown 和 HTML 报告；
- 增加 6 个核心回归测试和一套故意带冲突的演示资料。
