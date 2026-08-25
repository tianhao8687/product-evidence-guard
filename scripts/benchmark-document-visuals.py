from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.state import atomic_write_json
from server import ResidentModelCache


def _phase_record(
    *,
    label: str,
    input_dir: Path,
    output_dir: Path,
    model_path: Path,
    device: str,
    cache: ResidentModelCache,
) -> dict[str, Any]:
    started = time.perf_counter()
    prepared = cache.prepare(
        openvino_model=None,
        openvino_vlm_model=str(model_path),
        requested_device=device,
    )
    summary = analyze_directory(
        input_dir,
        output_dir,
        openvino_vlm_model=str(model_path),
        device=device,
        preloaded_image_reader=prepared["vlm"],
        preloaded_model_load_seconds=prepared["load_seconds"],
        preloaded_model_reused=prepared["reused"],
        device_selection=prepared["selection"],
    )
    wall_seconds = round(time.perf_counter() - started, 4)
    return {
        "label": label,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "wall_seconds": wall_seconds,
        "analysis_seconds": summary["analysis_seconds"],
        "model_load_seconds": summary["model_load_seconds"],
        "model_reused": summary["local_ai"]["model_reused"],
        "device": summary["local_ai"]["device"],
        "candidate_count": summary["candidate_count"],
        "blocking_conflict_count": summary["blocking_conflict_count"],
        "review_count": summary["review_count"],
        "pass_count": summary["pass_count"],
        "image_route_counts": summary["local_ai"]["image_route_counts"],
        "visual_content_cache": summary["visual_content_cache"],
        "unchanged_files_reused": summary["unchanged_files_reused"],
        "mixed_pdf": summary["mixed_pdf"],
        "errors": summary["errors"],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure cold model load, resident hot analysis, business-cache "
            "reuse, and an optional real mixed-PDF phase in one process."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--real-input", type=Path)
    parser.add_argument("--device", default="AUTO")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    input_dir = args.input.expanduser().resolve()
    model_path = args.model.expanduser().resolve()
    output_root = args.output.expanduser().resolve()
    real_input = (
        args.real_input.expanduser().resolve()
        if args.real_input is not None
        else None
    )
    if not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    if not model_path.is_dir():
        raise ValueError(f"Model directory does not exist: {model_path}")
    if real_input is not None and not real_input.is_dir():
        raise ValueError(
            f"Real mixed-PDF input directory does not exist: {real_input}"
        )
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(
            "Output directory must be new or empty so prior analysis state "
            "cannot contaminate the benchmark."
        )
    output_root.mkdir(parents=True, exist_ok=True)

    resident_cache = ResidentModelCache()
    phases: list[dict[str, Any]] = []
    phases.append(
        _phase_record(
            label="cold_model_and_cold_business_cache",
            input_dir=input_dir,
            output_dir=output_root / "cold",
            model_path=model_path,
            device=args.device,
            cache=resident_cache,
        )
    )
    phases.append(
        _phase_record(
            label="resident_model_and_cold_business_cache",
            input_dir=input_dir,
            output_dir=output_root / "hot",
            model_path=model_path,
            device=args.device,
            cache=resident_cache,
        )
    )
    phases.append(
        _phase_record(
            label="resident_model_and_warm_business_cache",
            input_dir=input_dir,
            output_dir=output_root / "hot",
            model_path=model_path,
            device=args.device,
            cache=resident_cache,
        )
    )
    if real_input is not None:
        phases.append(
            _phase_record(
                label="resident_model_real_mixed_pdf",
                input_dir=real_input,
                output_dir=output_root / "real-mixed-pdf",
                model_path=model_path,
                device=args.device,
                cache=resident_cache,
            )
        )

    cold = phases[0]
    hot = phases[1]
    result = {
        "schema_version": 1,
        "status": "completed",
        "model_path": str(model_path),
        "requested_device": args.device,
        "phases": phases,
        "comparison": {
            "cold_minus_hot_seconds": round(
                cold["wall_seconds"] - hot["wall_seconds"],
                4,
            ),
            "hot_faster_percent": round(
                (
                    1.0
                    - hot["wall_seconds"] / cold["wall_seconds"]
                )
                * 100,
                2,
            )
            if cold["wall_seconds"] > 0
            else 0.0,
            "cold_over_hot_ratio": round(
                cold["wall_seconds"] / hot["wall_seconds"],
                2,
            )
            if hot["wall_seconds"] > 0
            else None,
        },
    }
    result_path = output_root / "benchmark-results.json"
    atomic_write_json(result_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
