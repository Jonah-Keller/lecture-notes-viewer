#!/usr/bin/env bash
# Double-click this file to launch the Lecture Notes Viewer.
# It runs setup the first time, launches the server, and opens your browser.
# Press Ctrl-C in the Terminal window to stop the server.

set -e

# Resolve the real directory of this script even if it's a symlink (e.g. on Desktop).
SOURCE="${BASH_SOURCE[0]}"
while [ -L "$SOURCE" ]; do
    DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"
    SOURCE="$(readlink "$SOURCE")"
    [[ "$SOURCE" != /* ]] && SOURCE="$DIR/$SOURCE"
done
APP_DIR="$(cd -P "$(dirname "$SOURCE")" && pwd)"

cd "$APP_DIR"
exec ./start.sh
