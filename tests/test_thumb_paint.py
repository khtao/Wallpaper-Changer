"""缩略图完整性验证：直接绘制委托并做像素级检查。

与「读 item.icon()」不同，这里把 ThumbDelegate 真正画到画布上，
再用纯色图定位实际绘制区域，能真实反映用户看到的结果。

运行：  python tests/test_thumb_paint.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("HOME", "/tmp/wallpaper-changer-paint")
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)

from PIL import Image  # noqa: E402
from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QStyle, QStyleOptionViewItem,
)

from wallpaper_changer.fetcher import Fetcher  # noqa: E402
from wallpaper_changer.models import ImageRecord  # noqa: E402
from wallpaper_changer.ui.gallery import (  # noqa: E402
    THUMB_SIZES, GalleryView, thumb_key, thumb_path,
)

# 比例差异大、颜色可区分的测试图：纯色便于像素定位
CASES = {
    "超宽21_9": ((2100, 900), (255, 0, 0)),
    "标准16_9": ((1920, 1080), (0, 255, 0)),
    "竖图3_4": ((1200, 1600), (0, 0, 255)),
    "方图1_1": ((1000, 1000), (255, 0, 255)),
    "全景3_1": ((3000, 1000), (0, 200, 200)),
}


def painted_rect(view: GalleryView, row: int, color: tuple) -> tuple:
    """把第 row 项画到画布，返回该图实际绘制区域 (w, h, left, top)。"""
    item = view.list.item(row)
    grid = view.list.gridSize()
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, grid.width(), grid.height())
    opt.widget = view.list
    opt.palette = view.list.palette()
    opt.font = view.list.font()
    opt.state |= QStyle.State_Enabled

    canvas = QPixmap(grid.width(), grid.height())
    canvas.fill(QColor(255, 255, 255))
    painter = QPainter(canvas)
    view.delegate.paint(painter, opt, view.list.indexFromItem(item))
    painter.end()

    img = canvas.toImage()
    target = QColor(*color)
    min_x = min_y = 10 ** 9
    max_x = max_y = -1
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            # 与测试图主色接近的像素才算图片内容（留出抗锯齿容差）
            if (abs(c.red() - target.red()) < 40
                    and abs(c.green() - target.green()) < 40
                    and abs(c.blue() - target.blue()) < 40):
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    if max_x < 0:
        return (0, 0, 0, 0)
    return (max_x - min_x + 1, max_y - min_y + 1, min_x, min_y)


def main() -> int:
    app = QApplication(sys.argv)
    tmp = Path("/tmp/wallpaper-changer-paint-data")
    tmp.mkdir(parents=True, exist_ok=True)

    recs = []
    for name, ((w, h), color) in CASES.items():
        p = tmp / f"{name}.jpg"
        Image.new("RGB", (w, h), color).save(p)
        recs.append(ImageRecord(source="local", source_id=str(p),
                                local_path=str(p), title=name, width=w, height=h))

    fetcher = Fetcher(tmp / "cache")
    view = GalleryView(fetcher)
    view.resize(900, 600)
    view.show()

    def wait_thumbs():
        dl = time.time() + 25
        while time.time() < dl:
            app.processEvents()
            if all(thumb_path(fetcher, thumb_key(r)).exists() for r in recs):
                break
            time.sleep(0.03)
        for _ in range(50):
            app.processEvents()

    def check(tag: str) -> bool:
        ok = True
        box = view.list.iconSize()
        worst = 0.0
        for row, rec in enumerate(recs):
            w, h, left, top = painted_rect(view, row, CASES[rec.title][1])
            if w <= 0:
                print(f"  {tag} {rec.title}: 未绘制出图片 ❌")
                ok = False
                continue
            src = rec.width / rec.height
            shown = w / h
            rel = abs(src - shown) / src
            worst = max(worst, rel)
            # 必须落在图标框内且不超出：超出即为被裁切
            fits = w <= box.width() + 1 and h <= box.height() + 1
            good = rel <= 0.03 and fits
            ok &= good
            flag = "OK" if good else "❌"
            print(f"  {tag} {rec.title}: 原{src:.3f} 实际绘制{shown:.3f} "
                  f"({w}x{h}) 误差{rel:.2%} 在框内={fits} {flag}")
        print(f"  → {tag} 最大误差 {worst:.3%} 通过={ok}")
        return ok

    all_ok = True
    view.set_records(recs)
    wait_thumbs()
    all_ok &= check("首次")

    for n in range(3):
        view.set_records(recs)
        wait_thumbs()
        all_ok &= check(f"刷新{n + 1}")

    for size in THUMB_SIZES:
        view._apply_thumb_size(size)
        wait_thumbs()
        all_ok &= check(f"档位{size}")

    # 再刷新一次，确认切档后刷新依旧完整
    view.set_records(recs)
    wait_thumbs()
    all_ok &= check("切档后刷新")

    view.shutdown()
    print("\n结论:", "任何操作后图片均完整不裁切 ✅" if all_ok else "仍有问题 ❌")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
