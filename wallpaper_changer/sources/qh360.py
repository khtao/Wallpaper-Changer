"""360 壁纸来源：分类列表、分类分页、最近更新。

接口参考 https://mkblog.cn/581/ （2017）；接口可能被运营方下线，
失败时仅返回 FetchResult.error，不影响其它壁纸来源。

特别注意：API 返回的 url 才是高清图；url_thumb/url_mid 不作高清兜底。
图库缩略图由高清图 URL 派生独立缓存 key，以防双击误显示缩略图。
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List
from urllib.parse import urlparse, urlunparse

from ..models import FetchResult, ImageRecord
from .base import Source

LOG = logging.getLogger(__name__)

# 接口是历史公开接口；优先 HTTPS，只有 TLS 不可用时才回退到原有 HTTP。
CATEGORIES_API = "https://cdn.apc.360.cn/index.php"
WALLPAPER_API = "https://wallpaper.apc.360.cn/index.php"

# 早期官方分类快照，仅作离线界面默认值；联网可点击刷新。
BUILTIN_CATEGORIES: Dict[str, str] = {
    "36": "4K专区", "6": "美女模特", "30": "爱情美图", "9": "风景大片",
    "15": "小清新", "26": "动漫卡通", "11": "明星风尚", "14": "萌宠动物",
    "5": "游戏壁纸", "12": "汽车天下", "10": "炫酷时尚", "29": "月历壁纸",
    "7": "影视剧照", "13": "节日美图", "22": "军事天地", "16": "劲爆体育",
    "18": "BABY秀", "35": "文字控",
}

_RESOLUTION = re.compile(r"^\s*(\d{2,5})\s*[x×X]\s*(\d{2,5})\s*$")
_BDR_SEGMENT = re.compile(r"/bdr/__[0-9]{1,3}/")
_BDM_SEGMENT = re.compile(r"/bdm/\d+_\d+_\d+/")


def secure_image_url(url: str) -> str:
    """把 360 图片 CDN 链接统一为 HTTPS；不改变原图路径或质量。"""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        return ""
    # 接口字段始终来自 360 的图片站，避免恶意 URL 引导任意域下载。
    host = parsed.hostname.lower()
    if not (host.endswith(".qhimg.com") or host == "qhimg.com"
            or host.endswith(".qhmsg.com") or host == "qhmsg.com"):
        return ""
    try:
        if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            return ""
    except ValueError:
        return ""
    return urlunparse(parsed._replace(scheme="https", netloc=host))


def thumb_url(url: str) -> str:
    """基于高清图派生较小的独立预览地址；主图仍使用原始 URL。"""
    url = secure_image_url(url)
    if not url:
        return ""
    parsed = urlparse(url)
    path = parsed.path
    if _BDR_SEGMENT.search(path):
        path = _BDR_SEGMENT.sub("/bdm/640_360_80/", path, count=1)
    elif _BDM_SEGMENT.search(path):
        path = _BDM_SEGMENT.sub("/bdm/640_360_80/", path, count=1)
    else:
        return url  # 未知 CDN 路径不随意拼 URL
    return urlunparse(parsed._replace(path=path))


def _parse_resolution(value) -> tuple[int, int]:
    match = _RESOLUTION.match(str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _api_data(payload: object) -> list:
    if not isinstance(payload, dict):
        raise ValueError("返回内容不是 JSON 对象")
    if str(payload.get("errno", "0")) != "0":
        raise ValueError(str(payload.get("errmsg") or payload.get("errno") or "未知 API 错误"))
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("API 未返回 data 数组（可能已失效）")
    return rows


class Qh360Source(Source):
    name = "qh360"
    label = "360 高清壁纸"
    needs_network = True

    def _request(self, url: str, params: dict) -> list:
        last = None
        # 旧接口可能只支持 HTTP；不静默吞掉 HTTPS/HTTP 的双重失败。
        for endpoint in (url, url.replace("https://", "http://", 1)):
            if getattr(self, "cancelled", lambda: False)():
                raise InterruptedError("360 壁纸抓取已取消")
            try:
                response = self.session().get(endpoint, params=params, timeout=self.timeout())
                response.raise_for_status()
                return _api_data(response.json())
            except Exception as exc:  # noqa: BLE001
                last = exc
                LOG.debug("360 壁纸请求失败 %s: %s", endpoint, exc)
        raise RuntimeError(f"接口连接或解析失败：{last}")

    def discover_categories(self) -> Dict[str, str]:
        rows = self._request(CATEGORIES_API, {
            "c": "WallPaper", "a": "getAllCategoriesV2", "from": "360chrome"})
        found: Dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            cid, name = str(row.get("id", "")).strip(), str(row.get("name", "")).strip()
            if cid.isdigit() and name:
                found[cid] = name[:80]
        return found

    def _parse_image(self, item: object) -> ImageRecord | None:
        if not isinstance(item, dict):
            return None
        raw_url = item.get("url") or ""
        url = secure_image_url(raw_url)
        if not url:
            return None
        # HTTP 只是历史原图地址的备用协议；绝不使用 url_thumb 代替高清。
        fallback = (raw_url.strip() if isinstance(raw_url, str) and raw_url.startswith("http://")
                    else "")
        width, height = _parse_resolution(item.get("resolution"))
        tags = str(item.get("tag") or "")
        readable_tags = re.sub(r"_category_|_全部_|_", " ", tags).strip()
        image_id = str(item.get("id") or url)
        title = readable_tags[:100] or f"360 壁纸 {image_id}"
        return ImageRecord(
            source=self.name,
            source_id=image_id,
            remote_url=url,
            fallback_url=fallback,
            title=title,
            license="© 原图版权所有者（仅供个人使用）",
            webpage="https://www.360.cn/",
            width=width,
            height=height,
            tags=readable_tags,
            added_at=str(item.get("create_time") or ""),
        )

    def fetch(self, limit: int = 30) -> FetchResult:
        result = FetchResult(source=self.name)
        cap = max(1, min(int(limit), 120))
        page_size = max(1, min(int(getattr(self.settings, "page_size", 24) or 24), 50))
        max_pages = max(1, min(int(getattr(self.settings, "max_pages", 1) or 1), 5))
        categories = [str(c) for c in (getattr(self.settings, "categories", []) or [])
                      if str(c).isdigit()][:18]
        mode = getattr(self.settings, "mode", "category")
        # 没有选分类则自动退到最近更新；不会遍历整个 360 站点。
        targets = categories if mode == "category" and categories else [None]
        seen = set()
        errors: List[str] = []
        for cid in targets:
            for page in range(max_pages):
                if getattr(self, "cancelled", lambda: False)():
                    return result
                remain = cap - len(result.images)
                if remain <= 0:
                    break
                count = min(page_size, remain)
                args = {"c": "WallPaper", "from": "360chrome",
                        "start": page * page_size, "count": count}
                if cid is not None:
                    args.update({"a": "getAppsByCategory", "cid": cid})
                else:
                    args.update({"a": "getAppsByOrder", "order": "create_time"})
                try:
                    rows = self._request(WALLPAPER_API, args)
                except InterruptedError:
                    return result
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"分类 {cid or '最近更新'} 第 {page + 1} 页：{exc}")
                    break
                if not rows:
                    break
                for item in rows:
                    rec = self._parse_image(item)
                    if rec and rec.source_id not in seen:
                        seen.add(rec.source_id)
                        result.images.append(rec)
                        if len(result.images) >= cap:
                            break
                if len(rows) < count:
                    break
            if len(result.images) >= cap:
                break
        if errors:
            result.error = "；".join(errors[:3])
        elif not result.images:
            result.error = "360 接口未返回图片（旧接口可能已失效）"
        return result
