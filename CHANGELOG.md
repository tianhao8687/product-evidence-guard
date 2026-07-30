# Changelog

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
