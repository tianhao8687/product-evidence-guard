# 模型与运行时

官方资料复核日期：2026-07-30

本文记录所选本地视觉语言模型、官方运行合同，以及“官方资料确认”和“本机实际
验证”之间的区别。

## 所选模型

| 项目 | 已核实值 |
|---|---|
| Model ID | `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` |
| 发布者 | Hugging Face 上的 OpenVINO 组织 |
| 原始模型 | `Qwen/Qwen3-VL-8B-Instruct` |
| 格式 | OpenVINO IR |
| 权重压缩 | `INT4_SYM`，ratio `1.0`，group size `128` |
| Hugging Face 页面显示的仓库大小 | 5.46 GB |
| 模型卡声明的许可证 | Apache-2.0 |
| 模型卡声明的最低 OpenVINO 版本 | 2026.1.0 |
| 模型卡声明的最低 Optimum Intel 版本 | 1.27.0 |

来源：

- [OpenVINO 官方模型卡](https://huggingface.co/OpenVINO/Qwen3-VL-8B-Instruct-int4-ov)
- [官方文件树](https://huggingface.co/OpenVINO/Qwen3-VL-8B-Instruct-int4-ov/tree/main)
- [OpenVINO 2026 Release Notes](https://docs.openvino.ai/2026/about-openvino/release-notes-openvino.html)

5.46 GB 是官方页面显示的仓库大小，不是峰值 RAM 或 VRAM。本机下载的 revision
`f3d0bc7` 中，26 个必需 payload 共 `5,462,515,610` bytes；包含 Hugging Face
元数据的完整目录共 `5,462,526,140` bytes。模型卡没有公布这份精确工件的最低
内存要求；`info.json` 的 `mem_need_gb: 16` 是项目规划值，不是官方保证。一次
成功 CPU 监测轮峰值 Working Set 为 `11,626,774,528` bytes（约 10.83 GiB），
Private Bytes 为 `7,469,481,984` bytes（约 6.96 GiB）。单机单样本数据不能
替代更完整的压力测试。

## 运行时文件

上游仓库没有单独发布“最小文件集”。为避免凭空猜出更小的集合，Product Evidence
Guard 将官方快照中的全部 26 个非文档文件都视为完整性检查必需项：

```text
added_tokens.json
chat_template.jinja
config.json
generation_config.json
merges.txt
openvino_config.json
openvino_detokenizer.bin
openvino_detokenizer.xml
openvino_language_model.bin
openvino_language_model.xml
openvino_text_embeddings_model.bin
openvino_text_embeddings_model.xml
openvino_tokenizer.bin
openvino_tokenizer.xml
openvino_vision_embeddings_merger_model.bin
openvino_vision_embeddings_merger_model.xml
openvino_vision_embeddings_model.bin
openvino_vision_embeddings_model.xml
openvino_vision_embeddings_pos_model.bin
openvino_vision_embeddings_pos_model.xml
preprocessor_config.json
special_tokens_map.json
tokenizer.json
tokenizer_config.json
video_preprocessor_config.json
vocab.json
```

`.gitattributes` 和 `README.md` 是有用的仓库元数据，但不属于本项目的运行时
完整性检查。

官方文件树显示的最大文件包括：

| 文件 | 页面显示大小 |
|---|---:|
| `openvino_language_model.bin` | 4.23 GB |
| `openvino_text_embeddings_model.bin` | 623 MB |
| `openvino_vision_embeddings_merger_model.bin` | 574 MB |

合格下载器必须使用 Hugging Face snapshot API，先写入 partial 目录，验证所有
必需文件，拒绝 pointer/placeholder 文件，再原子提升为正式目录。发布阶段已经为
这 26 个 payload 生成逐文件 SHA-256 清单：
`<project-root>\release\model-sha256-manifest.json`。该清单尚未签名；文件名存在、
非空、固定 revision 和未签名 checksum 都不能冒充维护者签名。

## OpenVINO 软件包组合

OpenVINO 2026.1 为 `VLMPipeline` 增加了 Qwen3-VL 支持。当前锁定环境选择
稳定的 2026.2.1 系列：

```text
openvino==2026.2.1
openvino-genai==2026.2.1.0
openvino-tokenizers==2026.2.1.0
```

OpenVINO 文档说明 GenAI 依赖匹配版本的 OpenVINO Runtime 与 Tokenizers；
混用不兼容版本可能导致 ABI 失败。

Windows/Python `3.11.13` 的完整传递依赖固定在 `requirements.lock`。该正式
发布/Qoder 必备文件列出 27 个锁定包及分发哈希；`install-env.ps1` 使用固定
`uv 0.8.4` 与 `--require-hashes`。2026-07-30 已在 Windows PowerShell 5.1 以
`-Force` 干净重建，editable build 使用 `--no-build-isolation --no-index`，
环境盘点为 28 个已安装包（含本项目）且 `pip check` 兼容；二次执行命中 stamp
并快速跳过。

来源：

- [安装 OpenVINO GenAI](https://docs.openvino.ai/2026/get-started/install-openvino/install-openvino-genai.html)
- [OpenVINO GenAI 依赖兼容性](https://docs.openvino.ai/2025/get-started/install-openvino/configurations/genai-dependencies.html)

模型卡还给出了 Optimum Intel 路线。Product Evidence Guard 首选 OpenVINO
GenAI，因为模型已经导出为 OpenVINO IR；仅通过 `VLMPipeline` 调用这份预导出
模型时，不要求 Torch、Transformers 或 Optimum Intel。

## 官方单图 API 合同

模型卡和当前 API 支持从本地模型目录与选定设备创建管线：

```python
import numpy as np
import openvino as ov
import openvino_genai as ov_genai
from PIL import Image

image = Image.open(image_path).convert("RGB")
image_tensor = ov.Tensor(np.asarray(image)[None])

pipe = ov_genai.VLMPipeline(model_path, device)
result = pipe.generate(
    prompt,
    image=image_tensor,
    max_new_tokens=512,
)
text = result.texts[0]
```

多图时，当前 API 接受 `images=[tensor, ...]`。`generate()` 返回
`VLMDecodedResults`，解码文字位于 `result.texts[0]`。

来源：

- [VLMPipeline Python API](https://docs.openvino.ai/2026/api/genai_api/_autosummary/openvino_genai.VLMPipeline.html)
- [官方视觉处理示例](https://openvinotoolkit.github.io/openvino.genai/docs/use-cases/visual-processing/)

应用提示词和模型响应仍是不可信数据。它们必须通过 JSON 提取、schema 校验、
长度/数量上限、来源关联和字段白名单，才能生成候选。

## 设备支持边界

OpenVINO 2026.1 Release Notes 将 Qwen3 VL 列在 CPU/GPU 支持模型中，但没有
把 Qwen3 VL 列为 NPU 支持模型。

| 设备 | 状态 | 理由 |
|---|---|---|
| `CPU` | **已验证** | Ryzen 7 7800X3D 上完成加载和两步推理 |
| 适用 Intel `GPU` | **未完成** | 官方支持该路线，但本机没有适用 Intel GPU |
| `NPU` | **未完成** | 没有该精确模型的官方支持与真机双重证据，代码明确拒绝 |
| NVIDIA GPU | **未完成** | 不是本项目声明的 OpenVINO Intel GPU 路线 |

通用 NPU 能力不能证明这份 8B 模型在某个具体 NPU 上可用。只有同时具备模型特定
官方支持和目标机器加载/推理成功证据，才可以启用 NPU。

使用官方运行时 API 检测设备：

```powershell
python -c "from openvino import Core; print(Core().available_devices)"
```

来源：[OpenVINO 设备验证](https://docs.openvino.ai/2026/get-started/install-openvino/configurations/troubleshooting-install-config.html)

公开入口默认 `AUTO`。实现会先读取 `Core().available_devices` 和每个候选的
`FULL_DEVICE_NAME`：

1. 显式设备必须在可见列表中；
2. 显式 `NPU` 直接拒绝，直到该精确模型同时具备官方与真机证据；
3. `AUTO` 只选择完整设备名包含 `Intel` 的 GPU；
4. 没有适用 Intel GPU 时选择 CPU。

本机返回 `CPU`、`GPU`，GPU 全名是 `NVIDIA GeForce RTX 5070 (dGPU)`，因此
`AUTO` 应选择 CPU。已通过单元测试覆盖 Intel GPU、NVIDIA GPU 回退、显式设备
校验和 NPU 拒绝；真实模型公开入口记录的请求/实际设备均为 CPU。

## 当前代码边界

- 主图片和无文字层 PDF 页面路径已按代码检查接入 `QwenVlReader` 的两步调用；
- `visual-transcription.json` 已在主分析路径写出；
- pypdfium2 负责把无文字层页面渲染到临时目录，随后证据重定向回原 PDF 页码；
- Named Pipe 父服务管理常驻模型 worker 子进程；worker 按模型路径和实际设备
  缓存 pipeline；
- `run-summary.json` 分开记录 `requested_device`、实际 `device`、
  `device_selection_policy`、`available_devices`、`full_device_names` 和
  `model_reused`；
- 父服务以 300 秒心跳分别约束模型加载、每个文件和最终化阶段；边界超时会终止
  该服务创建并精确跟踪的 worker；
- client 对整个文件夹请求另设 1 小时上限；
- 真实 4 图推理期间，公开 `status` 已通过 Named Pipe 在 0.438 秒返回 `running`
  与 `available_operations`，响应没有 fallback 字段。

真实 CPU 冷/热和离线环境变量运行结论来自下方保留工件；扫描 PDF 和防火墙/抓包
网络审计仍未验证。

## 2026-07-30 本机真实单图记录

最终发布候选验证通过公开入口完成一次离线配置下的冷单图功能测试；更早的主引擎
smoke 与同 worker 冷/热复用证据继续保留为历史记录。真实单图只证明功能成功，
不代表真实业务准确率、Qoder 业务调用、GPU/NPU 或零外连验证。

| 项目 | 实际记录 |
|---|---|
| OS | Windows 11 专业版 10.0.26200，build 26200 |
| 源码身份 | 发布 ZIP 内 `manifest.json.commit` 为权威；ZIP 整体由同目录 `.sha256` 校验 |
| 最终工件 | `<artifact-root>\test-real-model-release-20260730\`；`status=passed` |
| 本地模型目录 | `<project-root>\.models\Qwen3-VL-8B-Instruct-int4-ov` |
| 上游 revision | `f3d0bc7` |
| 26 个必需 payload | `5,462,515,610` bytes |
| 含 Hugging Face 元数据目录 | `5,462,526,140` bytes |
| payload SHA-256 清单 | `<project-root>\release\model-sha256-manifest.json`；未签名 |
| Python | `3.11.13` |
| OpenVINO | `2026.2.1` |
| OpenVINO GenAI | `2026.2.1.0` |
| CPU | AMD Ryzen 7 7800X3D |
| 物理/逻辑核心 | 8 / 16 |
| 安装 RAM | 33,409,253,376 bytes，约 31.11 GiB |
| 电源计划 | 高性能，GUID `8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c` |
| `Core().available_devices` | `CPU`、`GPU` |
| GPU 设备全名 | NVIDIA GeForce RTX 5070 (dGPU)；未作为本项目 Intel GPU 路线验证 |
| 请求/实际推理设备 | `CPU` / `CPU` |
| 测试图片 | 701×1097 FDA/Wikimedia 公有领域商品标签图 |
| 图片 SHA-256 | `40C808CE56735A027CC990EE2E476A5B2538C538D1B20E0F6C2647182EABE7CF` |
| 最终模型复用标记 | `model_reused=false` |
| 最终加载 / 内层分析 / 外层 `analyze` | 3.6064 / 57.1824 / 61.895 s |
| 最终候选 | 1；状态保持 `pending` |
| 较早主引擎 smoke | 加载 4.1104 s；单图 54.6552 s；总计 58.7705 s |
| 较早公开入口冷请求 | `model_reused=false`；加载 3.7021 s；单图 52.1827 s |
| 较早同 worker 热请求 | `model_reused=true`；加载 0 s；单图 30.8367 s |
| 峰值 Working Set | `11,626,774,528` bytes，约 10.83 GiB；来自另一成功监测轮 |
| 峰值 Private Bytes | `7,469,481,984` bytes，约 6.96 GiB；来自同一监测轮 |
| 设备显存 | N/A；本次使用 CPU，未测 GPU 显存 |

最终保留的第一步原文为：

```text
NET WT 8.0oz (0.501b)
```

第二步输出 `field=net_weight`、`raw_value=8.0oz`，确定性归一化得到
`226.796185 g`。近似位置为 `[183,540,707,570]`，识别与映射自评分分别为
`0.85` 和 `0.91`，两者来源都标记 `model_self_assessment`。候选保持
`pending`，没有被自动确认。

一次较早运行使用 `max_new_tokens=900` 时输出被截断，Schema 层以
`item_not_object` 拒绝全部候选；后续修正后成功。失败工件与成功工件均保留，
不能用后一次成功覆盖前一次安全拒绝。

## 冻结 synthetic CPU Benchmark

commit `06f8360`、结果目录
`<artifact-root>\benchmark-final-06f8360-20260730\` 的运行状态为 `completed`。
30 张 synthetic 图片全部成功，10 份 synthetic 文档无错误。加载 3.701264 s，
首图 75.969346 s，冷总计 79.670610 s，热图中位数 76.013413 s，30 图批量
2221.638617 s，逐图中位数 75.991379 s、p90 85.350259 s；峰值进程内存为
11,663,728,640 B（10.861 GiB），GPU 内存 N/A。

字段 recall 25/25，mapping P/R/F1 均为 1，数字 27/27、单位 15/15，参数漏检
0/25、空样本编造 0/5、注入样本接受事实 0/2；冲突 TP2、FP0、FN0、TN10。
10 份文档无变化重跑复用 10/10，节省 0.0052551 s（8.9149%）。

这些质量值只适用于确定性生成的 synthetic 数据，不能表述为真实商品准确率。
完整数据集 hash 与原始计数见 [`BENCHMARK.md`](BENCHMARK.md)。

离线环境变量验证使用：

```powershell
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
$env:OPENVINO_TELEMETRY_DISABLED = "1"
```

在模型和依赖已经缓存时，本地真实图片推理成功。该方法没有防火墙阻断或抓包，
所以不能证明进程及全部依赖“零外连”。

## 验证记录

下表刻意把官方兼容性与真实执行分开：

| 检查项 | 截至 2026-07-30 的状态 | 证据 |
|---|---|---|
| 官方 model ID、许可证、大小和文件树 | **已验证** | 上方官方来源 |
| OpenVINO Qwen3-VL API 与最低版本 | **已验证** | 上方官方来源 |
| 模型快照完整下载 | **已验证** | revision `f3d0bc7`；26 payload `5,462,515,610` bytes，含元数据目录 `5,462,526,140` bytes |
| 模型 payload SHA-256 manifest | **已验证** | 26 文件清单已生成；尚未签名 |
| 必需文件和 partial 恢复 | **未完成** | 正式快照可加载；中断/续传路径尚无完整 Windows 记录 |
| 目标机器 OpenVINO 设备列表 | **已验证** | `CPU`、`GPU`；GPU 全名为 NVIDIA RTX 5070 |
| CPU 加载模型 | **已验证** | 最终离线工件冷加载成功，3.6064 s，`model_reused=false` |
| Intel GPU 加载模型 | **未完成** | 没有保留运行证据 |
| NPU 加载模型 | **未完成** | 不得宣传；显式请求会拒绝 |
| 处理真实商品图片 | **已验证** | 最终工件 `status=passed`，生成 1 个 pending 候选 |
| 冷/热模型复用 | **已验证** | 冷 3.7021/52.1827 s；热 0/30.8367 s；`model_reused=true` |
| RAM 与 VRAM | **已验证** | CPU 进程内存见上表；本次没有 GPU VRAM |
| Synthetic CPU Benchmark | **已验证** | commit `06f8360`；30 图成功、10 文档无错误；不等于真实业务准确率 |
| 推理中公开状态 | **已验证** | 4 图推理期间 Named Pipe 0.438 s 返回，无 fallback 字段 |
| 离线环境变量推理 | **已验证** | 最终工件完成本地推理；防火墙/抓包另列 |
| 防火墙阻断与抓包 | **未完成** | 尚未证明零外连 |
| 本地自动测试 | **已验证** | 123 项通过，50.978 s；Windows 内部 123 项 49.864 s 及 JSON smoke 通过 |

可准确表述为“精确 8B 模型已在 CPU 完成真实图功能验证和 synthetic 工程
Benchmark，并在离线环境变量下成功推理”。不得把 synthetic 质量值扩写成真实
业务准确率，也不得声称 GPU/NPU 已验证或抓包证明零外连。

## 真实运行必须记录什么

后续扩展测试时，继续保留成功和失败记录，不要改写过去失败：

- 日期、OS、CPU、RAM、Intel GPU/NPU 型号和驱动；
- `openvino`、`openvino-genai` 与 `openvino-tokenizers` 版本；
- 精确 model ID、本地 revision 和已检查文件清单；
- `Core().available_devices`；
- 请求设备和实际设备；
- 模型加载、首图和热调用时间；
- 峰值进程内存，以及可获得时的设备内存；
- 来源图片标识与 SHA-256，不公开机密内容；
- 原始模型响应、通过 schema 的输出和被拒项目；
- 推理时是否阻断联网；
- 每次失败的完整错误。
