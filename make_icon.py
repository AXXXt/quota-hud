# -*- coding: utf-8 -*-
"""生成 QuotaHUD 图标（仪表盘风格：深色圆角底 + 绿色脉冲点 + 柱状图）"""
from PIL import Image, ImageDraw

S = 256
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 圆角深色底
d.rounded_rectangle([12, 12, S - 12, S - 12], radius=52, fill=(26, 28, 34, 255), outline=(70, 78, 95, 255), width=6)

# 柱状图（消耗趋势）
bars = [(52, 150), (92, 110), (132, 165), (172, 80)]
for x, h in bars:
    d.rounded_rectangle([x, 196 - h, x + 30, 196], radius=8, fill=(91, 140, 255, 235))

# 右上角绿色脉冲点（健康状态）
d.ellipse([182, 44, 218, 80], fill=(63, 185, 80, 255))

# 底部进度条
d.rounded_rectangle([44, 214, 212, 226], radius=6, fill=(51, 56, 69, 255))
d.rounded_rectangle([44, 214, 150, 226], radius=6, fill=(210, 153, 34, 255))

import os
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
img.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png"))
print("icon ->", out)
