#!/usr/bin/env python3
"""把编年史里的 threads 落成表。

为什么要有表：
  brief.md 只能整页读，回答不了「哪条卡了 40 天」「这周哪几条状态变了」。
  表能筛，页不能。

设计（单人版 bi-temporal，只有单向时间轴）：
  threads      一条线索的**当前状态** + 它是从哪个 period 开始算的
  thread_state 状态迁移历史（valid_from / valid_to，过期不删）
  thread_alias 别名 → 同一个 thread（合并只加别名，原记录不动，可回滚）

判据：
  kind='issue'    排障类，有离散状态（进行中/卡住/转向/完成）
  kind='progress' 进度类，只有游标（「上次学到第 7 单元」），不套状态机
"""
import json
import re
import sqlite3
import time

DB = "/opt/memvault/data/memvault.db"

DDL = """
CREATE TABLE IF NOT EXISTS threads(
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'issue',   -- issue | progress
    state       TEXT NOT NULL DEFAULT 'active',  -- active | blocked | shifted | done | unknown
    note        TEXT DEFAULT '',
    cursor      TEXT DEFAULT NULL,               -- progress 类的游标（JSON）
    first_period TEXT,                           -- 最早出现在哪个 period
    last_period  TEXT,                           -- 最近出现在哪个 period
    valid_from  TEXT,                            -- 当前状态从什么时候开始
    last_touched TEXT,                           -- 最近一次被提到
    seen        INTEGER DEFAULT 1,               -- 被几个 period 提到过
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS thread_state(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    state       TEXT NOT NULL,
    note        TEXT DEFAULT '',
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,                            -- NULL = 当前
    source_period TEXT
);
CREATE TABLE IF NOT EXISTS thread_alias(
    alias       TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_threads_state ON threads(state);
CREATE INDEX IF NOT EXISTS idx_threads_touch ON threads(last_touched);
CREATE INDEX IF NOT EXISTS idx_tstate_tid ON thread_state(thread_id, valid_from);
"""

STATE_MAP = {
    "进行中": "active", "active": "active", "在做": "active", "推进中": "active",
    "卡住": "blocked", "停滞": "blocked", "卡着": "blocked", "阻塞": "blocked", "blocked": "blocked",
    "转向": "shifted", "改方向": "shifted", "shifted": "shifted",
    "完成": "done", "已完结": "done", "done": "done", "收口": "done",
}
PROGRESS_WORDS = ("学到", "学完", "进度", "第.*单元", "第.*章", "课程", "练习", "背书", "单词")


