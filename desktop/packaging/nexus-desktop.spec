"""PyInstaller spec for the Nexus Enterprise desktop app (macOS-first)."""

from __future__ import annotations

from pathlib import Path

# spec lives in desktop/packaging/, app sources in desktop/src/
SPEC_PATH = Path(SPECPATH).resolve()  # noqa: F821 - provided by PyInstaller
# SPECPATH is the spec file path on some PyInstaller versions and the
# directory on others; normalize to the directory.
SPEC_DIR = SPEC_PATH.parent if SPEC_PATH.suffix == ".spec" else SPEC_PATH
DESKTOP_DIR = SPEC_DIR.parent
SRC_DIR = DESKTOP_DIR / "src"
ASSETS_DIR = SPEC_DIR / "assets"
ICON_FILE = ASSETS_DIR / "nexus.icns"

APP_NAME = "Nexus Enterprise"
BUNDLE_ID = "com.nexus.enterprise.desktop"

a = Analysis(  # noqa: F821
    [str(SRC_DIR / "nexus_desktop" / "main.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "keyring",
        "keyring.backends.macOS.Keychain",
        "keyring.backends.Windows.WinVault",
        "keyring.backends.SecretService",
        "pydantic",
        "pydantic._internal._fields",
        "httpx",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="nexus-desktop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_FILE) if ICON_FILE.exists() else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="nexus-desktop",
)

app = BUNDLE(  # noqa: F821
    coll,
    name=f"{APP_NAME}.app",
    icon=str(ICON_FILE) if ICON_FILE.exists() else None,
    bundle_identifier=BUNDLE_ID,
    version="0.1.0",
    info_plist={
        "CFBundleDisplayName": APP_NAME,
        "CFBundleName": APP_NAME,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "13.0",
        "NSPrincipalClass": "NSApplication",
    },
)
