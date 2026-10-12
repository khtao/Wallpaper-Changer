"""Bzee 图库（photo.bzee.cn）壁纸来源。

站点结构（实测于 2026-10，原图目录经用户确认为 pic）：
  - 分类页：https://photo.bzee.cn/desktop/{slug}  手机：/mobile/{slug}
  - 缩略图：https://imgtu.bzee.cn/photo/thumb/{大类}/{子类}/{ID}_{宽}x{高}.webp
  - 原  图：https://imgtu.bzee.cn/photo/pic/{大类}/{子类}/{ID}.webp
    （即：thumb -> pic，并去掉 _{宽}x{高} 后缀；早期曾误用 /photo/，拿不到高清图）

该站没有公开 API，只能解析 HTML；且对部分出口 IP 有 WAF 拦截（返回 403）。
因此本来源做到三点：
  1) 分类可自动发现：抓首页解析出真实链接，不依赖硬编码的 slug；
  2) 原图优先、缩略图兜底：原图下载失败会自动改用缩略图；
  3) 失败只报错不抛异常：主流程会继续走其它来源。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from ..models import FetchResult, ImageRecord
from .base import Source

LOG = logging.getLogger(__name__)

SITE = "https://photo.bzee.cn"
IMG_HOST = "https://imgtu.bzee.cn"

# 预设分类：slug -> 中文名。
# 站点未提供 API，slug 以「运行时自动发现」为准（抓首页解析真实链接）；
# 这张表用于界面展示与默认值，若站点改版也能通过自动发现拿到新链接。
DESKTOP_CATEGORIES: Dict[str, str] = {
    "ziran": "自然风景",
    "katong": "卡通动漫",
    "renxiang": "人像写真",
    "tiyu": "体育运动",
    "yuzhou": "宇宙科幻",
    "jixie": "机械科技",
    "dongwu": "动物萌宠",
    "qinggan": "情感唯美",
    "yingshi": "影视剧照",
    "youxi": "游戏原画",
    "huihua": "绘画创意",
    "jianyue": "简约色彩",
    "jingwu": "静物特写",
}

MOBILE_CATEGORIES: Dict[str, str] = {
    "ziran": "自然风景",
    "renwu": "人物画照",
    "katong": "卡通动漫",
    "qinggan": "情感文字",
    "jianyue": "简约色彩",
    "qingliang": "清凉夏日",
}

# 站点首页给出的热门搜索词，作为关键词输入的候选提示
POPULAR_KEYWORDS = [
    "风景", "动漫", "美女", "游戏", "宇宙", "帅哥",
    "汽车", "AI人物", "城市", "动物", "宽屏",
]

# 图片 URL：https://imgtu.bzee.cn/photo/thumb/renxiang/yanzhi/13900ZMBZ_c76f0b70_400x711.webp
_IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
_SRC_RE = re.compile(r"""\b(?:src|data-src|data-original)\s*=\s*["']([^"']+)["']""", re.I)
_ALT_RE = re.compile(r"""\balt\s*=\s*["']([^"']*)["']""", re.I)
# 详情页/图集链接
_LINK_RE = re.compile(r"""href\s*=\s*["'](/[^"']*?/photo[^"']*?)["']""", re.I)
# 分类链接：/desktop/ziran 、/mobile/katong
_CAT_RE = re.compile(r"""href\s*=\s*["']/(desktop|mobile)/([a-z0-9_\-]+)["']""", re.I)
# ---------- 分页链接 ----------
_PAGE_RE = re.compile(r"""href\s*=\s*["']([^"']*?(?:page|p)=(\d+)[^"']*?)["']""", re.I)


# 原图所在目录（用户实测确认：/photo/pic/... 才是高清原图）
ORIGIN_DIR = "pic"
THUMB_DIR = "thumb"
_SIZE_SUFFIX_RE = re.compile(r"_\d+x\d+(?=\.[A-Za-z0-9]+$)")


def to_original(url: str) -> str:
    """缩略图 URL -> 高清原图 URL。

    /photo/thumb/renxiang/yanzhi/ID_400x711.webp
      -> /photo/pic/renxiang/yanzhi/ID.webp

    历史写法 /photo/renxiang/...（少了 pic 一层）也一并补全为
    /photo/pic/renxiang/... —— 注意是「插入」pic，不能把分类目录替换掉。
    """
    u = _SIZE_SUFFIX_RE.sub("", url)
    if f"/photo/{ORIGIN_DIR}/" in u:
        return u
    if f"/photo/{THUMB_DIR}/" in u:
        return u.replace(f"/photo/{THUMB_DIR}/", f"/photo/{ORIGIN_DIR}/", 1)
    # /photo/{分类}/... -> /photo/pic/{分类}/...
    return re.sub(r"/photo/(?!%s/|%s/)" % (ORIGIN_DIR, THUMB_DIR),
                  f"/photo/{ORIGIN_DIR}/", u, count=1)


