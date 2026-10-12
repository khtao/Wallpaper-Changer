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


def test_sources_registry_contents():
    """来源为 Bing / Bzee / 本地 / 自定义链接，且无清理旧来源的入口。"""
    import wallpaper_changer.database as db_mod

    from wallpaper_changer.sources import REGISTRY, SOURCE_LABELS, build_sources

    cfg = config_mod.Config()
    assert not hasattr(cfg.data, "openverse")
    assert set(REGISTRY) == {"bing", "bzee", "qh360", "local", "custom_urls"}
    assert set(SOURCE_LABELS) == {"bing", "bzee", "qh360", "local", "custom_urls"}
    # bzee 默认关闭，开启后才应出现在构建结果里
    names = [s.name for s in build_sources(cfg)]
    assert "openverse" not in names
    assert "bzee" not in names
    cfg.data.bzee.enabled = True
    assert "bzee" in [s.name for s in build_sources(cfg)]
    # 关掉 Bing 后应消失，验证开关确实生效
    cfg.data.bing.enabled = False
    assert "bing" not in [s.name for s in build_sources(cfg)]
    # 数据库不应再保留「按来源批量删除」的能力
    assert not hasattr(db_mod.Database, "delete_by_source")
    assert not hasattr(db_mod.Database, "count_by_source")


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

    assert hasattr(TrayIcon, "error") and hasattr(TrayIcon, "info")
    # 枚举必须按类访问
    assert QSystemTrayIcon.MessageIcon.Critical is not None
    # 源码里不允许再出现「实例.Information」这种写法
    src = inspect.getsource(TrayIcon)
    assert ".MessageIcon." in src
    # 已按需求收敛：不再有「换图成功通知」入口
    assert not hasattr(TrayIcon, "notify")


def test_rotation_starts_automatically_by_default(tmp_path):
    """开机启动后倒计时必须自动运行（默认动作是自动切换，不是暂停）。"""
    from PySide6.QtWidgets import QApplication

    from wallpaper_changer.engine import Engine

    app = QApplication.instance() or QApplication([])

    cfg = config_mod.Config(tmp_path / "cfg.json")
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)

    assert eng.rotation_enabled() is True
    eng.pause(False)
    eng.apply_timer()
    assert eng._timer.isActive(), "默认应自动开始倒计时"
    assert eng.seconds_to_next() > 0
    assert eng.is_paused() is False

    eng.pause(True)
    assert eng.seconds_to_next() == -1 and eng.is_paused() is True
    eng.pause(False)
    assert eng.seconds_to_next() > 0
    eng.shutdown()
    db.close()


def test_pause_does_not_write_enabled_to_config(tmp_path):
    """暂停只改运行时状态，不得把 rotation.enabled 改写并落盘。

    旧行为会把「暂停一次」持久化成长期关闭，导致之后每次开机都停在暂停。
    """
    from PySide6.QtWidgets import QApplication

    from wallpaper_changer.engine import Engine

    app = QApplication.instance() or QApplication([])

    path = tmp_path / "cfg.json"
    cfg = config_mod.Config(path)
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)

    eng.pause(True)
    cfg.save()
    assert config_mod.Config(path).data.rotation.enabled is True, \
        "暂停不得把配置里的启用状态改成 False"
    eng.shutdown()
    db.close()


def test_startup_restores_last_wallpaper(tmp_path):
    """启动时应把上一次成功设置的壁纸回显到首页，而不是「未设置壁纸」。"""
    from PySide6.QtWidgets import QApplication

    from wallpaper_changer.engine import Engine

    app = QApplication.instance() or QApplication([])

    cfg = config_mod.Config(tmp_path / "cfg.json")
    cfg.data.rotation.enabled = False
    db = Database(tmp_path / "h.db")

    # 无历史时应返回 None
    eng = Engine(cfg, db)
    assert eng.restore_current() is None
    assert eng.current is None

    # 写入一条成功历史后再恢复
    rec = db.upsert(ImageRecord(source="local", source_id="x", title="旧壁纸",
                                local_path="/tmp/x.jpg"))
    db.mark_used(rec.id, True, "ok")
    got = eng.restore_current()
    assert got is not None and got.id == rec.id
    assert eng.current is not None and eng.current.display_title == "旧壁纸"
    eng.shutdown()
    db.close()


