"""壁纸来源注册与调度。"""
from __future__ import annotations

from typing import Dict, List, Type

from .base import Source
from .bing import BingSource
from .custom_urls import CustomUrlSource
from .local import LocalSource

REGISTRY: Dict[str, Type[Source]] = {
    BingSource.name: BingSource,
    LocalSource.name: LocalSource,
    CustomUrlSource.name: CustomUrlSource,
}

# 显示名映射（来源名 -> 界面显示名）
SOURCE_LABELS: Dict[str, str] = {
    BingSource.name: BingSource.label,
    LocalSource.name: LocalSource.label,
    CustomUrlSource.name: CustomUrlSource.label,
}


def build_sources(config) -> List[Source]:
    """按配置构造已启用的来源实例。"""
    data = config.data
    sources: List[Source] = []
    if data.bing.enabled:
        sources.append(BingSource(data.bing, config))
    if data.local.enabled:
        sources.append(LocalSource(data.local, config))
    if data.custom_urls.enabled:
        sources.append(CustomUrlSource(data.custom_urls, config))
    return sources
