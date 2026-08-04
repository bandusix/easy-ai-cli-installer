# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for AI Tools Installer.
#
# Bundles the Python source together with the offline payload/ directory
# (Node.js runtime + every CLI's npm tarball + Codex/Lark binaries). The
# GUI uses get_resource_path() which works both in dev and inside the
# PyInstaller bundle.
#
# Build with:  pyinstaller build.spec
#
# Output:
#   Windows  → dist/AI_Tools_Installer.exe
#   macOS    → dist/AI_Tools_Installer.app  (then wrapped into .dmg by CI)

import sys

a = Analysis(
    ['gui_installer.py'],
    pathex=[],
    binaries=[],
    datas=[('payload', 'payload')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='AI_Tools_Installer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS: turn the single binary into a proper .app bundle so it can be
# distributed as a .dmg. Windows just gets the EXE from the block above.
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='AI_Tools_Installer.app',
        icon=None,
        bundle_identifier='com.bandusix.easyaiinstaller',
        info_plist={
            'CFBundleName': 'AI Tools Installer',
            'CFBundleDisplayName': 'AI Tools Installer',
            'NSHighResolutionCapable': 'True',
            'LSApplicationCategoryType': 'public.app-category.developer-tools',
        },
    )