# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the Windows portable build."""

# QtCharts is used by the price history window and must stay available.
UNUSED_PYSIDE6_MODULES = [
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DCore', 'PySide6.Qt3DExtras',
    'PySide6.Qt3DInput', 'PySide6.Qt3DLogic', 'PySide6.Qt3DRender',
    'PySide6.QtBluetooth', 'PySide6.QtCanvasPainter', 'PySide6.QtConcurrent',
    'PySide6.QtDataVisualization', 'PySide6.QtDBus', 'PySide6.QtDesigner',
    'PySide6.QtGraphs', 'PySide6.QtGraphsWidgets', 'PySide6.QtHelp',
    'PySide6.QtHttpServer', 'PySide6.QtLabsStyleKit', 'PySide6.QtLocation',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'PySide6.QtNetworkAuth', 'PySide6.QtNfc', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtPositioning', 'PySide6.QtQml', 'PySide6.QtQuick',
    'PySide6.QtQuick3D', 'PySide6.QtQuickControls2', 'PySide6.QtQuickTest',
    'PySide6.QtQuickWidgets', 'PySide6.QtRemoteObjects', 'PySide6.QtScxml',
    'PySide6.QtSensors', 'PySide6.QtSerialBus', 'PySide6.QtSerialPort',
    'PySide6.QtSpatialAudio', 'PySide6.QtSql', 'PySide6.QtStateMachine',
    'PySide6.QtSvg', 'PySide6.QtSvgWidgets', 'PySide6.QtTest',
    'PySide6.QtTextToSpeech', 'PySide6.QtUiTools', 'PySide6.QtWebChannel',
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebEngineWidgets', 'PySide6.QtWebSocket',
    'PySide6.QtWebSockets', 'PySide6.QtWebView',
]

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('LICENSE', '.'),
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
    excludes=UNUSED_PYSIDE6_MODULES + [
        'tkinter', 'idlelib', 'pydoc_data', 'distutils', 'ensurepip', 'venv',
    ],
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
