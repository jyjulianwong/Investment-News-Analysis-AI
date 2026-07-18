#!/usr/bin/env bash
# Serve a local copy of index.html pointing at http://localhost:8000.
# Requires run_local_server.sh to be running in a separate terminal.
#
# Usage:
#   ./scripts/run_local_client.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

TMP_DIR="/tmp/ina-client-local"
mkdir -p "$TMP_DIR"

sed 's|https://investment-news-analysis-ai.onrender.com|http://localhost:8000|g' \
    "$REPO_ROOT/client/index.html" > "$TMP_DIR/index.html"

cp "$REPO_ROOT/client/reports.html" "$TMP_DIR/reports.html"

for f in favicon.ico favicon.svg favicon-16x16.png favicon-32x32.png \
          apple-touch-icon.png android-chrome-192x192.png android-chrome-512x512.png \
          site.webmanifest; do
    cp "$REPO_ROOT/client/$f" "$TMP_DIR/$f"
done

echo "[run_local_client] Patched index.html → $TMP_DIR/index.html"
echo "[run_local_client] Copied  reports.html → $TMP_DIR/reports.html"
echo "[run_local_client] Copied  favicon assets → $TMP_DIR/"
echo "[run_local_client] Serving on http://localhost:3000"
echo "[run_local_client] Make sure run_local_server.sh is running on port 8000"
echo ""

open "http://localhost:3000" 2>/dev/null || xdg-open "http://localhost:3000" 2>/dev/null || true

cd "$TMP_DIR" && python3 -m http.server 3000
