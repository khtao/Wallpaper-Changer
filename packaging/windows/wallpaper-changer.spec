# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows / macOS 通用）。

用法：
    pyinstaller packaging/windows/wallpaper-changer.spec --noconfirm
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("wallpaper_changer")
hiddenimports += [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",
    "requests",
    "PIL",
    "PIL.Image",
]

excludes = [
    "tkinter", "unittest", "pydoc", "doctest", "email", "http.server",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSvg", "PySide6.QtTest", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtOpenGL", "PySide6.QtSql",
    "numpy", "matplotlib", "scipy", "pandas",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Wallpaper Changer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # 无控制台窗口
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
    version=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="Wallpaper Changer",
)
