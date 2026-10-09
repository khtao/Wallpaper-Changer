"""图片下载、缓存与本地校验。"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path

import requests

from .models import ImageRecord
from .sources.base import USER_AGENT

LOG = logging.getLogger(__name__)

CHUNK = 64 * 1024


class Fetcher:
    """把图片落到本地缓存目录，供设置壁纸使用。"""

    def __init__(self, cache_dir: Path, proxy: str = "", timeout: int = 20,
                 max_files: int = 300):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy
        self.timeout = timeout
        self.max_files = max_files

    # ---------- 路径 ----------
    def target_path(self, rec: ImageRecord) -> Path:
        key = rec.remote_url or rec.local_path or rec.source_id
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        ext = self._guess_ext(rec)
        return self.cache_dir / rec.source / f"{digest}{ext}"

    @staticmethod
    def _guess_ext(rec: ImageRecord) -> str:
        for candidate in (rec.local_path, rec.remote_url):
            if not candidate:
                continue
            name = candidate.split("?")[0].rsplit("/", 1)[-1]
            if "." in name:
                ext = "." + name.rsplit(".", 1)[-1].lower()
                if len(ext) <= 5 and ext.isascii():
                    return ext
        return ".jpg"

    # ---------- 下载 ----------
    def ensure_local(self, rec: ImageRecord, force: bool = False) -> str:
        """确保图片在本机可用，返回本地绝对路径。"""
        if rec.local_path and os.path.isfile(rec.local_path):
            self._fill_size(rec, Path(rec.local_path))
            return rec.local_path
        if not rec.remote_url:
            raise FileNotFoundError("既无本地文件也无远程地址")

        target = self.target_path(rec)
        if target.exists() and target.stat().st_size > 0 and not force:
            self._fill_size(rec, target)
            rec.local_path = str(target)
            return str(target)

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        headers = {"User-Agent": USER_AGENT, "Referer": rec.webpage or ""}
        with requests.get(
            rec.remote_url, stream=True, timeout=self.timeout,
            headers=headers, proxies=proxies,
        ) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("Content-Type", "")
            if ctype.startswith("text/") and "json" in ctype:
                raise ValueError(f"返回的不是图片（{ctype}）")
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(CHUNK):
                    if chunk:
                        fh.write(chunk)
        os.replace(tmp, target)
        self._fill_size(rec, target)
        rec.local_path = str(target)
        self.prune()
        return str(target)

    def _fill_size(self, rec: ImageRecord, path: Path) -> None:
        try:
            rec.file_size = path.stat().st_size
        except OSError:
            pass
        if not (rec.width and rec.height):
            try:
                from PIL import Image

                with Image.open(path) as im:
                    rec.width, rec.height = im.size
            except Exception:  # noqa: BLE001
                pass

    # ---------- 维护 ----------
    def prune(self, keep: int | None = None) -> int:
        """按修改时间清理旧缓存，返回删除数量。"""
        keep = keep if keep is not None else self.max_files
        if keep <= 0:
            return 0
        files = [p for p in self.cache_dir.rglob("*") if p.is_file()]
        if len(files) <= keep:
            return 0
        files.sort(key=lambda p: p.stat().st_mtime)
        removed = 0
        for p in files[: len(files) - keep]:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def cache_size(self) -> int:
        return sum(p.stat().st_size for p in self.cache_dir.rglob("*") if p.is_file())

    def clear(self) -> int:
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir, ignore_errors=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return 0

    def export_to(self, rec: ImageRecord, dest_dir: Path, name: str | None = None) -> Path:
        """把某张壁纸另存到指定目录（收藏导出用）。"""
        dest_dir.mkdir(parents=True, exist_ok=True)
        src = Path(self.ensure_local(rec))
        ext = src.suffix or ".jpg"
        safe = "".join(ch for ch in (name or rec.display_title) if ch not in '\\/:*?"<>|')
        dst = dest_dir / f"{safe[:80]}{ext}"
        n = 1
        while dst.exists():
            dst = dest_dir / f"{safe[:80]}_{n}{ext}"
            n += 1
        shutil.copy2(src, dst)
        return dst
