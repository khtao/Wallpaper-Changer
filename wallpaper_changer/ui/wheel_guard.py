"""阻止滚动设置页时误修改数字框、下拉选项或切换标签。

鼠标滚轮在这些控件上会默默修改当前值，即使用户的本意只是向下
翻动设置页。将事件交给外层滚动页；修改数值仍可通过点击、键盘完成。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QComboBox, QTabBar,
    QWidget,
)


class WheelSettingGuard(QObject):
    """仅拦截鼠标滚轮；不改变鼠标点击、键盘与正常的页面滚动。"""

    def install_on(self, root: QWidget) -> None:
        types = (QAbstractSpinBox, QComboBox, QTabBar)
        for child in root.findChildren(QWidget):
            if isinstance(child, types):
                child.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.Wheel:
            return super().eventFilter(watched, event)

        # 外层页面滚动依旧可以工作，避免滚轮碰到选项就突然卡住。
        # QWidget 的子层级经常是 表单 -> 页内容 -> viewport -> QScrollArea。
        parent = watched.parentWidget() if isinstance(watched, QWidget) else None
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                QApplication.sendEvent(parent.viewport(), event)
                return True
            parent = parent.parentWidget()
        # 图库工具栏、标签栏等没有外层滚动页，直接丢弃滚轮事件。
        event.accept()
        return True
