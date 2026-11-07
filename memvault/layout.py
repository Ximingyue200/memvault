#!/usr/bin/env python3
"""把 1024 维向量压到 2D，存成静态坐标。

为什么预计算：12000 个点实时跑力导向必卡。
手机上要的是「能拖能缩能点」，不是「每帧都在算物理」。

算法：协方差 → 特征分解 → 取最大两个特征向量投影。
1024x1024 的 eigh 大约 3-5 秒，一次性。
"""
import argparse
import math
import sqlite3
import time

import numpy as np

DB = "/opt/memvault/data/memvault.db"


def load_vectors(con):
    rows = list(con.execute(
        """SELECT v.card_id, v.dim, v.vec FROM vectors v
           JOIN cards c ON c.id = v.card_id
           WHERE c.status='active'"""
    ))
    ids, mat = [], []
    for cid, dim, blob in rows:
        v = np.frombuffer(blob, dtype=np.float32)
        if v.size != dim:
            continue
        ids.append(cid)
        mat.append(v)
    return ids, np.vstack(mat) if mat else np.zeros((0, 0), dtype=np.float32)


def project2d(mat):
    """PCA 到 2 维。返回 Nx2，已归一到 [-1,1]。"""
    n = mat.shape[0]
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32)
    mu = mat.mean(axis=0, dtype=np.float64)
    X = (mat - mu).astype(np.float64)
    # 协方差（1024x1024），比直接 SVD 的 N×1024 快得多
    C = (X.T @ X) / max(n - 1, 1)
    w, V = np.linalg.eigh(C)
    order = np.argsort(w)[::-1][:2]
    P = X @ V[:, order]
    # 归一到 [-1,1]
    for k in range(2):
        col = P[:, k]
        lo, hi = float(col.min()), float(col.max())
        rng = (hi - lo) or 1.0
        P[:, k] = (col - lo) / rng * 2.0 - 1.0
    return P.astype(np.float32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--limit", type=int, default=0, help="只取最新的 N 张（0=全部）")
    args = ap.parse_args()

    t0 = time.time()
    con = sqlite3.connect(args.db)
    ids, mat = load_vectors(con)
    if args.limit and len(ids) > args.limit:
        ids, mat = ids[-args.limit:], mat[-args.limit:]
    print(f"loaded {len(ids)} vectors {mat.shape} in {time.time()-t0:.1f}s")

    P = project2d(mat)
    print(f"projected in {time.time()-t0:.1f}s")

    # 时间维度 → 半径（越近的越靠内），主题角 → 角度
    meta = {r[0]: r for r in con.execute(
        """SELECT id, title, kind, topic, occurred_at, created_at,
                  substr(COALESCE(occurred_at, created_at), 1, 7) AS ym
           FROM cards WHERE status='active'"""
    )}

    con.execute("DROP TABLE IF EXISTS map2d")
    con.execute("""CREATE TABLE map2d(
        card_id INTEGER PRIMARY KEY, x REAL, y REAL,
        px REAL, py REAL, ring REAL, angle REAL)""")

    # ── 岛屿布局 ──────────────────────────────────────────────
    # 1024 维的 PCA 前两维只解释很少方差，直接散点会糊成一团。
    # 改成：把出现次数多的 topic 各自摆成一个「岛」，岛内再用 PCA 局部坐标。
    from collections import Counter, defaultdict
    tc = Counter(((meta.get(i) or [None] * 7)[3] or "") for i in ids)
    big = [t for t, n in tc.most_common() if n >= 8 and t]          # 主要岛
    bigset = set(big)
    rest = defaultdict(list)                                        # 剩余按 kind 落区

    # 黄金角螺旋排布岛心：越大的岛越靠中
    centers = {}
    GA = math.pi * (3 - 5 ** 0.5)
    nbig = max(len(big), 1)
    for i, t in enumerate(big):
        frac = (i + 0.5) / nbig
        rad = 0.10 + 0.60 * (frac ** 0.62)      # 大的在内圈，整体收在 0.70 以内
        a = i * GA
        centers[t] = (math.cos(a) * rad, math.sin(a) * rad)

    # 剩余（小主题 / 无主题）按 kind 分 6 个扇区，贴在外缘
    kinds = ["definition", "fact", "event", "experience", "preference", "tool"]
    kslot = {}
    for i, k in enumerate(kinds):
        a = 2 * math.pi * i / len(kinds) + 0.4
        kslot[k] = (math.cos(a) * 0.90, math.sin(a) * 0.90)

    # 每个岛内的点：用 PCA 局部坐标压成小半径
    island_r = {}
    for t in big:
        n = tc[t]
        island_r[t] = min(0.045, 0.010 + 0.0038 * (n ** 0.5))

    rows = []
    for k, cid in enumerate(ids):
        m = meta.get(cid)
        topic = (m[3] if m else None) or ""
        kind = (m[2] if m else None) or "unknown"
        px, py = float(P[k, 0]), float(P[k, 1])
        if topic in bigset:
            cx, cy = centers[topic]
            rr = island_r[topic]
            x = cx + px * rr
            y = cy + py * rr
        else:
            cx, cy = kslot.get(kind, (0, 0))
            # 小点堆在外缘，按 PCA 轻微散开
            x = cx * (1.0 + px * 0.10)
            y = cy * (1.0 + py * 0.10)
        ring = 0.0
        ang = math.atan2(y, x)
        rows.append((cid, x, y, px, py, ring, ang))

    con.executemany("INSERT INTO map2d VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    print(f"map2d: {len(rows)} rows, {len(big)} islands, "
          f"{len(tc)} topics, {time.time()-t0:.1f}s total")


if __name__ == "__main__":
    main()
