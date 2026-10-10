"""应用核心：单实例、日志、主题、CLI。"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import QApplication, QStyleFactory

from . import autostart
from .config import APP_NAME, APP_TITLE, IS_WINDOWS, VERSION, Config, app_data_dir
from .database import Database
from .engine import Engine
from .ipc import InstanceLock, pick_free_port, process_alive, send_command
from .ui.icons import app_icon
from .ui.main_window import MainWindow
from .ui.tray import TrayIcon

LOG = logging.getLogger(__name__)

# Qt 已知的无害告警：XDG Desktop Portal 注册冲突（QTBUG-145847 一类）。
# 只是日志噪音，不影响托盘、通知与设置壁纸，默认只记到 DEBUG。
QT_NOISE = (
    "Failed to register with host portal",
    "Could not register app ID",
)


def install_qt_message_filter() -> None:
    """过滤 Qt 的已知无害告警，保持终端与日志干净。

    设置环境变量 WC_QT_VERBOSE=1 可关闭过滤，便于排查 Qt 层问题。
    """
    if os.environ.get("WC_QT_VERBOSE"):
        return
    try:
        from PySide6.QtCore import QtFatalMsg, qInstallMessageHandler
    except Exception:  # noqa: BLE001
        return

    def _handler(mode, context, message: str) -> None:
        text = message or ""
        if any(p in text for p in QT_NOISE):
            LOG.debug("忽略 Qt 已知无害告警：%s", text)
            return
        if os.environ.get("WC_QT_VERBOSE") or mode != QtFatalMsg:
            # 与 Qt 默认行为保持一致：非致命消息原样输出到 stderr
            print(text, file=sys.stderr)
        if mode == QtFatalMsg:
            sys.stderr.flush()
            raise SystemExit(1)

    try:
        qInstallMessageHandler(_handler)
    except Exception:  # noqa: BLE001
        pass


def desktop_entry_installed(name: str = APP_NAME) -> bool:
    """判断桌面项是否已安装到系统（决定能否设置有效的 app id）。"""
    if IS_WINDOWS:
        return False
    for d in (Path.home() / ".local" / "share" / "applications",
              Path("/usr/share/applications")):
        if (d / f"{name}.desktop").exists():
            return True
    return False


def setup_logging(level: str = "INFO") -> Path:
    log_dir = app_data_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{APP_NAME}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    return path


class Application(QObject):
    """组合配置、数据库、引擎、窗口与托盘。"""

    def __init__(self, argv: list[str] | None = None):
        super().__init__()
        self.argv = list(argv if argv is not None else sys.argv)
        self.args = self._parse_args(self.argv[1:])
        self.config = Config()
        self.config.data.log_level = self.args.log_level or self.config.data.log_level
        self.log_path = setup_logging(self.config.data.log_level)
        self.db = Database()
        self.engine = Engine(self.config, self.db)
        self.window: MainWindow | None = None
        self.tray: TrayIcon | None = None
        self._server: QTcpServer | None = None
        self.lock: InstanceLock | None = None

    # ---------- CLI ----------
    @staticmethod
    def _parse_args(args: list[str]) -> argparse.Namespace:
        p = argparse.ArgumentParser(
            prog=APP_NAME, description="跨平台自动更换壁纸")
        p.add_argument("--minimized", action="store_true", help="启动后只显示托盘")
        p.add_argument("--next", action="store_true", help="立即切换到下一张并退出")
        p.add_argument("--prev", action="store_true", help="切换到上一张并退出")
        p.add_argument("--refresh", action="store_true", help="仅刷新来源图池")
        p.add_argument("--quit", action="store_true", help="通知已运行的实例退出")
        p.add_argument("--show", action="store_true", help="显示已运行实例的界面")
        p.add_argument("--no-tray", action="store_true", help="不创建托盘图标")
        p.add_argument("--doctor", action="store_true",
                       help="环境自检：打印依赖、目录权限与桌面环境信息")
        p.add_argument("--log-level", dest="log_level", default=None,
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])
        return p.parse_args(args)

    # ---------- 单实例 ----------
    def try_single_instance(self) -> bool:
        """尝试成为唯一实例。

        必须在创建 QApplication **之前**调用：第二个进程会直接把指令转发给
        已运行的实例后退出，不会初始化 Qt，因此也不会去注册 XDG Desktop
        Portal 的 app id（可避免 "Failed to register with host portal" 告警）。
        """
        self.lock = InstanceLock()
        if self.lock.acquire():
            return True
        # 已有实例在运行：转发指令后退出
        pid, _port = InstanceLock.read_peer()
        if pid and not process_alive(pid):
            LOG.warning("锁文件指向已退出的进程 %s，重置后继续启动", pid)
            try:
                self.lock.path.unlink()
            except OSError:
                pass
            if self.lock.acquire():
                return True
        cmd = ("quit" if self.args.quit else
               "next" if self.args.next else
               "prev" if self.args.prev else
               "refresh" if self.args.refresh else "show")
        send_command(cmd)
        self.lock = None
        return False

    def start_command_server(self) -> None:
        """监听本地端口，接收后来实例的指令（show/quit/next/prev/refresh）。"""
        if self.lock is None or not self.lock.owner:
            return
        try:
            self._server = QTcpServer(self)
            port = pick_free_port()
            if not self._server.listen(QHostAddress.LocalHost, port):
                LOG.warning("指令端口监听失败：%s", self._server.errorString())
                self._server = None
                return
            self._server.newConnection.connect(self._on_new_connection)
            self.lock.publish(port)
            LOG.debug("指令服务已监听 127.0.0.1:%d", port)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("启动指令服务失败：%s", exc)
            self._server = None

    def _on_new_connection(self) -> None:
        if not self._server:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if not conn:
                return
            conn.readyRead.connect(lambda c=conn: self._handle_message(c))
            conn.disconnected.connect(lambda c=conn: c.deleteLater())

    def _handle_message(self, conn) -> None:
        try:
            text = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
        except Exception:  # noqa: BLE001
            return
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        if not text:
            return
        LOG.info("收到指令：%s", text)
        cmd = text.split()[0]
        if cmd == "quit":
            self.quit()
        elif cmd == "next":
            self.engine.change_next("外部指令")
        elif cmd == "prev":
            self.engine.change_prev()
        elif cmd == "refresh":
            self.engine.refresh_pool(force=True)
        else:               # show 及其它：呼出界面
            if self.window:
                self.window.show()
                self.window.raise_()
                self.window.activateWindow()

    # ---------- 启动 ----------
    def run(self, qt_app: QApplication) -> int:
        qt_app.setApplicationName(APP_TITLE)
        qt_app.setApplicationDisplayName(APP_TITLE)
        # 只有在系统里确实装了同名 .desktop 时才声明 app id，
        # 否则 portal 会因找不到 App info 而报 "Could not register app ID"。
        if desktop_entry_installed():
            qt_app.setDesktopFileName(APP_NAME)
        qt_app.setWindowIcon(app_icon())
        qt_app.setQuitOnLastWindowClosed(False)
        self.apply_theme(self.config.data.theme)

        if self.args.next or self.args.prev or self.args.refresh:
            return self._run_headless(qt_app)

        self.window = MainWindow(self)
        # 首页直接显示「当前壁纸」：从历史里恢复上一次成功设置的记录
        restored = self.engine.restore_current()
        if restored:
            LOG.info("已恢复当前壁纸显示：%s", restored.display_title)
        else:
            LOG.info("尚无历史记录，首页显示未设置壁纸")
        if not self.args.no_tray:
            self.tray = TrayIcon(self)
        if not self.args.minimized and not self.config.data.start_minimized:
            self.window.show()
        else:
            LOG.info("以最小化方式启动")

        # 关键：启动后显式按配置装好倒计时。
        # 暂停只是运行时状态（不落盘），因此每次启动都会回到配置里的
        # 真实状态 —— 默认是「自动切换」。
        rot = self.config.data.rotation
        self.engine.pause(False)
        self.engine.apply_timer()
        if self.engine.rotation_enabled():
            LOG.info("自动轮换已启动：每 %s 分钟切换一张（%s 秒后首次切换）",
                     rot.interval_minutes, self.engine.seconds_to_next())
        else:
            LOG.warning("自动轮换未启用（rot.enabled=%s, interval=%s）："
                        "倒计时不会启动，如需自动切换请在「轮换」页勾选「启用自动轮换」",
                        rot.enabled, rot.interval_minutes)

        if self.config.data.rotation.on_startup_change:
            self.engine.change_next("启动")
        self.engine.refresh_pool()
        LOG.info("启动完成，日志：%s", self.log_path)
        return qt_app.exec()

    def _run_headless(self, qt_app: QApplication) -> int:
        """命令行一次性操作：换图 / 刷新图池。"""
        from PySide6.QtCore import QTimer

        holder = {"done": False, "ok": True}

        def finish(ok: bool = True) -> None:
            holder["done"] = True
            holder["ok"] = ok
            try:
                self.engine.shutdown()
            except Exception:  # noqa: BLE001
                pass
            qt_app.quit()

        state = {"pool": 0, "error": ""}

        def note_error(msg: str) -> None:
            """单个来源失败不判定整体失败，等抓取结束再统一判断。"""
            LOG.error(msg)
            state["error"] = msg

        self.engine.failed.connect(note_error)
        self.engine.refresh_finished.connect(lambda n, err: state.__setitem__("pool", n))
        self.engine.changed.connect(lambda rec: finish(True))
        self.engine.status.connect(lambda m: print(f"  {m}"))

        if self.args.next:
            # 先抓候选池，抓完再换一张；抓不到候选直接判失败
            self.engine.refresh_finished.connect(
                lambda n, err: (self.engine.change_next("命令行") if n else finish(False)))
            QTimer.singleShot(100, lambda: self.engine.refresh_pool(force=True))
        elif self.args.prev:
            QTimer.singleShot(100, self.engine.change_prev)
        else:
            self.engine.refresh_finished.connect(lambda n, err: finish(n > 0))
            QTimer.singleShot(100, lambda: self.engine.refresh_pool(force=True))

        QTimer.singleShot(60000, lambda: finish(False))   # 超时保护
        qt_app.exec()
        print("完成" if holder["ok"] else f"失败：{state['error'] or '超时或无可用图片'}")
        # 一次性命令直接结束进程，避免 Qt 在解释器清理阶段释放资源产生告警
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(0 if holder["ok"] else 1)

    # ---------- 主题 ----------
    def apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is None:
            return
        style = QStyleFactory.create("Fusion")
        if style:
            app.setStyle(style)
        if theme == "深色":
            app.setPalette(self._dark_palette(app))
        elif theme == "浅色":
            app.setPalette(app.style().standardPalette())
        else:
            try:
                from PySide6.QtGui import QColorConstants  # noqa: F401
            except Exception:  # noqa: BLE001
                pass
            app.setPalette(app.style().standardPalette())
        self.config.data.theme = theme

    @staticmethod
    def _dark_palette(app: QApplication):
        from PySide6.QtGui import QColor, QPalette

        pal = QPalette()
        pal.setColor(QPalette.Window, QColor(0x2B, 0x2B, 0x2B))
        pal.setColor(QPalette.WindowText, QColor(0xE8, 0xE8, 0xE8))
        pal.setColor(QPalette.Base, QColor(0x1E, 0x1E, 0x1E))
        pal.setColor(QPalette.AlternateBase, QColor(0x2B, 0x2B, 0x2B))
        pal.setColor(QPalette.ToolTipBase, QColor(0x2B, 0x2B, 0x2B))
        pal.setColor(QPalette.ToolTipText, QColor(0xE8, 0xE8, 0xE8))
        pal.setColor(QPalette.Text, QColor(0xE8, 0xE8, 0xE8))
        pal.setColor(QPalette.Button, QColor(0x35, 0x35, 0x35))
        pal.setColor(QPalette.ButtonText, QColor(0xE8, 0xE8, 0xE8))
        pal.setColor(QPalette.BrightText, QColor(0xFF, 0x55, 0x55))
        pal.setColor(QPalette.Highlight, QColor(0x2A, 0x82, 0xDA))
        pal.setColor(QPalette.HighlightedText, QColor(0xFF, 0xFF, 0xFF))
        pal.setColor(QPalette.Link, QColor(0x8A, 0xBD, 0xFF))
        return pal

    # ---------- 动作 ----------
    def toggle_favorite(self) -> None:
        if self.engine.current is None:
            if self.window:
                self.window.label_status.setText("当前没有壁纸")
            return
        fav = self.engine.toggle_favorite(self.engine.current)
        if self.window:
            self.window.btn_fav.setText("★ 取消收藏" if fav else "★ 收藏")
            self.window.refresh_views()
        # 收藏成功不再弹桌面通知（界面按钮文字与状态栏已给出反馈）

    def install_shortcuts(self) -> None:
        """安装应用菜单项与图标（Linux）。"""
        try:
            autostart.install_desktop_entry()
            autostart.install_icon()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("安装菜单项失败：%s", exc)

    def quit(self) -> None:
        LOG.info("退出应用")
        if self.window is not None and hasattr(self.window, "shutdown"):
            try:
                self.window.shutdown()
            except Exception:  # noqa: BLE001
                pass
        if self.engine:
            self.engine.shutdown() if hasattr(self.engine, "shutdown") else None
        if self._server:
            self._server.close()
        if self.lock is not None:
            self.lock.release()
        app = QApplication.instance()
        if app:
            app.quit()


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    # Windows 下让任务栏图标正确分组
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "WallpaperChanger.PySide6")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    # 关键：先解析参数并抢占实例锁，再有选择地创建 QApplication。
    # 第二个进程在这里就转发完指令退出了，不会再初始化 Qt，
    # 因此不会触发 XDG Desktop Portal 的 app id 注册冲突告警。
    try:
        args = Application._parse_args(argv[1:])
    except SystemExit as exc:               # --help 等
        return int(exc.code or 0)

    if args.doctor:
        # 真正的自检在 doctor.py（不依赖 PySide6，缺依赖时也能跑）
        from .doctor import run_doctor

        return run_doctor()

    if args.log_level:
        logging.getLogger().setLevel(getattr(logging, args.log_level, logging.INFO))

    ctx = Application(argv)
    if not ctx.try_single_instance():
        return 0

    install_qt_message_filter()
    app = QApplication(argv)
    ctx.start_command_server()
    try:
        code = ctx.run(app)
    finally:
        if ctx.lock is not None:
            ctx.lock.release()
    return code