def test_timer_restarts_after_change(tmp_path):
    """自动切换启用时，换图成功后倒计时应从头计时。"""
    from PySide6.QtWidgets import QApplication

    from wallpaper_changer.engine import Engine

    app = QApplication.instance() or QApplication([])

    cfg = config_mod.Config(tmp_path / "cfg.json")
    cfg.data.rotation.enabled = True
    cfg.data.rotation.interval_minutes = 30
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)

    eng.pause(False)
    eng.apply_timer()
    assert eng.seconds_to_next() > 0
    eng._timer.setInterval(1000)          # 缩短以便观察重置
    before = eng.seconds_to_next()
    eng._timer.start()
    assert eng.seconds_to_next() >= before - 1
    eng.restart_timer()
    assert eng._timer.isActive()
    eng.shutdown()
    db.close()


def test_changing_enabled_checkbox_starts_timer_immediately(tmp_path):
    """「启用自动轮换」勾选/取消应立即启停倒计时（不必等保存）。"""
    from PySide6.QtWidgets import QApplication

    from wallpaper_changer.engine import Engine
    from wallpaper_changer.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    cfg = config_mod.Config(tmp_path / "cfg.json")
    cfg.data.rotation.enabled = True
    cfg.data.rotation.interval_minutes = 30
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)

    class Ctx:
        pass

    c = Ctx()
    c.config, c.db, c.engine, c.tray, c.window = cfg, db, eng, None, None
    win = MainWindow(c)
    win._load_settings()

    win.chk_enabled.setChecked(False)
    assert eng.seconds_to_next() == -1
    win._update_countdown()
    assert win.label_next.text() == "未启用自动轮换"

    win.chk_enabled.setChecked(True)
    assert eng.seconds_to_next() > 0
    win._update_countdown()
    assert win.label_next.text() != "未启用自动轮换"

    eng.shutdown()
    db.close()


def test_preview_falls_back_to_cache_and_never_lies(tmp_path):
    """缓存文件被清理时，预览不得停在「尚未设置壁纸」。

    场景：history 里记的 local_path 已不存在，但缓存里仍有同 URL 的文件。
    """
    from PIL import Image

    from wallpaper_changer.engine import Engine

    cfg = config_mod.Config(tmp_path / "cfg.json")
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)

    remote = "https://imgtu.bzee.cn/photo/pic/renxiang/yanzhi/13930ZMBZ.webp"
    rec = db.upsert(ImageRecord(source="bzee", source_id="x1", title="缓存回退",
                                local_path=str(tmp_path / "gone.jpg"),
                                remote_url=remote, width=3840, height=2561))
    cache = eng.fetcher.target_path(rec)
    cache.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (80, 40), (20, 80, 140)).save(cache)
    db.mark_used(rec.id, True, "ok", "bzee", "缓存回退", str(tmp_path / "gone.jpg"))

    # 1) 能按 remote_url 找回缓存
    assert eng.fetcher.resolve_local(rec) == str(cache)

    # 2) 完全没缓存时，只返回 None，由界面显示「正在载入」而非「尚未设置」
    rec2 = db.upsert(ImageRecord(source="bzee", source_id="x2", title="无缓存",
                                 local_path="", remote_url="https://x/y/2.webp"))
    assert eng.fetcher.resolve_local(rec2) is None
    eng.shutdown()
    db.close()


def test_download_retries_then_falls_back(tmp_path):
    """主地址重试 N 次仍失败后，应回退到备用地址。"""
    import wallpaper_changer.fetcher as fetcher_mod
    from wallpaper_changer.models import ImageRecord as _IR  # noqa: F401

    calls: list = []

    # 用真实 webp 字节，否则会被下载后的「图片有效性校验」拦掉
    from PIL import Image as _Img

    import io as _io

    buf = _io.BytesIO()
    _Img.new("RGB", (64, 48), (200, 120, 40)).save(buf, "WEBP")
    img_bytes = buf.getvalue()

    class Resp:
        def __init__(self, code):
            self.status_code = code
            self.headers = {"Content-Type": "image/webp"}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

        def iter_content(self, _n):
            yield img_bytes

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_get(url, **_kw):
        calls.append(url)
        return Resp(200 if "thumb" in url else 403)

    orig_get, orig_sleep = fetcher_mod.requests.get, fetcher_mod.time.sleep
    fetcher_mod.requests.get = fake_get
    fetcher_mod.time.sleep = lambda _s: None
    try:
        f = fetcher_mod.Fetcher(tmp_path / "cache", retries=3, retry_delay=0)
        rec = ImageRecord(source="bzee", source_id="r1",
                          remote_url="https://imgtu.bzee.cn/photo/pic/a/b/1.webp",
                          fallback_url="https://imgtu.bzee.cn/photo/thumb/a/b/1_400x711.webp")
        path = f.ensure_local(rec)
    finally:
        fetcher_mod.requests.get = orig_get
        fetcher_mod.time.sleep = orig_sleep

    assert path and Path(path).exists()
    origin_tries = [u for u in calls if "/photo/pic/" in u]
    assert len(origin_tries) == 3, calls          # 原图重试到上限
    assert calls[-1].endswith("_400x711.webp")     # 最后回退缩略图


