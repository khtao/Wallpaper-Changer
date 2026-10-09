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

    print("== 托盘通知 ==")
    from wallpaper_changer.ui.tray import TrayIcon

    ctx.tray = TrayIcon(ctx)
    ctx.tray.info("冒烟测试通知", 500)
    win.hide_to_tray()          # 这里曾因 tray.Information 抛 AttributeError
    print("  托盘创建 + info() + 隐藏到托盘 均正常")

    print("== 图标 ==")
    assert not app_icon().isNull()

    engine.shutdown()
    db.close()
    print("\n全部冒烟通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
