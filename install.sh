#!/usr/bin/env bash
# One-shot installer for the Lecture Notes Viewer on macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Jonah-Keller/lecture-notes-viewer/main/install.sh | bash
#
# What it does:
#   1. Installs Xcode Command Line Tools if missing (gives you git + python3).
#   2. Clones the repo to ~/lecture-notes-viewer (or updates it if already there).
#   3. Creates a "Launch Lecture Notes" shortcut on your Desktop.
#   4. Hands off to start.sh, which sets up Python deps, Chromium, prompts for
#      your Anthropic API key, and launches the app in your browser.
#
# Re-running this script is safe — it updates and relaunches.

set -e

REPO_URL="https://github.com/Jonah-Keller/lecture-notes-viewer.git"
INSTALL_DIR="$HOME/lecture-notes-viewer"

echo
echo "═══════════════════════════════════════════════════════════"
echo "  Lecture Notes Viewer — installer"
echo "═══════════════════════════════════════════════════════════"
echo

# --- Step 1: Xcode Command Line Tools (provides git + python3) ----------------
if ! xcode-select -p >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
    echo "→ Installing Xcode Command Line Tools (one-time, ~5 minutes)."
    echo "  A system popup will appear. Click \"Install\" and accept the license."
    echo "  This script will wait until it finishes."
    echo
    xcode-select --install 2>/dev/null || true

    # Poll for completion (up to 30 minutes).
    SECONDS_WAITED=0
    until command -v git >/dev/null 2>&1 && xcode-select -p >/dev/null 2>&1; do
        sleep 5
        SECONDS_WAITED=$((SECONDS_WAITED + 5))
        if [ $((SECONDS_WAITED % 30)) -eq 0 ]; then
            echo "  ...still waiting for Xcode CLI tools to finish installing (${SECONDS_WAITED}s)"
        fi
        if [ "$SECONDS_WAITED" -gt 1800 ]; then
            echo
            echo "✗ Timed out waiting for Xcode CLI tools."
            echo "  Open Terminal, run: xcode-select --install"
            echo "  Once it finishes, re-run this installer."
            exit 1
        fi
    done
    echo "  ✓ Xcode CLI tools installed."
    echo
fi

# --- Step 2: Clone or update the repo -----------------------------------------
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "→ Updating existing install at $INSTALL_DIR"
    cd "$INSTALL_DIR"
    git pull --ff-only --quiet
else
    echo "→ Cloning repo to $INSTALL_DIR"
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi
echo "  ✓ Repo ready."
echo

# --- Step 3: Desktop launcher -------------------------------------------------
DESKTOP_LINK="$HOME/Desktop/Launch Lecture Notes.command"
ln -sf "$INSTALL_DIR/Launch Lecture Notes.command" "$DESKTOP_LINK"
chmod +x "$INSTALL_DIR/Launch Lecture Notes.command" "$INSTALL_DIR/start.sh"
echo "→ Desktop shortcut created: \"Launch Lecture Notes\""
echo "  (Double-click it any time to start the app.)"
echo

# --- Step 4: First-run setup + launch -----------------------------------------
echo "→ Running first-time setup. You'll be asked for your Anthropic API key."
echo "  Get one (free to create) at: https://console.anthropic.com/settings/keys"
echo
exec "$INSTALL_DIR/start.sh"
