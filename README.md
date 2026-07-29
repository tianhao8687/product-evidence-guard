# Product Evidence Guard（初版）

**一个在本机运行的“商品资料侦探”。**

把同一个商品的 Word、Excel、PDF、文本、CSV 和包装图片资料放进一个文件夹，它会：

1. 从每份资料里找出商品参数候选；
2. 记住每条参数来自哪个文件、哪一页、哪一行或哪个单元格；
3. 把 `0.32kg` 和 `320g` 判断为换算后一致；
4. 把“净重”和“包装重量”当作不同概念，不乱报冲突；
5. 找出真正互相打架的数值；
6. 输出可追溯报告，但**绝不自动把 AI 猜测写成正式产品参数**。

当前版本：`0.1.0`，用于验证“事实—证据—冲突”核心闭环。

## 现在已经能做什么

- 读取 `TXT / Markdown / CSV / JSON`；
- 安装可选依赖后读取 `DOCX / XLSX / 带文字层 PDF`；
- 读取图片 OCR 旁路文件 `*.ocr.json`；
- 识别常用商品字段：型号、材质、颜色、净重、毛重、尺寸、数量、电压、电流、功率、容量等；
- 统一重量、长度、电流、电压、功率等单位；
- 将结果分成：
  - 完全一致；
  - 单位换算后一致；
  - 表达可能兼容；
  - 证据不足；
  - 可能是版本更新；
  - 明确冲突；
- 输出三层可信度：文字识别、字段理解、证据一致；
- 按文件哈希缓存结果，只重新分析新增或修改过的资料；
- 可选接入本地 OpenVINO GenAI LLM 和 VLM，不调用云端 API。

## 30 秒运行演示

要求 Python 3.11+。

```powershell
cd product-evidence-guard
python -m product_evidence_guard analyze .\samples\demo --output .\demo-output
```

也可以运行：

```powershell
.\scripts\run-demo.ps1
```

然后打开：

```text
demo-output/evidence-report.html
```

演示资料故意埋了这些问题：

- `320g` 与 `0.32kg`：换算后一致；
- 包装图写 `300g`：与其他资料形成净重强冲突；
- 套装数量有 `2件` 和 `3件`：强冲突；
- `不锈钢` 与 `304不锈钢`：可能兼容，需要人工选择正式写法。

## 输出文件

```text
demo-output/
├── product-facts.json      # 候选事实、证据和分组结果
├── conflicts.md            # 适合人阅读的冲突报告
├── evidence-report.html    # 可视化证据报告
├── run-summary.json        # 本次分析摘要
└── analysis-state.json     # 增量分析缓存
```

`product-facts.json` 的状态固定为：

```text
pending_human_confirmation
```

这表示任何结果都仍需人工确认。

## 读取真实办公文档

```powershell
pip install -e ".[documents]"
```

然后可直接把 `.docx`、`.xlsx` 和带文字层的 `.pdf` 放入商品资料文件夹。

扫描 PDF 和图片在没有本地视觉模型时不会被偷偷猜测。初版有两种方式：

1. 提供 OCR 旁路文件；
2. 配置本地 OpenVINO VLM。

OCR 旁路示例：

```json
{
  "image": "包装正面.jpg",
  "blocks": [
    {
      "text": "净重 300g",
      "bbox": [90, 130, 350, 180],
      "confidence": 0.96
    }
  ]
}
```

文件名保存为：

```text
包装正面.jpg.ocr.json
```

## 可选：接入本地 OpenVINO AI

安装：

```powershell
pip install -e ".[openvino]"
```

使用本地 OpenVINO GenAI 文本模型补充规则未识别出的商品事实：

```powershell
python -m product_evidence_guard analyze .\商品资料 `
  --openvino-model C:\models\local-llm `
  --device CPU
```

使用本地 OpenVINO VLM 读取图片文字：

```powershell
python -m product_evidence_guard analyze .\商品资料 `
  --openvino-vlm-model C:\models\local-vlm `
  --device GPU
```

初版采用“规则先行、本地模型补充”的方式：

- 单位换算和冲突判定由确定性代码完成；
- 本地模型只提出额外候选；
- 模型输出必须通过字段白名单和结构校验；
- 模型不能自动确认事实。

## Qoder / Agent Skill 用法

把本目录作为 Skill 加载后，对 Agent 说：

> 检查 `D:\客户资料\PEG-100`，整理商品事实并把冲突证据告诉我。

Agent 应运行：

```powershell
python -m product_evidence_guard analyze "D:\客户资料\PEG-100"
```

然后优先阅读：

- `.peg-output/conflicts.md`
- `.peg-output/product-facts.json`

完整执行规则见 [`SKILL.md`](SKILL.md)。

## 测试

```powershell
python -m unittest discover -s tests -v
```

当前自动测试覆盖：

- `0.32kg = 320g` 不误报冲突；
- `320g ≠ 350g` 阻断确认；
- 净重和毛重不被混成同一字段；
- CSV 行号被保留；
- 未修改文件复用缓存；
- 修改后的文件证据会失效并重新分析；
- 图片 OCR 证据保留坐标和识别置信度。

## 初版明确没有做

- 没有做完整前端；
- 没有自动下载模型；
- 没有声称 VLM 等于专业 OCR；
- 没有自动确认或覆盖正式商品参数；
- 没有把竞品资料自动当作本商品事实；
- 没有做企业级 PIM、权限和多人协作；
- 没有承诺平台审核通过。

下一步应优先做：专用 OpenVINO OCR、商品字段小模型评测集、人工确认界面和可量化准确率/速度基准。
