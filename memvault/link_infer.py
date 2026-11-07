#!/usr/bin/env python3
"""预计算星图的推断边。

规则（来自评审）：
  旧卡 ↔ 旧卡 : 不造
  旧卡 → 新卡 : Top-2、余弦 >= 0.52、同 kind、标题不重复
  结构边      : 同 source_path 目录 + 日期差 <=3 天 (0.85) / 4-7 天 (0.65)

产出表 edges(src, dst, kind, weight, edge_type, label)
edge_type: fact | inferred | structural
"""
import sqlite3
import array
import json
import os
import re
import sys

import numpy as np

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
COS_MIN = 0.52
TOP_K = 2
DATE_NEAR = 3
DATE_WEAK = 7


def load_vecs(con):
    rows = con.execute(
        "SELECT card_id, dim, vec FROM vectors").fetchall()
    ids, mats = [], []
    for cid, dim, blob in rows:
        a = array.array("f")
        a.frombytes(blob)
        v = np.frombuffer(a.tobytes(), dtype=np.float32)
        if v.size != dim:
            continue
        n = np.linalg.norm(v)
        if n > 0:
            v = v / n
        ids.append(cid)
        mats.append(v)
    if not mats:
        return [], np.zeros((0, 0), dtype=np.float32)
    return ids, np.vstack(mats)


def main():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS edges(
        src INTEGER, dst INTEGER, kind TEXT, weight REAL,
        edge_type TEXT, label TEXT,
        PRIMARY KEY(src, dst, kind))""")
    con.execute("DELETE FROM edges")

    cards = {r[0]: r for r in con.execute(
        "SELECT id, kind, title, source_path, occurred_at FROM cards WHERE status='active'")}
    ids, mat = load_vecs(con)
    keep = [i for i, c in enumerate(ids) if c in cards]
    ids = [ids[i] for i in keep]
    mat = mat[keep]
    idx = {cid: i for i, cid in enumerate(ids)}

    # 新卡 = 有 card_evidence 的
    new_ids = {r[0] for r in con.execute(
        "SELECT DISTINCT card_id FROM card_evidence")}
    old_ids = [c for c in ids if c not in new_ids]
    print(f"cards={len(cards)} vec={len(ids)} new={len(new_ids)} old={len(old_ids)}")

    # ── 1. 同一次对话的事实边（有 evidence 的卡）──────────────
    fact = 0
    rows = con.execute(
        "SELECT ce.card_id, sp.conv_id FROM card_evidence ce "
        "JOIN spans sp ON sp.span_id = ce.span_id").fetchall()
    by_conv = {}
    for cid, conv in rows:
        if cid in cards and conv:
            by_conv.setdefault(conv, []).append(cid)
    for conv, lst in by_conv.items():
        u = list(dict.fromkeys(lst))
        for i in range(len(u)):
            for j in range(i + 1, len(u)):
                con.execute(
                    "INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?,?)",
                    (u[i], u[j], "co_conv", 1.0, "fact", "同次对话"))
                fact += 1
                if fact > 20000:
                    break
            if fact > 20000:
                break
        if fact > 20000:
            break
    print("fact edges:", fact)

    # ── 2. 旧卡 → 新卡 推断边 ────────────────────────────────
    inf = 0
    if old_ids and new_ids:
        oi = [idx[c] for c in old_ids if c in idx]
        ni = [idx[c] for c in new_ids if c in idx]
        O = mat[oi]
        N = mat[ni]
        oid = [ids[i] for i in oi]
        nid = [ids[i] for i in ni]
        B = 512
        for s in range(0, len(oid), B):
            sim = O[s:s + B] @ N.T          # (b, n_new)
            for r in range(sim.shape[0]):
                row = sim[r]
                top = np.argpartition(-row, min(TOP_K, len(row) - 1))[:TOP_K]
                a = oid[s + r]
                ka = cards[a][1]
                ta = (cards[a][2] or "")[:20]
                for t in top:
                    if row[t] < COS_MIN:
                        continue
                    b = nid[t]
                    if cards[b][1] != ka:      # 同 kind
                        continue
                    tb = (cards[b][2] or "")[:20]
                    if ta and tb and ta == tb:
                        continue
                    con.execute(
                        "INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?,?)",
                        (a, b, "inferred_sim", float(row[t]), "inferred", "推断·不可溯源"))
                    inf += 1
        print("inferred edges:", inf)

    # ── 3. 结构边：同目录 + 日期近 ───────────────────────────
    st = 0
    bydir = {}
    for cid, r in cards.items():
        sp = r[3] or ""
        m = re.match(r"(.+?)/(?:[^/]+)$", sp)
        d = m.group(1) if m else sp
        if d:
            bydir.setdefault(d, []).append(cid)
    for d, lst in bydir.items():
        if len(lst) < 2 or len(lst) > 60:
            continue
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a, b = lst[i], lst[j]
                da, db = cards[a][4], cards[b][4]
                if not da or not db:
                    continue
                from datetime import date
                try:
                    ya, ma, day_a = map(int, da[:10].split("-"))
                    yb, mb, day_b = map(int, db[:10].split("-"))
                    delta = abs((date(ya, ma, day_a) - date(yb, mb, day_b)).days)
                except Exception:
                    continue
                if delta <= DATE_NEAR:
                    con.execute("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?,?)",
                                (a, b, "structural_near", 0.85, "structural", "同目录·近3天"))
                    st += 1
                elif delta <= DATE_WEAK:
                    con.execute("INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?,?)",
                                (a, b, "structural_weak", 0.65, "structural", "同目录·近7天"))
                    st += 1
    print("structural edges:", st)

    con.commit()
    for t in ("fact", "inferred", "structural"):
        n = con.execute("SELECT count(*) FROM edges WHERE edge_type=?", (t,)).fetchone()[0]
        print(f"  {t}: {n}")
    print("TOTAL:", con.execute("SELECT count(*) FROM edges").fetchone()[0])
    con.close()


if __name__ == "__main__":
    main()
