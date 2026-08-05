from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from math import isfinite
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping
import uuid

from . import __version__
from .confirmation import initialize_confirmation_outputs, reconcile_confirmations
from .document_visuals import EmbeddedVisualAsset
from .extractor import extract_rule_candidates
from .graph import build_graph
from .hybrid_image_reader import (
    OBSERVATION_FACT_OVERRIDE_REASON,
    HybridImageReader,
)
from .model_output_schema import ModelOutputIssue
from .models import FactCandidate, SourceBlock
from .openvino_adapter import (
    OpenVinoDeviceSelection,
    OpenVinoFactExtractor,
    resolve_openvino_device,
)
from .parsers import (
    IMAGE_EXTENSIONS,
    MAX_PDF_PAGES,
    discover_files,
    parse_file,
    parse_docx_document,
    parse_pdf_document,
    parse_xlsx_document,
    render_pdf_page,
    render_pdf_visual_region,
    sha256_file,
)
from .qwen_vl_reader import QwenVlReadResult, QwenVlReader
from .reports import write_conflicts_markdown, write_html_report, write_product_facts
from .state import STATE_SCHEMA_VERSION, atomic_write_json, load_state


QWEN_MODEL_ID = "OpenVINO/Qwen3-VL-8B-Instruct-int4-ov"
ENGINE_SCHEMA_REVISION = (
    "competition-v1-openvino-ocr-qwen-hybrid-8-safe-observations-deadlines-roi"
)
MAX_FILES_PER_TASK = 100
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_TEXT_CHARS = 2_000_000
MAX_SINGLE_FILE_SECONDS = 300.0
MAX_PDF_VISUAL_TEXT_LINES = 128
MAX_PDF_VISUAL_TEXT_CHARS = 12_000
MIN_PDF_OBSERVATION_TEXT_LINES = 12
MIN_PDF_OBSERVATION_TEXT_CHARS = 200
PDF_OBSERVATION_VISUAL_KEYWORDS = {
    "chart",
    "curve",
    "figure",
    "graph",
    "plot",
    "typical performance",
    "typical characteristics",
    "曲线",
}


