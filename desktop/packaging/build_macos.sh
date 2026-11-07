#!/usr/bin/env bash
# Build the Nexus Enterprise macOS app (.app + .dmg) for internal use.
# Unsigned ad-hoc build: no Apple Developer ID required. First launch
# needs the right-click Open gesture for Gatekeeper (see README).
# Usage: ./desktop/packaging/build_macos.sh [--skip-dmg] [--clean]
# Output: dist/Nexus Enterprise.app, dist/Nexus-Enterprise-0.1.0-mac.dmg
set -euo pipefail

SKIP_DMG=0
CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --skip-dmg) SKIP_DMG=1 ;;
    --clean) CLEAN=1 ;;
    *) echo "Unknown arg: $arg" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESKTOP_DIR="$REPO_ROOT/desktop"
SPEC="$DESKTOP_DIR/packaging/nexus-desktop.spec"
DIST_DIR="$DESKTOP_DIR/dist"
BUNDLE_NAME="Nexus Enterprise.app"
DMG_NAME="Nexus-Enterprise-0.1.0-mac.dmg"

cd "$DESKTOP_DIR"

if [[ "$CLEAN" == "1" ]]; then
  echo "Cleaning previous build..."
  rm -rf build dist
fi

# PyInstaller + Pillow are build-only tools. Install them into the
# project venv if missing so `uv sync` alone never ships them.
if ! "$REPO_ROOT/.venv/bin/pyinstaller" --version >/dev/null 2>&1; then
  echo "Installing PyInstaller + Pillow into .venv..."
  uv pip install "pyinstaller>=6.0" "pillow>=10.0" --python "$REPO_ROOT/.venv/bin/python"
fi

# Regenerate the icon set so the bundle always carries fresh assets.
"$REPO_ROOT/.venv/bin/python" "$DESKTOP_DIR/packaging/assets/generate_icon.py"
if [[ -d "$DESKTOP_DIR/packaging/assets/icon.iconset" ]]; then
  iconutil -c icns "$DESKTOP_DIR/packaging/assets/icon.iconset" \
    -o "$DESKTOP_DIR/packaging/assets/nexus.icns"
fi

echo "Building ${BUNDLE_NAME}..."
"$REPO_ROOT/.venv/bin/pyinstaller" --noconfirm --clean "$SPEC"

APP_PATH="$DIST_DIR/$BUNDLE_NAME"
if [[ ! -d "$APP_PATH" ]]; then
  echo "Build failed: $APP_PATH not found" >&2
  exit 1
fi

# Ad-hoc local signing so the app launches on the build Mac without
# Gatekeeper quarantine prompts (no notarization — internal use only).
codesign --force --deep --sign - "$APP_PATH" >/dev/null 2>&1 || true

if [[ "$SKIP_DMG" == "1" ]]; then
  echo "Done: $APP_PATH (DMG skipped)"
  exit 0
fi

echo "Packing ${DMG_NAME}..."
DMG_PATH="$DIST_DIR/$DMG_NAME"
rm -f "$DMG_PATH"
hdiutil create "$DMG_PATH" -ov -volname "Nexus Enterprise" \
  -srcfolder "$APP_PATH" -format UDZO -imagekey zlib-level=9 >/dev/null

echo "Done:"
echo "  App: $APP_PATH"
echo "  DMG: $DMG_PATH"
echo "Gatekeeper: right-click Open on first launch (unsigned build)."
