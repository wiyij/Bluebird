"""生成 assets/bluebird.ico（快捷方式图标）。

    py tools/make_icon.py

纯 Pillow 画一个蓝底白气泡，尺寸 256/128/64/48/32/16 打包进一个 .ico。
图标随仓库走，换机器重建快捷方式时不用再找图。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "assets" / "bluebird.ico"
BLUE = "#0b57d0"
DEEP = "#0842a0"


def draw(size: int = 256) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    pad = size * 0.04
    d.rounded_rectangle([pad, pad, size - pad, size - pad],
                        radius=size * 0.22, fill=BLUE)

    # 气泡主体 + 左下角小尾巴
    box = [size * 0.20, size * 0.26, size * 0.80, size * 0.66]
    d.rounded_rectangle(box, radius=size * 0.10, fill="white")
    d.polygon([(size * 0.30, size * 0.62), (size * 0.28, size * 0.80),
               (size * 0.44, size * 0.64)], fill="white")

    # 三个点（正在输入）
    radius = size * 0.045
    for cx in (0.34, 0.50, 0.66):
        x, y = size * cx, size * 0.46
        d.ellipse([x - radius, y - radius, x + radius, y + radius], fill=DEEP)
    return image


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    image = draw(256)
    image.save(TARGET, format="ICO",
               sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"saved -> {TARGET} ({TARGET.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
