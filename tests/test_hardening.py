"""Reliability regression tests, no GUI required.

python -m pytest tests/test_hardening.py -q
"""
from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wallpaper_changer.fetcher import Fetcher, MAX_DOWNLOAD_BYTES
from wallpaper_changer.models import ImageRecord


def jpeg_bytes(size=(80, 45)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "orange").save(out, "JPEG")
    return out.getvalue()


class FakeResp:
    def __init__(self, data: bytes, headers=None):
        self.data = data
        self.headers = headers or {"Content-Type": "image/jpeg"}

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.data

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_atomic_cache_and_fallback_actual_dimensions(tmp_path, monkeypatch):
    data = jpeg_bytes((400, 711))
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("original.jpg"):
            raise ConnectionError("offline")
        return FakeResp(data)

    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    f = Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
    rec = ImageRecord("bing", "1", remote_url="https://example.com/original.jpg",
                      fallback_url="https://example.com/thumb.jpg", width=3840, height=2160)
    path = Path(f.ensure_local(rec))
    assert path.exists() and path.suffix == ".jpg"
    assert rec.width == 400 and rec.height == 711
    assert not list(path.parent.glob("*.part"))
    assert not list(path.parent.glob("*.tmp"))
    assert calls == [rec.remote_url, rec.fallback_url]


def test_oversized_content_is_rejected_without_partial_file(tmp_path, monkeypatch):
    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get",
                        lambda *a, **kw: FakeResp(b"x", {
                            "Content-Length": str(MAX_DOWNLOAD_BYTES + 1),
                            "Content-Type": "image/jpeg"}))
    f = Fetcher(tmp_path / "cache", retries=1)
    rec = ImageRecord("bing", "huge", remote_url="https://example.com/huge.jpg")
    with pytest.raises(RuntimeError, match="上限"):
        f.ensure_local(rec)
    assert not list(f.cache_dir.rglob("*.part"))
    assert not f.target_path(rec).exists()


def test_no_remote_cache_pollution_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get",
                        lambda *a, **kw: FakeResp(b"<html>Access denied</html>",
                                                   {"Content-Type": "text/html"}))
    f = Fetcher(tmp_path / "cache", retries=1)
    rec = ImageRecord("bing", "bad", remote_url="https://example.com/bad.jpg")
    with pytest.raises(RuntimeError):
        f.ensure_local(rec)
    assert not f.target_path(rec).exists()
    assert not list(f.cache_dir.rglob("*.part"))


def test_cancelled_download_cleans_temp(tmp_path, monkeypatch):
    cancelled = {"now": False}
    class SlowResp(FakeResp):
        def iter_content(self, chunk):
            yield b"abc"
            cancelled["now"] = True
            yield b"xyz"
    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get",
                        lambda *a, **kw: SlowResp(b""))
    f = Fetcher(tmp_path / "cache", retries=3)
    rec = ImageRecord("bing", "cancel", remote_url="https://example.com/c.jpg")
    with pytest.raises(InterruptedError):
        f.ensure_local(rec, cancelled=lambda: cancelled["now"])
    assert not list(f.cache_dir.rglob("*.part"))
    assert not f.target_path(rec).exists()


def test_same_url_requests_download_only_once(tmp_path, monkeypatch):
    lock = threading.Lock()
    count = [0]
    payload = jpeg_bytes()

    def get(*a, **kw):
        with lock:
            count[0] += 1
        time.sleep(.05)
        return FakeResp(payload)

    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    f = Fetcher(tmp_path / "cache", retries=1)
    rec1 = ImageRecord("bing", "same", remote_url="https://example.com/same.jpg")
    rec2 = ImageRecord("bing", "same", remote_url="https://example.com/same.jpg")
    outputs = []
    a = threading.Thread(target=lambda: outputs.append(f.ensure_local(rec1)))
    b = threading.Thread(target=lambda: outputs.append(f.ensure_local(rec2)))
    a.start(); b.start(); a.join(); b.join()
    assert len(outputs) == 2 and outputs[0] == outputs[1]
    assert count[0] == 1


def test_prune_preserves_thumbnails_and_active_wallpaper(tmp_path):
    f = Fetcher(tmp_path / "cache", max_files=2)
    orig = f.cache_dir / "bing"
    orig.mkdir()
    for i in range(4):
        (orig / f"{i}.jpg").write_bytes(bytes([i]))
        time.sleep(.002)
    thumbs = f.cache_dir / "thumbs"
    thumbs.mkdir()
    (thumbs / "example.jpg").write_bytes(b"thumbnail")
    (orig / "busy.part").write_bytes(b"partial")
    f.protected_paths = {orig / "0.jpg"}
    assert f.prune() == 2
    assert (orig / "0.jpg").exists()
    assert (thumbs / "example.jpg").exists()
    assert (orig / "busy.part").exists()


def test_damaged_user_original_is_not_deleted(tmp_path):
    original = tmp_path / "user.jpg"
    original.write_text("this is not a real image")
    f = Fetcher(tmp_path / "cache")
    rec = ImageRecord("local", "local", local_path=str(original))
    assert f.resolve_local(rec) is None
    assert original.exists()


