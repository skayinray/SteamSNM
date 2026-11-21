#!/usr/bin/env bash
# Build the AppImage. Optional argument: path to appimagetool.AppImage.
set -euo pipefail
cd "$(dirname "$0")"
TOOL="${1:-/tmp/appimagetool.AppImage}"
python3 tools/build_appimage.py "$TOOL"
