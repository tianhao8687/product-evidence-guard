from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from .extractor import FIELD_SPECS, field_schema_for_prompt
from .models import FactCandidate, SourceBlock
from .normalization import normalize_value
from .parsers import sha256_file


def _decoded_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    texts = getattr(result, "texts", None)
    if texts:
        return str(texts[0])
    return str(result)


def _first_json_array(text: str) -> list[Any]:
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL | re.IGNORECASE)
    candidates = [fenced.group(1)] if fenced else []
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for payload in candidates:
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
    return []


class OpenVinoFactExtractor:
    """Optional local LLM adapter using OpenVINO GenAI.

    The deterministic rule engine remains the source of truth for normalization and
    conflict classification. The model only proposes extra candidates.
    """

    def __init__(self, model_path: str | Path, device: str = "CPU") -> None:
        try:
            import openvino_genai as ov_genai
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "OpenVINO extraction requires openvino-genai. Install with: "
                "pip install -e '.[openvino]'"
            ) from exc
        self._pipe = ov_genai.LLMPipeline(str(model_path), device)
        self._allowed = {spec.name: spec for spec in FIELD_SPECS}

    def extract(self, blocks: Iterable[SourceBlock]) -> list[FactCandidate]:
        block_list = list(blocks)
        material = [
            {
                "block_id": block.block_id,
                "text": block.text,
                "source_file": block.source_file,
                "locator": block.locator,
            }
            for block in block_list
            if block.text.strip()
        ]
        if not material:
            return []
        prompt = (
            "你是本地商品事实提取器。只能从输入文字中提取明确写出的商品事实，禁止猜测。\n"
            "输出必须是 JSON 数组，不要解释。每项字段：block_id、field、raw_value、mapping_confidence。\n"
            "field 只能来自以下 schema：\n"
            f"{field_schema_for_prompt()}\n"
            "没有明确事实的块不要输出。mapping_confidence 为 0 到 1。\n"
            "输入：\n"
            f"{json.dumps(material, ensure_ascii=False)}"
        )
        response = _decoded_text(self._pipe.generate(prompt, max_new_tokens=800))
        rows = _first_json_array(response)
        blocks_by_id = {block.block_id: block for block in block_list}
        result: list[FactCandidate] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            block = blocks_by_id.get(str(row.get("block_id", "")))
            spec = self._allowed.get(str(row.get("field", "")))
            raw_value = str(row.get("raw_value", "")).strip()
            if block is None or spec is None or not raw_value:
                continue
            confidence = float(row.get("mapping_confidence", 0.65))
            confidence = max(0.0, min(1.0, confidence))
            normalized = normalize_value(spec.name, raw_value)
            candidate_id = hashlib.sha256(
                f"{block.block_id}\0{spec.name}\0{raw_value}\0openvino".encode("utf-8")
            ).hexdigest()[:20]
            result.append(
                FactCandidate(
                    candidate_id=candidate_id,
                    field=spec.name,
                    field_label=spec.label,
                    raw_value=raw_value,
                    normalized_value=normalized.value,
                    normalized_unit=normalized.unit,
                    source_block_id=block.block_id,
                    source_file=block.source_file,
                    source_kind=block.source_kind,
                    file_hash=block.file_hash,
                    locator=block.locator,
                    raw_text=block.text,
                    recognition_confidence=block.recognition_confidence,
                    mapping_confidence=confidence,
                    extraction_method="openvino_genai_llm",
                    scope=spec.scope,
                    notes=list(normalized.notes),
                )
            )
        return result


class OpenVinoImageTextReader:
    """Optional local VLM reader for image text using OpenVINO GenAI.

    This is an MVP adapter. A dedicated PP-OCR/OpenVINO pipeline can replace it
    later without changing the evidence graph contract.
    """

    def __init__(self, model_path: str | Path, device: str = "CPU") -> None:
        try:
            import numpy as np
            from PIL import Image
            from openvino import Tensor
            import openvino_genai as ov_genai
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "OpenVINO image reading requires openvino-genai, openvino, numpy and pillow. "
                "Install with: pip install -e '.[openvino]'"
            ) from exc
        self._np = np
        self._Image = Image
        self._Tensor = Tensor
        self._pipe = ov_genai.VLMPipeline(str(model_path), device)

    def read(self, image_path: Path, root: Path) -> list[SourceBlock]:
        image = self._Image.open(image_path).convert("RGB")
        array = self._np.asarray(image, dtype=self._np.uint8)
        tensor = self._Tensor(array)
        prompt = (
            "逐行抄录图片中与商品参数有关的可见文字。禁止猜测被遮挡或模糊的内容。"
            "只输出 JSON 数组，每项格式 {\"text\":\"...\"}，不要解释。"
        )
        response = _decoded_text(self._pipe.generate(prompt, images=[tensor], max_new_tokens=500))
        rows = _first_json_array(response)
        relative = image_path.relative_to(root).as_posix()
        file_hash = sha256_file(image_path)
        blocks: list[SourceBlock] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not str(row.get("text", "")).strip():
                continue
            text = str(row["text"]).strip()
            payload = f"{relative}\0{file_hash}\0{index}\0{text}".encode("utf-8")
            blocks.append(
                SourceBlock(
                    block_id=hashlib.sha256(payload).hexdigest()[:20],
                    source_file=relative,
                    source_kind="image_openvino_vlm",
                    file_hash=file_hash,
                    locator={"image": relative, "block": index},
                    text=text,
                    recognition_confidence=0.75,
                    extraction_method="openvino_genai_vlm",
                )
            )
        return blocks
