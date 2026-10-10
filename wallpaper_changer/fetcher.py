"""图片下载、缓存与本地校验。"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

import requests

from .models import ImageRecord
from .sources.base import USER_AGENT

LOG = logging.getLogger(__name__)

CHUNK = 64 * 1024


class Fetcher:
    """把图片落到本地缓存目录，供设置壁纸使用。"""

    def __init__(self, cache_dir: Path, proxy: str = "", timeout: int = 20,
                 max_files: int = 300, retries: int = 3, retry_delay: float = 0.8):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.proxy = proxy
        self.timeout = timeout
        self.max_files = max_files
        # 网络偶发失败很常见（尤其对 Bzee 这类有 WAF 的站点），默认重试 3 次
        self.retries = max(1, int(retries))
        self.retry_delay = float(retry_delay)

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
            # 命中缓存也校验一次：损坏文件（如历史遗留的错误页）不应被直接使用
            if self.is_valid_cached(target):
                self._fill_size(rec, target)
                rec.local_path = str(target)
                return str(target)
            try:
                target.unlink()
            except OSError:
                pass

        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".part")
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": rec.webpage or self._referer_for(rec),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        # 依次尝试：主地址 -> 备用地址（如 Bzee 原图不可用时的缩略图）
        candidates = [rec.remote_url]
        if rec.fallback_url and rec.fallback_url != rec.remote_url:
            candidates.append(rec.fallback_url)
        last_err: Optional[Exception] = None
        for url in candidates:
            ok = False
            for attempt in range(1, self.retries + 1):
                try:
                    with requests.get(
                        url, stream=True, timeout=self.timeout,
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
                    # 空文件视为失败（站点偶发返回 200 但无内容）
                    if os.path.getsize(tmp) == 0:
                        raise ValueError("下载到空文件")
                    # 站点有时返回 200 但内容是错误页（JSON/HTML），
                    # 直接存盘会让预览显示「无法解码」，必须在这一步拦掉。
                    self._verify_image(tmp)
                    if url != rec.remote_url:
                        LOG.info("主地址不可用，已改用备用地址：%s", url)
                    os.replace(tmp, target)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    LOG.debug("下载失败（第 %d/%d 次）%s: %s",
                              attempt, self.retries, url, exc)
                    try:
                        if os.path.exists(tmp):
                            os.remove(tmp)
                    except OSError:
                        pass
                    if attempt < self.retries:
                        time.sleep(self.retry_delay * attempt)   # 简单退避
            if ok:
                break
        else:
            raise RuntimeError(f"下载失败：{last_err}")
        self._fill_size(rec, target)
        rec.local_path = str(target)
        self.prune()
        return str(target)

    @staticmethod
    def _verify_image(path) -> None:
        """确认下载到的确实是图片，否则抛错（让上层重试/换源）。

        用 PIL 而不是只看扩展名或 Content-Type：站点被 WAF 拦时常常
        返回 200 + JSON 错误页，那样存下来的文件 Qt 解不开。
        """
        try:
            from PIL import Image
        except ImportError:      # 没装 Pillow 就只做最小检查
            with open(path, "rb") as fh:
                head = fh.read(16)
            if not head:
                raise ValueError("文件为空")
            return
        try:
            with Image.open(path) as im:
                im.verify()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"下载内容不是有效图片：{exc}") from exc

    def is_valid_cached(self, path) -> bool:
        """缓存文件是否仍是能被解码的图片（用于预览前自检）。"""
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return False
        try:
            self._verify_image(p)
            return True
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _referer_for(rec: ImageRecord) -> str:
        """按来源给一个合适的 Referer，降低被站点拒绝的概率。"""
        if rec.source == "bzee":
            return "https://photo.bzee.cn/"
        if rec.source == "bing":
            return "https://www.bing.com/"
        return ""

    def cached_path(self, rec: ImageRecord) -> Optional[Path]:
        """按 remote_url 计算缓存路径；文件确实存在且能解码才返回。"""
        if not rec.remote_url:
            return None
        p = self.target_path(rec)
        if not p.exists() or p.stat().st_size == 0:
            return None
        if not self.is_valid_cached(p):
            # 损坏的缓存（如当年存下的错误页）直接清掉，避免一直显示无法解码
            try:
                p.unlink()
                LOG.info("已删除损坏的缓存文件：%s", p)
            except OSError:
                pass
            return None
        return p

    def resolve_local(self, rec: ImageRecord) -> Optional[str]:
        """在不联网的前提下，尽力找出本机可用的图片文件。

        顺序：已记录的 local_path -> 主地址缓存 -> 备用地址缓存。
        用于预览等场景，避免缓存文件被清理后界面显示不出来。
        """
        if rec.local_path and os.path.isfile(rec.local_path):
            if self.is_valid_cached(rec.local_path):
                return rec.local_path
            # local_path 指向的文件已损坏，清掉后继续找缓存
            try:
                os.remove(rec.local_path)
            except OSError:
                pass
        p = self.cached_path(rec)
        if p:
            rec.local_path = str(p)
            return str(p)
        if rec.fallback_url:
            saved = rec.remote_url
            rec.remote_url = rec.fallback_url
            try:
                p2 = self.cached_path(rec)
            finally:
                rec.remote_url = saved
            if p2:
                rec.local_path = str(p2)
                return str(p2)
        return None

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
