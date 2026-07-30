from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Mapping
import uuid

from . import __version__
from .confirmation import initialize_confirmation_outputs, reconcile_confirmations
from .extractor import extract_rule_candidates
from .graph import build_graph
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
    parse_pdf_document,
    render_pdf_page,
    sha256_file,
)
from .qwen_vl_reader import QwenVlReadResult, QwenVlReader
from .reports import write_conflicts_markdown, write_html_report, write_product_facts
from .state import STATE_SCHEMA_VERSION, atomic_write_json, load_state


QWEN_MODEL_ID = "OpenVINO/Qwen3-VL-8B-Instruct-int4-ov"
ENGINE_SCHEMA_REVISION = "competition-v1-qwen-two-stage-2-pdf-scan"
MAX_FILES_PER_TASK = 100
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_TEXT_CHARS = 2_000_000
MAX_SINGLE_FILE_SECONDS = 300.0


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


def _engine_signature(openvino_model: str | None, openvino_vlm_model: str | None, device: str) -> str:
    payload = {
        "version": __version__,
        "schema_revision": ENGINE_SCHEMA_REVISION,
        "openvino_model": str(Path(openvino_model).resolve()) if openvino_model else None,
        "openvino_vlm_model": str(Path(openvino_vlm_model).resolve()) if openvino_vlm_model else None,
        "openvino_model_fingerprint": _model_fingerprint(openvino_model),
        "openvino_vlm_model_fingerprint": _model_fingerprint(openvino_vlm_model),
        "device": device,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


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
        f"{block.locator.get('transcription_id', '')}\0qwen_vl_pdf"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _retarget_pdf_visual_result(
    visual_result: QwenVlReadResult,
    *,
    source_file: str,
    file_hash: str,
    page_number: int,
    pixel_width: int,
    pixel_height: int,
) -> None:
    """Replace all temporary PNG evidence references with the original PDF page."""

    block_by_old_id: dict[str, SourceBlock] = {}
    for block in visual_result.source_blocks:
        old_id = block.block_id
        locator = {
            key: value
            for key, value in block.locator.items()
            if key not in {"image"}
        }
        locator.update(
            {
                "pdf": source_file,
                "page": page_number,
                "page_source": "rendered_scanned_page",
                "render_method": "pypdfium2",
                "rendered_page_pixels": [pixel_width, pixel_height],
            }
        )
        block.source_file = source_file
        block.source_kind = "pdf_scan_qwen_vl"
        block.file_hash = file_hash
        block.locator = locator
        block.extraction_method = "qwen_vl_visual_transcription"
        block.recognition_confidence_source = "model_self_assessment"
        block.provenance.update(
            {
                "source_document": source_file,
                "page": page_number,
                "render_method": "pypdfium2",
                "temporary_render_retained": False,
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
        candidate.extraction_method = "qwen_vl_field_mapping"
        candidate.mapping_confidence_source = "model_self_assessment"
        candidate.provenance.update(
            {
                "source_document": source_file,
                "page": page_number,
                "render_method": "pypdfium2",
                "temporary_render_retained": False,
                "recognition_confidence_source": block.recognition_confidence_source,
                "mapping_confidence_source": candidate.mapping_confidence_source,
            }
        )
        candidate.candidate_id = _pdf_candidate_id(block, candidate)
        retargeted_candidates.append(candidate)

    visual_result.fact_candidates = retargeted_candidates
    visual_result.image_file = f"{source_file}#page={page_number}"
    visual_result.file_hash = file_hash


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
    preloaded_image_reader: QwenVlReader | None = None,
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
    signature = _engine_signature(
        openvino_model,
        openvino_vlm_model,
        actual_device,
    )
    cache_usable = (
        same_input_root
        and previous.get("engine_signature") == signature
    )
    previous_files: dict[str, Any] = previous.get("files", {}) if cache_usable else {}

    model_load_started = time.perf_counter()
    llm_extractor = preloaded_llm_extractor or (
        OpenVinoFactExtractor(openvino_model, actual_device)
        if openvino_model
        else None
    )
    image_reader = preloaded_image_reader or (
        QwenVlReader.from_openvino(
            openvino_vlm_model,
            device=actual_device,
            model_id=QWEN_MODEL_ID,
        )
        if openvino_vlm_model
        else None
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
            cached_skipped_pages = cached.get("scanned_pdf_pages_skipped")
            if isinstance(cached_skipped_pages, list) and cached_skipped_pages:
                skipped_files.append(
                    {
                        "file": relative,
                        "reason": "scanned_pdf_pages_require_local_image_model",
                        "pages": ",".join(str(page) for page in cached_skipped_pages),
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
                    visual_result = image_reader.analyze_image(
                        path,
                        root,
                        deadline=file_started + MAX_SINGLE_FILE_SECONDS,
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
                    }
                    continue
            else:
                scanned_pdf_pages: tuple[int, ...] = ()
                if suffix == ".pdf":
                    pdf_result = parse_pdf_document(path, relative, file_hash)
                    blocks = list(pdf_result.blocks)
                    scanned_pdf_pages = pdf_result.scanned_page_numbers
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
            serialized_pdf_visuals: list[dict[str, Any]] = []
            if scanned_pdf_pages:
                if image_reader is None:
                    skipped_files.append(
                        {
                            "file": relative,
                            "reason": "scanned_pdf_pages_require_local_image_model",
                            "pages": ",".join(str(page) for page in scanned_pdf_pages),
                        }
                    )
                else:
                    with tempfile.TemporaryDirectory(prefix="peg-pdf-pages-") as temporary:
                        temporary_root = Path(temporary)
                        for page_number in scanned_pdf_pages:
                            rendered_path: Path | None = None
                            page_started = time.perf_counter()
                            try:
                                rendered_page = render_pdf_page(
                                    path,
                                    page_number,
                                    temporary_root,
                                )
                                rendered_path = rendered_page.image_path
                                visual_result = image_reader.analyze_image(
                                    rendered_page.image_path,
                                    temporary_root,
                                    deadline=file_started + MAX_SINGLE_FILE_SECONDS,
                                )
                                _retarget_pdf_visual_result(
                                    visual_result,
                                    source_file=relative,
                                    file_hash=file_hash,
                                    page_number=page_number,
                                    pixel_width=rendered_page.pixel_width,
                                    pixel_height=rendered_page.pixel_height,
                                )
                                serialized_visual = visual_result.to_dict()
                                serialized_visual.update(
                                    {
                                        "source_kind": "pdf_scan_qwen_vl",
                                        "source_file": relative,
                                        "page": page_number,
                                        "render_method": "pypdfium2",
                                        "rendered_page_retained": False,
                                        "elapsed_seconds": round(
                                            time.perf_counter() - page_started,
                                            4,
                                        ),
                                    }
                                )
                                serialized_pdf_visuals.append(serialized_visual)
                                visual_results.append(serialized_visual)
                                pdf_visual_candidates.extend(
                                    visual_result.fact_candidates
                                )
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

            candidates = rule_candidates + model_candidates + pdf_visual_candidates
            all_candidates.extend(candidates)
            state_entry: dict[str, Any] = {
                "file_hash": file_hash,
                "candidate_count": len(candidates),
                "candidates": _serialize_candidates(candidates),
            }
            if suffix == ".pdf":
                state_entry["scanned_pdf_pages"] = list(scanned_pdf_pages)
                if serialized_pdf_visuals:
                    state_entry["visual_results"] = serialized_pdf_visuals
                elif scanned_pdf_pages and image_reader is None:
                    state_entry["scanned_pdf_pages_skipped"] = list(
                        scanned_pdf_pages
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
    scanned_pages_processed = sum(
        1
        for item in visual_results
        if item.get("source_kind") == "pdf_scan_qwen_vl"
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
            "pages_processed_with_qwen_vl": scanned_pages_processed,
            "pages_skipped": max(0, scanned_pages_detected - scanned_pages_processed),
            "render_method": "pypdfium2",
            "temporary_pages_retained": False,
        },
        "limits": {
            "max_files": MAX_FILES_PER_TASK,
            "max_file_bytes": MAX_FILE_BYTES,
            "max_total_text_chars": MAX_TOTAL_TEXT_CHARS,
            "max_single_file_seconds": MAX_SINGLE_FILE_SECONDS,
            "max_pdf_pages": MAX_PDF_PAGES,
        },
        "local_ai": {
            "fact_extractor": bool(openvino_model),
            "image_reader": bool(openvino_vlm_model),
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
            "model_id": QWEN_MODEL_ID if openvino_vlm_model else None,
            "model_fingerprint": _model_fingerprint(openvino_vlm_model),
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
            "model_id": QWEN_MODEL_ID if openvino_vlm_model else None,
            "device": actual_device if openvino_vlm_model else None,
            "images": visual_results,
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
