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
