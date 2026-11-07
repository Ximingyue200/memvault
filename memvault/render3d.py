#!/usr/bin/env python3
"""服务端渲染 3D 点云 → PNG。

为什么走服务端：
  手机（尤其虚拟屏）拿不到 WebGL 上下文，Three.js 画布全黑。
  但 3000 个点的旋转 + 投影，numpy 几毫秒就算完，PIL 画点也就几十毫秒。
  浏览器只负责显示一张图 + 传手势，不需要 GL。

坐标：map3d 的 x/y/z（2D 岛屿布局 + 时间轴）
朝向：yaw 绕 y 轴，pitch 绕 x 轴
"""
import io
import math
import os
import sqlite3

import numpy as np
from PIL import Image, ImageDraw

DB = "/opt/memvault/data/memvault.db"

COLORS = {
    "fact": (90, 169, 255),
    "definition": (77, 171, 247),
    "event": (255, 200, 87),
    "experience": (255, 143, 171),
    "preference": (180, 140, 255),
    "tool": (95, 212, 138),
    "note": (223, 227, 234),
    "rule": (255, 107, 107),
}
BG = (6, 7, 10)

_cache = {"key": None, "pts": None}


def load_points(limit=4000):
    key = limit
    if _cache["key"] == key:
        return _cache["pts"]
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(
        """SELECT m.x, m.y, m.z, c.kind
           FROM map3d m JOIN cards c ON c.id=m.card_id
           WHERE c.status='active'
           ORDER BY EXISTS(SELECT 1 FROM edges e WHERE e.src=m.card_id OR e.dst=m.card_id) DESC,
                    m.card_id DESC
           LIMIT ?""", (limit,)).fetchall()
    con.close()
    xyz = np.array([[r[0], r[1], r[2]] for r in rows], dtype=np.float32) * 260.0
    kinds = [r[3] or "fact" for r in rows]
    rgb = np.array([COLORS.get(k, (140, 140, 140)) for k in kinds], dtype=np.uint8)
    _cache["key"] = key
    _cache["pts"] = (xyz, rgb, kinds)
    return _cache["pts"]


def render(yaw=0.0, pitch=0.25, w=900, h=1200, limit=4000, zoom=1.0):
    xyz, rgb, kinds = load_points(limit)
    if len(xyz) == 0:
        img = Image.new("RGB", (w, h), BG)
        buf = io.BytesIO(); img.save(buf, "PNG"); return buf.getvalue(), 0

    # 居中
    p = xyz - xyz.mean(axis=0, keepdims=True)

    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)

    # 绕 y 轴
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]], dtype=np.float32)
    # 绕 x 轴
    Rx = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]], dtype=np.float32)
    q = p @ Ry.T @ Rx.T

    # 透视
    span = float(np.abs(q).max()) or 1.0
    dist = span * 2.6 / max(0.2, float(zoom))
    z = q[:, 2] + dist
    z = np.where(z < 1e-3, 1e-3, z)
    f = (min(w, h) * 0.9) / (2.0 * math.tan(math.radians(28)))
    sx = q[:, 0] * f / z + w * 0.5
    sy2 = -q[:, 1] * f / z + h * 0.5

    # 按 z 排序，远的先画
    order = np.argsort(-z)
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img, "RGBA")

    # 按 z 分三档，远的暗、近的亮
    zmin, zmax = float(z.min()), float(z.max())
    rng = max(1e-6, zmax - zmin)
    rad = max(1.1, min(3.2, 1350.0 / math.sqrt(len(xyz))))

    for i in order:
        t = (z[i] - zmin) / rng          # 0 近 1 远
        a = int(255 * (1.0 - 0.62 * t))
        c = rgb[i]
        x0, y0 = sx[i], sy2[i]
        if x0 < -4 or y0 < -4 or x0 > w + 4 or y0 > h + 4:
            continue
        r = rad * (1.0 - 0.30 * t)
        # 外层光晕（暗）+ 内核（亮），小尺寸也能看出颜色
        d.ellipse([x0 - r * 2.1, y0 - r * 2.1, x0 + r * 2.1, y0 + r * 2.1],
                  fill=(int(c[0]), int(c[1]), int(c[2]), int(a * 0.16)))
        d.ellipse([x0 - r, y0 - r, x0 + r, y0 + r],
                  fill=(int(c[0]), int(c[1]), int(c[2]), a))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False, compress_level=3)
    return buf.getvalue(), len(xyz)


if __name__ == "__main__":
    raw, n = render()
    open("/tmp/snap.png", "wb").write(raw)
    print(f"{n} points → {len(raw)} bytes")
