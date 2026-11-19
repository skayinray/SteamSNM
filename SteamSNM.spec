# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-спецификация для Windows-сборки SteamSNM."""

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/resources/icon.png', 'resources'),
        ('src/resources/icon.ico', 'resources'),
        ('src/resources/SteamSNM.appdata.xml', 'resources'),
    ],
    hiddenimports=[
        'PySide6.QtCharts',
        'PySide6.QtNetwork',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'shiboken6',
    ],
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
    a.zipfiles,
    a.datas,
    [],
    name='SteamSNM',
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
    icon='src/resources/icon.ico',
)
