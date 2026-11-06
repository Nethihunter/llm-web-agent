#!/bin/bash
# Universal installer for llm-web-agent.
# Supported: Debian, Ubuntu, Linux Mint, Fedora, openSUSE (Leap / Tumbleweed).
# Run from the repository root:   ./scripts/install.sh
set -euo pipefail

info()  { printf '\033[1;34m[INFO]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[1;32m[ OK ]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*"; }
fail()  { printf '\033[1;31m[FAIL]\033[0m  %s\n' "$*" >&2; exit 1; }

# ---- 0. Sanity checks -------------------------------------------------------
[ -f requirements.txt ] && [ -f run.py ] \
    || fail "Run this script from the llm-web-agent repository root."

command -v python3 >/dev/null || fail "python3 not found."
PY=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
case "$PY" in
    3.11|3.12|3.13|3.14) ok "Python $PY detected" ;;
    *) fail "Python 3.11 or newer is required (found $PY)." ;;
esac

# ---- 1. Detect the distribution --------------------------------------------
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO="${ID:-unknown}"
    DISTRO_LIKE="${ID_LIKE:-}"
else
    DISTRO=unknown
    DISTRO_LIKE=""
fi
info "Detected distribution: ${PRETTY_NAME:-$DISTRO}"

# ---- 2. Virtual environment ------------------------------------------------
if [ ! -d .venv ]; then
    info "Creating virtual environment in .venv/"
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
ok "Virtual environment ready"

# ---- 3. Python packages ----------------------------------------------------
info "Installing Python packages (agent + dashboard)..."
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt
if [ -f requirements-server.txt ]; then
    python -m pip install -r requirements-server.txt
fi
ok "Python packages installed"

# ---- 4. System libraries for the browser -----------------------------------
# Playwright ships an installer for these; it handles Debian/Ubuntu and
# Fedora/RHEL. For openSUSE we fall back to zypper.
info "Installing system libraries for the browser..."
if python -m playwright install-deps >/dev/null 2>&1; then
    ok "System libraries installed via 'playwright install-deps'"
else
    warn "'playwright install-deps' failed, trying manual package installation..."
    case "$DISTRO" in
        debian|ubuntu|linuxmint|*debian*)
            sudo apt-get update
            sudo apt-get install -y \
                libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
                libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
                libxdamage1 libxfixes3 libxrandr2 libgbm1 \
                libpango-1.0-0 libcairo2 libasound2
            ;;
        fedora|*fedora*|rhel|centos)
            sudo dnf install -y \
                nss nspr atk at-spi2-atk cups-libs libdrm \
                libXcomposite libXdamage libXfixes libXrandr \
                mesa-libgbm pango cairo alsa-lib
            ;;
        opensuse*|suse*|sles)
            sudo zypper --non-interactive install \
                mozilla-nss mozilla-nspr libatk-1_0-0 libatk-bridge-2_0-0 \
                cups-libs libdrm2 libxkbcommon0 libXcomposite1 \
                libXdamage1 libXfixes3 libXrandr2 libgbm1 \
                pango cairo libasound2
            ;;
        *)
            warn "Unknown distribution family. Install the browser libraries"
            warn "listed at https://playwright.dev/python/docs/intro#system-requirements"
            ;;
    esac
fi

# ---- 5. Chromium used by Playwright ---------------------------------------
# `scripts/start_local_chrome.sh` can launch this binary directly if no
# system Chrome/Chromium is installed.
info "Installing the Chromium engine used by Playwright..."
python -m playwright install chromium
ok "Chromium installed"

# ---- 6. Starter key.json ---------------------------------------------------
if [ ! -f key.json ] && [ -f key.json.example ]; then
    cp key.json.example key.json
    warn "Created key.json from key.json.example."
    warn "Edit key.json and add your LLM provider API key before running a task."
fi

# ---- 7. Optional: dashboard static assets ---------------------------------
if [ -x scripts/fetch_dashboard_assets.sh ]; then
    info "Downloading dashboard static assets..."
    if ./scripts/fetch_dashboard_assets.sh; then
        ok "Dashboard assets ready"
    else
        warn "Could not download dashboard assets (optional; the agent itself works without them)."
    fi
fi

# ---- 8. Smoke test ---------------------------------------------------------
info "Running a smoke test..."
python - <<'PY'
import sys
try:
    import requests, playwright, bs4, lxml  # noqa: F401
    print("  - Third-party packages import correctly")
except ImportError as e:
    print(f"  x Third-party import failed: {e}"); sys.exit(1)

try:
    from llm.factory import create_client, load_config  # noqa: F401
    from html_processor import extract_clean_html       # noqa: F401
    from validator import validate_llm_output           # noqa: F401
    print("  - Project modules import correctly")
except Exception as e:
    print(f"  x Project import failed: {e}"); sys.exit(1)
PY
ok "Smoke test passed"

# ---- Done ------------------------------------------------------------------
echo
ok "Installation complete."
echo
echo "Next steps:"
echo "  1. Edit key.json and set your API key (e.g. DEEPSEEK_API_KEY)."
echo "  2. Start a browser:   source .venv/bin/activate && ./scripts/start_local_chrome.sh"
echo "  3. Run a demo task:   python3 run.py --task saucedemo_checkout --yes"
echo "  4. (Optional) Dashboard:  uvicorn api:app --reload --port 8000"
