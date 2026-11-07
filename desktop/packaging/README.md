# Nexus Desktop — macOS install (internal)

Unsigned development build for the internal team. No Apple Developer ID,
no notarization. The app talks to your Nexus API over HTTPS with the same
keyring-backed session as running from source.

## Build (Mac, Apple Silicon / Intel)

From the repository root:

```bash
./desktop/packaging/build_macos.sh
```

Output:

```text
desktop/dist/Nexus Enterprise.app
desktop/dist/Nexus-Enterprise-0.1.0-mac.dmg
```

The script installs `pyinstaller` + `pillow` into `.venv` if missing,
regenerates the icon set from `assets/generate_icon.py`, runs PyInstaller
with `nexus-desktop.spec`, applies an ad-hoc local signature, and packs
the DMG. Use `--skip-dmg` to skip the DMG and `--clean` to wipe `build/`
and `dist/`.

## Install

1. Mount `Nexus-Enterprise-0.1.0-mac.dmg` and drag **Nexus Enterprise**
   to Applications (or unzip and move the `.app` there).
2. Launch **once** via right-click → Open (Gatekeeper) and confirm. The
   app is unsigned, so Finder will warn; this is expected for internal
   builds. Subsequent launches work normally.
3. Sign in with your Nexus API URL (e.g. `http://127.0.0.1:8000/api/v1`
   or your hosted API), organisation slug and credentials. The refresh
   token is stored in your macOS Keychain; the access token stays in
   memory.
4. To update, replace the `.app` in Applications with a fresh build. Your
   Keychain entry and preferences (`QSettings`) carry over.

## Verify the bundle

```bash
codesign --verify --deep --strict "desktop/dist/Nexus Enterprise.app"
QT_QPA_PLATFORM=offscreen "desktop/dist/Nexus Enterprise.app/Contents/MacOS/nexus-desktop" &
# login window should appear; Ctrl-C to quit
```

## What the bundle contains

PyQt6, `httpx`, `pydantic` and `keyring` frozen with Python 3.13.
No database access, no bundled backend. All calculations and permissions
stay on the API.

## Next

Windows and Linux installers (PyInstaller one-folder via CI matrix) are
follow-ups to this macOS-first build.
