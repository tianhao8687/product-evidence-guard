#!/usr/bin/env sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
python -m product_evidence_guard analyze ./samples/demo --output ./demo-output
printf '%s\n' 'Open demo-output/evidence-report.html to inspect the evidence graph.'
