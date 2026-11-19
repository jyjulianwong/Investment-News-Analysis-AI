#!/usr/bin/env bash
# Builds server/build/lambda.zip: app code + dependencies built for Lambda's
# x86_64/Python 3.12 runtime. Used by CI and for manual deploys — Terraform's
# own zip (terraform/main.tf data.archive_file.server_src) only ever contains
# app code with no dependencies, just enough to create the function once.
# Same script as Obsidian-Server's.
set -euo pipefail

cd "$(dirname "$0")/../server"

rm -rf build
mkdir -p build/package

uv export --no-hashes --no-dev --format requirements-txt -o build/requirements.txt

# `uv pip install` (not plain `pip`) — it doesn't depend on whatever `pip`
# happens to be on PATH, and cross-platform wheel resolution is a first-class
# feature rather than a --platform flag bolted onto pip's own-platform installer.
uv pip install \
  --target build/package \
  --python-platform x86_64-manylinux2014 \
  --python-version 3.12 \
  --only-binary=:all: \
  -r build/requirements.txt

cp ./*.py build/package/

(cd build/package && zip -rq ../lambda.zip . -x "*.pyc" -x "__pycache__/*")

echo "Built server/build/lambda.zip"
