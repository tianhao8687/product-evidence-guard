# 初版技术结构

## 一句话

普通文档 AI 往往只返回“重量是 320g”。Product Evidence Guard 保存的是：

```text
事实候选 ← 证据块 ← 原文件版本
```

所以它可以回答：这个数值来自哪里、同一个字段有多少份证据、单位换算后是否一致、资料变化后哪些旧证据应该失效。

## 核心对象

### SourceBlock

从文件中读取出的最小可追溯证据块，包含：

- 文件相对路径；
- 文件 SHA-256；
- 页码、行号、单元格或图片坐标；
- 原文；
- 文字识别可信度；
- 读取方法。

### FactCandidate

从 SourceBlock 中提出的商品事实候选，包含：

- 标准字段；
- 原始值；
- 归一化值与单位；
- 字段理解可信度；
- 来源证据 ID；
- 提取方法；
- 概念口径。

### FactGroup

同一个字段的所有候选证据集合。初版分类为：

- `exact_match`
- `converted_match`
- `compatible_expression`
- `insufficient_evidence`
- `likely_version_update`
- `strong_conflict`

## 为什么规则先行

本地模型适合处理不规则表达，但以下工作不应交给模型凭感觉完成：

- `0.32kg` 与 `320g` 的换算；
- 强冲突分级；
- 源文件版本失效；
- 字段白名单；
- 正式事实是否可以写入。

因此初版使用：

```text
确定性解析与规则提取
        ↓
可选 OpenVINO 本地模型补充
        ↓
确定性归一化与冲突引擎
        ↓
人工确认
```

## 增量失效

`analysis-state.json` 按相对路径保存文件哈希和候选结果。

- 哈希未变：直接复用该文件的候选；
- 哈希变化：只重做该文件；
- 文件删除：该文件证据从新报告移除；
- 模型或设备变化：旧缓存全部失效。

这不是简单的“重新总结整个文件夹”，而是按证据来源更新。

## OpenVINO 接口

初版提供两个可选适配器：

- `OpenVinoFactExtractor`：本地 LLM 从规则未覆盖的文字块提出字段候选；
- `OpenVinoImageTextReader`：本地 VLM 读取图片中可见的商品参数文字。

两者都只产生候选，最终归一化和冲突判定仍由代码执行。

专用 PP-OCR/OpenVINO 管线可以在后续替换 VLM 读图，不需要改变 SourceBlock 和 FactCandidate 合同。
