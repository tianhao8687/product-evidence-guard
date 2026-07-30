from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .confirmation import ConfirmationError, apply_decision, export_confirmed
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
    analyze.add_argument(
        "--device",
        default="AUTO",
        help="OpenVINO device. AUTO chooses an Intel GPU when available, otherwise CPU.",
    )

    for command, help_text in (
        ("confirm", "Confirm one candidate after human review"),
        ("reject", "Reject one candidate after human review"),
    ):
        decision = subparsers.add_parser(command, help=help_text)
        decision.add_argument("--output-dir", required=True)
        decision.add_argument("--session-id", required=True)
        decision.add_argument("--candidate-id", required=True)
        decision.add_argument("--reason", required=True)

    export = subparsers.add_parser("export", help="Export currently confirmed, non-stale facts")
    export.add_argument("--output-dir", required=True)
    export.add_argument("--session-id", required=True)
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
            return 1
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if args.command in {"confirm", "reject"}:
        try:
            result = apply_decision(
                args.output_dir,
                session_id=args.session_id,
                candidate_id=args.candidate_id,
                reason=args.reason,
                action=args.command,
            )
        except ConfirmationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "export":
        try:
            result = export_confirmed(args.output_dir, session_id=args.session_id)
        except ConfirmationError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 1
