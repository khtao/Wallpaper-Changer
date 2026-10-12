"""图片下载、缓存与本地校验。"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
import tempfile
import threading
import weakref
from dataclasses import replace
from collections import OrderedDict
import re
from urllib.parse import urlsplit
from typing import Callable
from pathlib import Path
from typing import Optional

import requests

from .models import ImageRecord
from .sources.base import USER_AGENT

LOG = logging.getLogger(__name__)

CHUNK = 64 * 1024
# 远程来源不可控：防止 200 错误页/超大图片耗尽磁盘和内存。
MAX_DOWNLOAD_BYTES = 48 * 1024 * 1024
MAX_IMAGE_PIXELS = 64_000_000
MAX_CACHE_JPEG_BYTES = 80 * 1024 * 1024

# --------------------------------------------------------------------------- #
# 缓存图片统一格式
#   网络上抓来的图格式很杂（Bzee 是 webp、Bing 是 jpg、还有 png / avif …），
#   Qt 对部分格式/特性支持不稳定（典型：webp 在某些环境解不开，双击大图显示空白）。
#   因此：凡是需要下载的图片，缓存前一律转码为 JPEG，
#   保证「缓存里只有一种格式」，从根上消除解码失败。
#   本地文件夹来源不转码（用户自己的图，直接引用原件）。
# --------------------------------------------------------------------------- #
CACHE_FORMAT = "JPEG"
CACHE_EXT = ".jpg"
CACHE_JPEG_QUALITY = 92


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
        self.retry_delay = max(0.0, float(retry_delay))
        self._lock_guard = threading.Lock()
        # 弱引用锁在下载结束后可回收，不随应用长期运行而无限增长。
        self._download_locks = weakref.WeakValueDictionary()
        self._verified: OrderedDict[str, tuple[int, int, bool]] = OrderedDict()
        self.protected_paths: set[Path] = set()

    # ---------- 路径 ----------
    def target_path(self, rec: ImageRecord) -> Path:
        key = rec.remote_url or rec.local_path or rec.source_id
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        ext = self._guess_ext(rec)
        return self.cache_dir / rec.source / f"{digest}{ext}"

    @staticmethod
    def _guess_ext(rec: ImageRecord) -> str:
        """缓存文件扩展名。

        需要下载的图片（有 remote_url）一律按 JPEG 存，因为下载后会转码；
        只有本地文件的记录才沿用原扩展名。
        """
        if rec.remote_url:
            return CACHE_EXT
        for candidate in (rec.local_path, rec.remote_url):
            if not candidate:
                continue
            name = candidate.split("?")[0].rsplit("/", 1)[-1]
            if "." in name:
                ext = "." + name.rsplit(".", 1)[-1].lower()
                if len(ext) <= 5 and ext.isascii():
                    return ext
        return CACHE_EXT

    # ---------- 图库图片质量分级 ----------
    @staticmethod
    def thumbnail_record(rec: ImageRecord) -> ImageRecord:
        """返回独立的缩略图下载记录，不修改图库/数据库里的原图记录。

        Bzee 的 /photo/thumb/ 和 /photo/pic/ 必须拥有不同的缓存 key。
        否则缩略图下载成功后写入 rec.local_path，双击便可能只打开缩略图。
        """
        if not rec.remote_url:
            return rec
        if rec.source == "qh360":
            from .sources.qh360 import thumb_url
            url = thumb_url(rec.remote_url)
            return replace(rec, remote_url=url, fallback_url="", local_path="",
                           width=0, height=0, file_size=0)
        if rec.source != "bzee":
            # 非 Bzee 网络图片也不应由图库加载任务修改数据库记录。
            return replace(rec)
        from .sources.bzee import to_thumb
        if "/photo/thumb/" in rec.remote_url:
            url = rec.remote_url
        elif "/photo/thumb/" in rec.fallback_url:
            url = rec.fallback_url
        else:
            url = to_thumb(rec.remote_url)
        return replace(rec, remote_url=url, fallback_url="", local_path="",
                       width=0, height=0, file_size=0)

    @staticmethod
    def full_image_record(rec: ImageRecord) -> ImageRecord:
        """原图使用独立 URL 和缓存，不能把低清备用地址当成原图。"""
        if not rec.remote_url:
            return rec
        url = rec.remote_url
        if rec.source == "bzee":
            from .sources.bzee import to_original
            url = to_original(url)
        # 360 的 fallback_url 是同一张高清图的原始 HTTP 地址，不是缩略图。
        # 保留它以兼容部分只支持 HTTP 的老 CDN；Bzee 则不能保留低清备用。
        fallback = rec.fallback_url if rec.source == "qh360" else ""
        return replace(rec, remote_url=url, fallback_url=fallback, local_path="",
                       width=0, height=0, file_size=0)

    @staticmethod
    def _legacy_thumb_in_original_cache(rec: ImageRecord, path: Path) -> bool:
        """识别旧版本错误存进原图 key 的 Bzee 缩略图。"""
        if rec.source != "bzee" or not rec.remote_url:
            return False
        from .sources.bzee import is_original
        if not (is_original(rec.remote_url) or "/photo/thumb/" in rec.remote_url):
            return False
        thumb_url = (rec.fallback_url if "/photo/thumb/" in rec.fallback_url
                     else rec.remote_url if "/photo/thumb/" in rec.remote_url else "")
        if not thumb_url:
            from .sources.bzee import to_thumb
            thumb_url = to_thumb(rec.remote_url)
        match = re.search(r"_(\d+)x(\d+)(?=\.[a-zA-Z0-9]+(?:\?|$))", thumb_url)
        try:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            return False
        if match:
            tw, th = int(match.group(1)), int(match.group(2))
            # 压缩/转换可能令尺寸有少量差异，面积仍不会接近真正的 2K/4K 原图。
            return w <= int(tw * 1.1) and h <= int(th * 1.1)
        return max(w, h) < 900

    def resolve_full_image(self, rec: ImageRecord) -> Optional[str]:
        """仅返回可信的原图缓存；绝不回退到图库缩略图。"""
        if not rec.remote_url:
            return self.resolve_local(rec)
        full_rec = self.full_image_record(rec)
        # 同一张高清 360 图的 HTTPS 与 HTTP 缓存均可供预览，
        # 但 /bdm/ 缩略图缓存绝对不能冒充原图。
        paths = [self.cached_path(full_rec)]
        if rec.source == "qh360" and full_rec.fallback_url:
            alt = replace(full_rec, remote_url=full_rec.fallback_url, fallback_url="")
            paths.append(self.cached_path(alt))
        for path in paths:
            if path is None or self._legacy_thumb_in_original_cache(rec, path):
                continue
            rec.local_path = str(path)
            self._fill_size(rec, path, force_dimensions=True)
            return str(path)
        return None

    def ensure_full_image(self, rec: ImageRecord,
                          cancelled: Callable[[], bool] | None = None) -> str:
        """下载/使用真正的原图；原图获取失败则返回错误，不冒充为高清。"""
        path = self.resolve_full_image(rec)
        if path is not None:
            return path
        full_rec = self.full_image_record(rec)
        legacy = self.cached_path(full_rec) if full_rec.remote_url else None
        force = bool(legacy and self._legacy_thumb_in_original_cache(rec, legacy))
        path = self.ensure_local(full_rec, force=force, cancelled=cancelled)
        # 只有下载成功后才更新真实记录，失败时旧记录仍能正常显示缩略图。
        rec.local_path = path
        self._fill_size(rec, Path(path), force_dimensions=True)
        return path

    # ---------- 下载 ----------
    def ensure_local(self, rec: ImageRecord, force: bool = False,
                     cancelled: Callable[[], bool] | None = None) -> str:
        """按 URL 独占下载、大小限制、验证、转码及原子提交；支持线程协作取消。"""
        cancelled = cancelled or (lambda: False)
        if cancelled():
            raise InterruptedError("下载已取消")
        if rec.local_path and os.path.isfile(rec.local_path) and not force:
            if self.is_valid_cached(rec.local_path):
                self._fill_size(rec, Path(rec.local_path))
                return rec.local_path
            if not rec.remote_url:
                raise ValueError("本地图片无法解码，请检查图片文件")
        if not rec.remote_url:
            if rec.local_path and os.path.isfile(rec.local_path):
                return rec.local_path
            raise FileNotFoundError("既无本地文件也无远程地址")

        target = self.target_path(rec)
        # 同一 URL 的预览/轮换/图库请求同时进行时只允许一个写缓存。
        with self._lock_guard:
            lock = self._download_locks.setdefault(str(target), threading.Lock())
        with lock:
            if cancelled():
                raise InterruptedError("下载已取消")
            if target.is_file() and not force:
                if self.is_valid_cached(target):
                    self._fill_size(rec, target)
                    rec.local_path = str(target)
                    return str(target)
                target.unlink(missing_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": rec.webpage or self._referer_for(rec),
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
            candidates = [rec.remote_url]
            if rec.fallback_url and rec.fallback_url != rec.remote_url:
                candidates.append(rec.fallback_url)
            last_err: Exception | None = None
            for url in candidates:
                parsed = urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    last_err = ValueError("只允许 HTTP/HTTPS 图片地址")
                    continue
                for attempt in range(1, self.retries + 1):
                    if cancelled():
                        raise InterruptedError("下载已取消")
                    raw_name = jpg_name = None
                    try:
                        # 连接和读取超时分别限制；不能用单一超时视为总耗时。
                        timeout = (min(6, max(1, self.timeout)),
                                   min(10, max(1, self.timeout)))
                        deadline = time.monotonic() + min(90, max(25, self.timeout * 3))
                        with requests.get(url, stream=True, timeout=timeout,
                                          headers=headers, proxies=proxies) as resp:
                            resp.raise_for_status()
                            ctype = resp.headers.get("Content-Type", "").lower().split(";", 1)[0].strip()
                            if ctype.startswith("text/") or ctype in ("application/json", "application/xml"):
                                raise ValueError(f"服务器返回非图片内容：{ctype}")
                            length = resp.headers.get("Content-Length", "")
                            if length.isdigit() and int(length) > MAX_DOWNLOAD_BYTES:
                                raise ValueError("图片超出下载大小上限")
                            fd, raw_name = tempfile.mkstemp(prefix=target.stem + "_", suffix=".part",
                                                            dir=target.parent)
                            count = 0
                            with os.fdopen(fd, "wb") as fh:
                                for chunk in resp.iter_content(CHUNK):
                                    if cancelled():
                                        raise InterruptedError("下载已取消")
                                    if time.monotonic() > deadline:
                                        raise TimeoutError("下载耗时超过单次请求限制")
                                    if chunk:
                                        count += len(chunk)
                                        if count > MAX_DOWNLOAD_BYTES:
                                            raise ValueError("图片超出下载大小上限")
                                        fh.write(chunk)
                        if count == 0:
                            raise ValueError("服务器返回空图片")
                        self._verify_image(raw_name)
                        fd, jpg_name = tempfile.mkstemp(prefix=target.stem + "_", suffix=".jpg.tmp",
                                                        dir=target.parent)
                        os.close(fd)
                        # 不再把未转码的 WebP/AVIF 冒充为 .jpg。
                        if not self._store_as_jpeg(raw_name, jpg_name):
                            raise ValueError("图片转为 JPEG 失败")
                        if os.path.getsize(jpg_name) > MAX_CACHE_JPEG_BYTES:
                            raise ValueError("转换后的缓存图片过大")
                        if cancelled():
                            raise InterruptedError("下载已取消")
                        # 备用图片绝不能写在原图 URL 对应的缓存 key 下！
                        # 否则以后查看原图时会误认为缓存命中，直接显示低清备用图。
                        actual_target = (target if url == rec.remote_url else
                                         self.target_path(replace(rec, remote_url=url)))
                        actual_target.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(jpg_name, actual_target)
                        if url != rec.remote_url:
                            LOG.info("原图不可用，备用图片独立缓存：%s", url)
                        self._fill_size(rec, actual_target, force_dimensions=True)
                        rec.local_path = str(actual_target)
                        self.prune(protect={actual_target})
                        return str(actual_target)
                    except InterruptedError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        last_err = exc
                        LOG.debug("下载失败 %d/%d %s: %s", attempt, self.retries, url, exc)
                        if attempt < self.retries and self.retry_delay:
                            # 尽快响应退出命令，而不是无条件休眠几秒。
                            deadline = time.monotonic() + self.retry_delay * attempt
                            while time.monotonic() < deadline:
                                if cancelled():
                                    raise InterruptedError("下载已取消")
                                time.sleep(min(0.1, deadline - time.monotonic()))
                    finally:
                        for temp in (raw_name, jpg_name):
                            if temp:
                                try:
                                    Path(temp).unlink(missing_ok=True)
                                except OSError:
                                    pass
            raise RuntimeError(f"下载失败：{last_err}")

    # ---------- JPEG 转码 ----------
    @staticmethod
    def _store_as_jpeg(src, dest) -> bool:
        """把任意格式的图片转存为 JPEG，成功返回 True。

        - 带 alpha 的图（png/webp）合成到白色背景，避免出现黑块；
        - 已经是 JPEG 时若体积没优势也照常重写，保证缓存格式唯一。
        """
        try:
            from PIL import Image
        except ImportError:      # 没装 Pillow 就跳过转码
            return False
        try:
            with Image.open(src) as im:
                if im.width * im.height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数超过安全上限")
                im.load()
                if im.mode in ("RGBA", "LA", "P") and (
                    "transparency" in im.info or im.mode == "RGBA"
                ):
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.getchannel("A"))
                    im = bg
                elif im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                elif im.mode == "L":
                    im = im.convert("RGB")
                Path(dest).parent.mkdir(parents=True, exist_ok=True)
                im.save(dest, CACHE_FORMAT, quality=CACHE_JPEG_QUALITY,
                        optimize=True, progressive=True)
            return True
        except Exception as exc:  # noqa: BLE001
            LOG.debug("转码 JPEG 失败 %s: %s", src, exc)
            return False

    def migrate_cache_to_jpeg(self, cancelled: Callable[[], bool] | None = None) -> tuple:
        """把缓存目录里既有的非 JPEG 图片统一转码为 JPEG。

        老版本缓存里可能残留 webp / png（正是双击打不开的那些文件）。
        由于缓存文件名是「URL 哈希 + 扩展名」，转码后只改扩展名即可被
        新的查找逻辑命中，数据库中的记录无需改动。

        :return: (转码成功数, 删除的损坏/重复文件数)
        """
        converted = 0
        removed = 0
        if not self.cache_dir.exists():
            return converted, removed
        for p in sorted(self.cache_dir.rglob("*")):
            if cancelled is not None and cancelled():
                break
            if not p.is_file():
                continue
            # 跳过缩略图目录（它们本来就是 jpg）
            try:
                if "thumbs" in p.relative_to(self.cache_dir).parts:
                    continue
            except ValueError:
                pass
            if p.suffix.lower() == CACHE_EXT or p.suffix.lower() in (".part", ".tmp"):
                continue
            try:
                if not self.is_valid_cached(p):
                    p.unlink()
                    removed += 1
                    continue
            except OSError:
                continue
            dest = p.with_suffix(CACHE_EXT)
            # 不应将有效旧文件替换为损坏的同名 jpg。
            if dest.exists() and self.is_valid_cached(dest):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
                continue
            if dest in self.protected_paths or p in self.protected_paths:
                continue
            with self._lock_guard:
                busy = self._download_locks.get(str(dest))
            if busy is not None and busy.locked():
                continue
            tmp = None
            try:
                fd, tmp = tempfile.mkstemp(prefix=p.stem + "_", suffix=".tmp",
                                            dir=p.parent)
                os.close(fd)
                if self._store_as_jpeg(p, tmp):
                    os.replace(tmp, dest)
                    tmp = None
                    p.unlink()
                    converted += 1
                else:
                    LOG.debug("跳过无法转码的缓存文件：%s", p)
            except OSError as exc:
                LOG.debug("迁移缓存失败 %s: %s", p, exc)
            finally:
                if tmp:
                    Path(tmp).unlink(missing_ok=True)
        return converted, removed

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
                if im.width * im.height > MAX_IMAGE_PIXELS:
                    raise ValueError("图片像素数超过安全上限")
                im.verify()
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"下载内容不是有效图片：{exc}") from exc

    def is_valid_cached(self, path) -> bool:
        """同一文件未变动时免重复解码，降低图库多次刷新时的 CPU 和 IO。"""
        p = Path(path)
        try:
            stat = p.stat()
        except OSError:
            return False
        if stat.st_size <= 0:
            return False
        key = str(p)
        state = (stat.st_size, stat.st_mtime_ns)
        with self._lock_guard:
            old = self._verified.get(key)
            if old is not None and old[:2] == state:
                return old[2]
        try:
            self._verify_image(p)
            ok = True
        except Exception:  # noqa: BLE001
            ok = False
        with self._lock_guard:
            self._verified[key] = (state[0], state[1], ok)
            self._verified.move_to_end(key)
            while len(self._verified) > 512:
                self._verified.popitem(last=False)
        return ok

    @staticmethod
    def _referer_for(rec: ImageRecord) -> str:
        """按来源给一个合适的 Referer，降低被站点拒绝的概率。"""
        if rec.source == "bzee":
            return "https://photo.bzee.cn/"
        if rec.source == "bing":
            return "https://www.bing.com/"
        if rec.source == "qh360":
            return "https://www.360.cn/"
        return ""

    def cached_path(self, rec: ImageRecord) -> Optional[Path]:
        """按 remote_url 计算缓存路径；文件确实存在且能解码才返回。"""
        if not rec.remote_url:
            return None
        p = self.target_path(rec)
        try:
            if not p.is_file() or p.stat().st_size == 0:
                return None
        except OSError:
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
            # 用户本地文件不属于应用缓存，绝对不能因为解码失败直接删除。
            try:
                p = Path(rec.local_path).resolve()
                if p.is_relative_to(self.cache_dir.resolve()):
                    p.unlink(missing_ok=True)
            except OSError:
                pass
        p = self.cached_path(rec)
        if p:
            rec.local_path = str(p)
            return str(p)
        if rec.fallback_url:
            # 不修改共享的 record；后台下载线程可能正在读它的 remote_url。
            p2 = self.cached_path(replace(rec, remote_url=rec.fallback_url))
            if p2:
                rec.local_path = str(p2)
                return str(p2)
        return None

    def _fill_size(self, rec: ImageRecord, path: Path,
                   force_dimensions: bool = False) -> None:
        try:
            rec.file_size = path.stat().st_size
        except OSError:
            pass
        if force_dimensions or not (rec.width and rec.height):
            try:
                from PIL import Image

                with Image.open(path) as im:
                    rec.width, rec.height = im.size
            except Exception:  # noqa: BLE001
                pass

    # ---------- 维护 ----------
    def prune(self, keep: int | None = None, protect: set[Path] | None = None) -> int:
        """仅清理完整的原图缓存，忽略缩略图、下载临时文件和当前壁纸。"""
        keep = keep if keep is not None else self.max_files
        if keep <= 0:
            return 0
        excluded = set(self.protected_paths) | set(protect or ())
        files = []
        for p in self.cache_dir.glob("*/*"):
            if (p.is_file() and p.suffix.lower() not in (".part", ".tmp")
                    and p not in excluded and p.parent.name != "thumbs"):
                try:
                    files.append((p.stat().st_mtime, p))
                except OSError:
                    pass
        if len(files) + len(excluded) <= keep:
            return 0
        files.sort()
        removed = 0
        for _, p in files[:max(0, len(files) + len(excluded) - keep)]:
            try:
                # 正在写入的目标受 _download_locks 保护，清理时不碰它。
                with self._lock_guard:
                    busy = self._download_locks.get(str(p))
                if busy is not None and busy.locked():
                    continue
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed

    def cache_size(self) -> int:
        total = 0
        for p in self.cache_dir.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total

    def clear(self) -> int:
        """清理可安全删除的缓存，保留当前壁纸及正在写入的文件。"""
        removed = 0
        if not self.cache_dir.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            return removed
        for p in self.cache_dir.rglob("*"):
            if not p.is_file() or p in self.protected_paths or p.suffix in (".part", ".tmp"):
                continue
            with self._lock_guard:
                busy = self._download_locks.get(str(p))
            if busy is not None and busy.locked():
                continue
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
        return removed

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
