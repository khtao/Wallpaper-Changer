"""单实例锁 + 跨进程指令通道。

设计要点：
- **在 QApplication 创建之前**就能判断是否已有实例运行。这样第二个进程根本不会初始化 Qt，
  也就不会去注册 XDG Desktop Portal 的 app id（可避免
  ``qt.qpa.services: Failed to register with host portal`` 这类告警）。
- 锁用内核级文件锁（Linux ``flock`` / Windows ``msvcrt.locking``），进程退出即自动释放，
  不会残留陈旧锁文件。
- 指令通道使用 127.0.0.1 上的 TCP 端口：持有锁的实例用 Qt 的 QTcpServer 监听，
  后来的实例用标准库 socket 发一条短指令后退出（无需 Qt）。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from typing import Optional

from .config import IS_WINDOWS, app_data_dir

LOG = logging.getLogger(__name__)

LOCK_NAME = "instance.lock"
READ_TIMEOUT = 0.35


def lock_path() -> Path:
    return app_data_dir() / LOCK_NAME


# --------------------------------------------------------------------------- #
# 文件锁
# --------------------------------------------------------------------------- #
def _try_lock(fd: int) -> bool:
    """尝试对文件描述符加独占锁，成功返回 True。"""
    try:
        if IS_WINDOWS:
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    except ImportError:  # pragma: no cover - 极端环境
        return True      # 没有加锁能力时退化为“视作拿到锁”


class InstanceLock:
    """进程级独占锁，同时记录 PID 与指令端口。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else lock_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd: Optional[int] = None
        self.owner = False

    def acquire(self) -> bool:
        """尝试成为唯一实例；成功返回 True，并把 pid/port 写入锁文件。"""
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            LOG.warning("无法创建锁文件 %s：%s", self.path, exc)
            return True                      # 退化为允许启动，不能因为锁而打不开程序
        if not _try_lock(fd):
            os.close(fd)
            self.owner = False
            return False
        self.fd = fd
        self.owner = True
        return True

    def publish(self, port: int) -> None:
        """把监听端口写给后来的实例（写入前先清空旧内容）。"""
        if self.fd is None:
            return
        try:
            os.ftruncate(self.fd, 0)
            os.lseek(self.fd, 0, os.SEEK_SET)
            payload = json.dumps({"pid": os.getpid(), "port": port}).encode("utf-8")
            os.write(self.fd, payload)
            os.fsync(self.fd)
        except OSError as exc:
            LOG.warning("写入锁文件失败：%s", exc)

    @staticmethod
    def read_peer(path: Optional[Path] = None) -> tuple[int, int]:
        """读取当前持有锁的实例信息，返回 (pid, port)，读不到返回 (0, 0)。"""
        p = Path(path) if path else lock_path()
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}")
            return int(data.get("pid", 0)), int(data.get("port", 0))
        except Exception:  # noqa: BLE001 - 文件还没写完或内容异常
            return 0, 0

    def release(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        self.owner = False


# --------------------------------------------------------------------------- #
# 指令通道
# --------------------------------------------------------------------------- #
def send_command(command: str, path: Optional[Path] = None,
                 wait: float = 1.5) -> bool:
    """给已运行的实例发一条指令（show / quit / next / prev）。

    后来进程专用：只用标准库，不初始化 Qt。
    """
    deadline = time.time() + wait
    port = 0
    while time.time() < deadline:
        _pid, port = InstanceLock.read_peer(path)
        if port:
            break
        time.sleep(0.08)
    if not port:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5) as sock:
            sock.sendall(command.encode("utf-8"))
        LOG.info("已把指令 %s 转发给运行中的实例（端口 %d）", command, port)
        return True
    except OSError as exc:
        LOG.warning("转发指令失败：%s", exc)
        return False


def pick_free_port() -> int:
    """向系统申请一个空闲端口（绑定后立即释放，够用且简单）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def process_alive(pid: int) -> bool:
    """粗略判断某个 PID 是否仍在运行。"""
    if pid <= 0:
        return False
    if IS_WINDOWS:  # pragma: no cover - Windows 分支
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