def norm(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    s = re.sub(r"[，。、：:；;（）()【】\[\]《》\"'`~!！?？·\-_/]+", "", s)
    return s.replace("bootloader", "bl")


def toks(s):
    s = norm(s)
    out = set()
    for i in range(len(s) - 1):
        out.add(s[i:i + 2])
    return out


def jac(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def canon_state(s):
    if not s:
        return "unknown"
    for k, v in STATE_MAP.items():
        if k in s:
            return v
    return "unknown"


def classify_kind(name, note):
    blob = f"{name} {note or ''}"
    if any(re.search(w, blob) for w in PROGRESS_WORDS):
        return "progress"
    return "issue"


def load_items(con):
    """从编年史里把所有 threads 摊平成一串 (period, name, state, note)。"""
    items = []
    rows = con.execute(
        "SELECT period_id, start_ts, threads FROM chronicles "
        "WHERE granularity='month' AND threads IS NOT NULL ORDER BY start_ts"
    ).fetchall()
    for pid, st, raw in rows:
        try:
            arr = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            continue
        if not isinstance(arr, list):
            continue
        for it in arr:
            if isinstance(it, str):
                name, note, state = it.split("：")[0][:40], "", "unknown"
            elif isinstance(it, dict):
                name = (it.get("name") or "").strip()
                note = (it.get("note") or "").strip()
                state = (it.get("state") or "").strip()
            else:
                continue
            if not name:
                continue
            items.append({"period": pid, "start": st or "", "name": name,
                          "state": state, "note": note})
    return items


def main():
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    items = load_items(con)
    print(f"threads: {len(items)} 条原始记录")

    # ── 1. 按 Jaccard 归并同名（≥0.8 直接并） ──────────────
    groups = []   # {name, alias:set, hist:[item]}
    for it in items:
        t = toks(it["name"])
        hit = None
        for g in groups:
            if jac(t, g["tok"]) >= 0.80:
                hit = g
                break
        if hit:
            hit["hist"].append(it)
            hit["alias"].add(it["name"])
        else:
            groups.append({"name": it["name"], "tok": t, "alias": {it["name"]}, "hist": [it]})
    print(f"  → {len(groups)} 组")

    # ── 2. 每组算当前状态 + 迁移历史 ─────────────────────
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    n_new = n_upd = 0
    for g in groups:
        hist = sorted(g["hist"], key=lambda x: x["start"])
        import hashlib
        tid = "t_" + hashlib.sha1(norm(g["name"]).encode()).hexdigest()[:14]
        states = [(h["period"], canon_state(h["state"]), h["note"], h["start"]) for h in hist]
        # 连续相同状态合并
        runs = []
        for pid, st, note, start in states:
            if runs and runs[-1]["state"] == st:
                runs[-1]["last"] = pid
                if note:
                    runs[-1]["note"] = note
            else:
                runs.append({"state": st, "from": pid, "last": pid, "note": note, "start": start})
        cur = runs[-1]
        kind = classify_kind(g["name"], cur["note"])
        cursor = None
        if kind == "progress":
            m = re.search(r"(第\s*\d+\s*[单元章节课]|学到[^，。]{0,20})", cur["note"] or "")
            cursor = json.dumps({"at": m.group(1) if m else ""}, ensure_ascii=False)

        first = hist[0]["period"]
        last = hist[-1]["period"]
        exists = con.execute("SELECT 1 FROM threads WHERE thread_id=?", (tid,)).fetchone()
        if exists:
            con.execute(
                """UPDATE threads SET state=?, note=?, kind=?, cursor=?, last_period=?,
                   valid_from=?, last_touched=?, seen=?, updated_at=? WHERE thread_id=?""",
                (cur["state"], cur["note"], kind, cursor, last, cur["from"],
                 last, len(hist), now, tid))
            n_upd += 1
        else:
            con.execute(
                """INSERT INTO threads(thread_id,name,kind,state,note,cursor,first_period,
                   last_period,valid_from,last_touched,seen,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, g["name"], kind, cur["state"], cur["note"], cursor, first,
                 last, cur["from"], last, len(hist), now))
            n_new += 1

        # 迁移历史：先清后写（幂等）
        con.execute("DELETE FROM thread_state WHERE thread_id=?", (tid,))
        for i, r in enumerate(runs):
            vto = runs[i + 1]["from"] if i + 1 < len(runs) else None
            con.execute(
                """INSERT INTO thread_state(thread_id,state,note,valid_from,valid_to,source_period)
                   VALUES(?,?,?,?,?,?)""",
                (tid, r["state"], r["note"], r["from"], vto, r["last"]))

        for a in g["alias"]:
            con.execute("INSERT OR REPLACE INTO thread_alias(alias,thread_id) VALUES(?,?)", (a, tid))

    con.commit()
    print(f"  → threads: 新增 {n_new} / 更新 {n_upd}")

    print("\n状态分布：")
    for st, n in con.execute("SELECT state, count(*) FROM threads GROUP BY 1 ORDER BY 2 DESC"):
        print(f"  {st:9s} {n}")

    print("\n最近 30 天没动的进行中：")
    for r in con.execute(
        """SELECT name, state, last_period, seen FROM threads
           WHERE state IN ('active','blocked') ORDER BY last_period ASC LIMIT 6"""):
        print(f"  [{r[1]}] {r[0][:34]:36s} 最后 {r[2]} ({r[3]} 次)")

    # 顺手把 threads 导出成 JSON 给 brief 用
    rows = con.execute(
        """SELECT t.name,t.kind,t.state,t.note,t.first_period,t.last_period,t.seen
           FROM threads t ORDER BY t.state, t.last_period DESC""").fetchall()
    with open("/opt/memvault/data/threads.json", "w", encoding="utf-8") as f:
        json.dump([dict(zip(["name", "kind", "state", "note", "first", "last", "seen"], r))
                   for r in rows], f, ensure_ascii=False, indent=1)
    print(f"\n→ /opt/memvault/data/threads.json ({len(rows)} 条)")


if __name__ == "__main__":
    main()
