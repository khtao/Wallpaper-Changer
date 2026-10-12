"""配置模型与持久化。

配置以 JSON 保存在用户配置目录：
  Linux   : ~/.config/wallpaper-changer/config.json
  Windows : %APPDATA%\\WallpaperChanger\\config.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, get_origin, get_type_hints

APP_NAME = "wallpaper-changer"
APP_TITLE = "Wallpaper Changer"
ORG_NAME = "wallpaper-changer"
VERSION = "1.0.0"

IS_WINDOWS = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

LOG = logging.getLogger(__name__)


def app_data_dir() -> Path:
    """返回应用数据目录（壁纸缓存、数据库、日志）。"""
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "WallpaperChanger"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def config_dir() -> Path:
    if IS_WINDOWS:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "WallpaperChanger"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


def default_cache_dir() -> Path:
    return app_data_dir() / "cache"


def default_local_dir() -> Path:
    """默认本地图片目录（用户可改）。"""
    pictures = Path.home() / "Pictures"
    return pictures / "Wallpapers" if pictures.exists() else pictures


FIT_MODES_LINUX = {
    "填充（缩放裁切）": "zoom",
    "适应（完整显示）": "scaled",
    "拉伸": "stretched",
    "居中": "centered",
    "平铺": "wallpaper",
    "跨屏延展": "spanned",
}

FIT_MODES_WINDOWS = {
    "填充（缩放裁切）": "fill",
    "适应（完整显示）": "fit",
    "拉伸": "stretch",
    "居中": "center",
    "平铺": "tile",
    "跨屏延展": "span",
}


def fit_modes_for_platform() -> Dict[str, str]:
    return FIT_MODES_WINDOWS if IS_WINDOWS else FIT_MODES_LINUX


@dataclass
class BingSettings:
    enabled: bool = True
    market: str = "zh-CN"
    count: int = 8          # 拉取最近 N 天
    resolution: str = "UHD"  # UHD / 1920x1080 / 1366x768 / mobile


@dataclass
class Qh360Settings:
    """360 高清壁纸（历史公开接口，默认关闭避免不可用时拖慢启动）。"""
    enabled: bool = False
    mode: str = "category"  # category=指定分类 / latest=最近更新
    categories: List[str] = field(default_factory=lambda: ["36"])
    page_size: int = 24
    max_pages: int = 1


@dataclass
class LocalSettings:
    enabled: bool = False
    directories: List[str] = field(default_factory=list)
    recursive: bool = True


@dataclass
class BzeeSettings:
    """Bzee 图库（photo.bzee.cn）。"""

    enabled: bool = False
    library: str = "desktop"          # desktop=桌面图库 / mobile=手机图库
    categories: List[str] = field(default_factory=list)   # slug 列表，空=全部
    keywords: List[str] = field(default_factory=list)     # 标题需包含的关键词
    max_pages: int = 1                # 每个分类抓几页
    image_size: str = "origin"        # origin=原图（失败自动回退缩略图）/ thumb=缩略图


@dataclass
class CustomUrlSettings:
    enabled: bool = False
    urls: List[str] = field(default_factory=list)


@dataclass
class RotationSettings:
    enabled: bool = True
    interval_minutes: int = 30
    on_startup_change: bool = True   # 启动后立即换一张
    random_order: bool = True
    avoid_repeat_days: int = 7       # N 天内不重复
    fit_mode: str = "适应（完整显示）"
    set_dark_variant: bool = True    # Linux: 同时设置深色模式壁纸
    min_width: int = 1280
    min_height: int = 720
    timeout: int = 20


@dataclass
class AppSettings:
    bing: BingSettings = field(default_factory=BingSettings)
    bzee: BzeeSettings = field(default_factory=BzeeSettings)
    qh360: Qh360Settings = field(default_factory=Qh360Settings)
    local: LocalSettings = field(default_factory=LocalSettings)
    custom_urls: CustomUrlSettings = field(default_factory=CustomUrlSettings)
    rotation: RotationSettings = field(default_factory=RotationSettings)
    cache_dir: str = ""
    max_cache_files: int = 300
    autostart: bool = False
    start_minimized: bool = True
    close_to_tray: bool = True
    # 仅在「切换失败 / 严重错误」时弹桌面通知；成功换图、收藏等一律静默
    show_notifications: bool = True
    proxy: str = ""       # 如 http://127.0.0.1:7890
    log_level: str = "INFO"
    theme: str = "跟随系统"  # 跟随系统 / 浅色 / 深色


def _coerce(value: Any, annotation: Any, default: Any) -> Any:
    """把 JSON 里的值强制转换为字段声明的类型。

    手工编辑 config.json、或旧版本残留都可能写入字符串型数字
    （如 "count": "8"），直接传给 QSpinBox.setValue 会抛 TypeError，
    这里统一在读取阶段纠正。
    """
    if value is None:
        return default

    origin = get_origin(annotation)
    # List[str] / List[int] ...
    if origin in (list, List):
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        if isinstance(value, str):
            return [value] if value.strip() else []
        return default if isinstance(default, list) else []

    # bool 必须排在 int 之前判断（bool 是 int 的子类）
    if annotation is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)

    if annotation is int:
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default if isinstance(default, int) else 0

    if annotation is str:
        return value if isinstance(value, str) else str(value)

    return value


def _from_dict(cls, data: Dict[str, Any]):
    """用（可能不完整或类型不严谨的）字典构造 dataclass，保持默认值。"""
    if not isinstance(data, dict):
        return cls()
    try:
        hints = get_type_hints(cls)
    except Exception:  # noqa: BLE001 - 取不到注解就退化为原样赋值
        hints = {}
    kwargs = {}
    for f in cls.__dataclass_fields__.values():  # type: ignore[attr-defined]
        if f.name in data:
            default = getattr(cls(), f.name, f.default)
            kwargs[f.name] = _coerce(data[f.name], hints.get(f.name, type(default)), default)
    return cls(**kwargs)


class Config:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else config_dir() / "config.json"
        self.data: AppSettings = AppSettings()
        self.load()

    # ---- 读写 ----
    def load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                # 配置文件损坏时备份一份，再用默认设置启动，避免程序打不开
                LOG.warning("配置文件解析失败（%s），已备份并使用默认设置", exc)
                try:
                    backup = self.path.with_suffix(
                        self.path.suffix + ".broken")
                    self.path.replace(backup)
                except OSError:
                    pass
                raw = {}
        else:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        self.data = AppSettings(
            bing=_from_dict(BingSettings, raw.get("bing", {})),
            bzee=_from_dict(BzeeSettings, raw.get("bzee", {})),
            qh360=_from_dict(Qh360Settings, raw.get("qh360", {})),
            local=_from_dict(LocalSettings, raw.get("local", {})),
            custom_urls=_from_dict(CustomUrlSettings, raw.get("custom_urls", {})),
            rotation=_from_dict(RotationSettings, raw.get("rotation", {})),
        )
        # 顶层标量（同样按声明类型纠正）
        try:
            hints = get_type_hints(AppSettings)
        except Exception:  # noqa: BLE001
            hints = {}
        for key in (
            "cache_dir",
            "max_cache_files",
            "autostart",
            "start_minimized",
            "close_to_tray",
            "show_notifications",
            "proxy",
            "log_level",
            "theme",
        ):
            if key in raw:
                default = getattr(AppSettings(), key)
                setattr(self.data, key,
                        _coerce(raw[key], hints.get(key, type(default)), default))
        if not self.data.cache_dir:
            self.data.cache_dir = str(default_cache_dir())

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.data)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---- 便捷访问 ----
    @property
    def cache_dir(self) -> Path:
        return Path(self.data.cache_dir or default_cache_dir())

    def enabled_sources(self) -> List[str]:
        names = []
        if self.data.bing.enabled:
            names.append("bing")
        if self.data.bzee.enabled:
            names.append("bzee")
        if self.data.qh360.enabled:
            names.append("qh360")
        if self.data.local.enabled:
            names.append("local")
        if self.data.custom_urls.enabled:
            names.append("custom_urls")
        return names
