"""跨平台设置桌面壁纸。

Linux 支持：GNOME/Ubuntu（gsettings，含 Wayland）、KDE Plasma、XFCE、MATE、
            Cinnamon、Deepin，以及 feh / swaybg 兜底。
Windows 支持：SystemParametersInfoW + 注册表 WallpaperStyle（自动转 BMP）。
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

LOG = logging.getLogger(__name__)

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")


# --------------------------------------------------------------------------- #
# Linux
# --------------------------------------------------------------------------- #
def _env(*names) -> str:
    for n in names:
        v = os.environ.get(n, "")
        if v:
            return v.lower()
    return ""


def detect_desktop() -> str:
    """返回 gnome / kde / xfce / mate / cinnamon / deepin / sway / unknown。"""
    xdg = _env("XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XDG_SESSION_DESKTOP")
    if "gnome" in xdg or "ubuntu" in xdg or "pop" in xdg:
        return "gnome"
    if "kde" in xdg or "plasma" in xdg:
        return "kde"
    if "xfce" in xdg:
        return "xfce"
    if "mate" in xdg:
        return "mate"
    if "cinnamon" in xdg:
        return "cinnamon"
    if "deepin" in xdg or "dde" in xdg:
        return "deepin"
    if _env("SWAYSOCK") or "sway" in xdg:
        return "sway"
    if "unity" in xdg:
        return "gnome"
    return "unknown"


def _run(cmd: list, check: bool = True) -> bool:
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15
        )
        if check and proc.returncode != 0:
            LOG.warning("命令失败 %s: %s", cmd, proc.stderr.decode(errors="ignore"))
            return False
        return proc.returncode == 0
    except FileNotFoundError:
        LOG.warning("命令不存在：%s", cmd[0])
        return False
    except subprocess.TimeoutExpired:
        LOG.warning("命令超时：%s", cmd)
        return False


def _to_uri(path: str) -> str:
    p = Path(path).resolve()
    return p.as_uri()


def _set_gnome(path: str, mode: str, set_dark: bool) -> bool:
    uri = _to_uri(path)
    ok = _run(["gsettings", "set", "org.gnome.desktop.background", "picture-options", mode])
    ok &= _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri", uri])
    if set_dark:
        _run(["gsettings", "set", "org.gnome.desktop.background", "picture-uri-dark", uri])
    # Ubuntu 26.04 起部分会话使用 picture-uri-dark 作为默认，两个都设置最稳
    return ok


def _set_kde(path: str, mode: str) -> bool:
    script = (
        "var allDesktops = desktops();"
        "for (i=0;i<allDesktops.length;i++) {"
        "  d = allDesktops[i];"
        "  d.wallpaperPlugin = 'org.kde.image';"
        "  d.currentConfigGroup = Array('Wallpaper','org.kde.image','General');"
        "  d.writeConfig('Image', 'file://%s');"
        "  d.writeConfig('FillMode', %d);"
        "}" % (path, _kde_fill_mode(mode))
    )
    for tool in ("qdbus6", "qdbus", "dbus-send"):
        if shutil.which(tool):
            if tool == "dbus-send":
                cmd = [
                    "dbus-send", "--session", "--dest=org.kde.plasmashell",
                    "--type=method_call", "/PlasmaShell",
                    "org.kde.PlasmaShell.evaluateScript", f"string:{script}",
                ]
            else:
                cmd = [tool, "org.kde.plasmashell", "/PlasmaShell",
                       "org.kde.PlasmaShell.evaluateScript", script]
            if _run(cmd):
                return True
    if shutil.which("plasma-apply-wallpaperimage"):
        return _run(["plasma-apply-wallpaperimage", path])
    return False


def _kde_fill_mode(mode: str) -> int:
    # 0=Scaled 1=Centered 2=ScaledCrop(fill) 3=ScaledFit 5=Tile? 映射近似即可
    return {
        "zoom": 2, "scaled": 1, "stretched": 0, "centered": 6,
        "wallpaper": 3, "spanned": 2,
    }.get(mode, 2)


def _set_xfce(path: str, mode: str) -> bool:
    # 先查询所有 backdrop 属性，再逐个设置
    props = []
    try:
        out = subprocess.run(
            ["xfconf-query", "-c", "xfce4-desktop", "-l"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        ).stdout.decode()
        props = [ln for ln in out.splitlines() if ln.strip().endswith("last-image")]
    except Exception:  # noqa: BLE001
        pass
    if not props:
        props = ["/backdrop/screen0/monitor0/image-path"]
        return _run(["xfconf-query", "-c", "xfce4-desktop", "-p", props[0], "-s", path])
    ok = True
    for p in props:
        ok &= _run(["xfconf-query", "-c", "xfce4-desktop", "-p", p, "-s", path])
        style_prop = p.replace("last-image", "image-style")
        _run(["xfconf-query", "-c", "xfce4-desktop", "-p", style_prop,
              "-s", str(_xfce_style(mode))], check=False)
    return ok


def _xfce_style(mode: str) -> int:
    return {"zoom": 5, "scaled": 4, "stretched": 3, "centered": 1,
            "wallpaper": 2, "spanned": 5}.get(mode, 5)


def _set_mate(path: str, mode: str) -> bool:
    ok = _run(["gsettings", "set", "org.mate.background", "picture-options", mode])
    ok &= _run(["gsettings", "set", "org.mate.background", "picture-filename", path])
    return ok


def _set_cinnamon(path: str, mode: str) -> bool:
    ok = _run(["gsettings", "set", "org.cinnamon.desktop.background", "picture-options", mode])
    ok &= _run(["gsettings", "set", "org.cinnamon.desktop.background",
                "picture-uri", _to_uri(path)])
    return ok


def _set_deepin(path: str) -> bool:
    ok = _run(["gsettings", "set", "com.deepin.wrap.gnome.desktop.background",
               "picture-uri", _to_uri(path)], check=False)
    ok |= _run(["gsettings", "set", "org.gnome.desktop.background",
                "picture-uri", _to_uri(path)], check=False)
    return ok


def _set_feh(path: str, mode: str) -> bool:
    if not shutil.which("feh"):
        return False
    flag = {"zoom": "--bg-fill", "scaled": "--bg-max", "stretched": "--bg-scale",
            "centered": "--bg-center", "wallpaper": "--bg-tile", "spanned": "--bg-fill"}
    return _run(["feh", flag.get(mode, "--bg-fill"), path])


def _set_swaybg(path: str, mode: str) -> bool:
    if not shutil.which("swaybg"):
        return False
    m = {"zoom": "fill", "scaled": "fit", "stretched": "stretch",
         "centered": "center", "wallpaper": "tile", "spanned": "fill"}
    subprocess.Popen(["swaybg", "-i", path, "-m", m.get(mode, "fill")])
    return True


def set_wallpaper_linux(path: str, mode: str = "zoom", set_dark: bool = True) -> tuple:
    """返回 (是否成功, 使用的方式)。"""
    path = str(Path(path).resolve())
    desktop = detect_desktop()
    LOG.info("检测到桌面环境：%s", desktop)
    handlers = {
        "gnome": lambda: _set_gnome(path, mode, set_dark),
        "kde": lambda: _set_kde(path, mode),
        "xfce": lambda: _set_xfce(path, mode),
        "mate": lambda: _set_mate(path, mode),
        "cinnamon": lambda: _set_cinnamon(path, mode),
        "deepin": lambda: _set_deepin(path),
        "sway": lambda: _set_swaybg(path, mode),
    }
    if desktop in handlers:
        try:
            if handlers[desktop]():
                return True, desktop
        except Exception as exc:  # noqa: BLE001
            LOG.warning("使用 %s 方式设置失败：%s", desktop, exc)
    if _set_gnome(path, mode, set_dark):
        return True, "gnome"
    if _set_feh(path, mode):
        return True, "feh"
    if _set_swaybg(path, mode):
        return True, "swaybg"
    return False, desktop


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
SPI_SETDESKWALLPAPER = 0x0014
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDWININICHANGE = 0x02

WIN_STYLE = {
    "fill": ("10", "0"),
    "fit": ("6", "0"),
    "stretch": ("2", "0"),
    "center": ("0", "0"),
    "tile": ("0", "1"),
    "span": ("22", "0"),
}


def _win_prepare_bmp(path: str) -> str:
    """Windows 下 SPI_SETDESKWALLPAPER 对 BMP 最兼容，必要时转换。"""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return path
    if path.lower().endswith(".bmp"):
        return path
    try:
        out_dir = Path(os.environ.get("TEMP") or ".") / "WallpaperChanger"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "current.bmp"
        with Image.open(path) as im:
            im.convert("RGB").save(out, "BMP")
        return str(out)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("转换 BMP 失败，直接使用原图：%s", exc)
        return path


def set_wallpaper_windows(path: str, mode: str = "fill") -> tuple:
    import ctypes

    path = str(Path(path).resolve())
    bmp = _win_prepare_bmp(path)
    ok = bool(
        ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_SETDESKWALLPAPER, 0, bmp,
            SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE,
        )
    )
    if not ok:
        return False, "user32"
    try:
        import winreg

        style, tile = WIN_STYLE.get(mode, WIN_STYLE["fill"])
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Control Panel\\Desktop", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "WallpaperStyle", 0, winreg.REG_SZ, style)
            winreg.SetValueEx(key, "TileWallpaper", 0, winreg.REG_SZ, tile)
        ctypes.windll.user32.SystemParametersInfoW(  # type: ignore[attr-defined]
            SPI_SETDESKWALLPAPER, 0, bmp,
            SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("设置 WallpaperStyle 失败：%s", exc)
    return True, "user32"


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def set_wallpaper(path: str, mode: str = "填充（缩放裁切）", set_dark: bool = True) -> tuple:
    """跨平台设置壁纸。

    :param mode: GUI 里的显示名（如“填充（缩放裁切）”），内部会转换成平台值。
    :return: (成功与否, 说明)
    """
    from .config import FIT_MODES_LINUX, FIT_MODES_WINDOWS, IS_LINUX as _IS_LINUX

    if not os.path.isfile(path):
        return False, f"文件不存在：{path}"
    if IS_WINDOWS:
        value = FIT_MODES_WINDOWS.get(mode, "fill")
        return set_wallpaper_windows(path, value)
    value = FIT_MODES_LINUX.get(mode, "zoom")
    return set_wallpaper_linux(path, value, set_dark)


def screen_info() -> str:
    """返回屏幕信息（用于状态显示）。"""
    if IS_WINDOWS:
        try:
            import ctypes

            w = ctypes.windll.user32.GetSystemMetrics(0)  # type: ignore[attr-defined]
            h = ctypes.windll.user32.GetSystemMetrics(1)  # type: ignore[attr-defined]
            return f"{w}×{h}"
        except Exception:  # noqa: BLE001
            return "未知"
    try:
        out = subprocess.run(
            ["xrandr"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5
        ).stdout.decode(errors="ignore")
        for line in out.splitlines():
            if " primary " in line or " connected " in line:
                import re

                m = re.search(r"(\d+)x(\d+)", line)
                if m:
                    return f"{m.group(1)}×{m.group(2)}"
    except Exception:  # noqa: BLE001
        pass
    return platform.node() and "未知" or "未知"
