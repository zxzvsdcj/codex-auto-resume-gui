"""生成 Codex Auto Resume 应用图标。

设计理念：
- 圆角方形蓝绿渐变底（主色 #0ea5e9 -> #10b981，与 GUI 配色一致）
- 中央白色循环箭头（↻），象征"额度用完自动续跑/循环恢复"
- 箭头中心一个小圆点/闪电，象征 AI 任务
- 扁平现代风格，小尺寸清晰可辨
"""
from PIL import Image, ImageDraw
from pathlib import Path

OUT = Path(r"E:\cursor\codex-auto-resume\assets")
OUT.mkdir(exist_ok=True)

SIZES = [16, 24, 32, 48, 64, 128, 256]
GRID = 1024  # 超采样画布


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient_bg(size):
    """蓝绿对角渐变圆角方块。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    px = img.load()
    c1 = (14, 165, 233)   # #0ea5e9 sky-500
    c2 = (16, 185, 129)   # #10b981 emerald-500
    radius = int(size * 0.22)
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            px[x, y] = (*lerp(c1, c2, t), 255)
    # 圆角遮罩
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.putalpha(mask)
    return img


def draw_icon(size):
    s = size
    bg = gradient_bg(s)
    d = ImageDraw.Draw(bg)

    cx, cy = s / 2, s / 2
    # 循环箭头外半径
    r_outer = s * 0.30
    r_inner = s * 0.20
    # 箭头线宽
    lw = max(int(s * 0.085), 2)

    # 画一段圆弧（从右上 30° 到左下 210°），形成不闭合的 C 形，留口装箭头头
    # 用两段弧线：上半段 + 下半段
    white = (255, 255, 255, 255)
    # 外弧（粗）
    bbox = [cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer]
    # 从 300° 顺时针到 150°（即经过 0°/右侧），形成开口朝左下的大半圆
    d.arc(bbox, start=150, end=390, fill=white, width=lw)
    # 从 30° 顺时针到 210°（经过 180°/左侧），形成另一段
    d.arc(bbox, start=-30, end=210, fill=white, width=lw)

    # 箭头头：在右侧（约 0° 位置）画一个三角形指向顺时针方向
    # 右侧点 (cx + r_outer, cy)
    import math
    ang = 0  # 右侧
    tip = (cx + r_outer * math.cos(math.radians(ang)),
           cy + r_outer * math.sin(math.radians(ang)))
    # 三角形大小
    ah = s * 0.11
    aw = s * 0.075
    # 两个底点（沿圆弧切线方向）
    p1 = (tip[0] - ah * math.cos(math.radians(ang)) + aw * math.sin(math.radians(ang)),
          tip[1] - ah * math.sin(math.radians(ang)) - aw * math.cos(math.radians(ang)))
    p2 = (tip[0] - ah * math.cos(math.radians(ang)) - aw * math.sin(math.radians(ang)),
          tip[1] - ah * math.sin(math.radians(ang)) + aw * math.cos(math.radians(ang)))
    d.polygon([tip, p1, p2], fill=white)

    # 中心闪电（AI 任务感）
    # 简化：中心画一个小圆点
    dot_r = s * 0.055
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=(255, 255, 255, 235))

    return bg


# 生成各尺寸 png
png_paths = []
for sz in SIZES:
    img = draw_icon(sz)
    p = OUT / f"icon-{sz}.png"
    img.save(p)
    png_paths.append(p)
    print(f"saved {p}")

# 合成 ico（含多尺寸）
ico_img = draw_icon(256)
ico_path = OUT / "app.ico"
ico_img.save(ico_path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"saved {ico_path}")

# 预览大图
preview = draw_icon(512)
preview.save(OUT / "preview.png")
print(f"saved preview.png")
