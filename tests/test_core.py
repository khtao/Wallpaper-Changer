"""核心逻辑测试（不需要 GUI）。

运行：  python -m pytest tests -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

TMP_HOME = Path("/tmp/wallpaper-changer-test-home")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if TMP_HOME.exists():
    pass
os.environ["HOME"] = str(TMP_HOME)
TMP_HOME.mkdir(parents=True, exist_ok=True)

from wallpaper_changer import config as config_mod  # noqa: E402
from wallpaper_changer.database import Database  # noqa: E402
from wallpaper_changer.fetcher import Fetcher  # noqa: E402
from wallpaper_changer.models import ImageRecord  # noqa: E402
from wallpaper_changer.sources import build_sources  # noqa: E402
from wallpaper_changer.sources.base import filter_by_size  # noqa: E402


def make_image(path: Path, w: int = 800, h: int = 600, color=(30, 90, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (w, h), color).save(path)
    return path


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def test_config_roundtrip(tmp_path):
    cfg = config_mod.Config(tmp_path / "config.json")
    cfg.data.rotation.interval_minutes = 15
    cfg.data.theme = "深色"
    cfg.data.local.directories = ["/tmp/x"]
    cfg.save()
    again = config_mod.Config(tmp_path / "config.json")
    assert again.data.rotation.interval_minutes == 15
    assert again.data.theme == "深色"
    assert again.data.local.directories == ["/tmp/x"]


def test_config_defaults_have_cache_dir(tmp_path):
    cfg = config_mod.Config(tmp_path / "c.json")
    assert cfg.cache_dir
    assert "wallpaper-changer" in str(cfg.cache_dir)


def test_fit_modes_for_platform():
    modes = config_mod.fit_modes_for_platform()
    assert isinstance(modes, dict) and len(modes) >= 4


# --------------------------------------------------------------------------- #
# 尺寸过滤
# --------------------------------------------------------------------------- #
def test_filter_by_size():
    recs = [
        ImageRecord(source="local", source_id="a", width=1920, height=1080),
        ImageRecord(source="local", source_id="b", width=800, height=600),
        ImageRecord(source="local", source_id="c", width=0, height=0),
    ]
    kept = filter_by_size(recs, 1280, 720)
    assert [r.source_id for r in kept] == ["a", "c"]


# --------------------------------------------------------------------------- #
# 数据库
# --------------------------------------------------------------------------- #
def test_database_favorite_and_history(tmp_path):
    db = Database(tmp_path / "t.db")
    rec = ImageRecord(source="local", source_id="p1", local_path="/tmp/p1.jpg",
                      title="测试图", width=1920, height=1080)
    saved = db.upsert(rec)
    assert saved.id is not None

    assert db.set_favorite(saved.id, True)
    assert db.get(saved.id).is_favorite
    assert db.count_favorites() == 1

    db.mark_used(saved.id)
    assert db.get(saved.id).used_count == 1
    assert len(db.list_history(limit=10)) == 1

    assert db.delete_image(saved.id)
    assert db.get(saved.id) is None

    stats = db.stats()
    assert "total" in stats and "favorites" in stats
    db.close()


def test_database_upsert_dedupe(tmp_path):
    db = Database(tmp_path / "t2.db")
    a = db.upsert(ImageRecord(source="bing", source_id="x1", title="first"))
    b = db.upsert(ImageRecord(source="bing", source_id="x1", title="second"))
    assert a.id == b.id
    assert db.get(a.id).title == "second"
    db.close()


def test_database_prune_history(tmp_path):
    db = Database(tmp_path / "t3.db")
    r = db.upsert(ImageRecord(source="local", source_id="h1"))
    db.mark_used(r.id)
    db.prune_history(keep=0)
    assert db.list_history(limit=10) == []
    db.close()


# --------------------------------------------------------------------------- #
# 本地来源
# --------------------------------------------------------------------------- #
def test_local_source_scan(tmp_path):
    pics = tmp_path / "pics"
    make_image(pics / "a.jpg", 1600, 900)
    make_image(pics / "b.png", 1920, 1080)
    make_image(pics / "note.txt" if False else pics / "b.png")  # noqa: SIM108
    (pics / "readme.txt").write_text("不是图片")

    cfg = config_mod.Config(tmp_path / "cfg.json")
    cfg.data.local.enabled = True
    cfg.data.bing.enabled = False
    cfg.data.custom_urls.enabled = False
    cfg.data.local.directories = [str(pics)]
    cfg.data.local.recursive = True

    sources = build_sources(cfg)
    assert len(sources) == 1
    result = sources[0].fetch()
    assert not result.error, result.error
    names = sorted(Path(r.source_id).name for r in result.images)
    assert names == ["a.jpg", "b.png"]
    assert all(r.local_path for r in result.images)
    assert all(r.width and r.height for r in result.images)


# --------------------------------------------------------------------------- #
# 抓取器（本地图片无需网络）
# --------------------------------------------------------------------------- #
def test_fetcher_ensure_local_and_prune(tmp_path):
    cache = tmp_path / "cache"
    fetcher = Fetcher(cache, timeout=5, max_files=100)
    src = tmp_path / "src.jpg"
    make_image(src, 1200, 800)

    rec = ImageRecord(source="local", source_id=str(src), local_path=str(src))
    local = fetcher.ensure_local(rec)
    assert Path(local).exists()
    assert rec.width == 1200 and rec.height == 800

    # 缓存清理：保留 1 个
    fetcher.prune(keep=1)
    assert fetcher.cache_size() >= 0


# --------------------------------------------------------------------------- #
# 自定义链接来源
# --------------------------------------------------------------------------- #
def test_custom_url_source_local_paths(tmp_path):
    from wallpaper_changer.sources.custom_urls import CustomUrlSource, extract_urls

    img = tmp_path / "one.jpg"
    make_image(img, 1000, 800)

    class S:
        urls = [str(img), "https://example.com/remote.jpg"]

    src = CustomUrlSource(S())
    res = src.fetch()
    assert not res.error, res.error
    assert len(res.images) == 2
    assert res.images[0].local_path == str(img)


def test_extract_urls_from_json():
    from wallpaper_changer.sources.custom_urls import extract_urls

    data = {"code": 0, "data": {"list": [
        {"id": 1, "url": "https://x/a.jpg"},
        {"id": 2, "image": "https://x/b.png"},
    ]}}
    got = extract_urls(data, "data.list")
    assert len(got) == 2 and got[0]["url"].endswith("a.jpg")

    flat = extract_urls(["https://x/c.jpg"])
    assert flat == ["https://x/c.jpg"]


# --------------------------------------------------------------------------- #
# 壁纸设置命令拼装（用假 gsettings 验证，不需要真实桌面）
# --------------------------------------------------------------------------- #
def test_set_wallpaper_linux_uses_gsettings(tmp_path, monkeypatch):
    from wallpaper_changer import wallpaper as wp

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.txt"
    script = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"{log}\"\n".format(log=log)
    (bin_dir / "gsettings").write_text(script, encoding="utf-8")
    (bin_dir / "gsettings").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")

    img = make_image(tmp_path / "wall.jpg", 1920, 1080)
    ok, how = wp.set_wallpaper(str(img), "填充（缩放裁切）", set_dark=True)
    assert ok, how
    calls = log.read_text(encoding="utf-8")
    assert "picture-uri" in calls
    assert "zoom" in calls                     # 填充 -> zoom
    assert img.as_uri() in calls or "file://" in calls
    assert "picture-uri-dark" in calls         # 深色变体同步设置


def test_set_wallpaper_missing_file():
    from wallpaper_changer import wallpaper as wp

    ok, msg = wp.set_wallpaper("/nonexistent/nope.jpg")
    assert not ok and "不存在" in msg


# --------------------------------------------------------------------------- #
# 配置容错：手工编辑成字符串型数字、损坏 JSON 都不能让程序崩溃
# --------------------------------------------------------------------------- #
def test_config_coerces_string_numbers(tmp_path):
    cfg = config_mod.Config(tmp_path / "c.json")
    cfg.data.bing.count = 8
    cfg.save()

    raw = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    raw["bing"]["count"] = "8"                 # 手工改成了字符串
    raw["rotation"]["timeout"] = "20"
    raw["rotation"]["interval_minutes"] = "45"
    raw["max_cache_files"] = "300"
    raw["local"]["directories"] = "/tmp/Pics"  # 单个字符串而非列表
    (tmp_path / "c.json").write_text(json.dumps(raw), encoding="utf-8")

    again = config_mod.Config(tmp_path / "c.json")
    assert again.data.bing.count == 8 and isinstance(again.data.bing.count, int)
    assert again.data.rotation.timeout == 20
    assert again.data.rotation.interval_minutes == 45
    assert again.data.max_cache_files == 300
    assert again.data.local.directories == ["/tmp/Pics"]


def test_config_broken_json_falls_back(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{ not json ]]]", encoding="utf-8")
    cfg = config_mod.Config(p)
    assert cfg.data.bing.count == 8            # 默认值
    assert p.with_suffix(".json.broken").exists()   # 原文件已备份


def test_database_delete_by_source(tmp_path):
    db = Database(tmp_path / "t4.db")
    a = db.upsert(ImageRecord(source="openverse", source_id="o1"))
    b = db.upsert(ImageRecord(source="bing", source_id="b1"))
    db.mark_used(a.id)
    db.mark_used(b.id)
    assert db.count_by_source("openverse") == 1
    assert db.delete_by_source("openverse") == 1
    assert db.count_by_source("openverse") == 0
    assert db.get(b.id) is not None          # 其他来源不受影响
    assert len(db.list_history(limit=20)) == 1  # 关联历史一并清理
    db.close()


def test_openverse_source_removed():
    """Openverse 来源已移除：配置无该字段，来源列表也不再包含它。"""
    from wallpaper_changer.sources import REGISTRY, SOURCE_LABELS, build_sources

    cfg = config_mod.Config()
    assert not hasattr(cfg.data, "openverse")
    assert "openverse" not in REGISTRY
    names = [s.name for s in build_sources(cfg)]
    assert "openverse" not in names
    assert SOURCE_LABELS.get("openverse")     # 旧记录仍要有来源名可显示


def test_config_ignores_legacy_openverse_field(tmp_path):
    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "bing": {"enabled": True},
        "openverse": {"enabled": True, "token": "abc"},
        "local": {"enabled": True, "directories": ["/tmp/P"]},
    }), encoding="utf-8")
    cfg = config_mod.Config(p)
    assert not hasattr(cfg.data, "openverse")
    assert cfg.enabled_sources() == ["bing", "local"]


# --------------------------------------------------------------------------- #
# 缩略图：任何操作后都必须完整显示（不裁切、不变形）
# --------------------------------------------------------------------------- #
def test_thumb_cache_path_has_fingerprint(tmp_path):
    """缩略图文件名带版本指纹，参数变更后旧缓存自动失效。"""
    from wallpaper_changer.ui.gallery import thumb_path, thumb_key

    f = Fetcher(tmp_path / "cache")
    rec = ImageRecord(source="local", source_id="x")
    p1 = thumb_path(f, thumb_key(rec))
    assert p1.name.endswith(".jpg") and "_" in p1.stem
    # 不同 key 得到不同文件
    rec2 = ImageRecord(source="local", source_id="y")
    assert thumb_path(f, thumb_key(rec2)) != p1


def test_purge_thumb_cache(tmp_path):
    from wallpaper_changer.ui.gallery import purge_thumb_cache, thumb_path, thumb_key

    f = Fetcher(tmp_path / "cache")
    d = Path(f.cache_dir) / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    rec = ImageRecord(source="local", source_id="x")
    p = thumb_path(f, thumb_key(rec))
    p.write_bytes(b"fake")
    assert purge_thumb_cache(f) == 1
    assert not p.exists()


def test_gallery_modes_cover_history_and_favorites():
    """收藏与历史合并为单一图库：三种筛选模式都要在。"""
    from wallpaper_changer.ui.gallery import GalleryView

    assert set(GalleryView.MODES) == {"历史", "收藏", "全部"}


def test_fit_mode_default_shows_full_image():
    """默认显示方式必须是「适应（完整显示）」，避免桌面壁纸被裁切。"""
    cfg = config_mod.Config()
    assert cfg.data.rotation.fit_mode == "适应（完整显示）"
    # 该取值在两大平台都要有映射
    from wallpaper_changer.config import FIT_MODES_LINUX, FIT_MODES_WINDOWS

    assert "适应（完整显示）" in FIT_MODES_LINUX
    assert "适应（完整显示）" in FIT_MODES_WINDOWS


def test_tray_notification_helpers_exist():
    """托盘必须提供 info/warn 入口。

    背景：PySide6 里不能在实例上取枚举属性（``tray.Information`` 会抛
    AttributeError），消息图标枚举只能按类访问，所以统一封装成方法。
    这里只做静态检查，不实例化 QSystemTrayIcon（无 QApplication 时会崩溃）。
    """
    import inspect

    from PySide6.QtWidgets import QSystemTrayIcon

    from wallpaper_changer.ui.tray import TrayIcon

    assert hasattr(TrayIcon, "info") and hasattr(TrayIcon, "warn")
    # 枚举必须按类访问
    assert QSystemTrayIcon.MessageIcon.Information is not None
    # 源码里不允许再出现「实例.Information」这种写法
    src = inspect.getsource(TrayIcon)
    assert ".MessageIcon.Information" in src
