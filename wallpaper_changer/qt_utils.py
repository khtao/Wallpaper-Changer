"""Shiboken QObject 包装对象生命周期相关防御工具。"""
from __future__ import annotations


def thread_is_running(task) -> bool:
    """Qt 对象被 deleteLater 销毁后，访问 isRunning 会抛 RuntimeError。"""
    if task is None:
        return False
    try:
        from shiboken6 import isValid
        return bool(isValid(task) and task.isRunning())
    except (RuntimeError, ReferenceError):
        return False


def stop_thread(task) -> None:
    """请求线程协作停止并等待结束，跳过已销毁的 Qt 对象。"""
    if task is None:
        return
    try:
        from shiboken6 import isValid
        if not isValid(task):
            return
        task.requestInterruption()
        if task.isRunning():
            task.wait()
    except (RuntimeError, ReferenceError):
        # Python wrapper 仍存在，但底层 QObject 已释放。
        return