def to_thumb(url: str, size: str = "400x711") -> str:
    """原图 URL -> 缩略图 URL（用于兜底）。"""
    if f"/photo/{THUMB_DIR}/" in url:
        return url
    u = re.sub(r"/photo/(?:%s|[^/]+)/" % ORIGIN_DIR, f"/photo/{THUMB_DIR}/", url, count=1)
    stem, _, ext = u.rpartition(".")
    return f"{stem}_{size}.{ext}" if stem else u


def is_original(url: str) -> bool:
    return f"/photo/{ORIGIN_DIR}/" in url


def image_id(url: str) -> str:
    """取 URL 里的图片唯一 ID（去掉尺寸后缀，保证原图/缩略图同一 ID）。"""
    name = url.rsplit("/", 1)[-1]
    name = re.sub(r"_\d+x\d+(?=\.[A-Za-z0-9]+$)", "", name)
    return re.sub(r"\.[A-Za-z0-9]+$", "", name)


def category_of(url: str) -> str:
    """从图片 URL 里取大类目录（如 renxiang）。"""
    m = re.search(r"/photo/(?:thumb/)?([^/]+)/", url)
    return m.group(1) if m else ""


def parse_gallery(html: str) -> List[Tuple[str, str]]:
    """解析列表页 HTML，返回 [(图片URL, 标题), ...]（已去重、保持顺序）。"""
    out: List[Tuple[str, str]] = []
    seen = set()
    for tag in _IMG_TAG_RE.findall(html or ""):
        m = _SRC_RE.search(tag)
        if not m:
            continue
        url = m.group(1).strip()
        if "imgtu.bzee.cn" not in url and not url.startswith("/photo/"):
            continue
        if not re.search(r"\.(webp|jpg|jpeg|png)$", url, re.I):
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = IMG_HOST + url
        am = _ALT_RE.search(tag)
        title = (am.group(1) if am else "").strip()
        if url in seen:
            continue
        seen.add(url)
        out.append((url, title))
    return out


def parse_categories(html: str) -> Dict[str, Dict[str, str]]:
    """从首页/导航解析分类链接，返回 {'desktop': {slug: 名称}, 'mobile': {...}}。

    名称优先用页面上的中文，取不到则用 slug。
    """
    found: Dict[str, Dict[str, str]] = {"desktop": {}, "mobile": {}}
    for lib, slug in _CAT_RE.findall(html or ""):
        found.setdefault(lib, {})[slug.lower()] = slug
    # 尝试把链接文字作为中文名：<a href="/desktop/ziran">自然风景</a>
    for lib, slug, text in re.findall(
        r"""<a\b[^>]*href\s*=\s*["']/(desktop|mobile)/([a-z0-9_\-]+)["'][^>]*>(.*?)</a>""",
        html or "", re.I | re.S,
    ):
        name = re.sub(r"<[^>]+>", "", text).strip()
        if name and len(name) <= 12:
            found.setdefault(lib, {})[slug.lower()] = name
    return found


