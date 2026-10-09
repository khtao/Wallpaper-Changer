"""PyQt/PySide 无关的小工具：图标生成与加载。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtCore import Qt


def assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def icon_file() -> Path:
    return assets_dir() / "icon.png"


def app_icon() -> QIcon:
    """优先使用 assets/icon.png，缺失时用代码绘制的图标。"""
    path = icon_file()
    if path.exists():
        return QIcon(str(path))
    return _drawn_icon()


def _drawn_icon(size: int = 256) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("#2f6fed"))
    p.setPen(Qt.NoPen)
    r = size * 0.12
    p.drawRoundedRect(int(r), int(r), int(size - 2 * r), int(size - 2 * r),
                      size * 0.18, size * 0.18)
    p.setBrush(QColor("#ffffff"))
    p.drawEllipse(int(size * 0.28), int(size * 0.30), int(size * 0.30), int(size * 0.30))
    p.setBrush(QColor("#7fd18a"))
    p.drawRect(int(size * 0.18), int(size * 0.62), int(size * 0.64), int(size * 0.16))
    p.end()
    return QIcon(pix)


def emoji_icon(text: str, size: int = 64) -> QIcon:
    """把单个字符/emoji 画成图标，避免依赖系统 emoji 字体。"""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor("#ffffff"))
    font = p.font()
    font.setPointSize(int(size * 0.55))
    p.setFont(font)
    p.drawText(0, 0, size, size, Qt.AlignCenter, text)
    p.end()
    return QIcon(pix)
