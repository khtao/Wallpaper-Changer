"""Bing 每日壁纸（官方 HPImageArchive 接口，无需 API Key）。"""
from __future__ import annotations

import logging
import re
from typing import List

from ..models import FetchResult, ImageRecord
from .base import Source

LOG = logging.getLogger(__name__)

# 分辨率参数：Bing 支持在 url 上拼 &w= &h=，或使用内置 _1920x1080 后缀
RESOLUTIONS = {
    "UHD": (3840, 2160),
    "1920x1080": (1920, 1080),
    "1366x768": (1366, 768),
    "1280x720": (1280, 720),
    "mobile": (1080, 1920),
}


class BingSource(Source):
    name = "bing"
    label = "Bing 每日壁纸"
    needs_network = True

    API = "https://www.bing.com/HPImageArchive.aspx"

    def fetch(self, limit: int = 30) -> FetchResult:
        n = max(1, min(int(getattr(self.settings, "count", 8)), 16))
        mkt = getattr(self.settings, "market", "zh-CN") or "zh-CN"
        params = {"format": "js", "idx": 0, "n": n, "mkt": mkt}
        result = FetchResult(source=self.name)
        try:
            resp = self.session().get(self.API, params=params, timeout=self.timeout())
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            result.error = f"Bing 请求失败：{exc}"
            LOG.warning(result.error)
            return result

        for item in data.get("images", [])[:n]:
            rec = self._parse(item)
            if rec:
                result.images.append(rec)
        if not result.images:
            result.error = "Bing 未返回任何图片"
        return result

    def _parse(self, item: dict):
        urlbase = item.get("urlbase") or item.get("url") or ""
        if not urlbase:
            return None
        if urlbase.startswith("http"):
            base = urlbase.split("&pid")[0]
            url = f"{base}&pid=hp&w=3840&h=2160&rs=1&c=4"
        else:
            base = "https://www.bing.com" + urlbase.split("&pid")[0]
            url = f"{base}_UHD.jpg&pid=hp&w=3840&h=2160&rs=1&c=4"

        w, h = RESOLUTIONS.get(getattr(self.settings, "resolution", "UHD"), (3840, 2160))
        if getattr(self.settings, "resolution", "UHD") != "UHD":
            url = f"{base}_{w}x{h}.jpg" if not urlbase.startswith("http") else (
                f"{base}&pid=hp&w={w}&h={h}&rs=1&c=4"
            )

        copyright_text = item.get("copyright") or ""
        title, author = self._split_copyright(copyright_text)
        rec = ImageRecord(
            source=self.name,
            source_id=item.get("hsh") or item.get("startdate") or url,
            remote_url=url,
            title=title or item.get("title") or "Bing 壁纸",
            author=author,
            license="© Microsoft/Bing（版权归原作者，仅供个人欣赏）",
            webpage=item.get("copyrightlink") or "https://www.bing.com",
            width=w,
            height=h,
            added_at=item.get("startdate", ""),
            tags=item.get("copyright", ""),
            extra=str(item.get("enddate", "")),
        )
        return rec

    @staticmethod
    def _split_copyright(text: str):
        """'标题（© 作者）' -> (标题, 作者)。"""
        if not text:
            return "", ""
        m = re.match(r"^(.*?)\s*[（(]\s*©?\s*(.*?)\s*[）)]\s*$", text)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        if "()" in text:  # 兜底
            parts = text.split("(", 1)
            return parts[0].strip(), parts[1].rstrip(")").strip()
        return text.strip(), ""


def preview_urls(item: dict, limit: int = 4) -> List[str]:  # 便于调试/扩展
    urlbase = item.get("urlbase", "")
    return [f"https://www.bing.com{urlbase}_320x240.jpg" for _ in range(min(limit, 1))]