def test_bzee_gallery_thumb_and_view_original_are_independent(tmp_path, monkeypatch):
    from wallpaper_changer.sources.bzee import to_original
    image_url = "https://imgtu.bzee.cn/photo/thumb/ziran/fengjing/PIC_400x711.webp"
    full_url = to_original(image_url)
    thumb_bytes = jpeg_bytes((400, 711))
    original_bytes = jpeg_bytes((2000, 1200))
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return FakeResp(thumb_bytes if url == image_url else original_bytes)

    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    f = Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
    rec = ImageRecord("bzee", "PIC", remote_url=full_url, fallback_url=image_url)
    thumb_rec = f.thumbnail_record(rec)
    thumb_path = Path(f.ensure_local(thumb_rec))
    assert rec.local_path == ""    # 图库缩略图不再篡改原图记录
    assert f.resolve_full_image(rec) is None
    full_path = Path(f.ensure_full_image(rec))
    assert thumb_path != full_path
    assert rec.local_path == str(full_path)
    assert (rec.width, rec.height) == (2000, 1200)
    assert calls == [image_url, full_url]
    assert f.resolve_full_image(rec) == str(full_path)


def test_bzee_thumb_mode_still_opens_original(tmp_path, monkeypatch):
    from wallpaper_changer.sources.bzee import to_original
    thumb_url = "https://imgtu.bzee.cn/photo/thumb/ziran/abc/ID_400x711.webp"
    orig_url = to_original(thumb_url)
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return FakeResp(jpeg_bytes((1800, 1200)))

    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    f = Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
    rec = ImageRecord("bzee", "ID", remote_url=thumb_url, fallback_url=orig_url)
    full = Path(f.ensure_full_image(rec))
    assert full == f.target_path(f.full_image_record(rec))
    assert calls == [orig_url]
    assert rec.width == 1800


def test_legacy_fallback_cache_does_not_masquerade_as_original(tmp_path, monkeypatch):
    from wallpaper_changer.sources.bzee import to_original
    thumb_url = "https://imgtu.bzee.cn/photo/thumb/ziran/abc/ID_400x711.webp"
    orig_url = to_original(thumb_url)
    f = Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
    rec = ImageRecord("bzee", "ID", remote_url=orig_url, fallback_url=thumb_url)
    old_cache = f.target_path(rec)
    old_cache.parent.mkdir(parents=True, exist_ok=True)
    old_cache.write_bytes(jpeg_bytes((400, 711)))
    rec.local_path = str(old_cache)
    assert f.resolve_full_image(rec) is None
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return FakeResp(jpeg_bytes((2560, 1440)))
    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    assert f.ensure_full_image(rec) == str(old_cache)
    assert calls == [orig_url]
    assert rec.width == 2560 and rec.height == 1440


def test_full_image_failure_never_silently_downgrades_to_thumb(tmp_path, monkeypatch):
    from wallpaper_changer.sources.bzee import to_original
    thumb_url = "https://imgtu.bzee.cn/photo/thumb/ziran/abc/ID_400x711.webp"
    orig_url = to_original(thumb_url)
    f = Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
    rec = ImageRecord("bzee", "ID", remote_url=orig_url, fallback_url=thumb_url)
    thumb_rec = f.thumbnail_record(rec)

    def get(url, **kwargs):
        if url == orig_url:
            raise ConnectionError("origin is unavailable")
        return FakeResp(jpeg_bytes((400, 711)))

    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    thumb_path = f.ensure_local(thumb_rec)
    with pytest.raises(RuntimeError, match="下载失败"):
        f.ensure_full_image(rec)
    assert Path(thumb_path).exists()
    assert rec.local_path == ""
    assert f.resolve_full_image(rec) is None


def test_deleted_qthread_wrapper_is_ignored(monkeypatch):
    import sys
    from types import SimpleNamespace
    from wallpaper_changer.qt_utils import stop_thread, thread_is_running

    class DeletedTask:
        def isRunning(self):
            raise RuntimeError("Internal C++ object already deleted")
        def requestInterruption(self):
            raise RuntimeError("Internal C++ object already deleted")

    monkeypatch.setitem(sys.modules, "shiboken6", SimpleNamespace(isValid=lambda task: False))
    task = DeletedTask()
    assert thread_is_running(task) is False
    assert stop_thread(task) is None


def test_general_download_fallback_uses_its_own_cache_key(tmp_path, monkeypatch):
    """即使是轮换引擎发起的下载，缩略图兜底也不能污染原图缓存。"""
    thumb_bytes = jpeg_bytes((400, 711))
    orig = "https://imgtu.bzee.cn/photo/pic/a/b/X.webp"
    fallback = "https://imgtu.bzee.cn/photo/thumb/a/b/X_400x711.webp"
    def get(url, **kwargs):
        if url == orig:
            raise ConnectionError("original blocked")
        return FakeResp(thumb_bytes)
    monkeypatch.setattr("wallpaper_changer.fetcher.requests.get", get)
    f = Fetcher(tmp_path / "cache", retries=1, retry_delay=0)
    rec = ImageRecord("bzee", "X", remote_url=orig, fallback_url=fallback)
    assert Path(f.ensure_local(rec)).exists()
    assert rec.local_path == str(f.target_path(ImageRecord("bzee", "X", remote_url=fallback)))
    assert not f.target_path(rec).exists()
    assert f.resolve_full_image(rec) is None
