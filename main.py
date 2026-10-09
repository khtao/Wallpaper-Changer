#!/usr/bin/env python3
"""Wallpaper Changer 桌面入口。

用法::

    python main.py            # 正常启动（按配置决定是否显示窗口）
    python main.py --show     # 显示主界面
    python main.py --next     # 立即换一张
    python main.py --prev     # 回到上一张
    python main.py --refresh  # 从来源抓取新图
    python main.py --quit     # 退出正在运行的实例
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# --doctor 必须在导入 PySide6 之前处理，
# 否则缺依赖时连自检都跑不起来（导入即崩溃）。
if "--doctor" in sys.argv[1:]:
    from wallpaper_changer.doctor import run_doctor

    raise SystemExit(run_doctor())

from wallpaper_changer.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
