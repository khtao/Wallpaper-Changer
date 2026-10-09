"""自定义 URL 来源：每行一个图片直链 / 本地路径 / JSON 接口。

支持三种写法::

    https://example.com/a.jpg                     # 图片直链
    /home/user/Pictures/a.png                     # 本地文件
    json:https://example.com/api/wallpapers       # JSON 接口（自动提取图片链接）
    json:https://example.com/api/wallpapers#data.list   # 指定字段路径（点号分隔）
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, List
from urllib.parse import urlparse

from ..models import FetchResult, ImageRecord
from .base import Source

LOG = logging.getLogger(__name__)

# JSON 中被当作图片地址的候选字段
URL_KEYS = ("url", "image", "src", "download_url", "link", "file", "path", "full")
# JSON 中被当作标题 / 作者的候选字段
TITLE_KEYS = ("title", "name", "caption", "desc", "description")
AUTHOR_KEYS = ("author", "creator", "user", "artist", "copyright")


def _dig(data: Any, path: str) -> Any:
    """按 a.b.c 形式取嵌套字段，失败时返回原对象。"""
    if not path:
        return data
    node = data
    for part in path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _first_str(node: Any, keys: tuple) -> str:
    if isinstance(node, dict):
        for k in keys:
            v = node.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def extract_urls(data: Any, path: str = "") -> List[Any]:
    """从任意 JSON 结构中提取图片条目（字符串或含图片字段的字典）。"""
    node = _dig(data, path)
    if node is None:
        node = data
    out: List[Any] = []

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 6:
            return
        if isinstance(obj, str):
            if obj.startswith("http") and not obj.endswith((".html", ".php", ".json")):
                out.append(obj)
            return
        if isinstance(obj, dict):
            for key in URL_KEYS:
                v = obj.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    out.append(obj)
                    return
            for v in obj.values():
                walk(v, depth + 1)
            return
        if isinstance(obj, list):
            for v in obj:
                walk(v, depth + 1)

    walk(node)
    return out


class CustomUrlSource(Source):
    name = "custom_urls"
    label = "自定义图片链接"
    needs_network = True

    def fetch(self, limit: int = 200) -> FetchResult:
        result = FetchResult(source=self.name)
        urls = getattr(self.settings, "urls", []) or []
        entries = [u.strip() for u in urls if u and u.strip()]
        if not entries:
            result.error = "未配置任何链接"
            return result

        for entry in entries:
            if entry.lower().startswith("json:"):
                result.images.extend(self._from_json(entry[5:], limit))
                continue
            result.images.append(self._single(entry))
        if not result.images:
            result.error = "没有解析出任何可用图片"
        return result

    # ---------- 单条 ----------
    @staticmethod
    def _single(entry: str) -> ImageRecord:
        if entry.startswith("file://") or "://" not in entry:
            return ImageRecord(
                source="custom_urls",
                source_id=entry,
                local_path=urlparse(entry).path if entry.startswith("file://") else entry,
                title=entry.rsplit("/", 1)[-1],
                author="自定义",
                license="自定义",
                webpage=entry,
            )
        return ImageRecord(
            source="custom_urls",
            source_id=hashlib.md5(entry.encode("utf-8")).hexdigest(),
            remote_url=entry,
            title=entry.rsplit("/", 1)[-1] or entry,
            author="自定义链接",
            license="自定义",
            webpage=entry,
        )

    # ---------- JSON 接口 ----------
    def _from_json(self, spec: str, limit: int) -> List[ImageRecord]:
        url, _, field = spec.partition("#")
        url = url.strip()
        if not url:
            return []
        try:
            resp = self.session().get(url, timeout=self.timeout())
            resp.raise_for_status()
            items = extract_urls(resp.json(), field.strip())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("解析 JSON 来源失败 %s：%s", url, exc)
            return []

        records: List[ImageRecord] = []
        for item in items[:limit]:
            if isinstance(item, str):
                records.append(self._single(item))
                continue
            target = _first_str(item, URL_KEYS)
            if not target:
                continue
            records.append(
                ImageRecord(
                    source="custom_urls",
                    source_id=hashlib.md5(
                        (item.get("id") and str(item["id"]) or target).encode("utf-8")
                    ).hexdigest(),
                    remote_url=target,
                    title=_first_str(item, TITLE_KEYS) or target.rsplit("/", 1)[-1],
                    author=_first_str(item, AUTHOR_KEYS),
                    license="自定义接口",
                    webpage=_first_str(item, ("page", "webpage", "source")) or url,
                    width=int(item.get("width") or 0) if isinstance(item.get("width"), int) else 0,
                    height=int(item.get("height") or 0) if isinstance(item.get("height"), int) else 0,
                )
            )
        return records
