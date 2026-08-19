#!/usr/bin/env bash
# ============================================================================
#  Lecture Notes Viewer — double-click installer
#
#  Download this file, double-click it, and it does everything: installs the
#  developer tools if they're missing, downloads the app, puts a launcher on
#  your Desktop, and opens the app in your browser.
#
#  macOS will refuse to run a downloaded file on the first try. If you get
#  "cannot be opened because it is from an unidentified developer":
#      right-click (or Control-click) this file → Open → Open.
#  You only have to do that once.
# ============================================================================

# Keep the window open so any error is readable instead of flashing past.
trap 'echo; echo "──────────────────────────────────────────"; echo "Press Enter to close this window."; read -r _' EXIT

set -e

REPO_RAW="https://raw.githubusercontent.com/Jonah-Keller/lecture-notes-viewer/main"

echo
echo "═══════════════════════════════════════════════════════════"
echo "  Lecture Notes Viewer"
echo "═══════════════════════════════════════════════════════════"
echo

# Clear the download quarantine flag on ourselves so a re-run is friction-free.
xattr -d com.apple.quarantine "${BASH_SOURCE[0]}" 2>/dev/null || true

# If this file sits inside an existing clone, run that copy's installer directly.
HERE="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HERE/install.sh" ] && [ -d "$HERE/.git" ]; then
    exec bash "$HERE/install.sh"
fi

echo "→ Fetching the installer..."
TMP="$(mktemp -t lecnotes-install)"
if ! curl -fsSL "$REPO_RAW/install.sh" -o "$TMP"; then
    echo
    echo "✗ Could not download the installer."
    echo "  Check your internet connection and try again."
    exit 1
fi

exec bash "$TMP"
