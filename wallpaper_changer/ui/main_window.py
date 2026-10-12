"""主窗口：概览 / 来源 / 收藏 / 历史 / 高级设置。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QThread, QTimer, Signal, QSize
from PySide6.QtWidgets import QApplication  # noqa: F401  （发现分类时刷新界面）
from PySide6.QtGui import QAction, QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QGridLayout, QSizePolicy, QBoxLayout, QScrollArea,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget, QDoubleSpinBox,
)

from ..config import Config, IS_WINDOWS, fit_modes_for_platform
from ..database import Database
from ..models import ImageRecord
from ..qt_utils import stop_thread, thread_is_running
from ..wallpaper import detect_desktop, screen_info
from ..sources.bzee import (
    DESKTOP_CATEGORIES, MOBILE_CATEGORIES, POPULAR_KEYWORDS, BzeeSource,
)
from ..sources.qh360 import BUILTIN_CATEGORIES as QH360_CATEGORIES, Qh360Source
from .gallery import GalleryView, load_pixmap
from .wheel_guard import WheelSettingGuard

LOG = logging.getLogger(__name__)


class _PreviewLoader(QThread):
    """后台下载首页预览图（缓存缺失时补载）。"""

    done = Signal(str)      # 本地路径，失败则为空串

    def __init__(self, fetcher, rec: ImageRecord):
        super().__init__()
        self.fetcher = fetcher
        self.rec = rec

    def run(self) -> None:  # pragma: no cover - 线程环境
        try:
            path = self.fetcher.ensure_local(
                self.rec, cancelled=self.isInterruptionRequested)
            self.done.emit(path)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("预览图下载失败：%s", exc)
            self.done.emit("")


class _DiscoverTask(QThread):
    done = Signal(object)

    def __init__(self, settings, config, parent=None):
        super().__init__(parent)
        self.settings, self.config = settings, config

    def run(self) -> None:  # pragma: no cover
        src = BzeeSource(self.settings, self.config)
        try:
            found = src.discover_categories()
            if not self.isInterruptionRequested():
                self.done.emit(found)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Bzee 分类发现异常：%s", exc)
            if not self.isInterruptionRequested():
                self.done.emit({})
        finally:
            src.close()


class _Discover360Task(QThread):
    """后台读取 360 分类，网络失败不能阻塞主界面。"""
    done = Signal(object, str)

    def __init__(self, settings, config, parent=None):
        super().__init__(parent)
        self.settings, self.config = settings, config

    def run(self) -> None:  # pragma: no cover
        source = Qh360Source(self.settings, self.config)
        try:
            categories = source.discover_categories()
            if not self.isInterruptionRequested():
                self.done.emit(categories, "" if categories else "API 未返回分类")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("360 壁纸分类接口失败：%s", exc)
            if not self.isInterruptionRequested():
                self.done.emit({}, str(exc))
        finally:
            source.close()


class _CacheMaintenanceTask(QThread):
    """维护缓存使用独立线程，不在 GUI 主线程遍历/解码几百张图片。"""

    done = Signal(str, int, int, str)  # mode, count, removed, error

    def __init__(self, fetcher, mode: str, parent=None):
        super().__init__(parent)
        self.fetcher, self.mode = fetcher, mode

    def run(self) -> None:  # pragma: no cover
        try:
            if self.mode == "migrate":
                converted, removed = self.fetcher.migrate_cache_to_jpeg(
                    cancelled=self.isInterruptionRequested)
                if not self.isInterruptionRequested():
                    self.done.emit(self.mode, converted, removed, "")
                return
            bad = scanned = 0
            for p in Path(self.fetcher.cache_dir).rglob("*"):
                if self.isInterruptionRequested():
                    return
                if (not p.is_file() or p.suffix.lower() in (".part", ".tmp")
                        or p in self.fetcher.protected_paths):
                    continue
                scanned += 1
                try:
                    if not self.fetcher.is_valid_cached(p):
                        p.unlink()
                        bad += 1
                except OSError:
                    pass
            self.done.emit(self.mode, scanned, bad, "")
        except Exception as exc:  # noqa: BLE001
            LOG.exception("缓存维护异常")
            if not self.isInterruptionRequested():
                self.done.emit(self.mode, 0, 0, str(exc))


class MainWindow(QMainWindow):
    """配置主界面。"""

    settingsChanged = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx                 # Application 上下文
        self.gallery_mode = "历史"      # 图库默认查看范围：历史 / 收藏 / 全部
        self.config: Config = ctx.config
        self.db: Database = ctx.db
        self.engine = ctx.engine
        self._closing = False
        self.setWindowTitle("Wallpaper Changer · 自动壁纸")
        screen = QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        # Qt6 已按逻辑像素处理 DPI；按屏幕可用尺寸限制窗口，避免缩放 200% 时越界。
        self.resize(max(320, min(980, avail.width() - 24)) if avail else 980,
                    max(320, min(720, avail.height() - 24)) if avail else 720)
        self._preview_path = ""
        self._preview_pending = None
        self._build_ui()
        # 滚轮用于翻动页面，不允许意外改写下拉框或数值设置。
        self._wheel_setting_guard = WheelSettingGuard(self)
        self._wheel_setting_guard.install_on(self)
        self._load_settings()
        self._connect_engine()
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._update_countdown)
        self._tick.start(1000)

    # ------------------------------------------------------------------ #
    # 界面搭建
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # --- 顶部：当前壁纸 ---
        top = QBoxLayout(QBoxLayout.LeftToRight)
        self._top_layout = top
        self.preview = QLabel()
        self.preview.setMinimumSize(160, 96)
        self.preview.setMaximumSize(300, 170)
        self.preview.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setStyleSheet(
            "background:#202124;color:#9aa0a6;border:1px solid #444;border-radius:6px;")
        self.preview.setText("尚未设置壁纸")
        top.addWidget(self.preview)

        info = QVBoxLayout()
        self.label_title = QLabel("—")
        self.label_title.setWordWrap(True)
        font = self.label_title.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.label_title.setFont(font)
        self.label_meta = QLabel("—")
        self.label_meta.setWordWrap(True)
        self.label_meta.setStyleSheet("color:#8a8f98;")
        info.addWidget(self.label_title)
        info.addWidget(self.label_meta)
        info.addStretch(1)

        btns = QGridLayout()
        self.btn_prev = QPushButton("← 上一张")
        self.btn_next = QPushButton("下一张 →")
        self.btn_fav = QPushButton("★ 收藏")
        self.btn_refresh = QPushButton("↻ 抓取新图")
        for i, b in enumerate((self.btn_prev, self.btn_next,
                               self.btn_fav, self.btn_refresh)):
            btns.addWidget(b, i // 2, i % 2)
        self.btn_prev.clicked.connect(self.engine.change_prev)
        self.btn_next.clicked.connect(lambda: self.engine.change_next("手动"))
        self.btn_fav.clicked.connect(self._toggle_fav)
        self.btn_refresh.clicked.connect(lambda: self.engine.refresh_pool(force=True))
        info.addLayout(btns)
        top.addLayout(info, 1)
        root.addLayout(top, 0)

        # --- 标签页 ---
        self.tabs = QTabWidget()
        self.tabs.addTab(self._scroll_tab(self._build_general_tab()), "轮换")
        self.tabs.addTab(self._scroll_tab(self._build_sources_tab()), "图片来源")
        # 收藏与历史合并为同一个「图库」选项卡，用筛选下拉切换范围
        self.gallery = GalleryView(self.engine.fetcher)
        self.tabs.addTab(self.gallery, "图库")
        self.tabs.addTab(self._scroll_tab(self._build_advanced_tab()), "高级")
        self.tabs.addTab(self._build_about_tab(), "关于")
        root.addWidget(self.tabs, 3)

        # --- 底部按钮 ---
        bottom = QHBoxLayout()
        self.label_status = QLabel("就绪")
        self.label_status.setStyleSheet("color:#8a8f98;")
        self.btn_save = QPushButton("保存并应用")
        self.btn_save.clicked.connect(self._save_settings)
        self.btn_tray = QPushButton("隐藏到托盘")
        self.btn_tray.clicked.connect(self.hide_to_tray)
        bottom.addWidget(self.label_status, 1)
        bottom.addWidget(self.btn_tray)
        bottom.addWidget(self.btn_save)
        root.addLayout(bottom)

        self.setCentralWidget(central)

        # 图库信号（收藏与历史已合并在同一视图内）
        self.gallery.applyRequested.connect(self.engine.apply_record)
        self.gallery.favoriteToggled.connect(self.engine.toggle_favorite)
        self.gallery.deleteRequested.connect(self._delete_record)
        self.gallery.reloadRequested.connect(self.refresh_views)
        self.gallery.combo_mode.setCurrentText(self.gallery_mode)
        self.tabs.currentChanged.connect(self._on_tab_changed)

    @staticmethod
    def _scroll_tab(content: QWidget) -> QScrollArea:
        """设置项较多的页面允许双向滚动，200%/300% 缩放下不丢操作控件。"""
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(content)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        area.setFrameShape(QScrollArea.NoFrame)
        return area

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        layout = getattr(self, "_top_layout", None)
        if layout is not None:
            desired = (QBoxLayout.TopToBottom if self.width() < 700
                       else QBoxLayout.LeftToRight)
            if layout.direction() != desired:
                layout.setDirection(desired)
        # QLabel 的图片已限定了解码尺寸；窗口尺寸变化时只重用缓存文件。
        if getattr(self, "_preview_path", "") and hasattr(self, "preview"):
            if not getattr(self, "_preview_resize_pending", False):
                self._preview_resize_pending = True
                QTimer.singleShot(0, self._refresh_preview_after_resize)

    def _refresh_preview_after_resize(self) -> None:
        self._preview_resize_pending = False
        if self._preview_path and not self._closing:
            self._set_preview_pixmap(self._preview_path)

    # ---------- 轮换 ----------
    def _build_general_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        g = QGroupBox("定时轮换")
        f = QFormLayout(g)
        self.chk_enabled = QCheckBox("启用自动轮换")
        self.chk_enabled.setToolTip("勾选后倒计时立即开始；取消则停止。需点「保存并应用」才会长期生效。")
        # 勾选/取消即时启停倒计时，不必等保存
        self.chk_enabled.toggled.connect(self._on_enabled_toggled)
        self.spin_interval = QDoubleSpinBox()
        self.spin_interval.setRange(0.5, 1440)
        self.spin_interval.setDecimals(1)
        self.spin_interval.setSuffix(" 分钟")
        self.spin_interval.setValue(30)
        self.combo_unit = QComboBox()
        self.combo_unit.addItems(["分钟", "小时"])
        row_interval = QHBoxLayout()
        row_interval.addWidget(self.spin_interval)
        row_interval.addWidget(self.combo_unit)
        row_interval.addStretch(1)
        w_interval = QWidget()
        w_interval.setLayout(row_interval)
        self.chk_start_change = QCheckBox("启动程序时立即换一张")
        self.chk_random = QCheckBox("随机顺序（关闭则按来源顺序）")
        self.spin_avoid = QSpinBox()
        self.spin_avoid.setRange(0, 365)
        self.spin_avoid.setSuffix(" 天内不重复")
        self.combo_fit = QComboBox()
        self.combo_fit.addItems(list(fit_modes_for_platform()))
        self.chk_dark = QCheckBox("同时设置深色模式壁纸（Linux/GNOME）")
        self.spin_minw = QSpinBox()
        self.spin_minw.setRange(0, 20000)
        self.spin_minw.setSingleStep(10)
        self.spin_minh = QSpinBox()
        self.spin_minh.setRange(0, 20000)
        self.spin_minh.setSingleStep(10)
        row_min = QHBoxLayout()
        row_min.addWidget(self.spin_minw)
        row_min.addWidget(QLabel("×"))
        row_min.addWidget(self.spin_minh)
        row_min.addWidget(QLabel("最小分辨率"))
        row_min.addStretch(1)
        w_min = QWidget()
        w_min.setLayout(row_min)
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(5, 120)
        self.spin_timeout.setSuffix(" 秒 网络超时")

        f.addRow(self.chk_enabled)
        f.addRow("切换间隔：", w_interval)
        f.addRow(self.chk_start_change)
        f.addRow(self.chk_random)
        f.addRow(self.spin_avoid)
        f.addRow("显示方式：", self.combo_fit)
        f.addRow(self.chk_dark)
        f.addRow("图片下限：", w_min)
        f.addRow(self.spin_timeout)
        lay.addWidget(g)

        g2 = QGroupBox("当前状态")
        f2 = QFormLayout(g2)
        self.label_next = QLabel("—")
        self.label_pool = QLabel("—")
        self.label_desk = QLabel(detect_desktop() if not IS_WINDOWS else "Windows")
        self.label_screen = QLabel(screen_info())
        self.btn_pause = QPushButton("暂停")
        self.btn_pause.setCheckable(True)
        self.btn_pause.setChecked(False)
        self.btn_pause.setToolTip(
            "暂停仅作用于本次运行；重启程序会按配置自动开始倒计时。\n"
            "要长期关闭，请取消上方「启用自动轮换」。")
        self.btn_pause.toggled.connect(self._toggle_pause)
        self.btn_next_now = QPushButton("立即切换")
        self.btn_next_now.setToolTip("不等倒计时，马上换一张")
        self.btn_next_now.clicked.connect(lambda: self.engine.change_next("手动"))
        row_state = QHBoxLayout()
        row_state.addWidget(self.btn_pause)
        row_state.addWidget(self.btn_next_now)
        row_state.addStretch(1)
        w_state = QWidget()
        w_state.setLayout(row_state)
        f2.addRow("桌面环境：", self.label_desk)
        f2.addRow("屏幕：", self.label_screen)
        f2.addRow("候选池：", self.label_pool)
        f2.addRow("下次切换：", self.label_next)
        f2.addRow(w_state)
        lay.addWidget(g2)
        lay.addStretch(1)
        return w

    # ---------- 来源 ----------
    def _build_sources_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # Bing
        gb = QGroupBox("Bing 每日壁纸")
        gb.setCheckable(True)
        self.gb_bing = gb
        f = QFormLayout(gb)
        self.combo_market = QComboBox()
        self.combo_market.setEditable(True)
        self.combo_market.addItems(
            ["zh-CN", "en-US", "en-GB", "ja-JP", "de-DE", "fr-FR", "en-IN", "en-CA"])
        self.spin_bing_count = QSpinBox()
        self.spin_bing_count.setRange(1, 16)
        self.combo_bing_res = QComboBox()
        self.combo_bing_res.addItems(["UHD", "1920x1080", "1366x768", "1280x720", "mobile"])
        f.addRow("区域市场：", self.combo_market)
        f.addRow("拉取天数：", self.spin_bing_count)
        f.addRow("分辨率：", self.combo_bing_res)
        lay.addWidget(gb)

        # 360 高清壁纸：接口历史较久，独立开关，不能影响其它来源
        gb360 = QGroupBox("360 高清壁纸（历史接口，可能已停止维护）")
        gb360.setCheckable(True)
        self.gb_360 = gb360
        v360 = QVBoxLayout(gb360)
        self.combo_360_mode = QComboBox()
        self.combo_360_mode.addItem("按分类抓取", "category")
        self.combo_360_mode.addItem("最近更新", "latest")
        self.combo_360_mode.currentIndexChanged.connect(self._360_mode_changed)
        fm360 = QFormLayout()
        fm360.addRow("获取模式：", self.combo_360_mode)
        self.spin_360_count = QSpinBox()
        self.spin_360_count.setRange(1, 50)
        self.spin_360_pages = QSpinBox()
        self.spin_360_pages.setRange(1, 5)
        fm360.addRow("每页数量：", self.spin_360_count)
        fm360.addRow("抓取页数：", self.spin_360_pages)
        v360.addLayout(fm360)
        row360 = QHBoxLayout()
        self.btn_360_refresh = QPushButton("在线更新分类")
        self.btn_360_refresh.clicked.connect(self._360_discover)
        self.label_360_status = QLabel("旧接口可能不可用；未选分类时按最近更新抓取")
        self.label_360_status.setStyleSheet("color:#8a8f98;")
        row360.addWidget(self.btn_360_refresh)
        row360.addWidget(self.label_360_status, 1)
        v360.addLayout(row360)
        self.list_360_cat = QListWidget()
        self.list_360_cat.setSelectionMode(QListWidget.MultiSelection)
        self.list_360_cat.setToolTip("Ctrl / Shift 多选分类；未选则抓取最近更新。")
        v360.addWidget(self.list_360_cat)
        self._360_fill_categories()
        lay.addWidget(gb360)

        # Bzee 图库
        gbz = QGroupBox("Bzee 图库（photo.bzee.cn · 4K 高清）")
        gbz.setCheckable(True)
        self.gb_bzee = gbz
        vbz = QVBoxLayout(gbz)

        row_lib = QHBoxLayout()
        self.combo_bzee_lib = QComboBox()
        self.combo_bzee_lib.addItem("桌面图库", "desktop")
        self.combo_bzee_lib.addItem("手机图库", "mobile")
        self.combo_bzee_lib.setToolTip("切换图库会重新载入该库下的分类")
        self.combo_bzee_lib.currentIndexChanged.connect(self._bzee_lib_changed)
        self.btn_bzee_all = QPushButton("全选")
        self.btn_bzee_none = QPushButton("清空")
        self.btn_bzee_all.clicked.connect(lambda: self._bzee_select(True))
        self.btn_bzee_none.clicked.connect(lambda: self._bzee_select(False))
        row_lib.addWidget(QLabel("图库："))
        row_lib.addWidget(self.combo_bzee_lib)
        row_lib.addStretch(1)
        row_lib.addWidget(self.btn_bzee_all)
        row_lib.addWidget(self.btn_bzee_none)
        vbz.addLayout(row_lib)

        row_disc = QHBoxLayout()
        self.btn_bzee_disc = QPushButton("在线刷新分类")
        self.btn_bzee_disc.setToolTip(
            "联网抓取 photo.bzee.cn，用站点上真实的分类链接替换内置列表。"
            "若你的网络访问该站返回 403，请保持内置列表或手动勾选。")
        self.btn_bzee_disc.clicked.connect(self._bzee_discover)
        self.label_bzee_disc = QLabel("")
        self.label_bzee_disc.setStyleSheet("color:#8a8f98;")
        row_disc.addWidget(self.btn_bzee_disc)
        row_disc.addWidget(self.label_bzee_disc, 1)
        vbz.addLayout(row_disc)

        self.list_bzee_cat = QListWidget()
        self.list_bzee_cat.setSelectionMode(QListWidget.MultiSelection)
        self.list_bzee_cat.setToolTip(
            "不选 = 抓该图库首页（各分类混合的最新图）；\n"
            "勾选后只抓这些分类。Ctrl/Shift 可多选。")
        vbz.addWidget(self.list_bzee_cat, 1)

        self.edit_bzee_kw = QLineEdit()
        self.edit_bzee_kw.setPlaceholderText(
            "关键词，逗号分隔，留空不过滤。例：风景,星空,游戏")
        self.edit_bzee_kw.setToolTip("只对图片标题做包含匹配；站点热门词："
                                     + "、".join(POPULAR_KEYWORDS))
        self.spin_bzee_pages = QSpinBox()
        self.spin_bzee_pages.setRange(1, 5)
        self.spin_bzee_pages.setSuffix(" 页/分类")
        self.combo_bzee_size = QComboBox()
        self.combo_bzee_size.addItem("原图（推荐，失败自动用缩略图）", "origin")
        self.combo_bzee_size.addItem("缩略图（加载快、画质低）", "thumb")
        fbz = QFormLayout()
        fbz.addRow("关键词：", self.edit_bzee_kw)
        fbz.addRow("抓取页数：", self.spin_bzee_pages)
        fbz.addRow("图片尺寸：", self.combo_bzee_size)
        vbz.addLayout(fbz)
        lay.addWidget(gbz)

        # 本地
        gb3 = QGroupBox("本地文件夹")
        gb3.setCheckable(True)
        self.gb_local = gb3
        v3 = QVBoxLayout(gb3)
        self.list_dirs = QListWidget()
        row_dir = QHBoxLayout()
        self.btn_add_dir = QPushButton("添加目录")
        self.btn_del_dir = QPushButton("移除")
        self.chk_recursive = QCheckBox("包含子目录")
        self.btn_add_dir.clicked.connect(self._add_dir)
        self.btn_del_dir.clicked.connect(self._del_dir)
        row_dir.addWidget(self.btn_add_dir)
        row_dir.addWidget(self.btn_del_dir)
        row_dir.addWidget(self.chk_recursive)
        row_dir.addStretch(1)
        v3.addLayout(row_dir)
        v3.addWidget(self.list_dirs)
        lay.addWidget(gb3)

        # 自定义 URL
        gb4 = QGroupBox("自定义图片链接（每行一个）")
        gb4.setCheckable(True)
        self.gb_urls = gb4
        v4 = QVBoxLayout(gb4)
        self.edit_urls = QPlainTextEdit()
        self.edit_urls.setPlaceholderText(
            "https://example.com/a.jpg\nhttps://example.com/b.png\n"
            "也可填写 JSON 接口：json:https://example.com/api/wallpapers")
        v4.addWidget(self.edit_urls)
        lay.addWidget(gb4)
        lay.addStretch(1)
        return w

    # ---------- 高级 ----------
    def _build_advanced_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        g = QGroupBox("启动与托盘")
        f = QFormLayout(g)
        self.chk_autostart = QCheckBox("开机自动启动")
        self.chk_start_min = QCheckBox("开机启动时最小化到托盘")
        self.chk_close_tray = QCheckBox("关闭窗口时最小化到托盘（不退出）")
        self.chk_notify = QCheckBox("出错时显示桌面通知")
        self.chk_notify.setToolTip(
            "仅在切换失败或发生严重错误时弹出通知；换图成功、收藏等日常操作不会打扰你。")
        self.chk_systemd = QCheckBox("Linux：使用 systemd --user 而不是 XDG autostart")
        f.addRow(self.chk_autostart)
        f.addRow(self.chk_start_min)
        f.addRow(self.chk_close_tray)
        f.addRow(self.chk_notify)
        f.addRow(self.chk_systemd)
        lay.addWidget(g)

        g2 = QGroupBox("存储与网络")
        f2 = QFormLayout(g2)
        row_cache = QHBoxLayout()
        self.edit_cache = QLineEdit()
        self.btn_cache = QPushButton("选择…")
        self.btn_cache.clicked.connect(self._pick_cache)
        row_cache.addWidget(self.edit_cache, 1)
        row_cache.addWidget(self.btn_cache)
        w_cache = QWidget()
        w_cache.setLayout(row_cache)
        self.spin_cache_files = QSpinBox()
        self.spin_cache_files.setRange(10, 10000)
        self.spin_cache_files.setSuffix(" 个文件")
        self.edit_proxy = QLineEdit()
        self.edit_proxy.setPlaceholderText("如 http://127.0.0.1:7890 ，留空直连")
        self.combo_theme = QComboBox()
        self.combo_theme.addItems(["跟随系统", "浅色", "深色"])
        self.combo_log = QComboBox()
        self.combo_log.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        f2.addRow("缓存目录：", w_cache)
        f2.addRow("缓存上限：", self.spin_cache_files)
        f2.addRow("代理：", self.edit_proxy)
        f2.addRow("界面主题：", self.combo_theme)
        f2.addRow("日志级别：", self.combo_log)
        self.label_cache_use = QLabel("—")
        self.btn_clear_cache = QPushButton("清空缓存")
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        row_c2 = QHBoxLayout()
        row_c2.addWidget(self.label_cache_use, 1)
        row_c2.addWidget(self.btn_clear_cache)
        w_c2 = QWidget()
        w_c2.setLayout(row_c2)
        f2.addRow("缓存占用：", w_c2)
        lay.addWidget(g2)

        g3 = QGroupBox("维护")
        v3 = QHBoxLayout(g3)
        self.btn_clear_history = QPushButton("清空历史记录")
        self.btn_clear_history.clicked.connect(self._clear_history)
        self.btn_purge_bad = QPushButton("清理无效缓存")
        self.btn_purge_bad.setToolTip(
            "删除缓存目录里无法解码的文件（如站点返回的错误页）。\n"
            "若预览显示「无法解码」，点这里可一次性清干净。")
        self.btn_purge_bad.clicked.connect(self._purge_bad_cache)
        self.btn_to_jpg = QPushButton("缓存转 JPG")
        self.btn_to_jpg.setToolTip(
            "把缓存里残留的非 JPEG 图片（webp/png 等）统一转码为 JPEG。\n"
            "网络图片下载时已自动转码，这个按钮用于清理历史遗留文件；\n"
            "启动时会后台自动跑一次。")
        self.btn_to_jpg.clicked.connect(self._migrate_cache)
        self.btn_reset = QPushButton("恢复默认设置")
        self.btn_reset.clicked.connect(self._reset_settings)
        v3.addWidget(self.btn_clear_history)
        v3.addWidget(self.btn_purge_bad)
        v3.addWidget(self.btn_to_jpg)
        v3.addWidget(self.btn_reset)
        v3.addStretch(1)
        lay.addWidget(g3)
        lay.addStretch(1)
        return w

    # ---------- 关于 ----------
    def _build_about_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        text = QLabel(
            "<h2>Wallpaper Changer</h2>"
            "<p>跨平台自动更换桌面壁纸工具，基于 Python + Qt（PySide6）。</p>"
            "<ul>"
            "<li>来源：Bing 每日壁纸、本地文件夹、自定义链接（直链 / JSON 接口）</li>"
            "<li>支持收藏、历史回溯、托盘常驻、定时轮换</li>"
            "<li>Linux：GNOME/KDE/XFCE/MATE/Cinnamon/Deepin/sway，Wayland 与 X11 均可</li>"
            "<li>Windows：SystemParametersInfoW</li>"
            "</ul>"
            "<p>图片版权归各自作者所有，Bing 图片仅供个人欣赏，请勿用于商业用途。</p>"
        )
        text.setWordWrap(True)
        text.setTextFormat(Qt.RichText)
        lay.addWidget(text)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        lay.addWidget(QLabel("运行日志："))
        lay.addWidget(self.log_view, 1)
        return w

    # ------------------------------------------------------------------ #
    # 设置读写
    # ------------------------------------------------------------------ #
    @staticmethod
    def _set_int(widget, value, fallback: int = 0) -> None:
        """安全写入数值控件：配置里即使存成了字符串也不会崩。"""
        try:
            widget.setValue(int(value))
        except (TypeError, ValueError):
            widget.setValue(fallback)

    @staticmethod
    def _set_text(widget, value, fallback: str = "") -> None:
        widget.setText(str(value) if value is not None else fallback)

    @staticmethod
    def _set_checked(widget, value, fallback: bool = False) -> None:
        widget.setChecked(bool(value) if isinstance(value, (bool, int, str)) else fallback)

    def _set_combo(self, combo, value, fallback_index: int = 0) -> None:
        text = str(value) if value is not None else ""
        idx = combo.findText(text)
        combo.setCurrentIndex(idx if idx >= 0 else fallback_index)

    def _load_settings(self) -> None:
        d = self.config.data
        r = d.rotation
        self._set_checked(self.chk_enabled, r.enabled, True)
        try:
            minutes = int(r.interval_minutes)
        except (TypeError, ValueError):
            minutes = 30
        if minutes >= 60 and minutes % 60 == 0:
            self._set_combo(self.combo_unit, "小时")
            self._set_int(self.spin_interval, minutes / 60, 1)
        else:
            self._set_combo(self.combo_unit, "分钟")
            self._set_int(self.spin_interval, minutes, 30)
        self._set_checked(self.chk_start_change, r.on_startup_change, True)
        self._set_checked(self.chk_random, r.random_order, True)
        self._set_int(self.spin_avoid, r.avoid_repeat_days, 7)
        self._set_combo(self.combo_fit, r.fit_mode)
        self._set_checked(self.chk_dark, r.set_dark_variant, True)
        self._set_int(self.spin_minw, r.min_width, 1280)
        self._set_int(self.spin_minh, r.min_height, 720)
        self._set_int(self.spin_timeout, r.timeout, 20)

        self._set_checked(self.gb_bing, d.bing.enabled, True)
        self._set_combo(self.combo_market, d.bing.market)
        self._set_int(self.spin_bing_count, d.bing.count, 8)
        self._set_combo(self.combo_bing_res, d.bing.resolution)

        s360 = getattr(d, "qh360", None)
        if s360 is not None:
            self._set_checked(self.gb_360, s360.enabled, False)
            mode_idx = self.combo_360_mode.findData(s360.mode)
            self.combo_360_mode.setCurrentIndex(max(0, mode_idx))
            self._set_int(self.spin_360_count, s360.page_size, 24)
            self._set_int(self.spin_360_pages, s360.max_pages, 1)
            self._360_fill_categories(set(s360.categories or []))
            self._360_mode_changed()

        bz = getattr(d, "bzee", None)
        if bz is not None:
            self._set_checked(self.gb_bzee, bz.enabled, False)
            idx = self.combo_bzee_lib.findData(bz.library or "desktop")
            # 切图库会重建列表，先阻塞信号避免重复触发
            self.combo_bzee_lib.blockSignals(True)
            self.combo_bzee_lib.setCurrentIndex(max(0, idx))
            self.combo_bzee_lib.blockSignals(False)
            self._bzee_lib_changed()
            wanted = set(bz.categories or [])
            for i in range(self.list_bzee_cat.count()):
                it = self.list_bzee_cat.item(i)
                it.setSelected(it.data(Qt.UserRole) in wanted)
            self._set_text(self.edit_bzee_kw, ",".join(bz.keywords or []))
            self._set_int(self.spin_bzee_pages, bz.max_pages, 1)
            sidx = self.combo_bzee_size.findData(bz.image_size or "origin")
            self.combo_bzee_size.setCurrentIndex(max(0, sidx))

        self._set_checked(self.gb_local, d.local.enabled, False)
        self.list_dirs.clear()
        self.list_dirs.addItems([str(x) for x in (d.local.directories or [])])
        self._set_checked(self.chk_recursive, d.local.recursive, True)
        self._set_checked(self.gb_urls, d.custom_urls.enabled, False)
        self.edit_urls.setPlainText("\n".join(str(u) for u in (d.custom_urls.urls or [])))

        self._set_checked(self.chk_autostart, d.autostart, False)
        self._set_checked(self.chk_start_min, d.start_minimized, True)
        self._set_checked(self.chk_close_tray, d.close_to_tray, True)
        self._set_checked(self.chk_notify, d.show_notifications, True)
        self._set_text(self.edit_cache, d.cache_dir)
        self._set_int(self.spin_cache_files, d.max_cache_files, 300)
        self._set_text(self.edit_proxy, d.proxy)
        self._set_combo(self.combo_log, d.log_level, 1)      # 默认 INFO
        self._set_combo(self.combo_theme, d.theme)
        self._update_cache_label()

    def _collect_settings(self) -> None:
        d = self.config.data
        r = d.rotation
        r.enabled = self.chk_enabled.isChecked()
        value = self.spin_interval.value()
        r.interval_minutes = int(value * 60) if self.combo_unit.currentText() == "小时" else int(value)
        r.interval_minutes = max(1, r.interval_minutes)
        r.on_startup_change = self.chk_start_change.isChecked()
        r.random_order = self.chk_random.isChecked()
        r.avoid_repeat_days = self.spin_avoid.value()
        r.fit_mode = self.combo_fit.currentText()
        r.set_dark_variant = self.chk_dark.isChecked()
        r.min_width = self.spin_minw.value()
        r.min_height = self.spin_minh.value()
        r.timeout = self.spin_timeout.value()

        d.bing.enabled = self.gb_bing.isChecked()
        d.bing.market = self.combo_market.currentText().strip() or "zh-CN"
        d.bing.count = self.spin_bing_count.value()
        d.bing.resolution = self.combo_bing_res.currentText()

        s360 = getattr(d, "qh360", None)
        if s360 is not None:
            s360.enabled = self.gb_360.isChecked()
            s360.mode = self.combo_360_mode.currentData() or "category"
            s360.page_size = self.spin_360_count.value()
            s360.max_pages = self.spin_360_pages.value()
            s360.categories = self._360_selected_categories()

        bz = getattr(d, "bzee", None)
        if bz is not None:
            bz.enabled = self.gb_bzee.isChecked()
            bz.library = self.combo_bzee_lib.currentData() or "desktop"
            bz.categories = self._bzee_slugs()
            bz.keywords = [k.strip() for k in self.edit_bzee_kw.text().split(",")
                           if k.strip()]
            bz.max_pages = self.spin_bzee_pages.value()
            bz.image_size = self.combo_bzee_size.currentData() or "origin"

        d.local.enabled = self.gb_local.isChecked()
        d.local.directories = [
            self.list_dirs.item(i).text() for i in range(self.list_dirs.count())
        ]
        d.local.recursive = self.chk_recursive.isChecked()
        d.custom_urls.enabled = self.gb_urls.isChecked()
        d.custom_urls.urls = [
            u.strip() for u in self.edit_urls.toPlainText().splitlines() if u.strip()
        ]

        d.start_minimized = self.chk_start_min.isChecked()
        d.close_to_tray = self.chk_close_tray.isChecked()
        d.show_notifications = self.chk_notify.isChecked()
        d.cache_dir = self.edit_cache.text().strip()
        d.max_cache_files = self.spin_cache_files.value()
        d.proxy = self.edit_proxy.text().strip()
        d.log_level = self.combo_log.currentText()
        d.theme = self.combo_theme.currentText()

    def _save_settings(self) -> None:
        self._collect_settings()
        try:
            self.config.save()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        # 开机启动
        from .. import autostart

        want = self.chk_autostart.isChecked()
        use_systemd = self.chk_systemd.isChecked()
        if want:
            ok = autostart.enable(minimized=self.chk_start_min.isChecked(),
                                  use_systemd=use_systemd)
            if not ok:
                QMessageBox.warning(self, "提示", "写入开机启动项失败，请检查权限。")
        else:
            autostart.disable()
        self.config.data.autostart = autostart.is_enabled()
        self.chk_autostart.setChecked(self.config.data.autostart)
        self.config.save()

        self.engine.reload()
        self.ctx.apply_theme(self.combo_theme.currentText())
        self.label_status.setText("设置已保存并应用")
        self.settingsChanged.emit()
        self.refresh_views()

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择图片目录")
        if d:
            self.list_dirs.addItem(d)

    def _del_dir(self) -> None:
        for item in self.list_dirs.selectedItems():
            self.list_dirs.takeItem(self.list_dirs.row(item))

    def _pick_cache(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择缓存目录")
        if d:
            self.edit_cache.setText(str(Path(d) / "cache"))

    def _clear_cache(self) -> None:
        removed = self.engine.fetcher.clear()
        self._update_cache_label()
        QMessageBox.information(self, "完成",
            f"已清理 {removed} 个缓存文件。当前壁纸和正在下载的文件已保留。")
        self.refresh_views()

    def _update_cache_label(self) -> None:
        try:
            size = self.engine.fetcher.cache_size() / (1024 * 1024)
            self.label_cache_use.setText(f"{size:.1f} MB")
        except Exception:  # noqa: BLE001
            self.label_cache_use.setText("—")

    def _clear_history(self) -> None:
        if QMessageBox.question(self, "确认", "清空所有历史记录？收藏会保留。") == QMessageBox.Yes:
            self.db.clear_history()
            self.refresh_views()

    def _reset_settings(self) -> None:
        if QMessageBox.question(self, "确认", "恢复默认设置（不会删除收藏）？") == QMessageBox.Yes:
            self.config.data = type(self.config.data)()
            self.config.data.cache_dir = str(self.config.cache_dir)
            self._load_settings()
            self.engine.reload()

    def _delete_record(self, rec: ImageRecord) -> None:
        if QMessageBox.question(self, "确认", f"从库中删除「{rec.display_title}」？") != QMessageBox.Yes:
            return
        self.db.delete_image(rec.id)  # type: ignore[arg-type]
        self.refresh_views()

    def _toggle_fav(self) -> None:
        if not self.engine.current:
            return
        self.engine.toggle_favorite(self.engine.current)
        self.refresh_views()

    def hide_to_tray(self) -> None:
        # 静默隐藏到托盘：不再弹「已最小化」通知
        self.hide()

    def refresh_views(self) -> None:
        self.gallery.set_records(self._records_for(self.gallery))

    def _records_for(self, view: GalleryView) -> List[ImageRecord]:
        """按图库当前筛选返回记录：收藏 / 历史 / 全部。"""
        mode = view.combo_mode.currentText()
        if mode == "收藏":
            return self.db.list_favorites()
        if mode == "全部":
            return self.db.list_images(limit=500)
        recs: List[ImageRecord] = []
        seen = set()
        for row in self.db.list_history(limit=300):
            image_id = row["image_id"]
            if image_id and image_id not in seen:
                rec = self.db.get(image_id)
                if rec:
                    recs.append(rec)
                    seen.add(rec.id)
        return recs

    def _on_tab_changed(self, index: int) -> None:
        if self.tabs.tabText(index) == "图库":
            self.refresh_views()
        if self.tabs.tabText(index) == "高级":
            self._update_cache_label()

    # ---------- 引擎状态 ----------
    def _connect_engine(self) -> None:
        self.engine.changed.connect(self._on_changed)
        self.engine.status.connect(self._on_status)
        self.engine.failed.connect(self._on_failed)
        self.engine.pool_updated.connect(
            lambda n: self.label_pool.setText(f"{n} 张可用"))
        self.refresh_views()

    def _on_changed(self, rec: ImageRecord) -> None:
        self.label_title.setText(rec.display_title)
        meta = [f"来源：{rec.source}"]
        if rec.author:
            meta.append(f"作者：{rec.author}")
        if rec.width and rec.height:
            meta.append(f"{rec.width}×{rec.height}")
        if rec.license:
            meta.append(rec.license)
        if rec.is_favorite:
            meta.append("★ 已收藏")
        self.label_meta.setText(" · ".join(meta))
        self.btn_fav.setText("★ 取消收藏" if rec.is_favorite else "★ 收藏")
        self._update_preview(rec)
        # 换图成功不再弹桌面通知（已按需求收敛为：仅在失败/严重错误时通知）
        if self.tabs.tabText(self.tabs.currentIndex()) == "图库":
            self.refresh_views()

    def _update_preview(self, rec: ImageRecord) -> None:
        """更新首页预览图。

        关键点：**只要已经有当前壁纸，就不能再显示「尚未设置壁纸」**。
        缓存文件可能被清理（max_cache_files）或路径变化，此时要：
          1) 先按 remote_url 找回缓存文件；
          2) 缓存也没有就后台重新下载一张用于预览。
        """
        fetcher = getattr(self.engine, "fetcher", None)
        path = fetcher.resolve_local(rec) if fetcher else None
        if path:
            self._set_preview_pixmap(path)
            return
        # 本地没有：给出明确状态，并后台补载（无远程地址则只能占位）
        if not rec.remote_url:
            self.preview.setText("预览图已不在缓存中")
            return
        self.preview.setText("正在载入预览…")
        self._start_preview_loader(rec)

    def _set_preview_pixmap(self, path: str) -> None:
        self._preview_path = path
        dpr = max(1.0, self.devicePixelRatioF())
        size = QSize(max(1, int(self.preview.width() * dpr)),
                     max(1, int(self.preview.height() * dpr)))
        pix = load_pixmap(path, size)
        if pix is None or pix.isNull():
            self.preview.setText("预览图无法解码")
            return
        self.preview.setText("")
        self.preview.setPixmap(pix.scaled(self.preview.size(),
            Qt.KeepAspectRatio, Qt.SmoothTransformation))

    @staticmethod
    def _decode_with_pil(path: str):
        """用 Pillow 解码图片并转成 QPixmap；失败返回 None。"""
        try:
            from PIL import Image
            from PIL.ImageQt import ImageQt
        except ImportError:  # pragma: no cover - 依赖缺失
            return None
        try:
            with Image.open(path) as im:
                im = im.convert("RGBA")
                qim = ImageQt(im)
                from PySide6.QtGui import QPixmap

                return QPixmap.fromImage(qim)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Pillow 解码预览失败 %s: %s", path, exc)
            return None

    def _start_preview_loader(self, rec: ImageRecord) -> None:
        """后台下载预览图，避免界面卡住。"""
        prev = getattr(self, "_preview_loader", None)
        if thread_is_running(prev):
            self._preview_pending = rec
            return          # 旧任务完成后加载最新一张
        fetcher = getattr(self.engine, "fetcher", None)
        if fetcher is None:
            return
        loader = _PreviewLoader(fetcher, rec)
        loader.done.connect(lambda path, r=rec: self._on_preview_loaded(path, r))
        loader.finished.connect(lambda t=loader: self._preview_finished(t))
        loader.finished.connect(loader.deleteLater)
        self._preview_loader = loader
        loader.start()

    def _on_preview_loaded(self, path: str, rec: ImageRecord | None = None) -> None:
        if self._closing or (rec is not None and self.engine.current is not None
                and (rec.source, rec.source_id) !=
                    (self.engine.current.source, self.engine.current.source_id)):
            return
        if path:
            self._set_preview_pixmap(path)
        else:
            self.preview.setText("预览图暂时无法加载")

    def _preview_finished(self, loader: QThread) -> None:
        if getattr(self, "_preview_loader", None) is loader:
            self._preview_loader = None
        pending, self._preview_pending = self._preview_pending, None
        if pending is not None and not self._closing:
            self._start_preview_loader(pending)

    def _purge_bad_cache(self) -> None:
        self._run_maintenance("purge")

    def _migrate_cache(self) -> None:
        self._run_maintenance("migrate")

    def _run_maintenance(self, mode: str) -> None:
        existing = getattr(self, "_maintenance_task", None)
        if thread_is_running(existing):
            self.label_status.setText("已有缓存维护任务在运行")
            return
        startup_migration = getattr(self.ctx, "_migrate_task", None)
        if thread_is_running(startup_migration):
            self.label_status.setText("启动缓存迁移尚未结束，请稍后重试")
            return
        self.btn_purge_bad.setEnabled(False)
        self.btn_to_jpg.setEnabled(False)
        self.label_status.setText("正在后台迁移缓存…" if mode == "migrate"
                                  else "正在后台检查缓存…")
        task = _CacheMaintenanceTask(self.engine.fetcher, mode, self)
        self._maintenance_task = task
        task.done.connect(self._on_maintenance_done)
        task.finished.connect(lambda t=task: self._maintenance_finished(t))
        task.finished.connect(task.deleteLater)
        task.start()

    def _on_maintenance_done(self, mode: str, count: int,
                              removed: int, error: str) -> None:
        if self._closing:
            return
        if error:
            msg = f"缓存维护失败：{error}"
        elif mode == "migrate":
            msg = f"缓存转码完成：{count} 张已转为 JPEG，清理 {removed} 个无效文件"
        else:
            msg = f"缓存检查完成：清理 {removed} 个无效文件（共扫描 {count} 个）"
        self.label_status.setText(msg)
        self.append_log(msg)
        self._update_cache_label()
        self.refresh_views()

    def _maintenance_finished(self, task: QThread) -> None:
        if getattr(self, "_maintenance_task", None) is task:
            self._maintenance_task = None
        if not self._closing:
            self.btn_purge_bad.setEnabled(True)
            self.btn_to_jpg.setEnabled(True)

    def _clear_preview(self) -> None:
        """确无当前壁纸时才回到初始文案。"""
        self.preview.setPixmap(QPixmap())
        self.preview.setText("尚未设置壁纸")
        self.label_title.setText("—")
        self.label_meta.setText("—")

    def _on_status(self, text: str) -> None:
        self.label_status.setText(text)
        self.append_log(text)

    def _on_failed(self, text: str) -> None:
        self.label_status.setText(f"⚠ {text}")
        self.append_log(f"[错误] {text}")
        # 仅在失败 / 严重错误时弹桌面通知（成功换图、收藏等一律不打扰）
        if self.config.data.show_notifications:
            tray = getattr(self.ctx, "tray", None)
            if tray is not None:
                tray.error(text)

    def append_log(self, text: str) -> None:
        import time as _t

        self.log_view.appendPlainText(f"[{_t.strftime('%H:%M:%S')}] {text}")

    def _on_enabled_toggled(self, checked: bool) -> None:
        """「启用自动轮换」勾选状态变化时立即启停倒计时。"""
        self.config.data.rotation.enabled = bool(checked)
        self.engine.apply_timer()
        self._update_countdown()

    # ---------- Bzee ----------
    def _360_selected_categories(self) -> List[str]:
        return [str(self.list_360_cat.item(i).data(Qt.UserRole))
                for i in range(self.list_360_cat.count())
                if self.list_360_cat.item(i).isSelected()]

    def _360_fill_categories(self, selected=None) -> None:
        if selected is None:
            selected = set(self._360_selected_categories())
        selected = {str(x) for x in selected}
        self.list_360_cat.clear()
        # 旧配置里有新分类 ID 时仍可显示，不会因为网络不可用丢设置。
        mapping = dict(QH360_CATEGORIES)
        mapping.update({cid: f"分类 {cid}" for cid in selected if cid not in mapping})
        for cid, name in mapping.items():
            item = QListWidgetItem(f"{name}（{cid}）")
            item.setData(Qt.UserRole, cid)
            item.setSelected(cid in selected)
            self.list_360_cat.addItem(item)

    def _360_mode_changed(self, *_args) -> None:
        self.list_360_cat.setEnabled(self.combo_360_mode.currentData() == "category")

    def _360_discover(self) -> None:
        if self._closing or getattr(self, "_discover_360_task", None) is not None:
            return
        self.btn_360_refresh.setEnabled(False)
        self.label_360_status.setText("正在更新分类…")
        task = _Discover360Task(self.config.data.qh360, self.config, self)
        self._discover_360_task = task
        task.done.connect(self._360_discover_done)
        task.finished.connect(lambda t=task: self._360_discover_finished(t))
        task.finished.connect(task.deleteLater)
        task.start()

    def _360_discover_finished(self, task: QThread) -> None:
        if getattr(self, "_discover_360_task", None) is task:
            self._discover_360_task = None
        if not self._closing:
            self.btn_360_refresh.setEnabled(True)

    def _360_discover_done(self, found: dict, error: str) -> None:
        if self._closing:
            return
        if not found:
            self.label_360_status.setText(
                f"分类请求失败：{error[:110]}；可继续使用内置分类")
            return
        selected = set(self._360_selected_categories())
        QH360_CATEGORIES.update(found)
        self._360_fill_categories(selected)
        self.label_360_status.setText(f"在线更新成功：{len(found)} 个分类")

    def _bzee_lib_changed(self) -> None:
        """切换图库时重建分类列表（保留仍存在的勾选）。"""
        lib = self.combo_bzee_lib.currentData() or "desktop"
        names = DESKTOP_CATEGORIES if lib == "desktop" else MOBILE_CATEGORIES
        keep = {self.list_bzee_cat.item(i).data(Qt.UserRole)
                for i in range(self.list_bzee_cat.count())
                if self.list_bzee_cat.item(i).isSelected()}
        self.list_bzee_cat.clear()
        for slug, name in names.items():
            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, slug)
            item.setSelected(slug in keep)
            self.list_bzee_cat.addItem(item)

    def _bzee_select(self, select: bool) -> None:
        for i in range(self.list_bzee_cat.count()):
            self.list_bzee_cat.item(i).setSelected(select)

    def _bzee_discover(self) -> None:
        """分类发现异步运行，WAF/超时不会冻结整个窗口。"""
        if getattr(self, "_discover_task", None) is not None:
            return
        self.btn_bzee_disc.setEnabled(False)
        self.label_bzee_disc.setText("正在抓取 photo.bzee.cn …")
        task = _DiscoverTask(self.config.data.bzee, self.config, self)
        self._discover_task = task
        task.done.connect(self._bzee_discover_done)
        task.finished.connect(lambda t=task: self._discover_finished(t))
        task.finished.connect(task.deleteLater)
        task.start()

    def _discover_finished(self, task: QThread) -> None:
        if getattr(self, "_discover_task", None) is task:
            self._discover_task = None
        if not self._closing:
            self.btn_bzee_disc.setEnabled(True)

    def _bzee_discover_done(self, found: dict) -> None:
        if self._closing:
            return
        got = {k: v for k, v in (found or {}).items() if v}
        if not got:
            self.label_bzee_disc.setText(
                "未取到在线分类（站点可能拒绝了该请求），继续使用内置列表")
            return
        for lib, mapping in got.items():
            table = DESKTOP_CATEGORIES if lib == "desktop" else MOBILE_CATEGORIES
            table.clear()
            table.update(mapping)
        self._bzee_lib_changed()
        total = sum(len(v) for v in got.values())
        self.label_bzee_disc.setText(f"已更新：共 {total} 个分类（在线）")

    def _bzee_slugs(self) -> List[str]:
        return [self.list_bzee_cat.item(i).data(Qt.UserRole)
                for i in range(self.list_bzee_cat.count())
                if self.list_bzee_cat.item(i).isSelected()]

    def _toggle_pause(self, checked: bool) -> None:
        """暂停 / 继续倒计时（运行时，不落盘）。"""
        self.engine.pause(checked)
        self.btn_pause.setText("继续" if checked else "暂停")
        self._update_countdown()

    def _update_countdown(self) -> None:
        secs = self.engine.seconds_to_next()
        if secs < 0:
            # 区分两种「不走」的原因，避免误以为程序坏了
            if self.engine.is_paused():
                self.label_next.setText("已暂停（点「继续」恢复）")
            elif not self.engine.rotation_enabled():
                self.label_next.setText("未启用自动轮换")
            else:
                self.label_next.setText("—")
        else:
            m, s = divmod(secs, 60)
            h, m = divmod(m, 60)
            self.label_next.setText(
                f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d} 分 {s:02d} 秒")

    # ---------- 关闭 ----------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._closing:
            event.accept()
            return
        if self.config.data.close_to_tray and hasattr(self.ctx, "tray") and self.ctx.tray \
                and self.ctx.tray.isVisible():
            event.ignore()
            self.hide_to_tray()
            return
        event.accept()

    def shutdown(self) -> None:
        """请求取消并收拢界面线程，防止退出时 QThread 仍在运行。"""
        self._closing = True
        self._tick.stop()
        loader = getattr(self, "_preview_loader", None)
        stop_thread(loader)
        maintenance = getattr(self, "_maintenance_task", None)
        stop_thread(maintenance)
        discover = getattr(self, "_discover_task", None)
        stop_thread(discover)
        discover360 = getattr(self, "_discover_360_task", None)
        stop_thread(discover360)
        view = getattr(self, "gallery", None)
        if view is not None:
            view.shutdown()

    def really_quit(self) -> None:
        self._closing = True
        self.shutdown()
        self.close()
