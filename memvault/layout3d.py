#!/usr/bin/env python3
"""3D 坐标：复用 2D 岛屿布局的 (x,y)，z 轴用时间。

为什么这么算：
  3D 力导向在 12000 点上，手机每帧要算 O(n²) 斥力，必然卡。
  但记忆的形状本来就有两个维度——「内容相近」和「时间相近」。
  2D 已经把内容摊平了，z 轴直接放时间，既不糊又有信息。

前端拿到固定坐标后 cooldownTicks(0)，一次渲染，零物理。
"""
import json
import math
import sqlite3
import time

DB = "/opt/memvault/data/memvault.db"

DDL = """
CREATE TABLE IF NOT EXISTS map3d(
    card_id INTEGER PRIMARY KEY,
    x REAL, y REAL, z REAL,
    tz REAL,          -- 时间在 [0,1] 的位置，便于前端做时间轴
    month TEXT
);
"""


_MONTH_CACHE = {}

def month_of(con, cid):
    if cid in _MONTH_CACHE:
        return _MONTH_CACHE[cid]
    m = ""
    r = con.execute("SELECT occurred_at FROM cards WHERE id=?", (cid,)).fetchone()
    if r and r[0]:
        m = str(r[0])[:7]
    if not m:
        # 新卡：用 evidence 里最早的一条 span 时间
        r = con.execute(
            "SELECT MIN(sp.ts) FROM card_evidence ce JOIN spans sp ON sp.span_id=ce.span_id "
            "WHERE ce.card_id=?", (cid,)).fetchone()
        if r and r[0]:
            m = str(r[0])[:7]
    _MONTH_CACHE[cid] = m
    return m


def main():
    con = sqlite3.connect(DB)
    con.executescript(DDL)

    rows = con.execute(
        "SELECT m.card_id, m.x, m.y, c.kind FROM map2d m "
        "JOIN cards c ON c.id=m.card_id WHERE c.status='active'").fetchall()
    print(f"点：{len(rows)}")

    # 月份 → 归一化 z
    items = []
    for cid, x, y, kind in rows:
        m = month_of(con, cid)
        items.append((cid, x, y, kind, m))
    months = sorted({i[4] for i in items if i[4]})
    if not months:
        months = ["2026-11"]
    midx = {m: i / max(1, len(months) - 1) for i, m in enumerate(months)}
    print(f"月份：{len(months)} 个（{months[0]} → {months[-1]}）")

    # z 轴：时间。跨度 1.6，避免太扁
    out = []
    for cid, x, y, kind, m in items:
        tz = midx.get(m, 0.5)
        z = (tz - 0.5) * 1.6
        # 无月份的旧卡：z 用哈希散到整段，避免全挤在一层
        if not m:
            tz = (cid * 2654435761 % 10000) / 10000.0
            z = (tz - 0.5) * 1.6
        out.append((cid, x, y, z, tz, m))

    con.execute("DELETE FROM map3d")
    con.executemany("INSERT INTO map3d(card_id,x,y,z,tz,month) VALUES(?,?,?,?,?,?)", out)
    con.commit()
    print(f"map3d: {len(out)} 行")

    # 统计一下每层多少个点
    from collections import Counter
    c = Counter(i[5] for i in out)
    top = c.most_common(5)
    print("最密的月份：" + ", ".join(f"{m}({n})" for m, n in top))


if __name__ == "__main__":
    main()
