"""数据模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ImageRecord:
    """一张壁纸的元数据。"""

    source: str                 # bing / local / custom_urls
    source_id: str              # 来源侧唯一 ID（本地为文件绝对路径）
    remote_url: str = ""        # 远程下载地址（本地为空）
    fallback_url: str = ""      # 远程地址失败时的备用地址（如 Bzee 的缩略图）
    local_path: str = ""        # 本机已落盘的图片路径
    title: str = ""
    author: str = ""
    license: str = ""
    webpage: str = ""           # 来源页面（如 Bing 的 copyrightlink）
    width: int = 0
    height: int = 0
    file_size: int = 0
    tags: str = ""
    is_favorite: bool = False
    favorite_at: str = ""
    added_at: str = ""
    last_used_at: str = ""
    used_count: int = 0
    id: Optional[int] = None
    extra: str = ""

    @property
    def display_title(self) -> str:
        return self.title or self.source_id or "未命名"

    @property
    def resolution(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "未知"

    @property
    def source_name(self) -> str:
        """来源的中文显示名（如 Bing 每日一图）。"""
        try:
            from wallpaper_changer.sources import SOURCE_LABELS
        except Exception:  # noqa: BLE001
            return self.source
        return SOURCE_LABELS.get(self.source, self.source)


@dataclass
class FetchResult:
    """一次来源抓取的结果。"""

    images: list = field(default_factory=list)
    error: str = ""
    source: str = ""
