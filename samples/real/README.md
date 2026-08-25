# 真实样例说明

此目录只保留真实样例的来源与校验信息，不把第三方图片打进源码仓库或发布包。

最终真机验证使用的公开领域（Public Domain）样例：

- 文件：`wegmans-octopus-salad-label.jpg`
- 来源：[Wikimedia Commons 文件页](https://commons.wikimedia.org/wiki/File:Wegmans_Octopus_Salad,_Net_Wt._8_oz._2nd_label_(23418970383).jpg)
- 原始尺寸：701 × 1097
- 文件大小：448,698 bytes
- SHA-256：`40c808ce56735a027cc990ee2e476a5b2538c538d1b20e0f6c2647182eabe7cf`
- 复现位置：`<project-root>\samples\real\wegmans-octopus-salad-label.jpg`

下载后应先核对 SHA-256，再通过 `tests/test-real-model.ps1` 执行真实模型验证。
最终离线工件 `<artifact-root>\test-real-model-release-20260730\` 状态
`passed`：CPU、`model_reused=false`、加载 3.6064 s、内层分析 57.1824 s、外层
`analyze` 61.895 s，生成 1 个保持 `pending` 的候选。模型原文为
`NET WT 8.0oz (0.501b)`，映射值为 `8.0oz`，确定性归一为 `226.796185 g`。

这是单次真实图片功能证据，不是准确率结果，也不进入
`benchmark-final-06f8360-20260730` 的 synthetic 准确率统计。

## 真实文档评测样本

2026-07-31 又从官方公开页面下载 1 份 PDF、1 份 DOCX 和 2 份 XLSX，验证原生
文档解析、精确提取、冲突判断和增量缓存。原文件保存在 Git 忽略目录：

```text
<project-root>\samples\real\evaluation-documents-20260731\input\
```

| 文件 | 来源 | 大小（bytes） | SHA-256 |
|---|---|---:|---|
| `raspberry-pi-45w-power-supply-brief.pdf` | [Raspberry Pi 产品资料页](https://pip.raspberrypi.com/categories/1171-raspberry-pi-45w-usb-c-power-supply) | 520,517 | `7f8af5c509f01dea450f9cbcb5d10fbbe44ea1af8996433205aee9e2bae90eec` |
| `national-highways-technical-specification.docx` | [UK Contracts Finder 公告及附件](https://www.contractsfinder.service.gov.uk/notice/ff804aa0-b0a3-458e-a7f8-e1d030277c84) | 33,500 | `368a252c4f12a047beeafde0d0f5f52f38de16571f360766e896f9e3f5ee96f9` |
| `national-highways-equipment-list.xlsx` | [UK Contracts Finder 公告及附件](https://www.contractsfinder.service.gov.uk/notice/ff804aa0-b0a3-458e-a7f8-e1d030277c84) | 85,233 | `7514744665a16a70b16e7a208f16f76e0e2db5531767cbf18ede9fde99eba959` |
| `energy-star-dishwashers-archive.xlsx` | [ENERGY STAR 合作伙伴资源页](https://www.energystar.gov/products/dishwashers/partners) | 142,299 | `af601a65fd0e354a6c88517bb0c902855e96b5763768e4c2a3bf07edd8266624` |

请先复算 SHA-256，再按
[`docs/REAL_DOCUMENT_EVALUATION.md`](../../docs/REAL_DOCUMENT_EVALUATION.md)
中的公开入口命令复现。第三方原文件不能提交或放入发布 ZIP。

## 文档内视觉 PDF 样本

2026-07-31 还使用以下两份官方 PDF 验证 mixed visual page 检测、文字层保留和
必要时的本地 OCR/Qwen 路由。原文件及 TI 抽取页只位于
`<project-root>\.runtime\real-document-visual-samples\`，不进入发布 ZIP。

| 文件 | 来源 | 大小（bytes） | SHA-256 |
|---|---|---:|---|
| `raspberry-pi-5-mechanical-drawing.pdf` | [Raspberry Pi 官方机械图纸](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf) | 174,949 | `5dd680d6c1f5e7aa9c7b020695e315d04aac862df82ff01d4e9041cd0668d7f2` |
| `ti-tps65301-q1-datasheet.pdf` | [Texas Instruments 官方数据手册](https://www.ti.com/lit/ds/symlink/tps65301-q1.pdf) | 1,336,657 | `21d046b6d56a969cba807b51b48a90eac09d4a3934dc25befb296faf38aff512` |
| `ti-page-08-characteristics.pdf` | 从上项抽取第 8 页 | 51,727 | `39377f257e1a1ebd34d29937e146062ae6d4ef53477b34e16a5eae4919a0a31e` |
| `ti-page-24-mixed-curves.pdf` | 从上项抽取第 24 页 | 53,954 | `31fb09f069908ce1d09e747dfe398a849c37fd0d78d7b0794eb9f08d4ae3c70f` |

这些结果只证明页面级检测、定位、上下文保留和安全路由，不代表已经理解图形几何
或完成 PDF 曲线逐点数字化。脱敏运行摘要随源码保存在
[`docs/evidence/`](../../docs/evidence/)。
