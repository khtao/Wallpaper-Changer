"""环境自检（不依赖 PySide6，缺依赖时也能运行）。

用于排查「源码命令行能跑、打包安装后跑不起来」这类问题：
依赖是否齐全、目录是否可写、图标资源是否随包安装、
桌面环境能否识别、启动器与菜单项是否在位。

用法::

    wallpaper-changer --doctor
    python main.py --doctor
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from .config import APP_NAME, app_data_dir, config_dir

# 需要检查的依赖：导入名 -> 显示名 -> apt 包名（可能不存在）-> pip 包名
#
# 注意：PySide6 的 apt 包（python3-pyside6.*）位于 Ubuntu 的 universe 仓库，
# 并非所有系统都可用；很多人实际是用 pip 安装的。
# 因此提示里必须同时给出 apt 与 pip 两种方案，并说明 universe 的启用方式。
DEPENDENCIES = (
    ("PySide6.QtWidgets", "PySide6.QtWidgets", "python3-pyside6.qtwidgets", "PySide6"),
    ("PySide6.QtGui", "PySide6.QtGui", "python3-pyside6.qtwidgets", "PySide6"),
    ("PySide6.QtCore", "PySide6.QtCore", "python3-pyside6.qtwidgets", "PySide6"),
    ("PySide6.QtNetwork", "PySide6.QtNetwork", "python3-pyside6.qtnetwork", "PySide6"),
    ("requests", "requests", "python3-requests", "requests"),
    ("PIL", "Pillow", "python3-pil", "Pillow"),
)


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "OK  " if ok else "失败"
    print(f"  [{mark}] {label}" + (f"：{detail}" if detail else ""))
    return ok


def _assets_dir() -> Path:
    """项目 assets 目录（本文件位于 <根>/wallpaper_changer/doctor.py）。

    PyInstaller 打包后用 sys._MEIPASS 定位，避免图标找不到。
    """
    import sys

    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidate = Path(meipass) / "assets"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent.parent / "assets"


def _detect_desktop() -> str:
    try:
        from .wallpaper import detect_desktop

        return detect_desktop()
    except Exception as exc:  # noqa: BLE001
        return f"检测失败（{type(exc).__name__}）"


def _log_tail(n: int = 10) -> list[str]:
    log = app_data_dir() / f"{APP_NAME}.log"
    if not log.exists():
        return []
    try:
        return log.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def run_doctor() -> int:
    """打印环境自检报告；全部通过返回 0，否则返回 1。"""
    print("=== Wallpaper Changer 环境自检 ===")
    ok = True

    print("\n-- 运行环境 --")
    print(f"  Python     : {sys.executable}（{sys.version.split()[0]}）")
    entry = Path(sys.argv[0]).resolve() if sys.argv and sys.argv[0] else Path("-")
    print(f"  运行入口   : {entry}")
    print(f"  包安装位置 : {Path(__file__).resolve().parent.parent}")
    print(f"  系统       : {sys.platform}")
    print(f"  会话类型   : {os.environ.get('XDG_SESSION_TYPE', '未知')}")
    print(f"  桌面环境   : {os.environ.get('XDG_CURRENT_DESKTOP', '未知')}")
    print(f"  WAYLAND    : {os.environ.get('WAYLAND_DISPLAY', '未设置')}")
    print(f"  DISPLAY    : {os.environ.get('DISPLAY', '未设置')}")
    print(f"  HOME       : {os.environ.get('HOME', '未设置')}")

    print("\n-- 依赖 --")
    missing_apt: list[str] = []
    missing_pip: list[str] = []
    for mod, label, apt_pkg, pip_pkg in DEPENDENCIES:
        try:
            m = __import__(mod, fromlist=["x"])
            ver = getattr(m, "__version__", "") or "已安装"
            ok &= _check(label, True, ver)
        except Exception as exc:  # noqa: BLE001
            ok &= _check(label, False, f"{type(exc).__name__}: {exc}")
            missing_apt.append(apt_pkg)
            missing_pip.append(pip_pkg)

    print("\n-- 自动轮换 --")
    try:
        from .config import Config

        rot = Config().data.rotation
        enabled = bool(rot.enabled) and rot.interval_minutes > 0
        ok &= _check("启用自动轮换", enabled,
                     f"每 {rot.interval_minutes} 分钟" if enabled
                     else f"未启用（enabled={rot.enabled}, 间隔={rot.interval_minutes} 分钟）")
        if not enabled:
            print("    → 倒计时不会启动。请在「轮换」页勾选「启用自动轮换」并保存；")
            print("      若该选项之前被取消，多半是旧版本把「暂停」误写成了长期关闭。")
        ok &= _check("启动即换一张", rot.on_startup_change,
                     "开" if rot.on_startup_change else "关")
    except Exception as exc:  # noqa: BLE001
        ok &= _check("读取轮换配置", False, f"{type(exc).__name__}: {exc}")

    print("\n-- 目录与权限 --")
    for label, path in (("配置目录", config_dir()), ("数据目录", app_data_dir())):
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            ok &= _check(f"{label}可写", True, str(path))
        except Exception as exc:  # noqa: BLE001
            ok &= _check(f"{label}可写", False, f"{path} -> {exc}")

    print("\n-- 资源与安装 --")
    icon = _assets_dir() / "icon.png"
    ok &= _check("图标文件", icon.exists(), str(icon))
    launcher = shutil.which(APP_NAME)
    print(f"  启动器     : {launcher or '未安装（以源码方式运行）'}")
    entry_desktop = Path("/usr/share/applications") / f"{APP_NAME}.desktop"
    print(f"  菜单项     : {entry_desktop if entry_desktop.exists() else '未安装'}")
    print(f"  壁纸后端   : {_detect_desktop()}")

    print("\n-- 最近日志 --")
    tail = _log_tail()
    if tail:
        for line in tail:
            print(f"  {line}")
    else:
        print("  暂无日志")

    print()
    if ok:
        print("自检结论：环境正常 ✅")
    else:
        print("自检结论：存在问题 ❌")
        print("建议处理：")
        if missing_apt:
            print("  方式 A（apt，需启用 universe 仓库）：")
            print("    sudo add-apt-repository universe && sudo apt update")
            print(f"    sudo apt install {' '.join(dict.fromkeys(missing_apt))}")
            print("    若提示找不到包，说明该发行版未提供，请改用方式 B。")
        if missing_pip:
            print("  方式 B（pip，不依赖发行版仓库，推荐）：")
            pkgs = " ".join(dict.fromkeys(missing_pip))
            print(f"    python3 -m pip install --break-system-packages {pkgs}")
            print("    若提示 pip 不存在：sudo apt install python3-pip")
        print("  - 目录不可写时，检查 HOME 与 ~/.local/share 权限")
        print("  - 图标缺失说明 assets 未随包安装，请重新安装 deb")
        print("  - 若用 apt 安装后仍报缺依赖，多半是 PySide6 由 pip 安装、")
        print("    而 deb 依赖的是 apt 包名，两者互不可见，属正常现象。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run_doctor())
