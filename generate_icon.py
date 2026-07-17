#!/usr/bin/env python3
"""生成 PEWM 应用图标（assets/icon.ico）。

绘制靛蓝渐变圆角方块 + 白色字母 P，导出多尺寸 ICO（16~256）。
用法： py scripts/../generate_icon.py  或  py generate_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
SIZE = 512

TOP = (99, 91, 255)    # 顶部亮靛蓝
BOTTOM = (67, 56, 202) # 底部深靛蓝


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def make_icon(size=SIZE):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 纵向渐变
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    for y in range(size):
        c = lerp(TOP, BOTTOM, y / (size - 1)) + (255,)
        for x in range(size):
            px[x, y] = c

    # 圆角蒙版
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    # 字母 P
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in ("segoeuib.ttf", "msyhbd.ttc", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(candidate, int(size * 0.58))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()

    text = "P"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pos = ((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1])
    draw.text(pos, text, font=font, fill=(255, 255, 255, 255))
    return img


def main():
    OUT.mkdir(exist_ok=True)
    icon = make_icon()
    icon.save(OUT / "icon.png")
    icon.save(
        OUT / "icon.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成: {OUT / 'icon.ico'} 与 icon.png")


if __name__ == "__main__":
    main()
