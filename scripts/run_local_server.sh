#!/usr/bin/env bash
# Run the FastAPI server locally on port 8000 with the date frozen to 2000-01-01.
# Requires AWS credentials in the environment (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY).
#
# Usage:
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... ./scripts/run_local_server.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

cd "$REPO_ROOT/server"

INA_DATETIME_OVERRIDE="2000-01-01" \
CLIENT_GITHUB_PAGES_ORIGIN="http://localhost:3000" \
AWS_S3_INPUT_BUCKET_NAME="${AWS_ACCOUNT_ID}-jyjulianwong-ina-news-input" \
  uv run uvicorn main:app --reload --port 8000
