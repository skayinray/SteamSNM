#!/usr/bin/env bash
# Сборка AppImage. Необязательный аргумент — путь к appimagetool.AppImage.
set -euo pipefail
cd "$(dirname "$0")"
TOOL="${1:-/tmp/opencode/appimagetool.AppImage}"
python3 tools/build_appimage.py "$TOOL"
