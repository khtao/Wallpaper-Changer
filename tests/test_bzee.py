"""Bzee 图库来源解析单测（全部用 mock HTML，不联网）。

沙盒出口被 photo.bzee.cn 的 WAF 拦截（403），无法实测抓取，
因此这里用构造的 HTML 验证解析规则是否正确。

运行：  python tests/test_bzee.py
        或 pytest tests/test_bzee.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wallpaper_changer.sources.bzee import (  # noqa: E402
    DESKTOP_CATEGORIES, MOBILE_CATEGORIES, POPULAR_KEYWORDS, BzeeSource,
    category_of, image_id, is_original, parse_categories, parse_gallery,
    to_original, to_thumb,
)

# 构造一段接近真实的列表页 HTML
GALLERY_HTML = """
<html><body>
<div class="list">
  <a href="/desktop/renxiang"><img src="https://imgtu.bzee.cn/photo/thumb/renxiang/yanzhi/13900ZMBZ_c76f0b70_400x711.webp"
     alt="颜值爆表的 国内女生 脸部特写写真 4K超清画质 美女壁纸"></a>
  <a href="/desktop/ziran"><img data-src="https://imgtu.bzee.cn/photo/thumb/ziran/jianzhu/12333xDSJe8DYH2Vld5ABINIr_400x711.webp"
     alt="超清4K城市风光 城市摄影 高清素材 电脑桌面壁纸"></a>
  <img src="/photo/thumb/yuzhou/xingkong/6000XKYZ_HAI_400x711.webp" alt="银河星云倒映湖面 星空全景4K壁纸">
  <img src="https://cdn.example.com/logo.png" alt="站外图片应被忽略">
  <img src="https://imgtu.bzee.cn/photo/thumb/renxiang/yanzhi/13900ZMBZ_c76f0b70_400x711.webp"
       alt="重复图片应被去重">
</div>
</body></html>
"""

NAV_HTML = """
<nav>
  <a href="/desktop/ziran">自然风景</a>
  <a href="/desktop/katong">卡通动漫</a>
  <a href="/desktop/renxiang">人像写真</a>
  <a href="/desktop/yuzhou">宇宙科幻</a>
  <a href="/mobile/ziran">自然风景</a>
  <a href="/mobile/renwu">人物画照</a>
