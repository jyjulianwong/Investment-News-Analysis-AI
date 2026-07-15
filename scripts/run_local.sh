#!/usr/bin/env bash
# Run the LangGraph agent locally with dummy snippets.
# S3 reads and writes are bypassed; output is written to OUTPUT_DIR.
#
# Usage:
#   OPENROUTER_API_KEY=... TAVILY_API_KEY=... ./scripts/run_local.sh
#
# Optional env vars:
#   OUTPUT_DIR   Where to write report files (default: /tmp/ina-local)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LAMBDA_DIR="$REPO_ROOT/lambda"

: "${OPENROUTER_API_KEY:?Please export OPENROUTER_API_KEY before running this script}"
: "${TAVILY_API_KEY:?Please export TAVILY_API_KEY before running this script}"

OUTPUT_DIR="${OUTPUT_DIR:-/tmp/ina-local}"
mkdir -p "$OUTPUT_DIR"

echo "[run_local] Lambda dir : $LAMBDA_DIR"
echo "[run_local] Output dir : $OUTPUT_DIR"
echo "[run_local] Starting agent..."
echo ""

cd "$LAMBDA_DIR"
OUTPUT_DIR="$OUTPUT_DIR" uv run python "$SCRIPT_DIR/run_local.py"
