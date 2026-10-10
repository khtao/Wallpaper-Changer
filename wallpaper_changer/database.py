"""SQLite 存储：历史记录 + 收藏 + 状态。

数据库位置：
  Linux   : ~/.local/share/wallpaper-changer/history.db
  Windows : %APPDATA%\\WallpaperChanger\\history.db
"""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Optional

from .config import app_data_dir
from .models import ImageRecord

LOG = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    remote_url    TEXT,
    local_path    TEXT,
    title         TEXT,
    author        TEXT,
    license       TEXT,
    webpage       TEXT,
    width         INTEGER DEFAULT 0,
    height        INTEGER DEFAULT 0,
    file_size     INTEGER DEFAULT 0,
    tags          TEXT,
    is_favorite   INTEGER DEFAULT 0,
    favorite_at   TEXT,
    added_at      TEXT,
    last_used_at  TEXT,
    used_count    INTEGER DEFAULT 0,
    extra         TEXT,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id   INTEGER,
    source     TEXT,
    title      TEXT,
    local_path TEXT,
    set_at     TEXT,
    success    INTEGER DEFAULT 1,
    message    TEXT,
    FOREIGN KEY(image_id) REFERENCES images(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_images_fav   ON images(is_favorite);
CREATE INDEX IF NOT EXISTS idx_images_used  ON images(last_used_at);
CREATE INDEX IF NOT EXISTS idx_history_time ON history(set_at);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else app_data_dir() / "history.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 转换 ----------
    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ImageRecord:
        return ImageRecord(
            id=row["id"],
            source=row["source"],
            source_id=row["source_id"],
            remote_url=row["remote_url"] or "",
            local_path=row["local_path"] or "",
            title=row["title"] or "",
            author=row["author"] or "",
            license=row["license"] or "",
            webpage=row["webpage"] or "",
            width=row["width"] or 0,
            height=row["height"] or 0,
            file_size=row["file_size"] or 0,
            tags=row["tags"] or "",
            is_favorite=bool(row["is_favorite"]),
            favorite_at=row["favorite_at"] or "",
            added_at=row["added_at"] or "",
            last_used_at=row["last_used_at"] or "",
            used_count=row["used_count"] or 0,
            extra=row["extra"] or "",
        )

    # ---------- 写入 ----------
    def upsert(self, rec: ImageRecord) -> ImageRecord:
        """按 (source, source_id) 去重写入，返回带 id 的记录。"""
        cur = self.conn.execute(
            "SELECT id, is_favorite, favorite_at, used_count FROM images "
            "WHERE source=? AND source_id=?",
            (rec.source, rec.source_id),
        )
        row = cur.fetchone()
        if row is None:
            if not rec.added_at:
                rec.added_at = _now()
            cur = self.conn.execute(
                """INSERT INTO images (source, source_id, remote_url, local_path, title,
                       author, license, webpage, width, height, file_size, tags,
                       is_favorite, favorite_at, added_at, last_used_at, used_count, extra)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec.source,
                    rec.source_id,
                    rec.remote_url,
                    rec.local_path,
                    rec.title,
                    rec.author,
                    rec.license,
                    rec.webpage,
                    rec.width,
                    rec.height,
                    rec.file_size,
                    rec.tags,
                    int(rec.is_favorite),
                    rec.favorite_at,
                    rec.added_at,
                    rec.last_used_at,
                    rec.used_count,
                    rec.extra,
                ),
            )
            rec.id = cur.lastrowid
        else:
            rec.id = row["id"]
            rec.is_favorite = rec.is_favorite or bool(row["is_favorite"])
            rec.favorite_at = rec.favorite_at or row["favorite_at"] or ""
            rec.used_count = max(rec.used_count, row["used_count"] or 0)
            self.conn.execute(
                """UPDATE images SET remote_url=?, local_path=?, title=?, author=?,
                       license=?, webpage=?, width=?, height=?, file_size=?, tags=?,
                       is_favorite=?, favorite_at=?, used_count=?, extra=?
                   WHERE id=?""",
                (
                    rec.remote_url,
                    rec.local_path,
                    rec.title,
                    rec.author,
                    rec.license,
                    rec.webpage,
                    rec.width,
                    rec.height,
                    rec.file_size,
                    rec.tags,
                    int(rec.is_favorite),
                    rec.favorite_at,
                    rec.used_count,
                    rec.extra,
                    rec.id,
                ),
            )
        self.conn.commit()
        return rec

    def mark_used(self, image_id: Optional[int], success: bool = True,
                  message: str = "", source: str = "", title: str = "",
                  local_path: str = "") -> None:
        now = _now()
        if image_id:
            self.conn.execute(
                "UPDATE images SET last_used_at=?, used_count=used_count+1 WHERE id=?",
                (now, image_id),
            )
        self.conn.execute(
            "INSERT INTO history (image_id, source, title, local_path, set_at, success, message)"
            " VALUES (?,?,?,?,?,?,?)",
            (image_id, source, title, local_path, now, int(success), message),
        )
        self.conn.commit()

    def set_favorite(self, image_id: int, favorite: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE images SET is_favorite=?, favorite_at=? WHERE id=?",
            (int(favorite), _now() if favorite else "", image_id),
        )
        self.conn.commit()
        return bool(cur.rowcount)

    def delete_image(self, image_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM images WHERE id=?", (image_id,))
        self.conn.execute("DELETE FROM history WHERE image_id=?", (image_id,))
        self.conn.commit()
        return bool(cur.rowcount)

    def clear_history(self) -> None:
        self.conn.execute("DELETE FROM history")
        self.conn.commit()

    def prune_history(self, keep: int = 500) -> None:
        self.conn.execute(
            "DELETE FROM history WHERE id NOT IN "
            "(SELECT id FROM history ORDER BY id DESC LIMIT ?)",
            (keep,),
        )
        self.conn.commit()

    # ---------- 查询 ----------
    def get(self, image_id: int) -> Optional[ImageRecord]:
        cur = self.conn.execute("SELECT * FROM images WHERE id=?", (image_id,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def get_by_source_id(self, source: str, source_id: str) -> Optional[ImageRecord]:
        cur = self.conn.execute(
            "SELECT * FROM images WHERE source=? AND source_id=?", (source, source_id)
        )
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def list_favorites(self, limit: int = 500) -> List[ImageRecord]:
        cur = self.conn.execute(
            "SELECT * FROM images WHERE is_favorite=1 ORDER BY favorite_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_record(r) for r in cur.fetchall()]

    def last_applied_record(self) -> ImageRecord | None:
        """最近一次**成功**设置过的壁纸，用于启动时回显当前壁纸。

        优先取有 local_path 且文件仍在的记录；找不到就返回 None。
        """
        cur = self.conn.execute(
            "SELECT image_id, local_path FROM history "
            "WHERE success=1 ORDER BY id DESC LIMIT 20"
        )
        for row in cur.fetchall():
            image_id = row["image_id"]
            rec = self.get(image_id) if image_id else None
            if rec is not None:
                return rec
        return None

    def list_history(self, limit: int = 200) -> List[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
        )
        return cur.fetchall()

    def list_images(self, limit: int = 500, favorite_only: bool = False,
                    source: str = "") -> List[ImageRecord]:
        sql = "SELECT * FROM images"
        params: List[object] = []
        conds = []
        if favorite_only:
            conds.append("is_favorite=1")
        if source:
            conds.append("source=?")
            params.append(source)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY COALESCE(NULLIF(last_used_at,''), added_at) DESC LIMIT ?"
        params.append(limit)
        cur = self.conn.execute(sql, params)
        return [self._row_to_record(r) for r in cur.fetchall()]

    def recently_used_source_ids(self, days: int) -> set:
        """返回 N 天内使用过的 (source, source_id) 集合，用于避免重复。"""
        cutoff = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400)
        )
        cur = self.conn.execute(
            "SELECT source, source_id FROM images WHERE last_used_at <> '' AND last_used_at >= ?",
            (cutoff,),
        )
        return {(r["source"], r["source_id"]) for r in cur.fetchall()}

    def count_favorites(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM images WHERE is_favorite=1"
        ).fetchone()[0]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        fav = self.count_favorites()
        used = self.conn.execute(
            "SELECT COUNT(*) FROM images WHERE used_count > 0"
        ).fetchone()[0]
        return {"total": total, "favorites": fav, "used": used}

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:  # pragma: no cover
            pass
