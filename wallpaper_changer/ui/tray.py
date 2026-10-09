"""系统托盘图标与菜单。"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..models import ImageRecord
from .icons import app_icon

LOG = logging.getLogger(__name__)


class TrayIcon(QSystemTrayIcon):
    """托盘常驻，提供免开窗口的常用操作。"""

    def __init__(self, ctx, parent=None):
        icon = app_icon()
        super().__init__(icon, parent)
        self.ctx = ctx
        self.setToolTip("Wallpaper Changer")
        self.activated.connect(self._on_activated)
        self._build_menu()
        self.show()

    # ---------- 菜单 ----------
    def _build_menu(self) -> None:
        menu = QMenu()
        self.act_show = menu.addAction("打开主界面")
        self.act_show.triggered.connect(self.show_window)
        menu.addSeparator()

        self.act_next = menu.addAction("下一张壁纸")
        self.act_next.triggered.connect(lambda: self.ctx.engine.change_next("托盘"))
        self.act_prev = menu.addAction("上一张壁纸")
        self.act_prev.triggered.connect(self.ctx.engine.change_prev)
        self.act_fav = menu.addAction("★ 收藏当前")
        self.act_fav.triggered.connect(self.ctx.toggle_favorite)
        self.act_refresh = menu.addAction("从来源抓取新图")
        self.act_refresh.triggered.connect(lambda: self.ctx.engine.refresh_pool(force=True))
        menu.addSeparator()

        self.act_pause = menu.addAction("暂停自动轮换")
        self.act_pause.setCheckable(True)
        self.act_pause.setChecked(not self.ctx.config.data.rotation.enabled)
        self.act_pause.triggered.connect(self._toggle_pause)
        self.act_fav_only = menu.addAction("仅轮换收藏")
        self.act_fav_only.setCheckable(True)
        self.act_fav_only.triggered.connect(self._toggle_fav_only)
        menu.addSeparator()

        self.act_quit = menu.addAction("退出")
        self.act_quit.triggered.connect(self.ctx.quit)
        self.setContextMenu(menu)

    def _toggle_pause(self, checked: bool) -> None:
        self.ctx.engine.pause(checked)
        self.ctx.config.data.rotation.enabled = not checked
        self.ctx.config.save()
        if not checked:
            self.ctx.engine.apply_timer()

    def _toggle_fav_only(self, checked: bool) -> None:
        self.ctx.engine.set_favorite_only(checked)
        self.info("已切换为仅轮换收藏" if checked else "恢复全部图片轮换", 2000)

    # ---------- 通知 ----------
    def info(self, body: str, timeout: int = 2500, title: str = "Wallpaper Changer") -> None:
        """统一的普通通知。

        注意：PySide6 里消息图标枚举必须按类访问
        （``QSystemTrayIcon.MessageIcon.Information``），
        不能写成 ``实例.Information``，否则会抛 AttributeError。
        """
        self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, timeout)

    def warn(self, body: str, timeout: int = 3000, title: str = "Wallpaper Changer") -> None:
        self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Warning, timeout)

    # ---------- 行为 ----------
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger):
            self.show_window()

    def show_window(self) -> None:
        win = self.ctx.window
        if win:
            win.show()
            win.raise_()
            win.activateWindow()

    def notify(self, rec: ImageRecord) -> None:
        title = rec.display_title[:60] or "新壁纸"
        body = f"{rec.source}" + (f" · {rec.author}" if rec.author else "")
        if rec.local_path and Path(rec.local_path).exists():
            pix = QPixmap(rec.local_path).scaledToWidth(320)
            self.showMessage("已更换壁纸", f"{title}\n{body}", QIcon(pix), 4000)
        else:
            self.showMessage("已更换壁纸", f"{title}\n{body}",
                             QSystemTrayIcon.MessageIcon.Information, 4000)
