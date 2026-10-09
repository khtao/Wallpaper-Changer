#!/usr/bin/env python3
"""生成 assets/icon.png 与 assets/icon.ico（仅依赖 Pillow）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
SIZE = 512


def draw(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size
    # 圆角矩形底板
    d.rounded_rectangle([s * 0.06, s * 0.06, s * 0.94, s * 0.94],
                        radius=int(s * 0.20), fill=(47, 111, 237, 255))
    # 太阳
    d.ellipse([s * 0.20, s * 0.18, s * 0.48, s * 0.46], fill=(255, 213, 79, 255))
    # 山
    d.polygon([(s * 0.14, s * 0.76), (s * 0.40, s * 0.44), (s * 0.66, s * 0.76)],
              fill=(127, 209, 138, 255))
    d.polygon([(s * 0.46, s * 0.76), (s * 0.66, s * 0.52), (s * 0.88, s * 0.76)],
              fill=(96, 176, 108, 255))
    # 地面
    d.rounded_rectangle([s * 0.10, s * 0.72, s * 0.90, s * 0.84],
                        radius=int(s * 0.04), fill=(255, 255, 255, 235))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = draw(SIZE)
    png = OUT / "icon.png"
    base.save(png)
    ico = OUT / "icon.ico"
    base.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    # 托盘用的小尺寸
    for n in (16, 22, 24, 32, 48, 64, 128, 256):
        draw(n).save(OUT / f"icon_{n}.png")
    print(f"生成完成: {png} {ico}")


if __name__ == "__main__":
    main()
