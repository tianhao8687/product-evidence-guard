from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .engine import analyze_directory


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="product-evidence-guard",
        description="Local multi-source product fact verification with traceable evidence.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze = subparsers.add_parser("analyze", help="Analyze one product material directory")
    analyze.add_argument("input_dir", help="Directory containing product materials")
    analyze.add_argument(
        "--output",
        default=None,
        help="Output directory. Default: <input_dir>/.peg-output",
    )
    analyze.add_argument(
        "--openvino-model",
        default=None,
        help="Optional local OpenVINO GenAI LLM model directory for extra fact extraction",
    )
    analyze.add_argument(
        "--openvino-vlm-model",
        default=None,
        help="Optional local OpenVINO GenAI VLM model directory for image text reading",
    )
    analyze.add_argument("--device", default="CPU", help="OpenVINO device, e.g. CPU, GPU or NPU")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "analyze":
        input_dir = Path(args.input_dir)
        output = Path(args.output) if args.output else input_dir / ".peg-output"
        try:
            summary = analyze_directory(
                input_dir,
                output,
                openvino_model=args.openvino_model,
                openvino_vlm_model=args.openvino_vlm_model,
                device=args.device,
            )
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\n报告已生成：{output.resolve()}")
        return 0
    return 2
