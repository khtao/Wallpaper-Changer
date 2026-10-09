"""图库（历史 / 收藏 / 全部）缩略图网格视图。

渲染说明（重要）：
    Qt 在 IconMode 下由内置委托绘制图标，会把任意比例的图强行拉伸成图标框的
    矩形尺寸（实测 21:9、16:9、3:4 的图都被拉成同一尺寸，原始比例丢失），
    无论缩略图文件本身是什么比例。

    因此这里改用**自绘委托** ThumbDelegate：图片区域由我们自己计算，
    用 KeepAspectRatio 等比缩放后居中绘制，任何刷新、筛选、切换尺寸
    都不会裁切或变形。
"""
from __future__ import annotations

import hashlib
import logging
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QThread, Signal, QObject
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QFontMetrics, QIcon, QPainter, QPalette,
    QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QScrollArea, QStyledItemDelegate, QStyle, QVBoxLayout, QWidget,
)

from ..fetcher import Fetcher
from ..models import ImageRecord

LOG = logging.getLogger(__name__)

# 缩略图档位：名称 -> (格子宽, 图片框宽, 图片框高)
THUMB_SIZES = {
    "小": (200, 176, 120),
    "中": (300, 276, 180),
    "大": (420, 396, 260),
}
DEFAULT_THUMB_SIZE = "中"
# 生成缩略图时按此宽度缩放（完整保留原始比例，绝不裁切）
THUMB_SOURCE_WIDTH = 900
# 缓存文件名带版本指纹：参数变更后旧缓存自动失效
THUMB_VERSION = "v3"
# 格子中留给标题文字的高度
TEXT_HEIGHT = 36
# 缩略图解码缓存条目上限，控制内存占用
PIX_CACHE_MAX = 64

THUMB_W, THUMB_H = (THUMB_SIZES[DEFAULT_THUMB_SIZE][0],
                    THUMB_SIZES[DEFAULT_THUMB_SIZE][2] + TEXT_HEIGHT + 12)


class ThumbLoader(QThread):
    """后台生成缩略图，避免界面卡顿。"""

    ready = Signal(str, str)  # key, thumb_path

    def __init__(self, fetcher: Fetcher, jobs: List[tuple], parent=None):
        super().__init__(parent)
        self.fetcher = fetcher
        self.jobs = jobs
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # pragma: no cover
        try:
            from PIL import Image
        except Exception:  # noqa: BLE001
            return
        for rec, key in self.jobs:
            if self._stop:
                return
            dest = thumb_path(self.fetcher, key)
            if dest.exists():
                self.ready.emit(key, str(dest))
                continue
            try:
                src = self.fetcher.ensure_local(rec)
                with Image.open(src) as im:
                    im = im.convert("RGB")
                    # 只按宽度缩放、高度自适应 —— 完整保留原图比例，不做任何裁切
                    if im.width > THUMB_SOURCE_WIDTH:
                        ratio = THUMB_SOURCE_WIDTH / im.width
                        im = im.resize(
                            (THUMB_SOURCE_WIDTH, max(1, int(im.height * ratio))),
                            Image.LANCZOS,
                        )
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    im.save(dest, "JPEG", quality=88, optimize=True)
                self.ready.emit(key, str(dest))
            except Exception as exc:  # noqa: BLE001
                LOG.debug("生成缩略图失败 %s: %s", rec.display_title, exc)


def thumb_key(rec: ImageRecord) -> str:
    raw = f"{rec.source}:{rec.source_id}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _fingerprint() -> str:
    return hashlib.md5(
        f"{THUMB_VERSION}:{THUMB_SOURCE_WIDTH}".encode()).hexdigest()[:8]


def thumb_path(fetcher: Fetcher, key: str) -> Path:
    """缩略图缓存路径，文件名带版本指纹。"""
    return Path(fetcher.cache_dir) / "thumbs" / f"{key}_{_fingerprint()}.jpg"