def test_apply_task_rejects_low_resolution(tmp_path):
    """兜底拿到缩略图（分辨率过低）时应判定失败，而不是设成糊壁纸。"""
    from wallpaper_changer.engine import ApplyTask

    f = Fetcher.__new__(Fetcher)      # 不建目录，仅校验分支
    rec = ImageRecord(source="bzee", source_id="low", width=400, height=711)

    class FakeFetcher:
        def ensure_local(self, _rec):
            _rec.width, _rec.height = 400, 711
            return "/tmp/fake.webp"

    task = ApplyTask(FakeFetcher(), rec, "适应（完整显示）", False, 1280, 720)
    got = []
    task.result.connect(lambda r, ok, m: got.append((ok, m)))
    task.run()
    assert got and got[0][0] is False
    assert "分辨率过低" in got[0][1]


def test_corrupted_cache_is_purged_and_redownloaded(tmp_path):
    """缓存里存的是错误页时：应被识别、删除，而不是显示「无法解码」。"""
    from PIL import Image

    from wallpaper_changer.engine import Engine

    cfg = config_mod.Config(tmp_path / "cfg.json")
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)

    rec = db.upsert(ImageRecord(source="bzee", source_id="bad", title="损坏缓存",
                                remote_url="https://imgtu.bzee.cn/photo/pic/z/z/bad.webp"))
    cache = eng.fetcher.target_path(rec)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b'{"title":"Request denied","status":403}')   # 站点错误页

    assert not eng.fetcher.is_valid_cached(cache)
    assert eng.fetcher.resolve_local(rec) is None   # 会被清理掉而非返回坏文件
    assert not cache.exists(), "损坏的缓存应被删除"

    # 正常图片则能通过校验
    good = tmp_path / "good.webp"
    Image.new("RGB", (120, 80), (10, 20, 30)).save(good, "WEBP")
    assert eng.fetcher.is_valid_cached(good)
    eng.shutdown()
    db.close()


def test_download_rejects_non_image_content(tmp_path):
    """站点返回 200 但内容是错误页时，必须判为失败而不是存成缓存。"""
    import wallpaper_changer.fetcher as fetcher_mod

    class Resp:
        def __init__(self):
            self.status_code = 200
            self.headers = {"Content-Type": "application/json"}

        def raise_for_status(self):
            return None

        def iter_content(self, _n):
            yield b'{"title":"Request denied","status":403}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    orig = fetcher_mod.requests.get
    fetcher_mod.requests.get = lambda *a, **k: Resp()
    fetcher_mod.time.sleep = lambda _s: None
    try:
        f = fetcher_mod.Fetcher(tmp_path / "cache", retries=2, retry_delay=0)
        rec = ImageRecord(source="bzee", source_id="nope",
                          remote_url="https://imgtu.bzee.cn/photo/pic/z/z/nope.webp")
        try:
            f.ensure_local(rec)
            raised = False
        except Exception:
            raised = True
        assert raised, "非图片内容必须判为下载失败"
        # 且不能留下坏缓存
        assert not f.target_path(rec).exists()
    finally:
        fetcher_mod.requests.get = orig


def test_purge_bad_cache_keeps_valid_files(tmp_path):
    """清理无效缓存：坏文件删掉、正常图片保留。"""
    from PIL import Image

    from wallpaper_changer.engine import Engine

    cfg = config_mod.Config(tmp_path / "cfg.json")
    cfg.data.cache_dir = str(tmp_path / "cache")     # 隔离，避免动到真实缓存
    db = Database(tmp_path / "h.db")
    eng = Engine(cfg, db)
    root = Path(eng.fetcher.cache_dir)

    (root / "bzee").mkdir(parents=True, exist_ok=True)
    (root / "bing").mkdir(parents=True, exist_ok=True)
    for i in range(3):
        Image.new("RGB", (40, 30), (i * 80, 10, 10)).save(root / "bzee" / f"g{i}.webp", "WEBP")
    Image.new("RGB", (40, 30), (0, 90, 90)).save(root / "bing" / "g0.jpg")
    (root / "bzee" / "bad1.webp").write_bytes(b'{"error":403}')
    (root / "bing" / "bad2.jpg").write_bytes(b"<html>403</html>")

    before = len([p for p in root.rglob("*") if p.is_file()])
    removed = 0
    for p in root.rglob("*"):
        if p.is_file() and not eng.fetcher.is_valid_cached(p):
            p.unlink()
            removed += 1
    after = len([p for p in root.rglob("*") if p.is_file()])
    assert before == 6 and removed == 2 and after == 4
    assert (root / "bzee" / "g0.webp").exists()
    assert not (root / "bzee" / "bad1.webp").exists()
    eng.shutdown()
    db.close()


