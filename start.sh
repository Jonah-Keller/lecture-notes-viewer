#!/usr/bin/env bash
# Start the lecture notes viewer locally.
# Idempotent: safe to run repeatedly. First run does setup, later runs just launch.
# Usage: ./start.sh            (foreground, opens browser)
#        ./start.sh --bg       (background, log to .lecnotes.log)
#        ./start.sh --no-open  (don't open browser)
set -euo pipefail

cd "$(dirname "$0")"

OPEN_BROWSER=1
RUN_BG=0
for arg in "$@"; do
    case "$arg" in
        --bg) RUN_BG=1 ;;
        --no-open) OPEN_BROWSER=0 ;;
    esac
done

# --- Check Python 3 is available ----------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo
    echo "python3 is not installed."
    echo
    echo "    On macOS, install it with one of:"
    echo "      - Xcode Command Line Tools (recommended):  xcode-select --install"
    echo "      - Homebrew:                                 brew install python"
    echo "      - Or download from https://www.python.org/downloads/"
    echo
    echo "    Re-run this script after Python is installed."
    exit 1
fi

PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info[1])')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
    echo "Python 3.11+ is required (found $PY_MAJOR.$PY_MINOR)."
    echo "Install a newer Python from https://www.python.org/downloads/ and re-run."
    exit 1
fi

# --- Virtualenv ---------------------------------------------------------------
if [ ! -d .venv ]; then
    echo "Creating virtualenv..."
    python3 -m venv .venv
fi

.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

# --- Playwright Chromium (one-time, ~150MB) -----------------------------------
if ! .venv/bin/python -c "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(); b.close(); p.stop()" >/dev/null 2>&1; then
    echo "Installing Chromium for PDF export (one-time, ~150MB)..."
    .venv/bin/playwright install chromium
fi

# --- .env / API key -----------------------------------------------------------
if [ ! -f .env ]; then
    cp .env.example .env
fi

# If the API key is still the placeholder, prompt interactively.
if grep -qE '^ANTHROPIC_API_KEY=(sk-ant-\.\.\.|)$' .env 2>/dev/null; then
    echo
    echo "No Anthropic API key set yet."
    echo "Get one at: https://console.anthropic.com/settings/keys"
    echo
    printf "Paste your ANTHROPIC_API_KEY (starts with sk-ant-): "
    read -r USER_KEY
    if [ -z "$USER_KEY" ]; then
        echo "No key entered. Edit .env manually and re-run."
        exit 1
    fi
    /usr/bin/sed -i '' "s|^ANTHROPIC_API_KEY=.*$|ANTHROPIC_API_KEY=${USER_KEY}|" .env
    echo "Key saved to .env"
fi

# --- Launch -------------------------------------------------------------------
export FLASK_APP=app.py
URL="http://localhost:8080"

open_browser_when_ready() {
    for _ in $(seq 1 30); do
        if curl -fsS "$URL" >/dev/null 2>&1; then
            break
        fi
        sleep 0.3
    done
    if command -v open >/dev/null 2>&1; then
        open "$URL"
    fi
}

if [ "$RUN_BG" = "1" ]; then
    nohup .venv/bin/python app.py >> .lecnotes.log 2>&1 &
    echo $! > .lecnotes.pid
    echo "Started in background (pid $(cat .lecnotes.pid)). Tail: tail -f .lecnotes.log"
    echo "Open $URL"
    [ "$OPEN_BROWSER" = "1" ] && open_browser_when_ready
else
    echo
    echo "Lecture Notes Viewer running at $URL"
    echo "(Press Ctrl-C in this window to stop the server.)"
    echo
    [ "$OPEN_BROWSER" = "1" ] && (open_browser_when_ready &)
    exec .venv/bin/python app.py
fi
