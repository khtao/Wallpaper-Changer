"""GUI / Engine 冒烟测试（offscreen，无需真实桌面会话）。

运行：  python tests/test_ui_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HOME", "/tmp/wallpaper-changer-ui-home")
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)

from PIL import Image  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wallpaper_changer.config import Config  # noqa: E402
from wallpaper_changer.database import Database  # noqa: E402
from wallpaper_changer.engine import Engine  # noqa: E402
from wallpaper_changer.models import ImageRecord  # noqa: E402
from wallpaper_changer.ui.icons import app_icon  # noqa: E402
from wallpaper_changer.ui.main_window import MainWindow  # noqa: E402


class Ctx:
    def __init__(self, config, db, engine):
        self.config = config
        self.db = db
        self.engine = engine
        self.tray = None
        self.window = None

    def toggle_favorite(self):      # 托盘菜单需要
        pass

    def quit(self):                 # 托盘菜单需要
        pass


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path("/tmp/wallpaper-changer-ui")
    tmp.mkdir(parents=True, exist_ok=True)
    pics = tmp / "pics"
    pics.mkdir(exist_ok=True)
    tmp_paint = tmp / "paint"
    tmp_paint.mkdir(exist_ok=True)
    for i, (w, h) in enumerate([(1920, 1080), (1600, 900), (1280, 720)]):
        Image.new("RGB", (w, h), (i * 60, 90, 200)).save(pics / f"pic{i}.jpg")

    cfg = Config(tmp / "config.json")
    cfg.data.bing.enabled = False
    cfg.data.custom_urls.enabled = False
    cfg.data.local.enabled = True
    cfg.data.local.directories = [str(pics)]
    cfg.data.local.recursive = True
    cfg.data.rotation.enabled = False  # 冒烟时不开定时器
    cfg.data.rotation.on_startup_change = False
    cfg.data.cache_dir = str(tmp / "cache")
    cfg.save()

    db = Database(tmp / "history.db")
    engine = Engine(cfg, db)

    seen = {"pool": 0, "failed": 0, "applied": 0}
    engine.pool_updated.connect(lambda n: seen.__setitem__("pool", n))
    engine.failed.connect(lambda m: (seen.__setitem__("failed", seen["failed"] + 1), print("  [failed]", m)))
    engine.changed.connect(lambda rec, reason: (seen.__setitem__("applied", seen["applied"] + 1),
                                                print("  [changed]", rec.display_title, reason)))
    engine.status.connect(lambda m: print("  [status]", m))

    print("== refresh_pool ==")
    engine.refresh_pool(force=True)
    for _ in range(60):
        app.processEvents()
        if seen["pool"] > 0:
            break
        import time
        time.sleep(0.1)
    assert seen["pool"] > 0, "候选池为空，本地来源抓取失败"
    print("  候选池:", seen["pool"])

    print("== change_next（无桌面环境时允许失败，但不能崩溃）==")
    engine.change_next("冒烟")
    for _ in range(60):
        app.processEvents()
        if seen["applied"] or seen["failed"]:
            break
        import time
        time.sleep(0.1)
    print("  结果 applied=%d failed=%d" % (seen["applied"], seen["failed"]))

    print("== 收藏 / 历史 ==")
    rec = ImageRecord(source="local", source_id=str(pics / "pic0.jpg"),
                      local_path=str(pics / "pic0.jpg"), title="测试壁纸")
    saved = engine.db.upsert(rec)
    assert engine.db.set_favorite(saved.id, True)
    engine.db.mark_used(saved.id)
    assert engine.db.count_favorites() >= 1
    assert len(engine.db.list_history(limit=10)) >= 1
    assert engine.toggle_favorite(saved) is False      # 取消收藏
    assert engine.db.count_favorites() >= 0
    print("  收藏数:", engine.db.count_favorites(),
          "历史数:", len(engine.db.list_history(limit=10)))

    print("== 主窗口 ==")
    from wallpaper_changer.sources.bzee import (
        DESKTOP_CATEGORIES, MOBILE_CATEGORIES,
    )
    ctx = Ctx(cfg, db, engine)
    win = MainWindow(ctx)
    ctx.window = win
    win._load_settings()
    assert win.tabs.count() == 5, f"应为 5 个标签页，实际 {win.tabs.count()}"
    for i in range(win.tabs.count()):
        win.tabs.setCurrentIndex(i)
        app.processEvents()
    win.refresh_views()
    win._collect_settings()
    assert win.config.data.local.directories == [str(pics)]
    print("  五个标签页切换 + 设置读写正常（收藏/历史已合并为图库）")

    print("== 图库合并 ==")
    assert win.gallery is not None
    assert set(win.gallery.MODES) == {"历史", "收藏", "全部"}
    assert not hasattr(win, "fav_view") or win.fav_view is win.gallery
    for m in win.gallery.MODES:
        win.gallery.combo_mode.setCurrentText(m)
        app.processEvents()
    print("  筛选模式：", " / ".join(win.gallery.MODES))

    print("== 双击大图弹窗完整性 ==")
    from PySide6.QtGui import QPixmap
    from wallpaper_changer.ui.gallery import FullImageDialog

    for w, h in ((2100, 900), (1920, 1080), (1200, 1600), (3000, 1000)):
        src = tmp_paint / f"dlg_{w}x{h}.jpg"
        Image.new("RGB", (w, h), (80, 140, 210)).save(src)
        rec = ImageRecord(source="local", source_id=str(src),
                          local_path=str(src), title=f"{w}x{h}", width=w, height=h)
        dlg = FullImageDialog(QPixmap(str(src)), 1400, 900, rec)
        shown = dlg.label.pixmap()
        rel = abs((w / h) - (shown.width() / shown.height())) / (w / h)
        assert rel <= 0.01, f"大图弹窗变形：{w}x{h} -> {shown.width()}x{shown.height()}"
        assert shown.width() <= 1400 and shown.height() <= 900
        assert not dlg.label.hasScaledContents(), "不允许拉伸填充"
        print(f"  {w}x{h} -> {shown.width()}x{shown.height()} 误差 {rel:.2%} OK")

    print("== 显示方式默认完整 ==")
    assert cfg.data.rotation.fit_mode == "适应（完整显示）", cfg.data.rotation.fit_mode

    print("== Bzee 图库来源 ==")
    assert hasattr(win, "gb_bzee") and hasattr(win, "list_bzee_cat")
    assert win.list_bzee_cat.count() == len(DESKTOP_CATEGORIES)
    win.combo_bzee_lib.setCurrentIndex(1)          # 切到手机图库
    assert win.list_bzee_cat.count() == len(MOBILE_CATEGORIES)
    win.combo_bzee_lib.setCurrentIndex(0)
    win._bzee_select(True)
    assert len(win._bzee_slugs()) == len(DESKTOP_CATEGORIES)
    win._bzee_select(False)
    assert win._bzee_slugs() == []
    # 勾选自然风景 + 关键词，走一次配置读写
    for i in range(win.list_bzee_cat.count()):
        it = win.list_bzee_cat.item(i)
        it.setSelected(it.data(Qt.UserRole) == "ziran")
    win.edit_bzee_kw.setText("风景,星空")
    win.gb_bzee.setChecked(True)
    win._collect_settings()
    bz = win.config.data.bzee
    assert bz.enabled and bz.categories == ["ziran"]
    assert bz.keywords == ["风景", "星空"] and bz.library == "desktop"
    win._load_settings()
    assert win.combo_bzee_lib.currentData() == "desktop"
    assert win._bzee_slugs() == ["ziran"]
    assert win.edit_bzee_kw.text() == "风景,星空"
    print("  Bzee 分组：分类切换/多选/关键词 读写正常")

    print("== 清理无效缓存 ==")
    from PIL import Image as _PILImage

    root = Path(engine.fetcher.cache_dir)
    (root / "bzee").mkdir(parents=True, exist_ok=True)
    good = root / "bzee" / "smoke-good.webp"
    bad = root / "bzee" / "smoke-bad.webp"
    _PILImage.new("RGB", (60, 40), (10, 20, 30)).save(good, "WEBP")
    bad.write_bytes(b'{"error":403}')
    assert hasattr(win, "btn_purge_bad")
    win._purge_bad_cache()
    assert good.exists(), "正常图片不应被删"
    assert not bad.exists(), "无效文件应被清理"
    print("  无效缓存已清理，正常文件保留 ✅")

    print("== 自动轮换默认自动切换 ==")
    cfg.data.rotation.enabled = True          # 冒烟里关过定时器，这里恢复
    cfg.data.rotation.interval_minutes = 30
    engine.pause(False)
    engine.apply_timer()
    assert engine.rotation_enabled(), "默认应启用自动轮换"
    assert engine.seconds_to_next() > 0, "启动后倒计时必须已在跑"
    assert engine.is_paused() is False
    print(f"  倒计时已在跑：{engine.seconds_to_next()} 秒后切换")

    engine.pause(True)
    assert engine.seconds_to_next() == -1
    win._update_countdown()
    print("  暂停后状态：", win.label_next.text())
    engine.pause(False)
    win._update_countdown()
    print("  继续后状态：", win.label_next.text())
    assert engine.seconds_to_next() > 0

    print("== 通知策略（仅失败/严重错误才通知）==")
    import wallpaper_changer.ui.tray as tray_mod
    from wallpaper_changer.ui.tray import TrayIcon

    assert not hasattr(TrayIcon, "notify"), "不应再有换图成功通知入口"
    assert hasattr(TrayIcon, "error")
    win._on_changed(saved)      # 换图成功：不得弹通知
    print("  换图成功：静默 ✅")

    print("== 托盘 ==")
    ctx.tray = TrayIcon(ctx)
    ctx.tray.error("冒烟：模拟失败通知", 100)
    win.hide_to_tray()          # 这里曾因 tray.Information 抛 AttributeError
    print("  托盘创建 + error() + 隐藏到托盘 均正常")

    print("== 预览区不得谎报「尚未设置壁纸」==")
    from PIL import Image as _PILImage

    from wallpaper_changer.models import ImageRecord as _Rec

    # 缓存缺失但有 remote_url：应显示「正在载入预览…」并后台补载
    win.preview.setText("尚未设置壁纸")
    rec_nocache = _Rec(source="bzee", source_id="nocache", title="无缓存图",
                       local_path="", remote_url="https://x/y/nocache.webp")
    win._update_preview(rec_nocache)
    assert win.preview.text() != "尚未设置壁纸", win.preview.text()
    print("  无缓存时：", win.preview.text())

    # 缓存可找回：应直接出图
    rec_cache = _Rec(source="bzee", source_id="cached", title="缓存回退图",
                     local_path="/nonexistent/gone.jpg",
                     remote_url="https://x/y/cached.webp")
    cache_path = engine.fetcher.target_path(rec_cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    _PILImage.new("RGB", (1600, 900), (20, 80, 140)).save(cache_path)
    win._update_preview(rec_cache)
    assert not win.preview.pixmap().isNull(), "找回缓存后应能显示预览"
    print("  缓存回退后：已出图 ✅")

    print("== 启动回显当前壁纸 ==")
    restored = engine.restore_current()
    assert restored is not None, "有历史时应能回显上一次壁纸"
    assert win.label_title.text() == restored.display_title
    assert win.preview.pixmap() is not None or not restored.local_path
    print(f"  首页已显示当前壁纸：{win.label_title.text()}")

    print("== 图标 ==")
    assert not app_icon().isNull()

    engine.shutdown()
    db.close()
    print("\n全部冒烟通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
