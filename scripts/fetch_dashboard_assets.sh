#!/bin/bash
# Download the JavaScript files the dashboard templates expect.
# Versions are pinned. Run once, then commit the files (or add them to
# .gitignore and run this script in your Docker build).
set -uo pipefail

DEST="$(cd "$(dirname "$0")/.." && pwd)/dashboard/static"
mkdir -p "$DEST"
failed=0

fetch() {
    local url="$1" out="$2"
    printf '→ %s ... ' "$out"
    if curl -fsSL --max-time 60 "$url" -o "$DEST/$out" && [ -s "$DEST/$out" ]; then
        echo "ok ($(wc -c < "$DEST/$out") bytes)"
    else
        echo "FAILED ($url)"
        rm -f "$DEST/$out"
        failed=1
    fi
}

fetch https://cdn.jsdelivr.net/npm/htmx.org@1.9.12/dist/htmx.min.js  htmx.min.js
fetch https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js   chart.min.js
fetch https://cdn.jsdelivr.net/npm/alpinejs@3.13.5/dist/cdn.min.js    alpine.min.js

exit $failed
