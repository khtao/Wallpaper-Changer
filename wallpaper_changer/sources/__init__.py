"""壁纸来源注册与调度。"""
from __future__ import annotations

from typing import Dict, List, Type

from .base import Source
from .bing import BingSource
from .bzee import BzeeSource
from .custom_urls import CustomUrlSource
from .local import LocalSource
from .qh360 import Qh360Source

REGISTRY: Dict[str, Type[Source]] = {
    BingSource.name: BingSource,
    BzeeSource.name: BzeeSource,
    Qh360Source.name: Qh360Source,
    LocalSource.name: LocalSource,
    CustomUrlSource.name: CustomUrlSource,
}

# 显示名映射（来源名 -> 界面显示名）
SOURCE_LABELS: Dict[str, str] = {
    BingSource.name: BingSource.label,
    BzeeSource.name: BzeeSource.label,
    Qh360Source.name: Qh360Source.label,
    LocalSource.name: LocalSource.label,
    CustomUrlSource.name: CustomUrlSource.label,
}


def build_sources(config) -> List[Source]:
    """按配置构造已启用的来源实例。"""
    data = config.data
    sources: List[Source] = []
    if data.bing.enabled:
        sources.append(BingSource(data.bing, config))
    if getattr(data, "bzee", None) and data.bzee.enabled:
        sources.append(BzeeSource(data.bzee, config))
    if getattr(data, "qh360", None) and data.qh360.enabled:
        sources.append(Qh360Source(data.qh360, config))
    if data.local.enabled:
        sources.append(LocalSource(data.local, config))
    if data.custom_urls.enabled:
        sources.append(CustomUrlSource(data.custom_urls, config))
    return sources
