from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .extractor import extract_rule_candidates
from .graph import build_graph
from .models import FactCandidate, SourceBlock
from .openvino_adapter import OpenVinoFactExtractor, OpenVinoImageTextReader
from .parsers import IMAGE_EXTENSIONS, discover_files, parse_file, sha256_file
from .reports import write_conflicts_markdown, write_html_report, write_product_facts
from .state import atomic_write_json, load_state


def _engine_signature(openvino_model: str | None, openvino_vlm_model: str | None, device: str) -> str:
    payload = {
        "version": __version__,
        "openvino_model": str(Path(openvino_model).resolve()) if openvino_model else None,
        "openvino_vlm_model": str(Path(openvino_vlm_model).resolve()) if openvino_vlm_model else None,
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


def analyze_directory(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    openvino_model: str | None = None,
    openvino_vlm_model: str | None = None,
    device: str = "CPU",
) -> dict[str, Any]:
    root = Path(input_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Input directory does not exist: {root}")
    output.mkdir(parents=True, exist_ok=True)

    state_path = output / "analysis-state.json"
    previous = load_state(state_path)
    signature = _engine_signature(openvino_model, openvino_vlm_model, device)
    cache_usable = previous.get("engine_signature") == signature
    previous_files: dict[str, Any] = previous.get("files", {}) if cache_usable else {}

    llm_extractor = OpenVinoFactExtractor(openvino_model, device) if openvino_model else None
    image_reader = OpenVinoImageTextReader(openvino_vlm_model, device) if openvino_vlm_model else None

    all_candidates: list[FactCandidate] = []
    new_state_files: dict[str, Any] = {}
    changed_files: list[str] = []
    unchanged_files: list[str] = []
    skipped_files: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    discovered = discover_files(root)
    for path in discovered:
        if output == path or output in path.parents:
            continue
        relative = path.relative_to(root).as_posix()
        suffix = path.suffix.casefold()

        # An OCR sidecar already represents the image evidence. Avoid double counting it.
        if suffix in IMAGE_EXTENSIONS and _has_ocr_sidecar(path):
            skipped_files.append({"file": relative, "reason": "ocr_sidecar_present"})
            continue

        file_hash = sha256_file(path)
        cached = previous_files.get(relative)
        if cached and cached.get("file_hash") == file_hash and not cached.get("last_error"):
            candidates = _deserialize_candidates(cached.get("candidates", []))
            all_candidates.extend(candidates)
            new_state_files[relative] = cached
            unchanged_files.append(relative)
            continue

        changed_files.append(relative)
        try:
            if suffix in IMAGE_EXTENSIONS:
                if image_reader is None:
                    skipped_files.append({"file": relative, "reason": "no_local_image_model_or_ocr_sidecar"})
                    blocks: list[SourceBlock] = []
                else:
                    blocks = image_reader.read(path, root)
            else:
                blocks = parse_file(path, root)

            rule_candidates: list[FactCandidate] = []
            unmatched_blocks: list[SourceBlock] = []
            for block in blocks:
                extracted = extract_rule_candidates(block)
                if extracted:
                    rule_candidates.extend(extracted)
                else:
                    unmatched_blocks.append(block)

            model_candidates = llm_extractor.extract(unmatched_blocks) if llm_extractor and unmatched_blocks else []
            candidates = rule_candidates + model_candidates
            all_candidates.extend(candidates)
            new_state_files[relative] = {
                "file_hash": file_hash,
                "candidate_count": len(candidates),
                "candidates": _serialize_candidates(candidates),
            }
        except Exception as exc:  # keep one malformed file from killing the complete analysis
            errors.append({"file": relative, "error": f"{type(exc).__name__}: {exc}"})
            new_state_files[relative] = {
                "file_hash": file_hash,
                "candidate_count": 0,
                "candidates": [],
                "last_error": f"{type(exc).__name__}: {exc}",
            }

    current_paths = set(new_state_files)
    removed_files = sorted(set(previous_files) - current_paths) if cache_usable else []

    candidates, groups, relations = build_graph(all_candidates)
    run_summary = {
        "input_dir": str(root),
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
        "local_ai": {
            "fact_extractor": bool(openvino_model),
            "image_reader": bool(openvino_vlm_model),
            "device": device,
        },
    }

    write_product_facts(
        output / "product-facts.json",
        candidates=candidates,
        groups=groups,
        relations=relations,
        run_summary=run_summary,
    )
    write_conflicts_markdown(output / "conflicts.md", candidates, groups, relations)
    write_html_report(output / "evidence-report.html", candidates, groups, relations, run_summary)
    atomic_write_json(output / "run-summary.json", run_summary)
    atomic_write_json(
        state_path,
        {
            "schema_version": 1,
            "engine_signature": signature,
            "files": new_state_files,
        },
    )
    return run_summary