</nav>
"""


def test_to_original_uses_pic_dir():
    """原图目录必须是 /photo/pic/（用户实测确认，写成 /photo/ 拿不到高清图）。"""
    thumb = ("https://imgtu.bzee.cn/photo/thumb/renxiang/yanzhi/"
             "13930ZMBZ_9d086dc0_400x711.webp")
    origin = ("https://imgtu.bzee.cn/photo/pic/renxiang/yanzhi/"
              "13930ZMBZ_9d086dc0.webp")
    assert to_original(thumb) == origin
    assert is_original(origin) and not is_original(thumb)
    # 原图再转缩略图可回到 thumb
    assert to_thumb(origin) == thumb
    # 已是缩略图则原样返回
    assert to_thumb(thumb) == thumb
    # 已是原图不再二次转换（避免 /photo/pic/ 被误改）
    assert to_original(origin) == origin
    # 早期错误写法 /photo/ 也应被纠偏到 /photo/pic/
    legacy = ("https://imgtu.bzee.cn/photo/renxiang/yanzhi/"
              "13930ZMBZ_9d086dc0.webp")
    assert to_original(legacy) == origin


def test_image_id_ignores_size_suffix():
    thumb = ("https://imgtu.bzee.cn/photo/thumb/renxiang/yanzhi/"
             "13900ZMBZ_c76f0b70_400x711.webp")
    origin = ("https://imgtu.bzee.cn/photo/renxiang/yanzhi/"
              "13900ZMBZ_c76f0b70.webp")
    assert image_id(thumb) == image_id(origin) == "13900ZMBZ_c76f0b70"


def test_category_of():
    url = "https://imgtu.bzee.cn/photo/thumb/yuzhou/xingkong/X_400x711.webp"
    assert category_of(url) == "yuzhou"
    assert category_of("https://example.com/a.jpg") == ""


def test_parse_gallery_filters_and_dedups():
    items = parse_gallery(GALLERY_HTML)
    urls = [u for u, _ in items]
    # 3 张站内图（第 4 张重复、第 5 张站外被忽略）
    assert len(items) == 3, urls
    assert all("imgtu.bzee.cn" in u for u in urls)
    assert not any("cdn.example.com" in u for u in urls)
    # 相对路径需补全为绝对地址
    assert any(u.startswith("https://imgtu.bzee.cn/photo/thumb/yuzhou/") for u in urls)
    # 标题取自 alt
    titles = [t for _, t in items]
    assert any("银河星云" in t for t in titles)
    # 顺序保持、无重复
    assert len(set(urls)) == len(urls)


def test_parse_gallery_empty():
    assert parse_gallery("") == []
    assert parse_gallery("<html><body>无图片</body></html>") == []


def test_parse_categories():
    cats = parse_categories(NAV_HTML)
    assert cats["desktop"]["ziran"] == "自然风景"
    assert cats["desktop"]["yuzhou"] == "宇宙科幻"
    assert cats["mobile"]["renwu"] == "人物画照"
    assert set(cats["desktop"]) == {"ziran", "katong", "renxiang", "yuzhou"}


def test_parse_categories_no_href():
    assert parse_categories("<html>无分类</html>") == {"desktop": {}, "mobile": {}}


def test_source_urls_and_records():
    from wallpaper_changer.config import BzeeSettings

    s = BzeeSettings(library="desktop", categories=["ziran"], max_pages=2)
    src = BzeeSource(s)
    assert src.category_url("ziran") == "https://photo.bzee.cn/desktop/ziran"
    assert src.category_url("ziran", 2) == "https://photo.bzee.cn/desktop/ziran?page=2"
    assert src.category_url("") == "https://photo.bzee.cn/desktop"

    s2 = BzeeSettings(library="mobile", categories=[])
    src2 = BzeeSource(s2)
    assert src2.category_url() == "https://photo.bzee.cn/mobile"

    # 原图优先 + 缩略图兜底
    rec = src._make_record(
        "https://imgtu.bzee.cn/photo/thumb/ziran/jianzhu/12333ABC_400x711.webp",
        "超清4K城市风光 城市摄影",
    )
    assert rec is not None
    # 主地址必须是 /photo/pic/ 高清原图
    assert "/photo/pic/" in rec.remote_url, rec.remote_url
    assert "/photo/thumb/" not in rec.remote_url
    # 兜底为缩略图
    assert "/photo/thumb/" in rec.fallback_url, rec.fallback_url
    assert rec.source == "bzee"
    assert rec.tags == "自然风景"

    # 缩略图模式
    s3 = BzeeSettings(image_size="thumb")
    src3 = BzeeSource(s3)
    rec3 = src3._make_record(
        "https://imgtu.bzee.cn/photo/thumb/ziran/jianzhu/12333ABC_400x711.webp", "t")
    assert "/photo/thumb/" in rec3.remote_url
    assert "/photo/pic/" in rec3.fallback_url


def test_source_reports_error_when_blocked():
    """403 / 网络异常时不应抛异常，只返回 error，让主流程继续走其它来源。"""

    class FakeResp:
        status_code = 403
        text = ""
        encoding = "utf-8"

    class FakeSession:
        def get(self, *a, **k):
            return FakeResp()

    from wallpaper_changer.config import BzeeSettings

    src = BzeeSource(BzeeSettings(enabled=True))
    src._session = FakeSession()
    res = src.fetch(limit=10)
    assert res.images == []
    assert "403" in res.error


def test_source_fetch_with_mock_html():
    """用 mock 的 HTML 走完整 fetch 流程（含关键词过滤与去重）。"""
    from wallpaper_changer.config import BzeeSettings

    class FakeResp:
        status_code = 200
        text = GALLERY_HTML
        encoding = "utf-8"

    class FakeSession:
        def get(self, *a, **k):
            return FakeResp()

    s = BzeeSettings(enabled=True, library="desktop", categories=["ziran"],
                     max_pages=1)
    src = BzeeSource(s)
    src._session = FakeSession()

    res = src.fetch(limit=10)
    assert not res.error, res.error
    assert len(res.images) == 3
    # 全部都带 remote_url 与 fallback_url
    assert all(r.remote_url and r.fallback_url for r in res.images)

    # 关键词过滤：只保留标题含「星云」的
    s.keywords = ["星云"]
    res2 = src.fetch(limit=10)
    assert len(res2.images) == 1
    assert "银河星云" in res2.images[0].title


def test_categories_tables_exist():
    assert len(DESKTOP_CATEGORIES) >= 13
    assert len(MOBILE_CATEGORIES) >= 6
    assert POPULAR_KEYWORDS and "风景" in POPULAR_KEYWORDS
    # 名称应为中文，slug 应为英文
    for slug, name in DESKTOP_CATEGORIES.items():
        assert slug.isascii(), slug
        assert not name.isascii(), name


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  ✗ {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
