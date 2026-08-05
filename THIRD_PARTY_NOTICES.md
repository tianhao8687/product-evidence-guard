# 第三方软件声明

最后复核：2026-08-05

Product Evidence Guard 源代码依据 [`LICENSE`](LICENSE) 中的 MIT License
发布。该许可证不会取代下列模型、运行时或 Python 软件包各自的许可证。

本仓库不提交模型权重或第三方 Python wheel。Qwen 需要单独下载；RapidOCR 通过
哈希锁定 wheel 安装，而该 wheel 内含 PP-OCR ONNX 模型文件。它们继续受各自
许可证约束。若后续发行包捆绑 wheel、原生库或模型权重，发行者还必须一并提供
对应的上游许可证和声明文件。

## 可选模型

| 组件 | 用途 | 上游声明的许可证 | 官方来源 |
|---|---|---|---|
| `OpenVINO/Qwen3-VL-8B-Instruct-int4-ov` | 可选的本地图片—文字模型 | Apache-2.0 | [OpenVINO 模型卡](https://huggingface.co/OpenVINO/Qwen3-VL-8B-Instruct-int4-ov) |
| `Qwen/Qwen3-VL-8B-Instruct` | 转换模型卡标明的原始模型 | Apache-2.0（以转换模型卡陈述为准） | [OpenVINO 模型卡指向的原始模型](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) |
| RapidOCR wheel 内 PP-OCRv6/方向分类模型 | OpenVINO OCR 快速路径 | Apache-2.0（按 RapidOCR/PaddleOCR 上游声明） | [RapidOCR](https://github.com/RapidAI/RapidOCR)、[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) |

OpenVINO 仓库将所选工件描述为 OpenVINO IR 转换，并使用 NNCF 将权重压缩为
`INT4_SYM`。该模型不受 Product Evidence Guard 的 MIT License 覆盖，因此有意
排除在源码包和发布包之外。

## 直接 Python 依赖

下表记录 `requirements.txt` 当前锁定的直接依赖。`requirements.lock` 进一步
固定 Windows/Python 3.11.13 环境中的 35 个直接与传递软件包及其分发哈希，但它
只是机器可复现的安装清单，不是 SBOM，也不能替代各上游发行物中附带的完整许可
证文本。

| 软件包 | 当前锁定版本 | 上游许可证 | 上游来源 |
|---|---:|---|---|
| OpenVINO | 2026.2.1 | Apache-2.0 | [openvinotoolkit/openvino](https://github.com/openvinotoolkit/openvino) |
| OpenVINO GenAI | 2026.2.1.0 | Apache-2.0 | [openvinotoolkit/openvino.genai](https://github.com/openvinotoolkit/openvino.genai) |
| OpenVINO Tokenizers | 2026.2.1.0 | Apache-2.0 | [openvinotoolkit/openvino_tokenizers](https://github.com/openvinotoolkit/openvino_tokenizers) |
| RapidOCR | 3.9.1 | Apache-2.0 | [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) |
| huggingface-hub | 0.34.4 | Apache-2.0 | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) |
| NumPy | 2.2.6 | BSD-3-Clause | [numpy/numpy](https://github.com/numpy/numpy) |
| Pillow | 11.3.0 | MIT-CMU | [Pillow 许可证](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| python-docx | 1.2.0 | MIT | [python-openxml/python-docx](https://github.com/python-openxml/python-docx) |
| openpyxl | 3.1.5 | MIT | [openpyxl 软件包记录](https://pypi.org/project/openpyxl/) |
| pypdf | 6.0.0 | BSD-3-Clause | [py-pdf/pypdf](https://github.com/py-pdf/pypdf) |
| pypdfium2 | 5.12.1 | BSD-3-Clause / Apache-2.0，另含依赖许可证 | [pypdfium2 5.12.1 软件包记录](https://pypi.org/project/pypdfium2/5.12.1/) |
| setuptools | 80.9.0 | MIT | [pypa/setuptools](https://github.com/pypa/setuptools) |

### pypdfium2 与 PDFium 发行说明

`pypdfium2` 是当前无文字层 PDF 页面路径使用的本地渲染器；文字层 PDF 解析仍使用
`pypdf`。pypdfium2 项目声明其自身代码可按 BSD-3-Clause 或 Apache-2.0 使用。
其 wheel 通常捆绑 PDFium 二进制，而 PDFium 又包含受其他开源许可证约束的
第三方组件。

pypdfium2 的许可证说明要求：二进制发行时必须同时提供 PDFium 许可证和其依赖
许可证。因此，只要发布包包含 pypdfium2 wheel 或解出的 PDFium DLL，就必须：

1. 保留 pypdfium2 的 BSD-3-Clause 与 Apache-2.0 许可证文本；
2. 保留该精确 wheel 对应的 PDFium 许可证和构建专属依赖声明，包括其中的
   `BUILD_LICENSES` 或等效声明；
3. 盘点实际采用的 wheel 与平台构建，不得复制其他版本的声明集合冒充；
4. 检查所选构建是否还链接了受额外许可证约束的运行时。

来源：[pypdfium2 上游 Licensing 章节](https://github.com/pypdfium2-team/pypdfium2#licensing)。
项目选择这条路线，是为了避免强 copyleft 的 PDF 渲染依赖。当前锁定的
`pypdfium2 5.12.1` Windows x64 wheel 已完成精确盘点：其 PDFium 152.0.7947.0
构建对应的 16 份 `BUILD_LICENSES` 与 3 份分发许可证，合计 19 份、136,719
字节，均已逐字节复制到 [`docs/evidence/licenses/pypdfium2`](docs/evidence/licenses/pypdfium2)，
文件 SHA-256、wheel RECORD 校验和 PDFium DLL 身份见
[`docs/evidence/pypdfium2-notices.json`](docs/evidence/pypdfium2-notices.json)。可通过
`python scripts/generate-pdfium-notices.py --check` 在已安装的锁定环境中离线复核；
该证据不表示其他平台或未来版本使用同一声明集合。

## 传递依赖与发布责任

上表软件包可能安装额外依赖和原生组件，这里没有穷举它们的全部声明。发布二进制
包或离线包前，发布流程必须：

1. 盘点精确安装环境；
2. 收集每个被分发 wheel 或归档中的许可证与声明文件；
3. 复核 copyleft 与商业许可证条件；
4. 生成 SBOM 或等效依赖清单；
5. 确认发布物不含模型文件、用户数据、缓存、日志或凭据。

本文中的商标名称仅用于识别互操作软件，不代表相关组织为本项目背书。本文件只是
记录上游声明，不构成法律意见。
