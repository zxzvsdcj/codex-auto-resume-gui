"""重新设计更清晰的应用图标。

设计：粗体循环箭头 + 中心闪电，小尺寸下也清晰可辨。
"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(r"E:\cursor\codex-auto-resume\assets")
OUT.mkdir(exist_ok=True)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def draw_icon(size):
    s = size
    # 背景圆角方块
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c1 = (14, 165, 233)   # sky
    c2 = (16, 185, 129)   # emerald
    radius = int(s * 0.22)
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            d.point((x, y), fill=(*lerp(c1, c2, t), 255))
    # 圆角遮罩
    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius, fill=255)
    img.putalpha(mask)

    cx, cy = s / 2, s / 2
    white = (255, 255, 255, 255)

    # 粗体循环箭头：两个粗弧线
    lw = max(int(s * 0.14), 2)  # 比之前更粗
    r = s * 0.30
    bbox = [cx - r, cy - r, cx + r, cy + r]

    # 上半弧（从 150° 到 390°，经过右侧）
    d.arc(bbox, start=150, end=390, fill=white, width=lw)
    # 下半弧（从 -30° 到 210°，经过左侧）
    d.arc(bbox, start=-30, end=210, fill=white, width=lw)

    # 箭头头（右侧，更大）
    import math
    tip = (cx + r, cy)
    ah = s * 0.14  # 箭头长度
    aw = s * 0.09  # 箭头宽度
    p1 = (tip[0] - ah, cy - aw)
    p2 = (tip[0] - ah, cy + aw)
    d.polygon([tip, p1, p2], fill=white)

    return img


# 生成各尺寸
for sz in SIZES:
    img = draw_icon(sz)
    img.save(OUT / f"icon-{sz}.png")

# 生成 ico
ico_img = draw_icon(256)
ico_img.save(OUT / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# 预览
draw_icon(512).save(OUT / "preview.png")
print("OK")
