#!/usr/bin/env bash
# Build the Lambda Docker image and run the handler end-to-end with the date
# frozen to 2000-01-01. AWS credentials are passed via ~/.aws mount.
#
# Usage:
#   ./scripts/run_local_lambda.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

docker buildx build \
    --platform linux/amd64 \
    --provenance=false \
    --load \
    -t jyjulianwong-ina-lambda-agent-local \
    "$REPO_ROOT/lambda"

docker run --rm \
    --platform linux/amd64 \
    -v "$HOME/.aws:/root/.aws:ro" \
    -e AWS_PROFILE="${AWS_PROFILE:-default}" \
    -e INA_DATETIME_OVERRIDE="2000-01-01" \
    -e AWS_REGION_NAME="eu-west-2" \
    -e AWS_S3_INPUT_BUCKET_NAME="${AWS_ACCOUNT_ID}-jyjulianwong-ina-news-input" \
    -e AWS_S3_OUTPUT_BUCKET_NAME="${AWS_ACCOUNT_ID}-jyjulianwong-ina-news-output" \
    -e SSM_OPENROUTER_PARAM="/jyjulianwong-ina/openrouter_api_key" \
    -e SSM_TAVILY_PARAM="/jyjulianwong-ina/tavily_api_key" \
    -e PYTHONUNBUFFERED=1 \
    --entrypoint python \
    jyjulianwong-ina-lambda-agent-local \
    -c "import agent; result = agent.handler({}, None); print('[handler]', result)"
