"""壁纸来源插件公共接口。"""
from __future__ import annotations

import logging
from typing import List, Optional

import requests

from ..models import FetchResult, ImageRecord

LOG = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 WallpaperChanger/1.0"
)


class Source:
    """所有来源的基类。

    子类只需实现 :meth:`fetch`，返回 :class:`ImageRecord` 列表。
    """

    name = "base"
    label = "基础来源"
    needs_network = True

    def __init__(self, settings, config=None):
        self.settings = settings
        self.config = config

    # ---------- 工具 ----------
    def session(self) -> requests.Session:
        """返回请求会话。

        若外部设置了 ``source._session``（测试用假会话），直接复用，
        便于在不联网的情况下验证解析逻辑。
        """
        injected = getattr(self, "_session", None)
        if injected is not None:
            return injected
        s = requests.Session()
        s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
        if self.config and getattr(self.config.data, "proxy", ""):
            s.proxies.update(
                {"http": self.config.data.proxy, "https": self.config.data.proxy}
            )
        return s

    def timeout(self) -> int:
        rot = getattr(self.config.data, "rotation", None) if self.config else None
        return int(getattr(rot, "timeout", 20) or 20)

    # ---------- 实现点 ----------
    def fetch(self, limit: int = 30) -> FetchResult:  # pragma: no cover - 抽象
        raise NotImplementedError

    # ---------- 描述 ----------
    def describe(self) -> str:
        return self.label


def filter_by_size(records: List[ImageRecord], min_w: int, min_h: int) -> List[ImageRecord]:
    """过滤掉已知分辨率过小的图片（未知分辨率则保留）。"""
    if not min_w or not min_h:
        return records
    out: List[ImageRecord] = []
    for r in records:
        if r.width and r.height and (r.width < min_w or r.height < min_h):
            continue
        out.append(r)
    return out