def _purge(fetcher: Fetcher, keep_fp: Optional[str]) -> int:
    d = Path(fetcher.cache_dir) / "thumbs"
    if not d.exists():
        return 0
    n = 0
    for p in d.glob("*.jpg"):
        if keep_fp and p.name.endswith(f"_{keep_fp}.jpg"):
            continue
        try:
            p.unlink()
            n += 1
        except OSError:
            pass
    return n


def purge_thumb_cache(fetcher: Fetcher) -> int:
    """清空全部缩略图缓存，返回删除数。"""
    return _purge(fetcher, None)


def purge_stale_thumb_cache(fetcher: Fetcher) -> int:
    """删除旧参数生成的缩略图，返回删除数。"""
    return _purge(fetcher, _fingerprint())


class ThumbDelegate(QStyledItemDelegate):
    """自绘缩略图：等比缩放 + 居中，任何情况下都完整显示。"""

    def __init__(self, view: "GalleryView", parent=None):
        super().__init__(parent)
        self.view = view
        self._cache: Dict[tuple, QPixmap] = {}
        self._order: deque = deque()

    # ---------- 缩略图解码缓存 ----------
    def pixmap_for(self, path: str, box: QSize) -> Optional[QPixmap]:
        if not path:
            return None
        key = (path, box.width(), box.height())
        pix = self._cache.get(key)
        if pix is not None:
            return pix
        pm = QPixmap(path)
        if pm.isNull():
            return None
        # 只缩到不超过框，绝不放大填充、绝不裁切
        pm = pm.scaled(box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._cache[key] = pm
        self._order.append(key)
        while len(self._order) > PIX_CACHE_MAX:
            old = self._order.popleft()
            self._cache.pop(old, None)
        return pm

    def invalidate(self) -> None:
        self._cache.clear()
        self._order.clear()

    # ---------- 尺寸 ----------
    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return self.view.list.gridSize()

    # ---------- 绘制 ----------
    def paint(self, painter: QPainter, option, index) -> None:  # noqa: C901
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        favorite = bool(index.data(GalleryView.ROLE_FAVORITE))

        # 卡片背景
        pal = option.palette
        bg = QColor(pal.color(QPalette.ColorRole.Highlight)) if selected \
            else QColor(pal.color(QPalette.ColorRole.Base))
        if hovered and not selected:
            bg = bg.lighter(103)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(QRectF(rect).adjusted(3, 3, -3, -3), 6, 6)

        inner = rect.adjusted(8, 8, -8, -8)
        text_rect = QRect(inner.left(), inner.bottom() - TEXT_HEIGHT + 4,
                          inner.width(), TEXT_HEIGHT - 4)
        # 图片区以「图标框」为上限，绝不比图标框更宽/更高，
        # 否则绘制时会把已缩放好的图再次放大，导致超出格子。
        icon = self.view.list.iconSize()
        img_w = min(inner.width(), icon.width())
        img_h = min(max(8, inner.height() - TEXT_HEIGHT), icon.height())
        img_rect = QRect(inner.left() + (inner.width() - img_w) // 2,
                         inner.top(), img_w, img_h)

        # ---- 图片：等比缩放后居中，保证完整可见 ----
        path = index.data(GalleryView.ROLE_PATH)
        pix = self.pixmap_for(path, QSize(img_w, img_h)) if path else None
        if pix and not pix.isNull():
            target = self._fit_rect(pix.size(), img_rect)
            painter.drawPixmap(target, pix)
            painter.setPen(QPen(QColor(0, 0, 0, 60)))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(target).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        else:
            # 占位：还没生成好或生成失败
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(pal.color(
                QPalette.ColorRole.AlternateBase))))
            painter.drawRoundedRect(QRectF(img_rect), 4, 4)
            painter.setPen(QPen(pal.color(
                QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)))
            painter.drawText(img_rect, Qt.AlignCenter, "加载中…")

        # ---- 收藏角标 ----
        if favorite:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(255, 196, 0)))
            star = QRect(img_rect.right() - 20, img_rect.top() + 4, 16, 16)
            painter.drawEllipse(QRectF(star))
            painter.setPen(QPen(QColor(80, 55, 0)))
            f = QFont(painter.font())
            f.setPointSize(max(7, f.pointSize() - 2))
            painter.setFont(f)
            painter.drawText(star, Qt.AlignCenter, "★")

        # ---- 标题（两行，超出省略）----
        color = pal.color(QPalette.ColorRole.HighlightedText if selected
                          else QPalette.ColorRole.Text)
        painter.setPen(QPen(color))
        fm = QFontMetrics(option.font)
        title = index.data(Qt.DisplayRole) or ""
        elided = fm.elidedText(title, Qt.ElideRight, text_rect.width())
        painter.drawText(text_rect, Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap,
                         elided)
        painter.restore()

    @staticmethod
    def _fit_rect(src: QSize, box: QRect) -> QRect:
        """把 src 等比缩放到完全放进 box，返回居中后的矩形。

        缩放系数上限为 1：只缩小不放大，避免小图被拉伸模糊，
        同时保证绘制结果绝不超出 box（超出即意味着会被裁切）。
        """
        if src.width() <= 0 or src.height() <= 0 or box.width() <= 0 or box.height() <= 0:
            return box
        scale = min(box.width() / src.width(), box.height() / src.height(), 1.0)
        w = max(1, int(src.width() * scale))
        h = max(1, int(src.height() * scale))
        return QRect(box.left() + (box.width() - w) // 2,
                     box.top() + (box.height() - h) // 2, w, h)


class GalleryView(QWidget):
    """历史 / 收藏 / 全部 图片网格：筛选 + 自绘缩略图 + 右键菜单。"""

    applyRequested = Signal(object)       # ImageRecord
    favoriteToggled = Signal(object)      # ImageRecord
    deleteRequested = Signal(object)      # ImageRecord
    countChanged = Signal(int)
    reloadRequested = Signal()

    MODES = {"历史": "history", "收藏": "favorites", "全部": "all"}

    ROLE_KEY = Qt.UserRole          # 缩略图 key
    ROLE_PATH = Qt.UserRole + 1     # 缩略图文件路径
    ROLE_FAVORITE = Qt.UserRole + 2  # 是否收藏

    def __init__(self, fetcher: Fetcher, parent=None):
        super().__init__(parent)
        self.fetcher = fetcher
        self.records: Dict[str, ImageRecord] = {}
        self._loader: ThumbLoader | None = None
        self._thumb_paths: Dict[str, str] = {}
        try:
            purge_stale_thumb_cache(fetcher)   # 清掉旧参数生成的缩略图
        except Exception:  # noqa: BLE001
            pass
        self._build_ui()

    # ---------- 界面 ----------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(list(self.MODES))
        self.combo_mode.setToolTip("切换查看范围")
        self.combo_mode.currentTextChanged.connect(self.reload)
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("搜索标题 / 作者 / 来源…")
        self.edit_search.setClearButtonEnabled(True)
        self.edit_search.textChanged.connect(self._apply_filter)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_apply = QPushButton("设为壁纸")
        self.btn_apply.setToolTip("把选中的图片设为当前壁纸")
        self.btn_apply.clicked.connect(self._apply_selected)
        self.label_count = QLabel("0 张")

        self.combo_thumb = QComboBox()
        self.combo_thumb.addItems(list(THUMB_SIZES))
        self.combo_thumb.setCurrentText(DEFAULT_THUMB_SIZE)
        self.combo_thumb.setToolTip("调整缩略图显示大小（图片始终完整显示，不裁切）")
        self.combo_thumb.currentTextChanged.connect(self._apply_thumb_size)
        self.btn_rebuild = QPushButton("重建缩略图")
        self.btn_rebuild.setToolTip("清空缩略图缓存并重新生成，用于修复显示异常")
        self.btn_rebuild.clicked.connect(self.rebuild_thumbnails)

        for w, stretch in ((QLabel("筛选："), 0), (self.combo_mode, 0),
                           (self.edit_search, 1), (self.btn_refresh, 0),
                           (self.btn_apply, 0), (QLabel("尺寸："), 0),
                           (self.combo_thumb, 0), (self.btn_rebuild, 0),
                           (self.label_count, 0)):
            bar.addWidget(w, stretch)
        layout.addLayout(bar)

        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setSpacing(8)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemDoubleClicked.connect(
            lambda item: self._view_full(self.records.get(item.data(self.ROLE_KEY))))
        self.list.itemSelectionChanged.connect(self._sync_apply_button)
        self.delegate = ThumbDelegate(self, self.list)
        self.list.setItemDelegate(self.delegate)
        self._apply_thumb_size(DEFAULT_THUMB_SIZE)
        layout.addWidget(self.list, 1)

        self._apply_filter()

    # ---------- 显示尺寸 ----------
    def _apply_thumb_size(self, name: str) -> None:
        """切换档位：更新格子尺寸并强制重绘（委托按新尺寸重新等比缩放）。"""
        grid_w, icon_w, icon_h = THUMB_SIZES.get(name, THUMB_SIZES[DEFAULT_THUMB_SIZE])
        self.list.setIconSize(QSize(icon_w, icon_h))
        self.list.setGridSize(QSize(grid_w, icon_h + TEXT_HEIGHT + 12))
        if hasattr(self, "delegate"):
            self.delegate.invalidate()
            # 格子尺寸变了必须让视图重新布局，否则会沿用旧的项几何
            self.list.scheduleDelayedItemsLayout()
        self.list.viewport().update()

    # ---------- 数据 ----------
    def reload(self) -> None:
        """通知宿主重新查询数据（宿主再调用 set_records）。"""
        self.reloadRequested.emit()

    def rebuild_thumbnails(self) -> None:
        """清空缩略图缓存并重新加载。"""
        purge_thumb_cache(self.fetcher)
        self._thumb_paths = {}
        if hasattr(self, "delegate"):
            self.delegate.invalidate()
        self.reload()

    def shutdown(self) -> None:
        """退出前停掉后台线程，避免出现“线程仍在运行”的告警。"""
        loader = self._loader
        if loader is not None and loader.isRunning():
            loader.stop()
            loader.wait(2000)

    def set_records(self, records: List[ImageRecord]) -> None:
        if self._loader and self._loader.isRunning():
            self._loader.stop()
            self._loader.wait(200)
        self.list.clear()
        self._thumb_paths = {}
        self.records = {thumb_key(r): r for r in records}
        jobs = []
        for rec in records:
            key = thumb_key(rec)
            item = QListWidgetItem(self._title(rec))
            item.setData(self.ROLE_KEY, key)
            item.setData(self.ROLE_PATH, "")
            item.setData(self.ROLE_FAVORITE, bool(rec.is_favorite))
            item.setToolTip(self._tooltip(rec))
            item.setSizeHint(self.list.gridSize())
            self.list.addItem(item)
            jobs.append((rec, key))
        self.label_count.setText(f"{len(records)} 张")
        self.countChanged.emit(len(records))
        self._sync_apply_button()
        self._apply_filter()
        self._loader = ThumbLoader(self.fetcher, jobs)
        self._loader.ready.connect(self._on_thumb)
        self._loader.start()

    def _title(self, rec: ImageRecord) -> str:
        title = rec.display_title
        return title if len(title) <= 26 else title[:25] + "…"

    @staticmethod
    def _tooltip(rec: ImageRecord) -> str:
        lines = [
            rec.display_title,
            f"来源：{rec.source_name or rec.source}",
        ]
        if rec.author:
            lines.append(f"作者：{rec.author}")
        if rec.width and rec.height:
            lines.append(f"分辨率：{rec.width}×{rec.height}")
        if rec.webpage:
            lines.append(rec.webpage)
        return "\n".join(lines)

    def _on_thumb(self, key: str, path: str) -> None:
        self._thumb_paths[key] = path
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.data(self.ROLE_KEY) == key:
                item.setData(self.ROLE_PATH, path)
                return

    def _apply_filter(self, _text: str = "") -> None:
        text = self.edit_search.text().strip().lower()
        shown = 0
        total = self.list.count()
        for i in range(total):
            item = self.list.item(i)
            rec = self.records.get(item.data(self.ROLE_KEY))
            if not rec:
                continue
            hay = f"{rec.display_title} {rec.author} {rec.source} {rec.tags}".lower()
            visible = (not text) or (text in hay)
            item.setHidden(not visible)
            shown += 1 if visible else 0
        self.label_count.setText(f"{shown} / {total} 张")

    # ---------- 选中与操作 ----------
    def _selected_record(self) -> Optional[ImageRecord]:
        item = self.list.currentItem()
        if item is None:
            return None
        return self.records.get(item.data(self.ROLE_KEY))

    def _sync_apply_button(self) -> None:
        if hasattr(self, "btn_apply"):
            self.btn_apply.setEnabled(self._selected_record() is not None)

    def _apply_selected(self) -> None:
        rec = self._selected_record()
        if rec:
            self.applyRequested.emit(rec)

    # ---------- 右键菜单 ----------
    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        rec = self.records.get(item.data(self.ROLE_KEY))
        if not rec:
            return
        menu = QMenu(self)
        act_view = QAction("查看完整大图", self)
        act_apply = QAction("设为当前壁纸", self)
        act_fav = QAction("取消收藏" if rec.is_favorite else "收藏", self)
        act_open = QAction("打开原图链接", self)
        act_export = QAction("另存为…", self)
        act_del = QAction("从库中删除", self)
        act_copy = QAction("复制标题", self)
        for a in (act_view, act_apply, act_fav, act_open, act_export, act_del, act_copy):
            menu.addAction(a)
        act_view.triggered.connect(lambda: self._view_full(rec))
        act_apply.triggered.connect(lambda: self.applyRequested.emit(rec))
        act_fav.triggered.connect(lambda: self.favoriteToggled.emit(rec))
        act_open.triggered.connect(lambda: self._open(rec))
        act_export.triggered.connect(lambda: self._export(rec))
        act_del.triggered.connect(lambda: self.deleteRequested.emit(rec))
        act_copy.triggered.connect(lambda: self._copy(rec.display_title))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # ---------- 完整大图 ----------
    def _view_full(self, rec: Optional[ImageRecord]) -> None:
        """双击 / 菜单：弹出窗口完整显示这张图（等比缩放，不裁切）。"""
        if rec is None:
            return
        try:
            path = self.fetcher.ensure_local(rec)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "无法查看", f"图片不可用：{exc}")
            return
        pix = QPixmap(str(path))
        if pix.isNull():
            QMessageBox.warning(self, "无法查看", "无法读取该图片文件")
            return

        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        max_w = int(avail.width() * 0.86) if avail else 1400
        max_h = int(avail.height() * 0.86) if avail else 900
        self._last_full_pix = pix
        self._last_full_rec = rec
        dlg = FullImageDialog(pix, max_w, max_h, rec, self)
        dlg.exec()

    def _open(self, rec: ImageRecord) -> None:
        url = rec.webpage or rec.remote_url
        if not url:
            QMessageBox.information(self, "提示", "该图片没有可用的网页链接")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))

    def _export(self, rec: ImageRecord) -> None:
        dest = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not dest:
            return
        try:
            path = self.fetcher.export_to(rec, Path(dest))
            QMessageBox.information(self, "已保存", f"已保存到：\n{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)


class FullImageDialog(QDialog):
    """完整显示一张图：窗口变化时始终等比缩放，绝不裁切。"""

    def __init__(self, pixmap: QPixmap, max_w: int, max_h: int,
                 rec: ImageRecord | None = None, parent=None):
        super().__init__(parent)
        self._pix = pixmap
        self._max_w, self._max_h = max_w, max_h
        self.rec = rec
        self.setWindowTitle(
            f"{rec.display_title} · {rec.resolution}" if rec else "完整图片")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setPixmap(self._scaled())
        area = QScrollArea(self)
        area.setWidgetResizable(True)
        area.setWidget(self.label)
        lay.addWidget(area)
        size = self._scaled().size()
        self.resize(min(size.width() + 28, max_w), min(size.height() + 60, max_h))

    def _scaled(self) -> QPixmap:
        return self._pix.scaled(QSize(self._max_w, self._max_h),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        # 窗口尺寸变化时重新按当前视口等比缩放，保证永远完整可见
        if hasattr(self, "label"):
            self.label.setPixmap(self._scaled())
