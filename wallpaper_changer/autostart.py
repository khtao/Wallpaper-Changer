"""开机自启动管理。

Linux   ：写入 ~/.config/autostart/wallpaper-changer.desktop（freedesktop 标准，
          所有主流桌面通用）；同时提供 systemd --user 单元的可选安装。
Windows ：写入 HKCU\\...\\Run 注册表项（可带 --minimized 参数）。
"""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .config import APP_NAME, APP_TITLE, IS_WINDOWS, config_dir

LOG = logging.getLogger(__name__)

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Version=1.0
Name={title}
Comment=自动更换桌面壁纸
Exec={exec}
Icon={icon}
Terminal=false
Categories=Utility;Settings;DesktopSettings;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
Keywords=wallpaper;background;
"""

SERVICE_TEMPLATE = """[Unit]
Description={title}
After=graphical-session.target

[Service]
Type=simple
ExecStart={exec}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _launch_command(minimized: bool = True) -> str:
    """返回用于自启动的命令行字符串。

    注意：不能用 ``python3 -m wallpaper_changer`` —— 以 deb 安装到
    /opt 后，开机自启动时的工作目录不是项目根，该模块无法被导入，
    自启动会静默失败。这里按优先级选择真正可用的启动方式。
    """
    args = " --minimized" if minimized else ""
    if getattr(sys, "frozen", False):      # PyInstaller 打包
        exe = sys.executable
    else:
        launcher = shutil.which(APP_NAME)  # deb / AppImage 安装后的启动器
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        if launcher:
            exe = launcher
        elif main_py.exists():              # 源码运行：直接用绝对路径
            path = str(main_py)
            exe = f'{sys.executable} "{path}"' if " " in path \
                else f"{sys.executable} {path}"
        else:
            exe = f"{sys.executable} -m wallpaper_changer"
    return f"{exe}{args}"


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #
def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def desktop_file_path() -> Path:
    return autostart_dir() / f"{APP_NAME}.desktop"


def systemd_unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / f"{APP_NAME}.service"


def icon_path() -> str:
    """返回已安装图标路径（不存在则返回包名，交给桌面环境解析）。"""
    candidates = [
        Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_NAME}.png",
        Path("/usr/share/pixmaps") / f"{APP_NAME}.png",
        Path(__file__).resolve().parent.parent.parent / "assets" / "icon.png",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return APP_NAME


def _enable_linux(minimized: bool = True, use_systemd: bool = False) -> bool:
    if use_systemd:
        unit = systemd_unit_path()
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(
            SERVICE_TEMPLATE.format(title=APP_TITLE, exec=_launch_command(minimized)),
            encoding="utf-8",
        )
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], timeout=15, check=False)
            subprocess.run(["systemctl", "--user", "enable", "--now", f"{APP_NAME}.service"],
                           timeout=30, check=False)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("systemd 启用失败：%s", exc)
        return True

    path = desktop_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        DESKTOP_TEMPLATE.format(
            title=APP_TITLE,
            exec=_launch_command(minimized),
            icon=icon_path(),
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return True


def _disable_linux() -> bool:
    removed = False
    for p in (desktop_file_path(), systemd_unit_path()):
        if p.exists():
            if p.suffix == ".service":
                subprocess.run(["systemctl", "--user", "disable", "--now", f"{APP_NAME}.service"],
                               timeout=30, check=False)
            try:
                p.unlink()
                removed = True
            except OSError as exc:
                LOG.warning("删除 %s 失败：%s", p, exc)
    return removed


def is_enabled_linux() -> bool:
    return desktop_file_path().exists() or systemd_unit_path().exists()


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _enable_windows(minimized: bool = True) -> bool:
    import winreg

    cmd = _launch_command(minimized)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "WallpaperChanger", 0, winreg.REG_SZ, cmd)
    return True


def _disable_windows() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "WallpaperChanger")
        return True
    except FileNotFoundError:
        return False


def is_enabled_windows() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "WallpaperChanger")
            return bool(value)
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 统一接口
# --------------------------------------------------------------------------- #
def enable(minimized: bool = True, use_systemd: bool = False) -> bool:
    try:
        return _enable_windows(minimized) if IS_WINDOWS else _enable_linux(minimized, use_systemd)
    except Exception as exc:  # noqa: BLE001
        LOG.error("启用开机启动失败：%s", exc)
        return False


def disable() -> bool:
    try:
        return _disable_windows() if IS_WINDOWS else _disable_linux()
    except Exception as exc:  # noqa: BLE001
        LOG.error("取消开机启动失败：%s", exc)
        return False


def is_enabled() -> bool:
    try:
        return is_enabled_windows() if IS_WINDOWS else is_enabled_linux()
    except Exception:  # noqa: BLE001
        return False


def install_desktop_entry(bin_path: str = "wallpaper-changer") -> Path:
    """安装应用菜单项（Linux, 非 autostart）。"""
    path = Path.home() / ".local" / "share" / "applications" / f"{APP_NAME}.desktop"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        DESKTOP_TEMPLATE.format(title=APP_TITLE, exec=bin_path, icon=icon_path()),
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def install_icon(source_png: str) -> Path | None:
    dest = Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"{APP_NAME}.png"
    try:
        if source_png and Path(source_png).exists():
            shutil.copy2(source_png, target)
            return target
    except OSError as exc:
        LOG.warning("安装图标失败：%s", exc)
    return None
