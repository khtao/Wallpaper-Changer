"""主窗口：概览 / 来源 / 收藏 / 历史 / 高级设置。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget, QDoubleSpinBox,
)

from ..config import Config, IS_WINDOWS, fit_modes_for_platform
from ..database import Database
from ..models import ImageRecord
from ..wallpaper import detect_desktop, screen_info
from .gallery import GalleryView

LOG = logging.getLogger(__name__)


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
        self.resize(980, 720)
        self._build_ui()
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
        top = QHBoxLayout()
        self.preview = QLabel()
        self.preview.setFixedSize(300, 170)
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

        btns = QHBoxLayout()
        self.btn_prev = QPushButton("← 上一张")
        self.btn_next = QPushButton("下一张 →")
        self.btn_fav = QPushButton("★ 收藏")
        self.btn_refresh = QPushButton("↻ 抓取新图")
        for b in (self.btn_prev, self.btn_next, self.btn_fav, self.btn_refresh):
            btns.addWidget(b)
        self.btn_prev.clicked.connect(self.engine.change_prev)
        self.btn_next.clicked.connect(lambda: self.engine.change_next("手动"))
        self.btn_fav.clicked.connect(self._toggle_fav)
        self.btn_refresh.clicked.connect(lambda: self.engine.refresh_pool(force=True))
        info.addLayout(btns)
        top.addLayout(info, 1)
        root.addLayout(top, 1)

        # --- 标签页 ---
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_general_tab(), "轮换")
        self.tabs.addTab(self._build_sources_tab(), "图片来源")
        # 收藏与历史合并为同一个「图库」选项卡，用筛选下拉切换范围
        self.gallery = GalleryView(self.engine.fetcher)
        self.tabs.addTab(self.gallery, "图库")
        self.tabs.addTab(self._build_advanced_tab(), "高级")
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
        self.btn_reset = QPushButton("恢复默认设置")
        self.btn_reset.clicked.connect(self._reset_settings)
        v3.addWidget(self.btn_clear_history)
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
        self.engine.fetcher.clear()
        self._update_cache_label()
        QMessageBox.information(self, "完成", "缓存已清空")

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
        if rec.local_path and Path(rec.local_path).exists():
            pix = QPixmap(rec.local_path)
            if not pix.isNull():
                self.preview.setPixmap(pix.scaled(
                    self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        # 换图成功不再弹桌面通知（已按需求收敛为：仅在失败/严重错误时通知）
        if self.tabs.tabText(self.tabs.currentIndex()) == "图库":
            self.refresh_views()

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
        """退出前停掉界面侧的后台线程。"""
        view = getattr(self, "gallery", None)
        if view is not None:
            view.shutdown()

    def really_quit(self) -> None:
        self._closing = True
        self.shutdown()
        self.close()
