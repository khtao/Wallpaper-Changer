"""本地文件夹来源（可多目录、递归扫描，含 EXIF 方向无关的分辨率读取）。"""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import FetchResult, ImageRecord
from .base import Source

LOG = logging.getLogger(__name__)

IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif",
    ".tif", ".tiff", ".avif", ".jfif",
}


class LocalSource(Source):
    name = "local"
    label = "本地文件夹"
    needs_network = False

    def fetch(self, limit: int = 200) -> FetchResult:
        result = FetchResult(source=self.name)
        dirs = getattr(self.settings, "directories", []) or []
        recursive = bool(getattr(self.settings, "recursive", True))
        if not dirs:
            result.error = "未配置本地目录"
            return result

        seen = 0
        for d in dirs:
            root = Path(d).expanduser()
            if not root.exists():
                LOG.warning("本地目录不存在：%s", root)
                continue
            try:
                it = root.rglob("*") if recursive else root.glob("*")
                for p in it:
                    if seen >= limit:
                        break
                    if not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
                        continue
                    rec = self._make_record(p)
                    if rec:
                        result.images.append(rec)
                        seen += 1
            except Exception as exc:  # noqa: BLE001
                LOG.warning("扫描 %s 失败：%s", root, exc)
        if not result.images:
            result.error = "本地目录中没有找到图片"
        return result

    def _make_record(self, path: Path):
        try:
            stat = path.stat()
        except OSError:
            return None
        w, h = 0, 0
        try:
            from PIL import Image  # 本地依赖可选

            with Image.open(path) as im:
                w, h = im.size
        except Exception:  # noqa: BLE001
            pass
        return ImageRecord(
            source=self.name,
            source_id=str(path.resolve()),
            local_path=str(path.resolve()),
            title=path.stem,
            author="本地文件",
            license="本地文件",
            webpage=str(path.parent),
            width=w,
            height=h,
            file_size=stat.st_size,
        )
