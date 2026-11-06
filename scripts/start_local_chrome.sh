#!/bin/bash
# Start a local Chrome/Chromium with remote debugging on 127.0.0.1:9222.
# Uses a dedicated profile directory — never your daily Chrome profile.
#
# It searches, in this order:
#   1. google-chrome-stable / google-chrome       (system install)
#   2. chromium / chromium-browser                (distro package)
#   3. the Chromium binary bundled by Playwright  (~/.cache/ms-playwright)
set -euo pipefail

BROWSER=""
for candidate in \
    "$(command -v google-chrome-stable 2>/dev/null || true)" \
    "$(command -v google-chrome        2>/dev/null || true)" \
    "$(command -v chromium             2>/dev/null || true)" \
    "$(command -v chromium-browser     2>/dev/null || true)"
do
    if [ -n "$candidate" ]; then BROWSER="$candidate"; break; fi
done

if [ -z "$BROWSER" ]; then
    BROWSER=$(find "${HOME}/.cache/ms-playwright" -type f -name chrome 2>/dev/null | head -n 1 || true)
fi

if [ -z "$BROWSER" ]; then
    echo "❌ No Chrome or Chromium found."
    echo "   Run ./scripts/install.sh first,"
    echo "   or install google-chrome / chromium from your distribution."
    exit 1
fi

echo "🚀 Starting $BROWSER (remote debugging on 127.0.0.1:9222)"
"$BROWSER" \
    --remote-debugging-port=9222 \
    --user-data-dir="$HOME/.llm-web-agent-chrome" \
    --no-first-run \
    --no-default-browser-check &