def _model_fingerprint(model_path: str | None) -> str | None:
    if not model_path:
        return None
    root = Path(model_path).expanduser().resolve()
    digest = hashlib.sha256()
    digest.update(str(root).encode("utf-8"))
    for relative in (
        "config.json",
        "openvino_config.json",
        "openvino_language_model.xml",
        "openvino_language_model.bin",
        "openvino_vision_embeddings_merger_model.bin",
    ):
        path = root / relative
        if not path.is_file():
            digest.update(f"{relative}:missing".encode("utf-8"))
            continue
        stat = path.stat()
        digest.update(f"{relative}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
        if path.suffix.casefold() in {".json", ".xml"} and stat.st_size <= 5_000_000:
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _component_identity(component: Any | None) -> dict[str, Any] | None:
    if component is None:
        return None
    backend = getattr(component, "_backend", None)
    identity: dict[str, Any] = {
        "class": (
            f"{type(component).__module__}.{type(component).__qualname__}"
        ),
    }
    for key in ("model_id", "device"):
        value = getattr(component, key, None)
        if value is None and backend is not None:
            value = getattr(backend, key, None)
        if value is not None:
            identity[key] = str(value)
    return identity


def _image_reader_identity(
    image_reader: QwenVlReader | HybridImageReader | None,
) -> dict[str, Any] | None:
    if image_reader is None:
        return None
    identity = _component_identity(image_reader) or {}
    if isinstance(image_reader, HybridImageReader):
        identity.update(
            {
                "strategy": "openvino_ocr_with_qwen_visual_fallback",
                "ocr": _component_identity(
                    getattr(image_reader, "_ocr_backend", None)
                ),
                "qwen": _component_identity(
                    getattr(image_reader, "_qwen_reader", None)
                ),
                "ocr_unavailable_reason": getattr(
                    image_reader,
                    "_ocr_unavailable_reason",
                    None,
                ),
            }
        )
    else:
        identity["strategy"] = "qwen_visual"
    return identity


def _engine_signature(
    openvino_model: str | None,
    openvino_vlm_model: str | None,
    device: str,
    *,
    image_reader_identity: Mapping[str, Any] | None = None,
) -> str:
    payload = {
        "version": __version__,
        "schema_revision": ENGINE_SCHEMA_REVISION,
        "openvino_model": str(Path(openvino_model).resolve()) if openvino_model else None,
        "openvino_vlm_model": str(Path(openvino_vlm_model).resolve()) if openvino_vlm_model else None,
        "openvino_model_fingerprint": _model_fingerprint(openvino_model),
        "openvino_vlm_model_fingerprint": _model_fingerprint(openvino_vlm_model),
        "device": device,
        "image_reader_identity": dict(image_reader_identity or {}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _visual_failure_result(
    image_path: Path,
    root: Path,
    *,
    content_hash: str,
    code: str,
    message: str,
    prior_route: str | None = None,
) -> QwenVlReadResult:
    try:
        image_file = image_path.relative_to(root).as_posix()
    except ValueError:
        image_file = image_path.name
    result = QwenVlReadResult(
        image_file=image_file,
        file_hash=content_hash,
        image_route=code,
        errors=[
            ModelOutputIssue(
                stage="visual_runtime",
                code=code,
                message=message,
            )
        ],
    )
    if prior_route:
        result.fallback_reasons.append(f"discarded_late_route:{prior_route}")
    return result


def _analyze_visual_with_request_cache(
    image_reader: QwenVlReader | HybridImageReader,
    image_path: Path,
    root: Path,
    *,
    deadline: float | None,
    cache_namespace: str,
    cache: dict[str, QwenVlReadResult],
    stats: dict[str, int],
    observation_only: bool = False,
) -> tuple[QwenVlReadResult, bool, str]:
    """Analyze exact image bytes once per request and return an unshared result.

    Cached values are canonical reader results before any source-document
    retargeting. Both insertion and retrieval deep-copy the mutable result so
    a DOCX/XLSX/PDF locator can never contaminate another source.
    """

    content_hash = sha256_file(image_path)
    purpose = "observations" if observation_only else "fact_candidates"
    cache_key = f"{cache_namespace}:{purpose}:{content_hash}"
    before_deadline = deadline is None or time.perf_counter() < deadline
    if not before_deadline:
        stats["misses"] += 1
        return (
            _visual_failure_result(
                image_path,
                root,
                content_hash=content_hash,
                code="deadline_exceeded",
                message=(
                    "视觉分析在调用读取器前已超过单文件时限；"
                    "本次结果已丢弃并将在下次重试。"
                ),
            ),
            False,
            content_hash,
        )
    cached = cache.get(cache_key)
    if cached is not None:
        stats["hits"] += 1
        stats["avoided_reader_calls"] += 1
        return deepcopy(cached), True, content_hash

    stats["misses"] += 1
    if observation_only and not isinstance(image_reader, HybridImageReader):
        result = _visual_failure_result(
            image_path,
            root,
            content_hash=content_hash,
            code="observations_unavailable",
            message=(
                "仅观察模式要求 HybridImageReader 的本地 OCR；"
                "未调用普通 Qwen 读取器，也未生成事实候选。"
            ),
        )
    elif observation_only:
        result = image_reader.analyze_image(
            image_path,
            root,
            deadline=deadline,
            observation_only=True,
        )
    else:
        result = image_reader.analyze_image(
            image_path,
            root,
            deadline=deadline,
        )
    observation_fact_override = (
        observation_only
        and OBSERVATION_FACT_OVERRIDE_REASON in result.fallback_reasons
    )
    if observation_only and not observation_fact_override:
        # Defence in depth: observation-only data can never be promoted to
        # formal product facts, even if a custom reader violates its contract.
        result.fact_candidates.clear()
        result.mappings.clear()
    completed_before_deadline = (
        deadline is None or time.perf_counter() < deadline
    )
    if not completed_before_deadline:
        result = _visual_failure_result(
            image_path,
            root,
            content_hash=content_hash,
            code="deadline_exceeded",
            message=(
                "视觉读取器在单文件时限后才返回；"
                "其候选和观察已丢弃并将在下次重试。"
            ),
            prior_route=result.image_route,
        )
    elif result.ok:
        cache[cache_key] = deepcopy(result)
    return result, False, content_hash


def _serialize_candidates(candidates: list[FactCandidate]) -> list[dict[str, Any]]:
    return [candidate.to_dict() for candidate in candidates]


def _deserialize_candidates(rows: list[dict[str, Any]]) -> list[FactCandidate]:
    result: list[FactCandidate] = []
    for row in rows:
        try:
            result.append(FactCandidate.from_dict(row))
        except (TypeError, KeyError):
            continue
    return result


def _has_ocr_sidecar(path: Path) -> bool:
    return path.with_name(path.name + ".ocr.json").exists()


def _pdf_block_id(
    *,
    source_file: str,
    file_hash: str,
    page_number: int,
    block: SourceBlock,
) -> str:
    payload = json.dumps(
        {
            "source_file": source_file,
            "file_hash": file_hash,
            "page": page_number,
            "transcription_id": block.locator.get("transcription_id"),
            "raw_text": block.text,
            "bbox_1000": block.locator.get("bbox_1000"),
            "position_precision": block.locator.get("position_precision"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _pdf_candidate_id(block: SourceBlock, candidate: FactCandidate) -> str:
    payload = (
        f"{block.block_id}\0{candidate.field}\0{candidate.raw_value}\0"
        f"{block.locator.get('transcription_id', '')}\0local_image_pdf"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _remap_pdf_crop_bbox(
    bbox_1000: Any,
    *,
    crop_box_pdf: tuple[float, float, float, float],
    page_width_pdf: float,
    page_height_pdf: float,
) -> list[int] | None:
    try:
        bbox_values = tuple(bbox_1000)
        if len(bbox_values) != 4:
            return None
        left_n, top_n, right_n, bottom_n = (
            float(value) for value in bbox_values
        )
        left, bottom, right, top = (
            float(value) for value in crop_box_pdf
        )
    except (TypeError, ValueError):
        return None
    values = (
        left_n,
        top_n,
        right_n,
        bottom_n,
        left,
        bottom,
        right,
        top,
        page_width_pdf,
        page_height_pdf,
    )
    if (
        any(not isfinite(value) for value in values)
        or page_width_pdf <= 0
        or page_height_pdf <= 0
        or not 0 <= left_n < right_n <= 1000
        or not 0 <= top_n < bottom_n <= 1000
        or not 0 <= left < right <= page_width_pdf
        or not 0 <= bottom < top <= page_height_pdf
    ):
        return None
    x1 = left + left_n / 1000 * (right - left)
    x2 = left + right_n / 1000 * (right - left)
    y_top = top - top_n / 1000 * (top - bottom)
    y_bottom = top - bottom_n / 1000 * (top - bottom)
    mapped = [
        max(0, min(1000, round(1000 * x1 / page_width_pdf))),
        max(
            0,
            min(1000, round(1000 * (page_height_pdf - y_top) / page_height_pdf)),
        ),
        max(0, min(1000, round(1000 * x2 / page_width_pdf))),
        max(
            0,
            min(
                1000,
                round(
                    1000
                    * (page_height_pdf - y_bottom)
                    / page_height_pdf
                ),
            ),
        ),
    ]
    if mapped[0] >= mapped[2] or mapped[1] >= mapped[3]:
        return None
    return mapped


def _embedded_block_id(
    *,
    source_file: str,
    file_hash: str,
    asset_id: str,
    block: SourceBlock,
) -> str:
    payload = json.dumps(
        {
            "source_file": source_file,
            "file_hash": file_hash,
            "asset_id": asset_id,
            "transcription_id": block.locator.get("transcription_id"),
            "raw_text": block.text,
            "bbox_1000": block.locator.get("bbox_1000"),
            "position_precision": block.locator.get("position_precision"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _embedded_candidate_id(
    block: SourceBlock,
    candidate: FactCandidate,
    *,
    asset_id: str,
) -> str:
    payload = (
        f"{block.block_id}\0{candidate.field}\0{candidate.raw_value}\0"
        f"{block.locator.get('transcription_id', '')}\0{asset_id}\0"
        "local_embedded_document_image"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _visual_reader_suffix(visual_result: QwenVlReadResult) -> str:
    if not visual_result.ok:
        return "local_ai_error"
    return (
        "openvino_rapidocr"
        if visual_result.image_route.startswith("ocr_")
        else "qwen_vl"
    )


def _pdf_text_layer_contexts(
    blocks: list[SourceBlock],
) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[str]] = {}
    for block in blocks:
        if block.source_kind != "pdf_text":
            continue
        page = block.locator.get("page")
        if not isinstance(page, int):
            continue
        grouped.setdefault(page, []).append(block.text)

    contexts: dict[int, dict[str, Any]] = {}
    for page, lines in grouped.items():
        retained: list[str] = []
        retained_chars = 0
        for line in lines:
            if len(retained) >= MAX_PDF_VISUAL_TEXT_LINES:
                break
            remaining = MAX_PDF_VISUAL_TEXT_CHARS - retained_chars
            if remaining <= 0:
                break
            bounded = line[:remaining]
            if bounded:
                retained.append(bounded)
                retained_chars += len(bounded)
        total_chars = sum(len(line) for line in lines)
        contexts[page] = {
            "source": "pdf_text_layer",
            "line_count": len(lines),
            "text_char_count": total_chars,
            "lines": retained,
            "truncated": (
                len(retained) < len(lines)
                or retained_chars < total_chars
            ),
        }
    return contexts


def _use_pdf_observation_only_mode(
    *,
    page_mode: str,
    reason: Mapping[str, Any],
    text_layer_context: Mapping[str, Any],
) -> bool:
    """Use OCR as observations only on clearly chart-like rich-text pages."""

    keyword = str(reason.get("keyword") or "").strip().casefold()
    return (
        page_mode == "mixed"
        and keyword in PDF_OBSERVATION_VISUAL_KEYWORDS
        and int(text_layer_context.get("line_count", 0) or 0)
        >= MIN_PDF_OBSERVATION_TEXT_LINES
        and int(text_layer_context.get("text_char_count", 0) or 0)
        >= MIN_PDF_OBSERVATION_TEXT_CHARS
    )


def _retarget_pdf_visual_result(
    visual_result: QwenVlReadResult,
    *,
    source_file: str,
    file_hash: str,
    page_number: int,
    pixel_width: int,
    pixel_height: int,
    page_mode: str = "scanned",
    visual_reason: str | None = None,
    crop_box_pdf: tuple[float, float, float, float] | None = None,
    page_width_pdf: float | None = None,
    page_height_pdf: float | None = None,
    crop_ratio: float = 1.0,
    crop_strategy: str = "full_page",
    crop_fallback_reason: str | None = None,
) -> None:
    """Replace all temporary PNG evidence references with the original PDF page."""

    if page_mode not in {"scanned", "mixed"}:
        raise ValueError(f"Unsupported PDF visual page mode: {page_mode}")
    source_prefix = "pdf_scan" if page_mode == "scanned" else "pdf_mixed_page"
    is_roi = (
        crop_box_pdf is not None
        and page_width_pdf is not None
        and page_height_pdf is not None
        and crop_strategy != "full_page"
    )
    page_source = (
        "rendered_scanned_page"
        if page_mode == "scanned"
        else (
            "rendered_mixed_page_roi"
            if is_roi
            else "rendered_mixed_page"
        )
    )
    crop_metadata: dict[str, Any] = {
        "crop_strategy": crop_strategy,
        "crop_ratio": round(float(crop_ratio), 6),
        "crop_fallback_reason": crop_fallback_reason,
    }
    if (
        crop_box_pdf is not None
        and page_width_pdf is not None
        and page_height_pdf is not None
    ):
        crop_metadata.update(
            {
                "crop_box_pdf": [
                    round(float(value), 6) for value in crop_box_pdf
                ],
                "page_size_pdf": [
                    round(float(page_width_pdf), 6),
                    round(float(page_height_pdf), 6),
                ],
            }
        )

    if is_roi:
        remapped_transcriptions = []
        for transcription in visual_result.transcriptions:
            remapped = _remap_pdf_crop_bbox(
                transcription.bbox_1000,
                crop_box_pdf=crop_box_pdf,
                page_width_pdf=page_width_pdf,
                page_height_pdf=page_height_pdf,
            )
            remapped_transcriptions.append(
                replace(
                    transcription,
                    bbox_1000=(
                        tuple(remapped)
                        if remapped is not None
                        else None
                    ),
                    position_precision=(
                        transcription.position_precision
                        if remapped is not None
                        else "unavailable"
                    ),
                )
            )
        visual_result.transcriptions = remapped_transcriptions
        for observation in visual_result.ocr_observations:
            original_bbox = observation.get("bbox_1000")
            remapped = _remap_pdf_crop_bbox(
                original_bbox,
                crop_box_pdf=crop_box_pdf,
                page_width_pdf=page_width_pdf,
                page_height_pdf=page_height_pdf,
            )
            if remapped is not None:
                observation["crop_bbox_1000"] = list(original_bbox)
                observation["bbox_1000"] = remapped
                observation["coordinate_source"] = (
                    "rapidocr_detected_box+pdf_crop_affine"
                )
            elif original_bbox is not None:
                observation["crop_bbox_1000"] = list(original_bbox)
                observation["bbox_1000"] = None
                observation["coordinate_source"] = (
                    "pdf_crop_affine_failed"
                )

    block_by_old_id: dict[str, SourceBlock] = {}
    for block in visual_result.source_blocks:
        old_id = block.block_id
        pdf_source_kind = f"{source_prefix}_{_visual_reader_suffix(visual_result)}"
        locator = {
            key: value
            for key, value in block.locator.items()
            if key not in {"image"}
        }
        original_bbox = locator.get("bbox_1000")
        if is_roi and original_bbox is not None:
            remapped = _remap_pdf_crop_bbox(
                original_bbox,
                crop_box_pdf=crop_box_pdf,
                page_width_pdf=page_width_pdf,
                page_height_pdf=page_height_pdf,
            )
            if remapped is not None:
                locator["crop_bbox_1000"] = list(original_bbox)
                locator["bbox_1000"] = remapped
                coordinate_source = str(
                    locator.get("coordinate_source")
                    or "image_bbox_1000"
                )
                locator["coordinate_source"] = (
                    f"{coordinate_source}+pdf_crop_affine"
                )
            else:
                locator["crop_bbox_1000"] = list(original_bbox)
                locator["bbox_1000"] = None
                locator["position_precision"] = "unavailable"
                locator["coordinate_source"] = "pdf_crop_affine_failed"
        locator.update(
            {
                "pdf": source_file,
                "page": page_number,
                "page_mode": page_mode,
                "page_source": page_source,
                "visual_reason": visual_reason,
                "render_method": "pypdfium2",
                "rendered_page_pixels": [pixel_width, pixel_height],
                **crop_metadata,
            }
        )
        block.source_file = source_file
        block.source_kind = pdf_source_kind
        block.file_hash = file_hash
        block.locator = locator
        block.provenance.update(
            {
                "source_document": source_file,
                "page": page_number,
                "page_mode": page_mode,
                "visual_reason": visual_reason,
                "render_method": "pypdfium2",
                "temporary_render_retained": False,
                **crop_metadata,
            }
        )
        block.block_id = _pdf_block_id(
            source_file=source_file,
            file_hash=file_hash,
            page_number=page_number,
            block=block,
        )
        block_by_old_id[old_id] = block

    retargeted_candidates: list[FactCandidate] = []
    for candidate in visual_result.fact_candidates:
        block = block_by_old_id.get(candidate.source_block_id)
        if block is None:
            continue
        candidate.source_block_id = block.block_id
        candidate.source_file = source_file
        candidate.source_kind = block.source_kind
        candidate.file_hash = file_hash
        candidate.locator = dict(block.locator)
        candidate.provenance.update(
            {
                "source_document": source_file,
                "page": page_number,
                "page_mode": page_mode,
                "visual_reason": visual_reason,
                "render_method": "pypdfium2",
                "temporary_render_retained": False,
                **crop_metadata,
                "recognition_confidence_source": block.recognition_confidence_source,
                "mapping_confidence_source": candidate.mapping_confidence_source,
            }
        )
        candidate.candidate_id = _pdf_candidate_id(block, candidate)
        retargeted_candidates.append(candidate)

    visual_result.fact_candidates = retargeted_candidates
    visual_result.image_file = f"{source_file}#page={page_number}"
    visual_result.file_hash = file_hash


def _retarget_embedded_visual_result(
    visual_result: QwenVlReadResult,
    *,
    asset: EmbeddedVisualAsset,
) -> None:
    """Replace temporary extracted-image references with the parent document."""

    source_kind = f"{asset.source_kind}_{_visual_reader_suffix(visual_result)}"
    block_by_old_id: dict[str, SourceBlock] = {}
    for block in visual_result.source_blocks:
        old_id = block.block_id
        locator = {
            key: value
            for key, value in block.locator.items()
            if key != "image"
        }
        locator.update(
            {
                **asset.locator,
                "document": asset.source_file,
                "asset_id": asset.asset_id,
                "embedded_name": asset.embedded_name,
                "asset_content_hash": asset.content_hash,
                "asset_source": "extracted_embedded_image",
            }
        )
        if asset.alt_text:
            locator["alt_text"] = asset.alt_text
        block.source_file = asset.source_file
        block.source_kind = source_kind
        block.file_hash = asset.file_hash
        block.locator = locator
        block.provenance.update(
            {
                "source_document": asset.source_file,
                "asset_id": asset.asset_id,
                "asset_content_hash": asset.content_hash,
                "embedded_name": asset.embedded_name,
                "temporary_extraction_retained": False,
            }
        )
        block.block_id = _embedded_block_id(
            source_file=asset.source_file,
            file_hash=asset.file_hash,
            asset_id=asset.asset_id,
            block=block,
        )
        block_by_old_id[old_id] = block

    retargeted_candidates: list[FactCandidate] = []
    for candidate in visual_result.fact_candidates:
        block = block_by_old_id.get(candidate.source_block_id)
        if block is None:
            continue
        candidate.source_block_id = block.block_id
        candidate.source_file = asset.source_file
        candidate.source_kind = source_kind
        candidate.file_hash = asset.file_hash
        candidate.locator = dict(block.locator)
        candidate.provenance.update(
            {
                "source_document": asset.source_file,
                "asset_id": asset.asset_id,
                "asset_content_hash": asset.content_hash,
                "embedded_name": asset.embedded_name,
                "temporary_extraction_retained": False,
                "recognition_confidence_source": block.recognition_confidence_source,
                "mapping_confidence_source": candidate.mapping_confidence_source,
            }
        )
        candidate.candidate_id = _embedded_candidate_id(
            block,
            candidate,
            asset_id=asset.asset_id,
        )
        retargeted_candidates.append(candidate)

    visual_result.fact_candidates = retargeted_candidates
    visual_result.image_file = (
        f"{asset.source_file}#asset={asset.asset_id}"
    )
    visual_result.file_hash = asset.file_hash


def _confirmation_status_counts(
    output: Path,
    candidates: list[FactCandidate],
) -> dict[str, int]:
    counts = Counter(candidate.status for candidate in candidates)
    state_path = output / "confirmation-state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    decisions = state.get("decisions")
    if isinstance(decisions, dict):
        counts["stale"] += sum(
            1
            for decision in decisions.values()
            if isinstance(decision, dict) and decision.get("status") == "stale"
        )
    return {
        status: int(counts.get(status, 0))
        for status in ("pending", "confirmed", "rejected", "stale")
    }


def analyze_directory(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    openvino_model: str | None = None,
    openvino_vlm_model: str | None = None,
    device: str = "AUTO",
    preloaded_llm_extractor: OpenVinoFactExtractor | None = None,
    preloaded_image_reader: QwenVlReader | HybridImageReader | None = None,
    preloaded_model_load_seconds: float | None = None,
    preloaded_model_reused: bool | None = None,
    device_selection: OpenVinoDeviceSelection | None = None,
    progress_callback: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    root = Path(input_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Input directory does not exist: {root}")
    if output == root:
        raise ValueError("Output directory must not be the same as the input directory.")
    output.mkdir(parents=True, exist_ok=True)
    output_is_inside_input = root in output.parents

    has_local_model = bool(
        openvino_model
        or openvino_vlm_model
        or preloaded_llm_extractor
        or preloaded_image_reader
    )
    if has_local_model:
        selection = device_selection or resolve_openvino_device(device)
        actual_device = selection.actual
    else:
        selection = None
        actual_device = str(device or "AUTO")

    state_path = output / "analysis-state.json"
    previous = load_state(state_path)
    previous_input_root = previous.get("input_root")
    same_input_root = False
    if isinstance(previous_input_root, str) and previous_input_root.strip():
        try:
            same_input_root = (
                Path(previous_input_root).expanduser().resolve() == root
            )
        except (OSError, RuntimeError):
            same_input_root = False
    session_id = (
        str(previous.get("session_id"))
        if same_input_root and previous.get("session_id")
        else uuid.uuid4().hex
    )
    model_load_started = time.perf_counter()
    llm_extractor = preloaded_llm_extractor or (
        OpenVinoFactExtractor(openvino_model, actual_device)
        if openvino_model
        else None
    )
    image_reader = preloaded_image_reader or (
        HybridImageReader.from_openvino(
            openvino_vlm_model,
            device=actual_device,
            model_id=QWEN_MODEL_ID,
        )
        if openvino_vlm_model
        else None
    )
    reader_identity = _image_reader_identity(image_reader)
    signature = _engine_signature(
        openvino_model,
        openvino_vlm_model,
        actual_device,
        image_reader_identity=reader_identity,
    )
    cache_usable = (
        same_input_root
        and previous.get("engine_signature") == signature
    )
    previous_files: dict[str, Any] = (
        previous.get("files", {}) if cache_usable else {}
    )
    if preloaded_model_load_seconds is not None:
        model_load_seconds = round(float(preloaded_model_load_seconds), 4)
    else:
        model_load_seconds = (
            round(time.perf_counter() - model_load_started, 4)
            if openvino_model or openvino_vlm_model
            else 0.0
        )
    model_reused = (
        bool(preloaded_model_reused)
        if preloaded_model_reused is not None
        else bool(
            preloaded_llm_extractor is not None
            or preloaded_image_reader is not None
        )
    )

    all_candidates: list[FactCandidate] = []
    visual_results: list[dict[str, Any]] = []
    document_visuals: list[dict[str, Any]] = []
    visual_content_cache: dict[str, QwenVlReadResult] = {}
    visual_content_cache_stats = {
        "hits": 0,
        "misses": 0,
        "avoided_reader_calls": 0,
    }
    new_state_files: dict[str, Any] = {}
    changed_files: list[str] = []
    unchanged_files: list[str] = []
    skipped_files: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    file_timings: list[dict[str, Any]] = []
    total_text_chars = 0

    discovered = [
        path
        for path in discover_files(root)
        if not (
            output_is_inside_input
            and (output == path or output in path.parents)
        )
    ]
    if len(discovered) > MAX_FILES_PER_TASK:
        raise ValueError(
            f"Too many input files: {len(discovered)} (limit: {MAX_FILES_PER_TASK})"
        )
    for file_index, path in enumerate(discovered, start=1):
        if output_is_inside_input and (output == path or output in path.parents):
            continue
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()
        file_started = time.perf_counter()
        if progress_callback is not None:
            progress_callback(
                "file_started",
                {"file": relative, "index": file_index},
            )

        # An OCR sidecar already represents the image evidence. Avoid double counting it.
        if suffix in IMAGE_EXTENSIONS and _has_ocr_sidecar(path):
            skipped_files.append({"file": relative, "reason": "ocr_sidecar_present"})
            continue

        file_size = path.stat().st_size
        if file_size > MAX_FILE_BYTES:
            skipped_files.append(
                {
                    "file": relative,
                    "reason": f"file_size_limit_exceeded:{MAX_FILE_BYTES}",
                }
            )
            continue

        file_hash = sha256_file(path)
        cached = previous_files.get(relative)
        if (
            cached
            and cached.get("file_hash") == file_hash
            and not cached.get("last_error")
            and not cached.get("skipped_reason")
            and not cached.get("retry_required")
        ):
            candidates = _deserialize_candidates(cached.get("candidates", []))
            all_candidates.extend(candidates)
            cached_visual = cached.get("visual_result")
            if isinstance(cached_visual, dict):
                visual_results.append(cached_visual)
            cached_visuals = cached.get("visual_results")
            if isinstance(cached_visuals, list):
                visual_results.extend(
                    item for item in cached_visuals if isinstance(item, dict)
                )
            cached_document_visuals = cached.get("document_visuals")
            if isinstance(cached_document_visuals, list):
                document_visuals.extend(
                    item
                    for item in cached_document_visuals
                    if isinstance(item, dict)
                )
            cached_skipped_pages = cached.get("scanned_pdf_pages_skipped")
            if isinstance(cached_skipped_pages, list) and cached_skipped_pages:
                skipped_files.append(
                    {
                        "file": relative,
                        "reason": "scanned_pdf_pages_require_local_image_model",
                        "pages": ",".join(str(page) for page in cached_skipped_pages),
                    }
                )
            cached_mixed_pages = cached.get("mixed_pdf_pages_skipped")
            if isinstance(cached_mixed_pages, list) and cached_mixed_pages:
                skipped_files.append(
                    {
                        "file": relative,
                        "reason": (
                            "mixed_pdf_pages_require_local_image_model"
                        ),
                        "pages": ",".join(
                            str(page) for page in cached_mixed_pages
                        ),
                    }
                )
            cached_embedded_assets = [
                str(item.get("asset_id"))
                for item in (
                    cached_document_visuals
                    if isinstance(cached_document_visuals, list)
                    else []
                )
                if isinstance(item, dict)
                and item.get("asset_type") == "embedded_image"
                and item.get("status") == "requires_local_image_model"
                and item.get("asset_id")
            ]
            if cached_embedded_assets:
                skipped_files.append(
                    {
                        "file": relative,
                        "reason": (
                            "embedded_document_images_require_local_image_model"
                        ),
                        "assets": ",".join(cached_embedded_assets),
                    }
                )
            new_state_files[relative] = cached
            unchanged_files.append(relative)
            file_timings.append(
                {
                    "file": relative,
                    "seconds": round(time.perf_counter() - file_started, 4),
                    "cache": "reused",
                }
            )
            continue

        changed_files.append(relative)
        try:
            if suffix in IMAGE_EXTENSIONS:
                if image_reader is None:
                    skipped_files.append({"file": relative, "reason": "no_local_image_model_or_ocr_sidecar"})
                    new_state_files[relative] = {
                        "file_hash": file_hash,
                        "candidate_count": 0,
                        "candidates": [],
                        "skipped_reason": "no_local_image_model_or_ocr_sidecar",
                    }
                    continue
                else:
                    visual_started = time.perf_counter()
                    (
                        visual_result,
                        _,
                        _,
                    ) = _analyze_visual_with_request_cache(
                        image_reader,
                        path,
                        root,
                        deadline=file_started + MAX_SINGLE_FILE_SECONDS,
                        cache_namespace=f"{signature}:direct:{relative}",
                        cache=visual_content_cache,
                        stats=visual_content_cache_stats,
                    )
                    serialized_visual = visual_result.to_dict()
                    serialized_visual["elapsed_seconds"] = round(
                        time.perf_counter() - visual_started,
                        4,
                    )
                    visual_results.append(serialized_visual)
                    candidates = visual_result.fact_candidates
                    all_candidates.extend(candidates)
                    for issue in visual_result.errors:
                        errors.append(
                            {
                                "file": relative,
                                "error": (
                                    f"model_output:{issue.stage}:{issue.code}:"
                                    f"{issue.message}"
                                ),
                            }
                        )
                    new_state_files[relative] = {
                        "file_hash": file_hash,
                        "candidate_count": len(candidates),
                        "candidates": _serialize_candidates(candidates),
                        "visual_result": serialized_visual,
                        "retry_required": bool(visual_result.errors),
                    }
                    continue
            else:
                scanned_pdf_pages: tuple[int, ...] = ()
                mixed_pdf_pages: tuple[int, ...] = ()
                pdf_visual_reasons: dict[int, dict[str, Any]] = {}
                pdf_text_layer_contexts: dict[int, dict[str, Any]] = {}
                embedded_visual_assets: tuple[EmbeddedVisualAsset, ...] = ()
                structured_document_visuals: tuple[Any, ...] = ()
                document_visual_issues: tuple[dict[str, Any], ...] = ()
                if suffix == ".pdf":
                    pdf_result = parse_pdf_document(path, relative, file_hash)
                    blocks = list(pdf_result.blocks)
                    scanned_pdf_pages = pdf_result.scanned_page_numbers
                    mixed_pdf_pages = pdf_result.mixed_visual_page_numbers
                    pdf_visual_reasons = {
                        int(item["page"]): dict(item)
                        for item in pdf_result.visual_page_reasons
                        if isinstance(item, dict)
                        and isinstance(item.get("page"), int)
                    }
                    pdf_text_layer_contexts = _pdf_text_layer_contexts(
                        blocks
                    )
                elif suffix == ".docx":
                    document_result = parse_docx_document(
                        path,
                        relative,
                        file_hash,
                    )
                    blocks = list(document_result.blocks)
                    embedded_visual_assets = document_result.visual_assets
                    structured_document_visuals = (
                        document_result.structured_visuals
                    )
                    document_visual_issues = document_result.visual_issues
                elif suffix == ".xlsx":
                    document_result = parse_xlsx_document(
                        path,
                        relative,
                        file_hash,
                    )
                    blocks = list(document_result.blocks)
                    embedded_visual_assets = document_result.visual_assets
                    structured_document_visuals = (
                        document_result.structured_visuals
                    )
                    document_visual_issues = document_result.visual_issues
                else:
                    blocks = parse_file(path, root)

            block_char_count = sum(len(block.text) for block in blocks)
            if total_text_chars + block_char_count > MAX_TOTAL_TEXT_CHARS:
                skipped_files.append(
                    {
                        "file": relative,
                        "reason": f"total_text_limit_exceeded:{MAX_TOTAL_TEXT_CHARS}",
                    }
                )
                new_state_files[relative] = {
                    "file_hash": file_hash,
                    "candidate_count": 0,
                    "candidates": [],
                    "skipped_reason": "total_text_limit_exceeded",
                }
                continue
            total_text_chars += block_char_count

            rule_candidates: list[FactCandidate] = []
            unmatched_blocks: list[SourceBlock] = []
            for block in blocks:
                extracted = extract_rule_candidates(block)
                if extracted:
                    rule_candidates.extend(extracted)
                else:
                    unmatched_blocks.append(block)

            model_candidates: list[FactCandidate] = []
            if llm_extractor and unmatched_blocks:
                try:
                    model_candidates = llm_extractor.extract(unmatched_blocks)
                except Exception as exc:
                    errors.append(
                        {
                            "file": relative,
                            "error": f"model_extraction:{type(exc).__name__}",
                    }
                )
            pdf_visual_candidates: list[FactCandidate] = []
            embedded_visual_candidates: list[FactCandidate] = []
            serialized_file_visuals: list[dict[str, Any]] = []
            file_visual_retry_required = False
            file_document_visuals: list[dict[str, Any]] = [
                visual.to_record()
                for visual in structured_document_visuals
            ]
            file_document_visuals.extend(
                dict(issue) for issue in document_visual_issues
            )
            mixed_pdf_model_pages = tuple(
                page
                for page in mixed_pdf_pages
                if int(
                    pdf_visual_reasons.get(page, {}).get(
                        "large_image_count",
                        0,
                    )
                    or 0
                )
                > 0
            )
            mixed_pdf_text_layer_pages = tuple(
                page
                for page in mixed_pdf_pages
                if page not in mixed_pdf_model_pages
            )
            for page_number in mixed_pdf_text_layer_pages:
                reason = pdf_visual_reasons.get(page_number, {})
                context = pdf_text_layer_contexts.get(page_number, {})
                file_document_visuals.append(
                    {
                        "schema_version": 1,
                        "asset_type": "pdf_page_visual",
                        "source_file": relative,
                        "source_kind": "pdf_mixed_page_text_layer",
                        "file_hash": file_hash,
                        "page": page_number,
                        "page_mode": "mixed",
                        "visual_reason": reason.get("reason"),
                        "detection": dict(reason),
                        "status": "structured_from_text_layer",
                        "text_layer_context": dict(context),
                        "text_layer_line_count": int(
                            context.get("line_count", 0)
                        ),
                        "candidate_count": sum(
                            1
                            for candidate in (
                                rule_candidates + model_candidates
                            )
                            if candidate.locator.get("page")
                            == page_number
                        ),
                        "model_call_avoided": True,
                    }
                )

            if embedded_visual_assets:
                if image_reader is None:
                    skipped_files.append(
                        {
                            "file": relative,
                            "reason": (
                                "embedded_document_images_require_local_image_model"
                            ),
                            "assets": ",".join(
                                asset.asset_id
                                for asset in embedded_visual_assets
                            ),
                        }
                    )
                    file_document_visuals.extend(
                        asset.to_record(
                            status="requires_local_image_model"
                        )
                        for asset in embedded_visual_assets
                    )
                else:
                    with tempfile.TemporaryDirectory(
                        prefix="peg-document-assets-"
                    ) as temporary:
                        temporary_root = Path(temporary)
                        for asset in embedded_visual_assets:
                            asset_started = time.perf_counter()
                            asset_record = asset.to_record(
                                status="processing"
                            )
                            asset_path = (
                                temporary_root
                                / f"{asset.asset_id}{asset.extension}"
                            )
                            if (
                                time.perf_counter()
                                >= file_started + MAX_SINGLE_FILE_SECONDS
                            ):
                                asset_record.update(
                                    {
                                        "status": "skipped",
                                        "skip_reason": (
                                            "single_file_deadline_exceeded"
                                        ),
                                    }
                                )
                                file_visual_retry_required = True
                                file_document_visuals.append(asset_record)
                                continue
                            try:
                                asset_path.write_bytes(asset.data)
                                (
                                    visual_result,
                                    visual_cache_hit,
                                    visual_content_hash,
                                ) = _analyze_visual_with_request_cache(
                                    image_reader,
                                    asset_path,
                                    temporary_root,
                                    deadline=(
                                        file_started
                                        + MAX_SINGLE_FILE_SECONDS
                                    ),
                                    cache_namespace=signature,
                                    cache=visual_content_cache,
                                    stats=visual_content_cache_stats,
                                )
                                _retarget_embedded_visual_result(
                                    visual_result,
                                    asset=asset,
                                )
                                serialized_visual = visual_result.to_dict()
                                document_source_kind = (
                                    f"{asset.source_kind}_"
                                    f"{_visual_reader_suffix(visual_result)}"
                                )
                                serialized_visual.update(
                                    {
                                        "source_kind": (
                                            document_source_kind
                                        ),
                                        "source_file": relative,
                                        "document_asset_id": asset.asset_id,
                                        "asset_content_hash": (
                                            asset.content_hash
                                        ),
                                        "visual_content_hash": (
                                            visual_content_hash
                                        ),
                                        "visual_cache_hit": (
                                            visual_cache_hit
                                        ),
                                        "visual_cache_scope": "request",
                                        "asset_locator": dict(
                                            asset.locator
                                        ),
                                        "temporary_extraction_retained": (
                                            False
                                        ),
                                        "elapsed_seconds": round(
                                            time.perf_counter()
                                            - asset_started,
                                            4,
                                        ),
                                    }
                                )
                                serialized_file_visuals.append(
                                    serialized_visual
                                )
                                visual_results.append(serialized_visual)
                                embedded_visual_candidates.extend(
                                    visual_result.fact_candidates
                                )
                                asset_record.update(
                                    {
                                        "status": (
                                            "processed"
                                            if visual_result.ok
                                            else "processed_with_errors"
                                        ),
                                        "source_kind": (
                                            document_source_kind
                                        ),
                                        "image_route": (
                                            visual_result.image_route
                                        ),
                                        "visual_content_hash": (
                                            visual_content_hash
                                        ),
                                        "visual_cache_hit": (
                                            visual_cache_hit
                                        ),
                                        "visual_cache_scope": "request",
                                        "ocr_line_count": (
                                            visual_result.ocr_line_count
                                        ),
                                        "ocr_observation_count": len(
                                            visual_result.ocr_observations
                                        ),
                                        "candidate_count": len(
                                            visual_result.fact_candidates
                                        ),
                                        "transcription_count": len(
                                            visual_result.transcriptions
                                        ),
                                        "elapsed_seconds": round(
                                            time.perf_counter()
                                            - asset_started,
                                            4,
                                        ),
                                    }
                                )
                                if visual_result.errors:
                                    file_visual_retry_required = True
                                for issue in visual_result.errors:
                                    errors.append(
                                        {
                                            "file": relative,
                                            "error": (
                                                "embedded_asset:"
                                                f"{asset.asset_id}:"
                                                f"model_output:{issue.stage}:{issue.code}:"
                                                f"{issue.message}"
                                            ),
                                        }
                                    )
                            except Exception as exc:
                                file_visual_retry_required = True
                                asset_record.update(
                                    {
                                        "status": "error",
                                        "error": type(exc).__name__,
                                    }
                                )
                                errors.append(
                                    {
                                        "file": relative,
                                        "error": (
                                            "embedded_asset:"
                                            f"{asset.asset_id}:"
                                            f"{type(exc).__name__}"
                                        ),
                                    }
                                )
                            finally:
                                asset_path.unlink(missing_ok=True)
                                file_document_visuals.append(
                                    asset_record
                                )

            pdf_visual_page_numbers = (
                *scanned_pdf_pages,
                *(
                    page
                    for page in mixed_pdf_model_pages
                    if page not in scanned_pdf_pages
                ),
            )
            if pdf_visual_page_numbers:
                if image_reader is None:
                    if scanned_pdf_pages:
                        skipped_files.append(
                            {
                                "file": relative,
                                "reason": (
                                    "scanned_pdf_pages_require_local_image_model"
                                ),
                                "pages": ",".join(
                                    str(page)
                                    for page in scanned_pdf_pages
                                ),
                            }
                        )
                    if mixed_pdf_model_pages:
                        skipped_files.append(
                            {
                                "file": relative,
                                "reason": (
                                    "mixed_pdf_pages_require_local_image_model"
                                ),
                                "pages": ",".join(
                                    str(page)
                                    for page in mixed_pdf_model_pages
                                ),
                            }
                        )
                    for page_number in pdf_visual_page_numbers:
                        reason = pdf_visual_reasons.get(
                            page_number,
                            {},
                        )
                        file_document_visuals.append(
                            {
                                "schema_version": 1,
                                "asset_type": "pdf_page_visual",
                                "source_file": relative,
                                "file_hash": file_hash,
                                "page": page_number,
                                "page_mode": reason.get(
                                    "page_mode",
                                    (
                                        "scanned"
                                        if page_number
                                        in scanned_pdf_pages
                                        else "mixed"
                                    ),
                                ),
                                "visual_reason": reason.get(
                                    "reason"
                                ),
                                "detection": dict(reason),
                                "text_layer_context": dict(
                                    pdf_text_layer_contexts.get(
                                        page_number,
                                        {},
                                    )
                                ),
                                "text_layer_line_count": int(
                                    pdf_text_layer_contexts.get(
                                        page_number,
                                        {},
                                    ).get("line_count", 0)
                                ),
                                "status": (
                                    "requires_local_image_model"
                                ),
                            }
                        )
                else:
                    with tempfile.TemporaryDirectory(
                        prefix="peg-pdf-pages-"
                    ) as temporary:
                        temporary_root = Path(temporary)
                        for page_number in pdf_visual_page_numbers:
                            rendered_path: Path | None = None
                            page_started = time.perf_counter()
                            reason = pdf_visual_reasons.get(
                                page_number,
                                {},
                            )
                            page_text_context = (
                                pdf_text_layer_contexts.get(
                                    page_number,
                                    {},
                                )
                            )
                            page_mode = str(
                                reason.get(
                                    "page_mode",
                                    (
                                        "scanned"
                                        if page_number
                                        in scanned_pdf_pages
                                        else "mixed"
                                    ),
                                )
                            )
                            observation_only = _use_pdf_observation_only_mode(
                                page_mode=page_mode,
                                reason=reason,
                                text_layer_context=page_text_context,
                            )
                            analysis_purpose = (
                                "visual_observations"
                                if observation_only
                                else "product_fact_candidates"
                            )
                            page_record: dict[str, Any] = {
                                "schema_version": 1,
                                "asset_type": "pdf_page_visual",
                                "source_file": relative,
                                "file_hash": file_hash,
                                "page": page_number,
                                "page_mode": page_mode,
                                "visual_reason": reason.get(
                                    "reason"
                                ),
                                "detection": dict(reason),
                                "text_layer_context": dict(
                                    page_text_context
                                ),
                                "text_layer_line_count": int(
                                    page_text_context.get(
                                        "line_count",
                                        0,
                                    )
                                ),
                                "analysis_purpose": analysis_purpose,
                                "qwen_call_avoided_by_text_layer_context": (
                                    False
                                ),
                                "status": "processing",
                            }
                            if (
                                time.perf_counter()
                                >= file_started + MAX_SINGLE_FILE_SECONDS
                            ):
                                page_record.update(
                                    {
                                        "status": "skipped",
                                        "skip_reason": (
                                            "single_file_deadline_exceeded"
                                        ),
                                    }
                                )
                                file_visual_retry_required = True
                                file_document_visuals.append(
                                    page_record
                                )
                                continue
                            try:
                                rendered_page = (
                                    render_pdf_visual_region(
                                        path,
                                        page_number,
                                        temporary_root,
                                    )
                                    if page_mode == "mixed"
                                    else render_pdf_page(
                                        path,
                                        page_number,
                                        temporary_root,
                                    )
                                )
                                rendered_path = rendered_page.image_path
                                crop_box_pdf = getattr(
                                    rendered_page,
                                    "crop_box_pdf",
                                    None,
                                )
                                page_width_pdf = getattr(
                                    rendered_page,
                                    "page_width_pdf",
                                    None,
                                )
                                page_height_pdf = getattr(
                                    rendered_page,
                                    "page_height_pdf",
                                    None,
                                )
                                crop_ratio = float(
                                    getattr(
                                        rendered_page,
                                        "crop_ratio",
                                        1.0,
                                    )
                                )
                                crop_strategy = str(
                                    getattr(
                                        rendered_page,
                                        "strategy",
                                        "full_page",
                                    )
                                )
                                crop_fallback_reason = getattr(
                                    rendered_page,
                                    "fallback_reason",
                                    None,
                                )
                                pdf_crop_metadata: dict[str, Any] = {
                                    "crop_strategy": crop_strategy,
                                    "crop_ratio": round(crop_ratio, 6),
                                    "crop_fallback_reason": (
                                        crop_fallback_reason
                                    ),
                                }
                                if crop_box_pdf is not None:
                                    pdf_crop_metadata["crop_box_pdf"] = [
                                        round(float(value), 6)
                                        for value in crop_box_pdf
                                    ]
                                if (
                                    page_width_pdf is not None
                                    and page_height_pdf is not None
                                ):
                                    pdf_crop_metadata["page_size_pdf"] = [
                                        round(float(page_width_pdf), 6),
                                        round(float(page_height_pdf), 6),
                                    ]
                                (
                                    visual_result,
                                    visual_cache_hit,
                                    visual_content_hash,
                                ) = _analyze_visual_with_request_cache(
                                    image_reader,
                                    rendered_page.image_path,
                                    temporary_root,
                                    deadline=(
                                        file_started
                                        + MAX_SINGLE_FILE_SECONDS
                                    ),
                                    cache_namespace=signature,
                                    cache=visual_content_cache,
                                    stats=visual_content_cache_stats,
                                    observation_only=observation_only,
                                )
                                observation_fact_override = (
                                    observation_only
                                    and OBSERVATION_FACT_OVERRIDE_REASON
                                    in visual_result.fallback_reasons
                                )
                                effective_observation_only = (
                                    observation_only
                                    and not observation_fact_override
                                )
                                actual_analysis_purpose = (
                                    "product_fact_candidates"
                                    if observation_fact_override
                                    else analysis_purpose
                                )
                                qwen_call_avoided = (
                                    effective_observation_only
                                    and visual_result.ok
                                    and visual_result.image_route
                                    == "ocr_observations"
                                )
                                raw_coordinate_space = (
                                    "rendered_crop_image_1000"
                                    if crop_strategy != "full_page"
                                    else "pdf_page_1000"
                                )
                                _retarget_pdf_visual_result(
                                    visual_result,
                                    source_file=relative,
                                    file_hash=file_hash,
                                    page_number=page_number,
                                    pixel_width=(
                                        rendered_page.pixel_width
                                    ),
                                    pixel_height=(
                                        rendered_page.pixel_height
                                    ),
                                    page_mode=page_mode,
                                    visual_reason=reason.get("reason"),
                                    crop_box_pdf=crop_box_pdf,
                                    page_width_pdf=page_width_pdf,
                                    page_height_pdf=page_height_pdf,
                                    crop_ratio=crop_ratio,
                                    crop_strategy=crop_strategy,
                                    crop_fallback_reason=(
                                        crop_fallback_reason
                                    ),
                                )
                                serialized_visual = (
                                    visual_result.to_dict()
                                )
                                pdf_source_kind = (
                                    (
                                        "pdf_scan"
                                        if page_mode == "scanned"
                                        else "pdf_mixed_page"
                                    )
                                    + "_"
                                    + _visual_reader_suffix(
                                        visual_result
                                    )
                                )
                                serialized_visual.update(
                                    {
                                        "source_kind": pdf_source_kind,
                                        "source_file": relative,
                                        "page": page_number,
                                        "page_mode": page_mode,
                                        "visual_reason": reason.get(
                                            "reason"
                                        ),
                                        "render_method": "pypdfium2",
                                        "rendered_page_retained": False,
                                        "visual_content_hash": (
                                            visual_content_hash
                                        ),
                                        "visual_cache_hit": (
                                            visual_cache_hit
                                        ),
                                        "visual_cache_scope": "request",
                                        "analysis_purpose": (
                                            actual_analysis_purpose
                                        ),
                                        "observation_mode_selected": (
                                            observation_only
                                        ),
                                        "observation_mode_overridden": (
                                            observation_fact_override
                                        ),
                                        "qwen_call_avoided_by_text_layer_context": (
                                            qwen_call_avoided
                                        ),
                                        "raw_coordinate_space": (
                                            raw_coordinate_space
                                        ),
                                        "transcription_coordinate_space": (
                                            "pdf_page_1000"
                                        ),
                                        "ocr_observation_coordinate_space": (
                                            "pdf_page_1000"
                                        ),
                                        **pdf_crop_metadata,
                                        "elapsed_seconds": round(
                                            time.perf_counter()
                                            - page_started,
                                            4,
                                        ),
                                    }
                                )
                                serialized_file_visuals.append(
                                    serialized_visual
                                )
                                visual_results.append(serialized_visual)
                                if not effective_observation_only:
                                    pdf_visual_candidates.extend(
                                        visual_result.fact_candidates
                                    )
                                page_record.update(
                                    {
                                        "status": (
                                            "processed"
                                            if visual_result.ok
                                            else "processed_with_errors"
                                        ),
                                        "source_kind": pdf_source_kind,
                                        "image_route": (
                                            visual_result.image_route
                                        ),
                                        "visual_content_hash": (
                                            visual_content_hash
                                        ),
                                        "visual_cache_hit": (
                                            visual_cache_hit
                                        ),
                                        "visual_cache_scope": "request",
                                        "analysis_purpose": (
                                            actual_analysis_purpose
                                        ),
                                        "observation_mode_selected": (
                                            observation_only
                                        ),
                                        "observation_mode_overridden": (
                                            observation_fact_override
                                        ),
                                        "qwen_call_avoided_by_text_layer_context": (
                                            qwen_call_avoided
                                        ),
                                        "candidate_count": len(
                                            visual_result.fact_candidates
                                        ),
                                        "transcription_count": len(
                                            visual_result.transcriptions
                                        ),
                                        "ocr_observation_count": len(
                                            visual_result.ocr_observations
                                        ),
                                        "rendered_page_pixels": [
                                            rendered_page.pixel_width,
                                            rendered_page.pixel_height,
                                        ],
                                        "raw_coordinate_space": (
                                            raw_coordinate_space
                                        ),
                                        "transcription_coordinate_space": (
                                            "pdf_page_1000"
                                        ),
                                        "ocr_observation_coordinate_space": (
                                            "pdf_page_1000"
                                        ),
                                        **pdf_crop_metadata,
                                        "elapsed_seconds": round(
                                            time.perf_counter()
                                            - page_started,
                                            4,
                                        ),
                                    }
                                )
                                if visual_result.errors:
                                    file_visual_retry_required = True
                                for issue in visual_result.errors:
                                    errors.append(
                                        {
                                            "file": relative,
                                            "error": (
                                                f"pdf_page:{page_number}:"
                                                f"model_output:{issue.stage}:{issue.code}:"
                                                f"{issue.message}"
                                            ),
                                        }
                                    )
                            except Exception as exc:
                                file_visual_retry_required = True
                                page_record.update(
                                    {
                                        "status": "error",
                                        "error": type(exc).__name__,
                                    }
                                )
                                errors.append(
                                    {
                                        "file": relative,
                                        "error": (
                                            f"pdf_page:{page_number}:"
                                            f"{type(exc).__name__}"
                                        ),
                                    }
                                )
                            finally:
                                if rendered_path is not None:
                                    rendered_path.unlink(missing_ok=True)
                                file_document_visuals.append(
                                    page_record
                                )

            candidates = (
                rule_candidates
                + model_candidates
                + embedded_visual_candidates
                + pdf_visual_candidates
            )
            all_candidates.extend(candidates)
            document_visuals.extend(file_document_visuals)
            state_entry: dict[str, Any] = {
                "file_hash": file_hash,
                "candidate_count": len(candidates),
                "candidates": _serialize_candidates(candidates),
            }
            if file_document_visuals:
                state_entry["document_visuals"] = file_document_visuals
            if serialized_file_visuals:
                state_entry["visual_results"] = (
                    serialized_file_visuals
                )
            if file_visual_retry_required:
                state_entry["retry_required"] = True
            if suffix == ".pdf":
                state_entry["scanned_pdf_pages"] = list(scanned_pdf_pages)
                state_entry["mixed_pdf_pages"] = list(mixed_pdf_pages)
                state_entry["pdf_visual_page_reasons"] = [
                    pdf_visual_reasons[page]
                    for page in sorted(pdf_visual_reasons)
                ]
                if scanned_pdf_pages and image_reader is None:
                    state_entry["scanned_pdf_pages_skipped"] = list(
                        scanned_pdf_pages
                    )
                if mixed_pdf_model_pages and image_reader is None:
                    state_entry["mixed_pdf_pages_skipped"] = list(
                        mixed_pdf_model_pages
                    )
            new_state_files[relative] = state_entry
        except Exception as exc:  # keep one malformed file from killing the complete analysis
            errors.append({"file": relative, "error": f"{type(exc).__name__}: {exc}"})
            new_state_files[relative] = {
                "file_hash": file_hash,
                "candidate_count": 0,
                "candidates": [],
                "last_error": f"{type(exc).__name__}: {exc}",
            }
        finally:
            elapsed = time.perf_counter() - file_started
            file_timings.append(
                {
                    "file": relative,
                    "seconds": round(elapsed, 4),
                    "cache": "analyzed",
                    "timeout_limit_exceeded": elapsed > MAX_SINGLE_FILE_SECONDS,
                }
            )

    current_paths = set(new_state_files)
    if progress_callback is not None:
        progress_callback(
            "finalizing",
            {"files_processed": len(new_state_files)},
        )
    removed_files = sorted(set(previous_files) - current_paths) if cache_usable else []

    candidates, groups, relations = build_graph(all_candidates)
    confirmation_statuses = reconcile_confirmations(
        output,
        session_id=session_id,
        candidates=candidates,
    )
    for candidate in candidates:
        candidate.status = confirmation_statuses.get(candidate.candidate_id, "pending")
    confirmation_counts = _confirmation_status_counts(output, candidates)
    scanned_pages_detected = sum(
        len(entry.get("scanned_pdf_pages", []))
        for entry in new_state_files.values()
        if isinstance(entry, dict) and isinstance(entry.get("scanned_pdf_pages"), list)
    )
    scanned_pages_processed_with_qwen = sum(
        1
        for item in visual_results
        if item.get("source_kind") == "pdf_scan_qwen_vl"
        and item.get("ok") is True
    )
    scanned_pages_processed_with_ocr = sum(
        1
        for item in visual_results
        if item.get("source_kind") == "pdf_scan_openvino_rapidocr"
        and item.get("ok") is True
    )
    scanned_pages_processed = (
        scanned_pages_processed_with_qwen
        + scanned_pages_processed_with_ocr
    )
    mixed_pages_detected = sum(
        len(entry.get("mixed_pdf_pages", []))
        for entry in new_state_files.values()
        if isinstance(entry, dict)
        and isinstance(entry.get("mixed_pdf_pages"), list)
    )
    mixed_pages_processed_with_qwen = sum(
        1
        for item in visual_results
        if item.get("source_kind") == "pdf_mixed_page_qwen_vl"
        and item.get("ok") is True
    )
    mixed_pages_processed_with_ocr = sum(
        1
        for item in visual_results
        if item.get("source_kind")
        == "pdf_mixed_page_openvino_rapidocr"
        and item.get("ok") is True
    )
    mixed_pages_processed = (
        mixed_pages_processed_with_qwen
        + mixed_pages_processed_with_ocr
    )
    mixed_pages_structured_from_text_layer = sum(
        1
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("status") == "structured_from_text_layer"
    )
    mixed_pages_handled = (
        mixed_pages_processed
        + mixed_pages_structured_from_text_layer
    )
    mixed_pages_observation_selected = sum(
        1
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("page_mode") == "mixed"
        and (
            item.get("analysis_purpose") == "visual_observations"
            or item.get("observation_mode_selected") is True
        )
    )
    mixed_pages_observation_only = sum(
        1
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("page_mode") == "mixed"
        and item.get("analysis_purpose") == "visual_observations"
        and item.get("status") == "processed"
    )
    mixed_pages_observation_errors = sum(
        1
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("page_mode") == "mixed"
        and item.get("analysis_purpose") == "visual_observations"
        and item.get("status") in {"processed_with_errors", "error"}
    )
    mixed_qwen_calls_avoided = sum(
        1
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("page_mode") == "mixed"
        and item.get("status") == "processed"
        and item.get("qwen_call_avoided_by_text_layer_context") is True
    )
    mixed_roi_records = [
        item
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("page_mode") == "mixed"
        and item.get("status") == "processed"
        and item.get("crop_strategy") not in {None, "full_page"}
    ]
    mixed_full_page_visual_records = [
        item
        for item in document_visuals
        if item.get("asset_type") == "pdf_page_visual"
        and item.get("page_mode") == "mixed"
        and item.get("status") == "processed"
        and item.get("crop_strategy") == "full_page"
    ]
    embedded_image_records = [
        item
        for item in document_visuals
        if item.get("asset_type") == "embedded_image"
    ]
    structured_chart_records = [
        item
        for item in document_visuals
        if item.get("asset_type") == "native_chart"
    ]
    document_visual_issue_records = [
        item
        for item in document_visuals
        if item.get("asset_type") == "document_visual_issue"
    ]
    embedded_processed = sum(
        1
        for item in embedded_image_records
        if item.get("status") == "processed"
    )
    embedded_processed_with_ocr = sum(
        1
        for item in embedded_image_records
        if item.get("status") == "processed"
        and str(item.get("image_route") or "").startswith("ocr_")
    )
    embedded_processed_with_qwen = sum(
        1
        for item in embedded_image_records
        if item.get("status") == "processed"
        and str(item.get("image_route") or "").startswith("qwen_")
    )
    image_route_counts = Counter(
        str(item.get("image_route") or "unknown")
        for item in visual_results
    )
    run_summary = {
        "session_id": session_id,
        "input_name": root.name,
        "files_discovered": len(discovered),
        "changed_or_new_files": sorted(changed_files),
        "unchanged_files_reused": sorted(unchanged_files),
        "removed_files_invalidated": removed_files,
        "skipped_files": skipped_files,
        "errors": errors,
        "candidate_count": len(candidates),
        "fact_group_count": len(groups),
        "blocking_conflict_count": sum(1 for group in groups if group.severity == "block"),
        "review_count": sum(1 for group in groups if group.severity == "review"),
        "pass_count": sum(1 for group in groups if group.severity == "pass"),
        "engine_signature_changed": not cache_usable,
        "analysis_seconds": round(time.perf_counter() - started_at, 4),
        "model_load_seconds": model_load_seconds,
        "file_timings": file_timings,
        "confirmation_status_counts": confirmation_counts,
        "scanned_pdf": {
            "pages_detected": scanned_pages_detected,
            "pages_processed_with_qwen_vl": (
                scanned_pages_processed_with_qwen
            ),
            "pages_processed_with_openvino_ocr": (
                scanned_pages_processed_with_ocr
            ),
            "pages_processed_with_local_image_ai": scanned_pages_processed,
            "pages_skipped": max(0, scanned_pages_detected - scanned_pages_processed),
            "render_method": "pypdfium2",
            "temporary_pages_retained": False,
        },
        "mixed_pdf": {
            "pages_detected": mixed_pages_detected,
            "pages_processed_with_qwen_vl": (
                mixed_pages_processed_with_qwen
            ),
            "pages_processed_with_openvino_ocr": (
                mixed_pages_processed_with_ocr
            ),
            "pages_processed_with_local_image_ai": (
                mixed_pages_processed
            ),
            "pages_structured_from_text_layer": (
                mixed_pages_structured_from_text_layer
            ),
            "pages_handled_total": mixed_pages_handled,
            "pages_processed_as_observations": (
                mixed_pages_observation_only
            ),
            "pages_selected_for_observations": (
                mixed_pages_observation_selected
            ),
            "pages_observation_errors": (
                mixed_pages_observation_errors
            ),
            "qwen_calls_avoided_by_text_layer_context": (
                mixed_qwen_calls_avoided
            ),
            "pages_rendered_with_roi": len(mixed_roi_records),
            "pages_rendered_full_page": len(
                mixed_full_page_visual_records
            ),
            "mean_roi_area_reduction": round(
                sum(
                    1.0 - float(item.get("crop_ratio", 1.0))
                    for item in mixed_roi_records
                )
                / len(mixed_roi_records),
                4,
            )
            if mixed_roi_records
            else 0.0,
            "pages_skipped": max(
                0,
                mixed_pages_detected - mixed_pages_handled,
            ),
            "render_method": "pypdfium2",
            "temporary_pages_retained": False,
        },
        "document_visuals": {
            "embedded_images_detected": len(
                embedded_image_records
            ),
            "embedded_images_processed": embedded_processed,
            "embedded_images_processed_with_openvino_ocr": (
                embedded_processed_with_ocr
            ),
            "embedded_images_processed_with_qwen_vl": (
                embedded_processed_with_qwen
            ),
            "embedded_images_skipped": max(
                0,
                len(embedded_image_records) - embedded_processed,
            ),
            "embedded_images_with_errors": sum(
                1
                for item in embedded_image_records
                if item.get("status") in {"processed_with_errors", "error"}
            ),
            "native_charts_detected": len(
                structured_chart_records
            ),
            "native_charts_structured": sum(
                1
                for item in structured_chart_records
                if item.get("status") == "structured"
            ),
            "issues": len(document_visual_issue_records),
            "temporary_assets_retained": False,
        },
        "visual_content_cache": dict(visual_content_cache_stats),
        "limits": {
            "max_files": MAX_FILES_PER_TASK,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_text_chars": MAX_TOTAL_TEXT_CHARS,
            "max_single_file_seconds": MAX_SINGLE_FILE_SECONDS,
            "max_pdf_pages": MAX_PDF_PAGES,
        },
        "local_ai": {
            "fact_extractor": bool(openvino_model),
            "image_reader": image_reader is not None,
            "device": actual_device,
            "requested_device": selection.requested if selection else str(device),
            "device_selection_policy": selection.policy if selection else "not_used",
            "available_devices": (
                list(selection.available_devices) if selection else []
            ),
            "full_device_names": (
                selection.full_device_names if selection else {}
            ),
            "model_reused": model_reused,
            "model_id": (
                QWEN_MODEL_ID
                if openvino_vlm_model
                else (
                    reader_identity.get("model_id")
                    if reader_identity
                    else None
                )
            ),
            "model_fingerprint": _model_fingerprint(openvino_vlm_model),
            "image_strategy": (
                reader_identity.get("strategy")
                if reader_identity
                else None
            ),
            "image_reader_identity": reader_identity,
            "image_route_counts": dict(sorted(image_route_counts.items())),
        },
    }

    write_product_facts(
        output / "product-facts.json",
        candidates=candidates,
        groups=groups,
        relations=relations,
        run_summary=run_summary,
    )
    atomic_write_json(
        output / "visual-transcription.json",
        {
            "schema_version": 1,
            "model_id": run_summary["local_ai"]["model_id"],
            "device": actual_device if image_reader is not None else None,
            "images": visual_results,
        },
    )
    atomic_write_json(
        output / "document-visuals.json",
        {
            "schema_version": 1,
            "status": "observations_not_facts",
            "warning": (
                "这里保存的是可追溯观察和结构化上下文，"
                "不是 FactCandidate 或正式事实；如需采用其中信息，"
                "必须另行核对并进入人工确认流程。"
            ),
            "summary": run_summary["document_visuals"],
            "visuals": document_visuals,
        },
    )
    write_conflicts_markdown(
        output / "conflicts.md",
        candidates,
        groups,
        relations,
        run_summary=run_summary,
    )
    write_html_report(output / "evidence-report.html", candidates, groups, relations, run_summary)
    atomic_write_json(output / "run-summary.json", run_summary)
    atomic_write_json(
        state_path,
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "engine_signature": signature,
            "session_id": session_id,
            "input_root": str(root),
            "files": new_state_files,
        },
    )
    initialize_confirmation_outputs(output, session_id=session_id)
    return run_summary
