# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（Windows）。

用法：
    pyinstaller packaging/windows/wallpaper-changer.spec --noconfirm

可选环境变量：
    WC_VERSION     版本号，默认 1.0.0（只写进文件元数据，不影响 EXE 名）
    WC_ONEFILE     设为 1 则打成单文件 exe（启动慢、杀软误报高；默认目录版）
    WC_CONSOLE     设为 1 则保留控制台窗口（排错用，默认关闭）

说明：
  * 目录版（onedir）启动更快、更不容易被杀软误报，推荐；
  * 单文件版每次启动都要解压到临时目录，约慢 2~5 秒。
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

# --------------------------------------------------------------------------- #
# 路径：全部按 spec 文件位置推算，避免从任意目录运行时找不到 main.py / assets
#   （PyInstaller 会以 spec 所在目录解析相对路径，直接写 "main.py" 会指向
#    packaging/windows/main.py 而报错）
# --------------------------------------------------------------------------- #
try:
    SPECPATH          # PyInstaller 执行 spec 时注入
except NameError:
    SPECPATH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
ICON = os.path.join(ROOT, "assets", "icon.ico")
ENTRY = os.path.join(ROOT, "main.py")

# --------------------------------------------------------------------------- #
# 版本与模式
# --------------------------------------------------------------------------- #
VERSION = os.environ.get("WC_VERSION", "1.1.3")
ONEFILE = os.environ.get("WC_ONEFILE", "") == "1"
CONSOLE = os.environ.get("WC_CONSOLE", "") == "1"
APP_NAME = "Wallpaper Changer"


# --------------------------------------------------------------------------- #
# Windows 文件版本信息（右键 exe → 属性 → 详细信息 能看到）
#   用 PyInstaller 自带的 versioninfo 生成器写出临时版本文件；
#   生成失败也不影响打包，只是 exe 没有版本元数据。
# --------------------------------------------------------------------------- #
def _make_version_file(version: str):
    try:
        from PyInstaller.utils.win32.versioninfo import (
            FixedFileInfo, StringFileInfo, StringStruct, StringTable,
            VarFileInfo, VarStruct, VSVersionInfo,
        )
    except Exception:  # noqa: BLE001
        return None

    def _parts(v: str):
        """'1.1.3' -> (1, 1, 3, 0)，非数字部分忽略。"""
        nums = []
        for seg in str(v).split("."):
            digits = "".join(ch for ch in seg if ch.isdigit())
            nums.append(int(digits) if digits else 0)
        while len(nums) < 4:
            nums.append(0)
        return tuple(nums[:4])

    ms, ls = None, None
    try:
        a1, a2, a3, a4 = _parts(version)
        ms = (a1 << 16) | (a2 & 0xFFFF)
        ls = (a3 << 16) | (a4 & 0xFFFF)
    except Exception:  # noqa: BLE001
        pass

    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_parts(version), prodvers=_parts(version),
                          mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1,
                          subtype=0x0, date=(0, 0)),
        kids=[
            StringFileInfo([StringTable(
                "080404b0",       # 简体中文 / Unicode
                [
                    StringStruct("CompanyName", "Wallpaper Changer"),
                    StringStruct("FileDescription", "自动更换壁纸工具"),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", "Wallpaper Changer"),
                    StringStruct("LegalCopyright", "Copyright © Wallpaper Changer"),
                    StringStruct("OriginalFilename", "Wallpaper Changer.exe"),
                    StringStruct("ProductName", "Wallpaper Changer"),
                    StringStruct("ProductVersion", version),
                ])]),
            VarFileInfo([VarStruct("Translation", [2052, 1200])]),
        ],
    )
    if ms is not None:      # 补齐 ffi 内部字段（不同 PyInstaller 版本构造签名略有差异）
        try:
            info.ffi.mask = 0x3F
        except Exception:  # noqa: BLE001
            pass
    # 写到 spec 同目录（PyInstaller 会以 spec 所在目录解析相对路径）
    path = os.path.join(SPECPATH, "file_version_info.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(info))
        return path
    except OSError:
        return None


VERFILE = _make_version_file(VERSION)

# --------------------------------------------------------------------------- #
# 隐藏导入
#   这是 Windows 打包最容易踩的坑：动态 import 的模块不会被自动收集，
#   运行到那一行才报 ModuleNotFoundError。
# --------------------------------------------------------------------------- #
hiddenimports = collect_submodules("wallpaper_changer")      # 本项目全部子模块
hiddenimports += [
    # --- Qt ---
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtNetwork",        # requests / 网络相关
    # --- Pillow：预览解码兜底用到 ImageQt，webp 需要显式带上编解码插件 ---
    "PIL",
    "PIL.Image",
    "PIL.ImageQt",              # 预览区 Qt 解不开时的 Pillow 兜底
    "PIL._webp",                # Bzee 图片是 webp，缺了会无法解码
    "PIL._imaging",
    "PIL._imagingcms",
    # --- requests 全家桶（requests 内部按需 import，必须显式列出）---
    "requests",
    "requests.adapters",
    "requests.auth",
    "requests.models",
    "requests.sessions",
    "urllib3",
    "urllib3.contrib",
    "urllib3.contrib.secure",
    "urllib3.util",
    "certifi",
    "charset_normalizer",
    "idna",
]

# --------------------------------------------------------------------------- #
# 排除：只砍真正用不到的重型模块
#   注意：不要排除 PIL / requests 的子模块，也不要排除 PySide6.QtNetwork
# --------------------------------------------------------------------------- #
excludes = [
    "tkinter", "unittest", "pydoc", "doctest",
    "http.server", "xmlrpc", "sqlite3.test",
    # Qt 重型无关组件
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtTest", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtSql", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPrintSupport",
    # 科学计算（本项目不用，体积大头）
    "numpy", "matplotlib", "scipy", "pandas", "IPython", "notebook",
]

# --------------------------------------------------------------------------- #
# 随包数据：图标资源（界面与托盘都要用）
# --------------------------------------------------------------------------- #
datas = [
    (os.path.join(ROOT, "assets"), "assets"),
]

a = Analysis(
    [ENTRY],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

# PyInstaller 6.x 起 a.zipfiles 为空/已废弃，这里做兼容处理
_zipfiles = getattr(a, "zipfiles", []) or []

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        _zipfiles,
        a.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=CONSOLE,
        disable_windowed_traceback=False,
        icon=ICON,
        version=VERFILE,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=CONSOLE,
        disable_windowed_traceback=False,
        icon=ICON,
        version=VERFILE,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        _zipfiles,
        a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
