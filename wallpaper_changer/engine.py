"""轮换引擎：来源抓取 → 候选池 → 下载 → 设置壁纸 → 记录历史。"""
from __future__ import annotations

import logging
import random
import time
from collections import deque
from typing import Deque, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

from .config import Config
from .database import Database
from .fetcher import Fetcher
from .models import ImageRecord
from .sources import build_sources
from .wallpaper import set_wallpaper

LOG = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 后台任务
# --------------------------------------------------------------------------- #
class ApplyTask(QThread):
    """下载并设置一张壁纸。"""

    result = Signal(object, bool, str)  # record, ok, message

    def __init__(self, fetcher: Fetcher, record: ImageRecord, mode: str, set_dark: bool):
        super().__init__()
        self.fetcher = fetcher
        self.record = record
        self.mode = mode
        self.set_dark = set_dark

    def run(self) -> None:  # pragma: no cover - 需要线程环境
        try:
            path = self.fetcher.ensure_local(self.record)
            ok, msg = set_wallpaper(path, self.mode, self.set_dark)
            self.result.emit(self.record, bool(ok), str(msg))
        except Exception as exc:  # noqa: BLE001
            LOG.exception("应用壁纸失败")
            self.result.emit(self.record, False, str(exc))


class RefreshTask(QThread):
    """抓取所有启用来源，返回图片列表。"""

    result = Signal(object, str)  # list[ImageRecord], error_message

    def __init__(self, config: Config, limit: int = 60):
        super().__init__()
        self.config = config
        self.limit = limit

    def run(self) -> None:  # pragma: no cover
        records: List[ImageRecord] = []
        errors: List[str] = []
        for src in build_sources(self.config):
            try:
                res = src.fetch(limit=self.limit)
                if res.error:
                    errors.append(f"{src.label}：{res.error}")
                records.extend(res.images)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{src.label}：{exc}")
        self.result.emit(records, "；".join(errors))