class BzeeSource(Source):
    """Bzee 图库：按类别 / 关键词抓取 4K 壁纸。"""

    name = "bzee"
    label = "Bzee 图库"
    needs_network = True

    # 网络偶发失败较多，列表页请求默认重试 2 次（403 不重试）
    retries = 2
    retry_delay = 0.6

    # ---------- URL ----------
    def category_url(self, slug: str = "", page: int = 1) -> str:
        lib = getattr(self.settings, "library", "desktop") or "desktop"
        base = f"{SITE}/{lib}"
        if slug:
            base = f"{base}/{slug}"
        if page > 1:
            base = f"{base}?page={page}"
        return base

    # ---------- 抓取 ----------
    def fetch(self, limit: int = 30) -> FetchResult:
        result = FetchResult(source=self.name)
        slugs = list(getattr(self.settings, "categories", []) or [])
        pages = max(1, min(int(getattr(self.settings, "max_pages", 1) or 1), 5))
        keywords = [k.strip() for k in (getattr(self.settings, "keywords", []) or []) if k.strip()]
        errors: List[str] = []

        targets: List[str] = []
        if slugs:
            for s in slugs:
                for p in range(1, pages + 1):
                    targets.append(self.category_url(s, p))
        else:
            # 未指定分类：抓图库首页（含各分类的最新图）
            for p in range(1, pages + 1):
                targets.append(self.category_url("", p))

        seen: set = set()
        for url in targets:
            if getattr(self, "cancelled", lambda: False)():
                break
            if len(result.images) >= limit:
                break
            html = self._get(url, errors)
            if not html:
                continue
            new = 0
            for img_url, title in parse_gallery(html):
                if len(result.images) >= limit:
                    break
                rec = self._make_record(img_url, title)
                if rec is None or rec.source_id in seen:
                    continue
                if keywords and not any(k in rec.title for k in keywords):
                    continue
                seen.add(rec.source_id)
                result.images.append(rec)
                new += 1
            # 该页没有新图，后续页大概率也没有，提前结束
            if new == 0 and url != targets[0]:
                break

        if not result.images:
            result.error = "；".join(errors) or (
                "未抓到图片：请检查分类/关键词是否过窄，"
                "或该站点对你的网络出口返回了 403"
            )
        elif errors:
            LOG.warning("Bzee 部分请求失败：%s", "；".join(errors))
        return result

    def _get(self, url: str, errors: List[str]) -> str:
        """抓取一个页面；网络抖动时自动重试，403 判定为不可恢复直接放弃。"""
        last = ""
        for attempt in range(1, self.retries + 1):
            if getattr(self, "cancelled", lambda: False)():
                return ""
            try:
                resp = self.session().get(
                    url, timeout=self.timeout(),
                    headers={
                        "Referer": SITE + "/",
                        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    },
                )
                if resp.status_code == 403:
                    # WAF 拦截，重试没有意义
                    errors.append("站点拒绝了请求（403），可能是网络出口被拦")
                    return ""
                if resp.status_code != 200:
                    last = f"HTTP {resp.status_code}"
                else:
                    resp.encoding = resp.encoding or "utf-8"
                    return resp.text
            except Exception as exc:  # noqa: BLE001
                last = type(exc).__name__
                LOG.debug("Bzee 请求失败（第 %d/%d 次）%s: %s",
                          attempt, self.retries, url, exc)
            if attempt < self.retries:
                time.sleep(self.retry_delay * attempt)
        errors.append(f"{url} -> {last}")
        return ""

    def _make_record(self, img_url: str, title: str) -> Optional[ImageRecord]:
        iid = image_id(img_url)
        if not iid:
            return None
        prefer = getattr(self.settings, "image_size", "origin") or "origin"
        if prefer == "thumb":
            # 只要缩略图：以缩略图为主，原图为兜底
            remote = to_thumb(img_url)
            fallback = to_original(img_url)
        else:
            # 默认：高清原图为主（/photo/pic/），缩略图兜底
            remote = to_original(img_url)
            fallback = to_thumb(img_url)
            if remote == fallback:      # 理论上不会相同，兜底防呆
                fallback = to_thumb(remote)

        cat = category_of(img_url)
        lib = getattr(self.settings, "library", "desktop")
        names = DESKTOP_CATEGORIES if lib == "desktop" else MOBILE_CATEGORIES
        cat_name = names.get(cat, cat)

        return ImageRecord(
            source=self.name,
            source_id=iid,
            remote_url=remote,
            fallback_url=fallback,
            title=title or f"Bzee {cat_name}".strip(),
            author="",
            license="Bzee 图库（版权归原作者，仅供个人欣赏）",
            webpage=self.category_url(cat) if cat else SITE,
            tags=cat_name,
            extra=cat,
        )

    # ---------- 分类发现 ----------
    def discover_categories(self) -> Dict[str, Dict[str, str]]:
        """联网抓取首页，返回站点真实存在的分类链接。"""
        try:
            resp = self.session().get(
                SITE, timeout=self.timeout(),
                headers={"Referer": SITE + "/", "Accept-Language": "zh-CN,zh;q=0.9"},
            )
            if resp.status_code != 200:
                return {}
            return parse_categories(resp.text)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("Bzee 分类发现失败：%s", exc)
            return {}

    def describe(self) -> str:
        lib = getattr(self.settings, "library", "desktop")
        slugs = getattr(self.settings, "categories", []) or []
        lib_name = "桌面图库" if lib == "desktop" else "手机图库"
        if not slugs:
            return f"{self.label}（{lib_name}·全部）"
        names = DESKTOP_CATEGORIES if lib == "desktop" else MOBILE_CATEGORIES
        return f"{self.label}（{lib_name}·{'/'.join(names.get(s, s) for s in slugs)}）"


def _abs(url: str) -> str:  # 便于扩展：站内相对链接转绝对
    return urljoin(SITE, url)
