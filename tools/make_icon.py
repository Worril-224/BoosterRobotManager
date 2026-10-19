"""生成应用图标 ``tools/app_icon.png``（1024×1024）。

只在本机生成图标时使用，需要 Pillow：

    /path/to/python -m pip install Pillow
    python tools/make_icon.py

图标为深蓝色渐变圆角底 + 机器人头像；运行 ``tools/build_launcher.py`` 时
会把它转换成 macOS 的 ``.icns`` 并放进 .app。
"""

from __future__ import annotations

from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError as exc:  # pragma: no cover - 仅生成图标时需要
    raise SystemExit("需要 Pillow：pip install Pillow") from exc

SIZE = 1024
OUTPUT = Path(__file__).resolve().parent / "app_icon.png"


def main() -> int:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # -- 深蓝渐变圆角背景 ------------------------------------------------
    gradient = Image.new("RGB", (SIZE, SIZE))
    pixels = gradient.load()
    top = (37, 99, 235)      # #2563eb
    bottom = (11, 18, 32)    # #0b1220
    for y in range(SIZE):
        ratio = y / (SIZE - 1)
        color = tuple(
            int(top[index] + (bottom[index] - top[index]) * ratio) for index in range(3)
        )
        for x in range(SIZE):
            pixels[x, y] = color

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, SIZE - 1, SIZE - 1], radius=220, fill=255
    )
    image.paste(gradient, (0, 0), mask)

    draw = ImageDraw.Draw(image)

    # -- 天线 ------------------------------------------------------------
    draw.rounded_rectangle([496, 168, 528, 300], radius=16, fill=(203, 213, 225, 255))
    draw.ellipse([466, 140, 558, 232], fill=(126, 198, 153, 255))

    # -- 耳朵 ------------------------------------------------------------
    draw.rounded_rectangle([196, 418, 280, 596], radius=34, fill=(203, 213, 225, 255))
    draw.rounded_rectangle([744, 418, 828, 596], radius=34, fill=(203, 213, 225, 255))

    # -- 头部 ------------------------------------------------------------
    draw.rounded_rectangle([266, 276, 758, 754], radius=150, fill=(241, 245, 249, 255))
    draw.rounded_rectangle([326, 346, 698, 684], radius=104, fill=(30, 41, 59, 255))

    # -- 眼睛 + 高光 -----------------------------------------------------
    draw.ellipse([404, 420, 524, 540], fill=(126, 198, 153, 255))
    draw.ellipse([500, 420, 620, 540], fill=(126, 198, 153, 255))
    draw.ellipse([424, 440, 464, 480], fill=(255, 255, 255, 235))
    draw.ellipse([520, 440, 560, 480], fill=(255, 255, 255, 235))

    # -- 嘴 --------------------------------------------------------------
    draw.rounded_rectangle([446, 584, 578, 616], radius=16, fill=(96, 165, 250, 255))

    # -- 底部小装饰 ------------------------------------------------------
    draw.rounded_rectangle([420, 690, 604, 704], radius=7, fill=(71, 85, 105, 255))

    image.save(OUTPUT)
    print(f"图标已生成：{OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
