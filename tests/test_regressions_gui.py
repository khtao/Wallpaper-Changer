"""图库原图尺寸与缓存线程退出回归测试：需在安装 PySide6 后执行。"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QObject, QThread, Qt, QPoint, QPointF  # noqa: E402
from PySide6.QtGui import QPixmap, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QSpinBox,
                                QTabWidget, QWidget, QVBoxLayout, QScrollArea)  # noqa: E402
from shiboken6 import delete, isValid  # noqa: E402

from wallpaper_changer.app import Application  # noqa: E402
from wallpaper_changer.models import ImageRecord  # noqa: E402
from wallpaper_changer.ui.gallery import FullImageDialog, GalleryView  # noqa: E402
from wallpaper_changer.ui.wheel_guard import WheelSettingGuard  # noqa: E402
from wallpaper_changer.fetcher import Fetcher  # noqa: E402


@pytest.fixture
def qtapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_full_image_dialog_not_sized_from_uninitialized_viewport(qtapp):
    pix = QPixmap(1600, 900)
    pix.fill(Qt.red)
    rec = ImageRecord("local", "test", title="4K 壁纸", width=3840, height=2160)
    dlg = FullImageDialog(pix, 1400, 900, rec)
    try:
        assert dlg.width() >= 1200, "大图窗口不应启动成 600px 的缩略图窗口"
        assert dlg.height() >= 650
        dlg.show()
        qtapp.processEvents()
        # 首次布局完成后画面尺寸应跟随窗口，而不是 QLabel 里几百像素的副本。
        rendered = dlg.canvas.image_draw_rect()
        assert rendered.width() > 1000
        assert rendered.height() > 500
        assert dlg.canvas.image_size == pix.size()
        dlg.resize(800, 540)
        qtapp.processEvents()
        smaller = dlg.canvas.image_draw_rect()
        assert smaller.width() < rendered.width()
        dlg.resize(1400, 850)
        qtapp.processEvents()
        larger = dlg.canvas.image_draw_rect()
        assert larger.width() > smaller.width()
    finally:
        dlg.close()


def test_quit_survives_already_deleted_migration_thread(qtapp):
    dead = QThread()
    delete(dead)  # 准确复现 libshiboken: Internal C++ object already deleted
    flags = {"engine": 0, "db": 0, "lock": 0}
    ctx = SimpleNamespace(
        _quitting=False,
        _migrate_task=dead,
        window=None,
        engine=SimpleNamespace(shutdown=lambda: flags.__setitem__("engine", flags["engine"]+1)),
        db=SimpleNamespace(close=lambda: flags.__setitem__("db", flags["db"]+1)),
        lock=SimpleNamespace(release=lambda: flags.__setitem__("lock", flags["lock"]+1)),
        _server=None,
    )
    Application.quit(ctx)
    Application.quit(ctx)
    assert flags == {"engine": 1, "db": 1, "lock": 1}
    assert ctx._migrate_task is None


def test_startup_migration_finished_task_is_kept_alive(qtapp):
    class FastFetcher:
        def migrate_cache_to_jpeg(self, cancelled):
            return (0, 0)

    ctx = QObject()
    ctx.engine = SimpleNamespace(fetcher=FastFetcher())
    ctx._migrate_task = None
    Application._migrate_cache(ctx)
    task = ctx._migrate_task
    assert task is not None
    assert task.wait(3000)
    for _ in range(3):
        qtapp.processEvents()
    assert isValid(task), "线程完成后仍需允许退出代码访问 QThread"
    task.requestInterruption()


def test_gallery_doubleclick_preview_uses_full_cache_not_thumb(qtapp, tmp_path, monkeypatch):
    """两种图片都已存在时，查看必须画大图而非缩略图。"""
    from PIL import Image
    from wallpaper_changer.ui.gallery import FullImageDialog
    fetcher = Fetcher(tmp_path / "cache")
    rec = ImageRecord(
        source="bzee", source_id="same", title="高分辨率测试",
        remote_url="https://imgtu.bzee.cn/photo/pic/abc.webp",
        fallback_url="https://imgtu.bzee.cn/photo/thumb/abc_200x113.webp",
    )
    full_file = fetcher.target_path(fetcher.full_image_record(rec))
    thumb_file = fetcher.target_path(fetcher.thumbnail_record(rec))
    full_file.parent.mkdir(parents=True, exist_ok=True)
    thumb_file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3200, 1800), "green").save(full_file)
    Image.new("RGB", (200, 113), "red").save(thumb_file)

    dimensions = []

    def fake_exec(dlg):
        dimensions.append((dlg.canvas.image_size.width(), dlg.canvas.image_size.height()))
        return 0

    monkeypatch.setattr(FullImageDialog, "exec", fake_exec)
    view = GalleryView(fetcher)
    view._view_full(rec)
    try:
        assert dimensions and dimensions[0][0] > 200
        assert abs(dimensions[0][0] / dimensions[0][1] - 3200 / 1800) < 0.02
        assert rec.local_path == str(full_file)
    finally:
        view.shutdown()


def test_mouse_wheel_does_not_modify_settings(qtapp):
    root = QWidget()
    area = QScrollArea(root)
    page = QWidget()
    layout = QVBoxLayout(page)
    spin = QSpinBox(page)
    spin.setRange(0, 100)
    spin.setValue(30)
    double = QDoubleSpinBox(page)
    double.setValue(1.5)
    combo = QComboBox(page)
    combo.addItems(["第一项", "第二项", "第三项"])
    layout.addWidget(spin)
    layout.addWidget(double)
    layout.addWidget(combo)
    layout.addStretch()
    area.setWidget(page)
    tabs = QTabWidget(root)
    tabs.addTab(QWidget(), "一")
    tabs.addTab(QWidget(), "二")
    outer = QVBoxLayout(root)
    outer.addWidget(area)
    outer.addWidget(tabs)
    guard = WheelSettingGuard(root)
    guard.install_on(root)
    root.show()
    qtapp.processEvents()
    for control in (spin, double, combo, tabs.tabBar()):
        control.setFocus()
        ev = QWheelEvent(QPointF(4, 4), QPointF(4, 4), QPoint(0, 0),
                         QPoint(0, -120), Qt.NoButton, Qt.NoModifier,
                         Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(control, ev)
    assert spin.value() == 30
    assert double.value() == 1.5
    assert combo.currentIndex() == 0
    assert tabs.currentIndex() == 0
    # 点击/键盘选择仍然可用
    combo.setCurrentIndex(2)
    assert combo.currentIndex() == 2
    root.close()
