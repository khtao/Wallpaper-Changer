"""系统托盘图标与菜单。"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QMenu, QSystemTrayIcon

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
        # 暂停是运行时状态：每次启动都回到「自动切换」，
        # 不再读取/改写配置里的 rotation.enabled
        self.act_pause.setChecked(False)
        self.act_pause.triggered.connect(self._toggle_pause)
        self.act_fav_only = menu.addAction("仅轮换收藏")
        self.act_fav_only.setCheckable(True)
        self.act_fav_only.triggered.connect(self._toggle_fav_only)
        menu.addSeparator()

        self.act_quit = menu.addAction("退出")
        self.act_quit.triggered.connect(self.ctx.quit)
        self.setContextMenu(menu)

    def _toggle_pause(self, checked: bool) -> None:
        """暂停 / 继续倒计时。

        只改运行时状态，不落盘：否则「暂停过一次」会被写成配置里的
        永久关闭，导致之后每次开机都停在暂停状态。
        要长期关闭请用界面上的「启用自动轮换」。
        """
        self.ctx.engine.pause(checked)
        # 静默处理：菜单项本身的勾选状态就是反馈，不再弹桌面通知

    def _toggle_fav_only(self, checked: bool) -> None:
        self.ctx.engine.set_favorite_only(checked)

    # ---------- 通知 ----------
    # 通知策略：仅在「切换失败 / 严重错误」时打扰用户。
    # 换图成功、收藏、暂停等日常操作一律静默（状态栏与日志可见）。
    def error(self, text: str, timeout: int = 6000) -> None:
        """严重错误 / 切换失败通知。"""
        body = (text[:180] + "…") if len(text) > 180 else text
        self.showMessage("Wallpaper Changer · 出错了", body,
                         QSystemTrayIcon.MessageIcon.Critical, timeout)

    def info(self, body: str, timeout: int = 2500, title: str = "Wallpaper Changer") -> None:
        """保留给「必须让用户知道」的场景（如依赖缺失）。

        注意：PySide6 里消息图标枚举必须按类访问
        （``QSystemTrayIcon.MessageIcon.Information``），
        不能写成 ``实例.Information``，否则会抛 AttributeError。
        """
        self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, timeout)

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