# --------------------------------------------------------------------------- #
# 引擎
# --------------------------------------------------------------------------- #
class Engine(QObject):
    changed = Signal(object)        # ImageRecord —— 成功换上新壁纸
    failed = Signal(str)            # 出错信息
    status = Signal(str)            # 状态文本
    pool_updated = Signal(int)      # 候选池数量
    refreshing = Signal(bool)       # 是否正在抓取
    refresh_finished = Signal(int, str)  # 抓取完成：(候选数量, 错误汇总)

    def __init__(self, config: Config, db: Database, parent=None):
        super().__init__(parent)
        self.config = config
        self.db = db
        self.fetcher = Fetcher(
            config.cache_dir,
            proxy=config.data.proxy,
            timeout=config.data.rotation.timeout,
            max_files=config.data.max_cache_files,
        )
        self.pool: Deque[ImageRecord] = deque()
        self.current: Optional[ImageRecord] = None
        self._stack: List[ImageRecord] = []      # 已应用历史（用于“上一张”）
        self._refreshing = False
        self._applying = False
        self._favorite_only = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.change_next(reason="定时"))
        self._tasks: List[QThread] = []
        self.last_refresh: float = 0.0
        self.apply_timer()

    # ---------- 定时器 ----------
    def apply_timer(self) -> None:
        rot = self.config.data.rotation
        if rot.enabled and rot.interval_minutes > 0:
            self._timer.setInterval(int(rot.interval_minutes * 60 * 1000))
            self._timer.start()
        else:
            self._timer.stop()

    def seconds_to_next(self) -> int:
        if not self._timer.isActive():
            return -1
        return int(self._timer.remainingTime() / 1000)

    def pause(self, paused: bool) -> None:
        if paused:
            self._timer.stop()
        else:
            self.apply_timer()

    # ---------- 候选池 ----------
    def refresh_pool(self, force: bool = False) -> None:
        if self._refreshing:
            return
        if not force and self.pool and time.time() - self.last_refresh < 60:
            return
        self._refreshing = True
        self.refreshing.emit(True)
        self.status.emit("正在获取图片…")
        task = RefreshTask(self.config)
        task.result.connect(self._on_refreshed)
        task.finished.connect(lambda: self._reap(task))
        self._tasks.append(task)
        task.start()

    def _on_refreshed(self, records: List[ImageRecord], error: str) -> None:
        self._refreshing = False
        self.refreshing.emit(False)
        self.last_refresh = time.time()
        rot = self.config.data.rotation
        recent = self.db.recently_used_source_ids(rot.avoid_repeat_days)
        saved: List[ImageRecord] = []
        for rec in records:
            if rec.width and rec.height and (
                rec.width < rot.min_width or rec.height < rot.min_height
            ):
                continue
            saved.append(self.db.upsert(rec))
        candidates = [r for r in saved if (r.source, r.source_id) not in recent]
        if not candidates:
            candidates = saved  # 全用过了就放宽
        if rot.random_order:
            random.shuffle(candidates)
        self.pool = deque(candidates)
        self.pool_updated.emit(len(self.pool))
        msg = f"获取到 {len(self.pool)} 张候选"
        if error:
            msg += f"（部分来源失败：{error}）"
            self.failed.emit(error)
        self.status.emit(msg)
        self.refresh_finished.emit(len(self.pool), error)

    # ---------- 切换 ----------
    def change_next(self, reason: str = "手动", favorite_only: bool | None = None) -> None:
        if favorite_only is not None:
            self._favorite_only = favorite_only
        if self._applying:
            self.status.emit("正在切换中，请稍候…")
            return
        rec = self._pick()
        if rec is None:
            self.status.emit("候选为空，正在获取…")
            self.refresh_pool(force=True)
            return
        self._start_apply(rec, reason)

    def apply_record(self, rec: ImageRecord, reason: str = "手动选择") -> None:
        if self._applying:
            return
        self._start_apply(rec, reason)

    def _start_apply(self, rec: ImageRecord, reason: str) -> None:
        self._applying = True
        self.status.emit(f"{reason}：正在下载 {rec.display_title}")
        rot = self.config.data.rotation
        task = ApplyTask(self.fetcher, rec, rot.fit_mode, rot.set_dark_variant)
        task.result.connect(self._on_applied)
        task.finished.connect(lambda: self._reap(task))
        self._tasks.append(task)
        task.start()

    def _on_applied(self, rec: ImageRecord, ok: bool, message: str) -> None:
        self._applying = False
        if not ok:
            self.failed.emit(f"设置壁纸失败：{message}")
            self.status.emit("设置失败，尝试下一张…")
            if self.pool:
                self._start_apply(self.pool.popleft(), "失败重试")
            return
        rec = self.db.upsert(rec)
        self.db.mark_used(rec.id, True, message, rec.source, rec.display_title, rec.local_path)
        if self.current is not None:
            self._stack.append(self.current)
            self._stack = self._stack[-50:]
        self.current = rec
        self.changed.emit(rec)
        self.status.emit(f"已应用：{rec.display_title}（{rec.source}）")
        if len(self.pool) < 3:
            self.refresh_pool(force=True)

    def change_prev(self) -> None:
        """回到上一张（历史栈）。"""
        if not self._stack:
            rows = self.db.list_history(limit=10)
            for row in rows[1:]:
                if row["image_id"]:
                    rec = self.db.get(row["image_id"])
                    if rec and rec.local_path:
                        self.apply_record(rec, "上一张")
                        return
            self.status.emit("没有更早的历史了")
            return
        rec = self._stack.pop()
        self.apply_record(rec, "上一张")

    def _pick(self) -> Optional[ImageRecord]:
        if self._favorite_only:
            favs = [r for r in self.db.list_favorites() if r not in self._stack[-3:]]
            if favs:
                return random.choice(favs)
            self.status.emit("收藏为空，改用普通候选")
        while self.pool:
            rec = self.pool.popleft()
            if rec.local_path or rec.remote_url:
                return rec
        return None

    def _reap(self, task: QThread) -> None:
        try:
            self._tasks.remove(task)
        except ValueError:
            pass
        task.deleteLater()

    # ---------- 收藏 ----------
    def toggle_favorite_current(self) -> Optional[bool]:
        if not self.current:
            return None
        return self.toggle_favorite(self.current)

    def toggle_favorite(self, rec: ImageRecord) -> bool:
        rec = self.db.upsert(rec)
        new_state = not rec.is_favorite
        self.db.set_favorite(rec.id, new_state)  # type: ignore[arg-type]
        rec.is_favorite = new_state
        if self.current and self.current.id == rec.id:
            self.current.is_favorite = new_state
        self.status.emit("已收藏" if new_state else "已取消收藏")
        return new_state

    # ---------- 配置变更 ----------
    def set_favorite_only(self, enabled: bool) -> None:
        """是否只在收藏中轮换。"""
        self._favorite_only = bool(enabled)
        self.status.emit("已切换为仅轮换收藏" if enabled else "恢复全部图片轮换")

    def shutdown(self) -> None:
        """退出前停止线程与定时器。"""
        self._timer.stop()
        for task in list(self._tasks):
            if hasattr(task, "stop"):
                task.stop()
            if task.isRunning():
                task.wait(1000)

    def reload(self) -> None:
        self.fetcher.cache_dir = self.config.cache_dir
        self.fetcher.proxy = self.config.data.proxy
        self.fetcher.timeout = self.config.data.rotation.timeout
        self.fetcher.max_files = self.config.data.max_cache_files
        self.apply_timer()
        self.pool.clear()
        self.refresh_pool(force=True)
