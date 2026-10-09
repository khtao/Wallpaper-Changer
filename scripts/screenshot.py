#!/usr/bin/env python3
"""离屏渲染主界面，生成文档用截图（无需真实桌面会话）。

用法:  python scripts/screenshot.py [输出路径]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HOME", "/tmp/wallpaper-changer-shot")
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)

from PIL import Image  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from wallpaper_changer.config import Config  # noqa: E402
from wallpaper_changer.database import Database  # noqa: E402
from wallpaper_changer.engine import Engine  # noqa: E402
from wallpaper_changer.ui.main_window import MainWindow  # noqa: E402


class Ctx:
    def __init__(self, config, db, engine):
        self.config = config
        self.db = db
        self.engine = engine
        self.window = None
        self.tray = None


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "screenshot-main.png")
    out.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    tmp = Path("/tmp/wallpaper-changer-shot/data")
    tmp.mkdir(parents=True, exist_ok=True)

    pics = tmp / "pics"
    pics.mkdir(exist_ok=True)
    # 造几张示例图，让收藏/历史页有内容
    colors = [(38, 92, 168), (44, 120, 90), (140, 76, 40), (86, 60, 130)]
    for i, c in enumerate(colors):
        Image.new("RGB", (1920, 1080), c).save(pics / f"sample{i}.jpg")

    cfg = Config(tmp / "config.json")
    cfg.data.bing.enabled = True
    cfg.data.local.enabled = True
    cfg.data.local.directories = [str(pics)]
    cfg.data.cache_dir = str(tmp / "cache")
    cfg.data.rotation.enabled = True
    cfg.data.rotation.interval_minutes = 30

    db = Database(tmp / "history.db")
    engine = Engine(cfg, db)
    ctx = Ctx(cfg, db, engine)
    win = MainWindow(ctx)
    ctx.window = win

    # 预置一张当前壁纸，让预览区与信息区有内容
    from wallpaper_changer.models import ImageRecord

    rec = ImageRecord(
        source="bing", source_id="demo", local_path=str(pics / "sample0.jpg"),
        title="示例壁纸：晨雾中的山谷", author="Bing",
        license="© Microsoft/Bing", webpage="https://www.bing.com",
        width=1920, height=1080,
    )
    saved = db.upsert(rec)
    db.set_favorite(saved.id, True)
    db.mark_used(saved.id)
    win._on_changed(saved)
    win.refresh_views()
    win.resize(1100, 780)
    win.show()
    for _ in range(10):
        app.processEvents()

    QTimer.singleShot(200, lambda: win.grab().save(str(out)))
    QTimer.singleShot(600, app.quit)
    app.exec()

    print(f"截图已保存到: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