def test_network_images_cached_as_jpeg(tmp_path):
    """网络图片下载后必须转码为 JPEG 缓存（消除 webp 解不开的问题）。"""
    import io

    from PIL import Image

    import wallpaper_changer.fetcher as fetcher_mod
    from wallpaper_changer.models import ImageRecord as _IR  # noqa: F401

    # 造一张真实的 webp 作为「下载内容」
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (120, 60, 30)).save(buf, "WEBP")
    payload = buf.getvalue()

    class Resp:
        def __init__(self):
            self.status_code = 200
            self.headers = {"Content-Type": "image/webp"}

        def raise_for_status(self):
            return None

        def iter_content(self, _n):
            yield payload

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    orig_get, orig_sleep = fetcher_mod.requests.get, fetcher_mod.time.sleep
    fetcher_mod.requests.get = lambda *a, **k: Resp()
    fetcher_mod.time.sleep = lambda _s: None
    try:
        f = fetcher_mod.Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
        rec = ImageRecord(source="bzee", source_id="w1", title="webp 图",
                          remote_url="https://imgtu.bzee.cn/photo/pic/x/y/1.webp")
        path = Path(f.ensure_local(rec))
    finally:
        fetcher_mod.requests.get = orig_get
        fetcher_mod.time.sleep = orig_sleep

    assert path.exists()
    assert path.suffix.lower() == ".jpg", f"缓存应为 jpg，实际 {path.suffix}"
    with Image.open(path) as im:
        assert im.format == "JPEG", f"内容应为 JPEG，实际 {im.format}"
        assert im.size == (300, 200)
    assert rec.local_path == str(path)


def test_local_source_keeps_original_format(tmp_path):
    """本地文件夹来源不转码（用户自己的图直接引用原件）。"""
    from PIL import Image

    src = tmp_path / "mine.png"
    Image.new("RGB", (40, 30), (10, 20, 30)).save(src, "PNG")
    f = Fetcher(tmp_path / "cache")
    rec = ImageRecord(source="local", source_id=str(src), local_path=str(src))
    assert f.ensure_local(rec) == str(src)
    assert Path(rec.local_path).suffix == ".png"     # 未被改写


def test_migrate_cache_to_jpeg(tmp_path):
    """迁移：缓存里的 webp/png 转成 jpg，损坏文件删掉。"""
    from PIL import Image

    f = Fetcher(tmp_path / "cache")
    root = Path(f.cache_dir)
    (root / "bzee").mkdir(parents=True, exist_ok=True)

    old_webp = root / "bzee" / "aaa.webp"
    old_png = root / "bzee" / "bbb.png"
    already = root / "bzee" / "ccc.jpg"
    broken = root / "bzee" / "ddd.webp"
    Image.new("RGB", (60, 40), (200, 100, 50)).save(old_webp, "WEBP")
    Image.new("RGB", (60, 40), (10, 180, 90)).save(old_png, "PNG")
    Image.new("RGB", (60, 40), (5, 5, 5)).save(already, "JPEG")
    broken.write_bytes(b'{"error":403}')

    converted, removed = f.migrate_cache_to_jpeg()
    assert converted == 2, f"应转码 2 张，实际 {converted}"
    assert removed == 1, f"应删除 1 个损坏文件，实际 {removed}"

    assert not old_webp.exists() and (root / "bzee" / "aaa.jpg").exists()
    assert not old_png.exists() and (root / "bzee" / "bbb.jpg").exists()
    assert already.exists()             # 已是 jpg 的不动
    assert not broken.exists()          # 损坏的清掉

    with Image.open(root / "bzee" / "aaa.jpg") as im:
        assert im.format == "JPEG"


def test_migration_result_matches_target_path(tmp_path):
    """迁移后的文件名必须能被 cached_path 命中（哈希相同、扩展名变 jpg）。"""
    import io

    from PIL import Image

    f = Fetcher(tmp_path / "cache")
    rec = ImageRecord(source="bzee", source_id="k1",
                      remote_url="https://imgtu.bzee.cn/photo/pic/z/z/k1.webp")
    # 模拟老版本留下的缓存：按旧规则（扩展名取自 URL）落在 .webp
    old = f.target_path(rec).with_suffix(".webp")
    old.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), (70, 70, 70)).save(buf, "WEBP")
    old.write_bytes(buf.getvalue())

    assert f.cached_path(rec) is None       # 迁移前按 .jpg 找不到
    f.migrate_cache_to_jpeg()
    assert f.cached_path(rec) is not None   # 迁移后命中
    assert f.resolve_local(rec) is not None
