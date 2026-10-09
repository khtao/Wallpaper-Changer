"""支持 `python -m wallpaper_changer` 启动。"""
from __future__ import annotations

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
