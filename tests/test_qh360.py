"""360 新来源离线测试：不依赖真实 API、不需要 PySide6。"""
from __future__ import annotations

import json
from dataclasses import asdict

from wallpaper_changer.config import Config, Qh360Settings
from wallpaper_changer.fetcher import Fetcher
from wallpaper_changer.models import ImageRecord
from wallpaper_changer.sources import SOURCE_LABELS, build_sources
from wallpaper_changer.sources.qh360 import (
    Qh360Source, _parse_resolution, secure_image_url, thumb_url,
)


RAW = "http://p18.qhimg.com/bdr/__85/t018fe600776261336e.jpg"


def picture(pid, w="3840x2160"):
    return {"id": str(pid), "class_id": "36", "resolution": w,
            "url": RAW.replace("336e", str(pid)), "url_thumb": "http://p18.qhimg.com/t0001.jpg",
            "tag": "_全部_ _category_风景大片_", "create_time": "2018-08-17 14:06:56"}


class Response:
    def __init__(self, data, error=False):
        self.data, self.error = data, error

    def raise_for_status(self):
        if self.error:
            raise ValueError("HTTP 403")

    def json(self):
        return self.data


class FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, dict(params), timeout))
        action = params["a"]
        key = (action, params.get("cid"), params.get("start"))
        entry = self.mapping.get(key)
        if isinstance(entry, Exception):
            raise entry
        if entry is None:
            return Response({"errno": "0", "data": []})
        return Response(entry)


def test_url_scheme_and_thumbnail_are_separate():
    original = secure_image_url(RAW)
    assert original == RAW.replace("http://", "https://")
    assert "/bdr/__85/" in original
    thumb = thumb_url(original)
    assert "/bdm/640_360_80/" in thumb
    assert thumb != original
    assert secure_image_url("https://evil.example.com/t123.jpg") == ""
    assert secure_image_url("file:///etc/passwd") == ""
    assert secure_image_url("https://p18.qhimg.com:abc/image.jpg") == ""
    assert _parse_resolution("3840x2160") == (3840, 2160)
    assert _parse_resolution("broken") == (0, 0)


def test_categories_and_error_handling():
    source = Qh360Source(Qh360Settings(enabled=True))
    fake = FakeSession({("getAllCategoriesV2", None, None): {
        "errno": "0", "data": [{"id": 36, "name": "4K专区"}, {"id": "9", "name": "风景大片"}]}})
    source._session = fake
    assert source.discover_categories() == {"36": "4K专区", "9": "风景大片"}
    assert fake.calls[0][1]["from"] == "360chrome"
    source._session = FakeSession({("getAllCategoriesV2", None, None): {
        "errno": "-1", "errmsg": "服务不可用", "data": []}})
    try:
        source.discover_categories()
        assert False, "expected error"
    except RuntimeError as e:
        assert "服务不可用" in str(e)


def test_category_paging_dedupe_and_original_path():
    settings = Qh360Settings(enabled=True, categories=["36"], mode="category",
                            max_pages=2, page_size=2)
    source = Qh360Source(settings)
    fake = FakeSession({
        ("getAppsByCategory", "36", 0): {"errno": "0", "data": [picture(1), picture(2)]},
        ("getAppsByCategory", "36", 2): {"errno": "0", "data": [picture(2), picture(3)]},
    })
    source._session = fake
    result = source.fetch(limit=10)
    assert not result.error, result.error
    assert [r.source_id for r in result.images] == ["1", "2", "3"]
    assert result.images[0].width == 3840 and result.images[0].height == 2160
    assert result.images[0].remote_url.startswith("https://")
    assert "/bdr/__85/" in result.images[0].remote_url
    assert result.images[0].fallback_url.startswith("http://")
    assert fake.calls[0][1] == {"c": "WallPaper", "from": "360chrome", "start": 0,
                                 "count": 2, "a": "getAppsByCategory", "cid": "36"}
    assert fake.calls[1][1]["start"] == 2


def test_latest_mode_and_bad_rows():
    source = Qh360Source(Qh360Settings(mode="latest", categories=["36"], page_size=3))
    fake = FakeSession({("getAppsByOrder", None, 0): {
        "errno": "0", "data": [picture(4), {"id": "5", "url": "http://badhost.example/img.jpg"}, "bad"]}})
    source._session = fake
    result = source.fetch(limit=3)
    assert len(result.images) == 1
    assert fake.calls[0][1]["a"] == "getAppsByOrder"
    assert fake.calls[0][1]["order"] == "create_time"


def test_empty_and_unavailable_api_recover():
    source = Qh360Source(Qh360Settings(categories=["9"], mode="category"))
    source._session = FakeSession({("getAppsByCategory", "9", 0): ValueError("blocked")})
    result = source.fetch()
    assert result.images == [] and "blocked" in result.error
    source._session = FakeSession({})
    assert "未返回图片" in source.fetch().error


def test_config_registered_and_preserves_existing(tmp_path):
    cfg = Config(tmp_path / "config.json")
    assert not cfg.data.qh360.enabled
    cfg.data.qh360.enabled = True
    cfg.data.qh360.categories = ["36", "9"]
    cfg.data.qh360.mode = "category"
    cfg.data.bing.enabled = False
    cfg.save()
    cfg2 = Config(tmp_path / "config.json")
    assert cfg2.data.qh360.categories == ["36", "9"]
    assert [type(src).__name__ for src in build_sources(cfg2)] == ["Qh360Source"]
    assert SOURCE_LABELS["qh360"] == "360 高清壁纸"


def test_thumb_cache_key_is_not_original_cache_key(tmp_path):
    fetcher = Fetcher(tmp_path)
    original = Qh360Source(Qh360Settings())._parse_image(picture(15))
    thumb = fetcher.thumbnail_record(original)
    full = fetcher.full_image_record(original)
    assert thumb.remote_url != full.remote_url
    assert "/bdm/" in thumb.remote_url
    assert "/bdr/" in full.remote_url
    assert thumb.fallback_url == ""
    assert full.fallback_url == original.fallback_url
    assert fetcher.target_path(full) != fetcher.target_path(thumb)
    assert original.local_path == ""


def test_http_high_resolution_fallback_cache_still_previews_full(tmp_path):
    """当 360 图片 CDN 只支持 HTTP 时，已下载的高清图不能被当缩略图。"""
    from PIL import Image
    fetcher = Fetcher(tmp_path / "cache")
    original = Qh360Source(Qh360Settings())._parse_image(picture(26))
    fallback_record = ImageRecord(source="qh360", source_id=original.source_id,
                                  remote_url=original.fallback_url)
    path = fetcher.target_path(fallback_record)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2560, 1440), "green").save(path)
    original.local_path = str(path)
    assert fetcher.resolve_full_image(original) == str(path)
    assert original.width == 2560 and original.height == 1440
