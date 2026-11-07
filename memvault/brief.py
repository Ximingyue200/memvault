# -*- coding: utf-8 -*-
"""答案页生成器：从 chronicles.threads 抽线索 -> 归并 -> 生成 brief.md
DeepSeek 的建议：先让 141 条以最丑的格式出现在眼前一周，再谈结构化。
"""
import sqlite3, json, re, sys, os
from collections import OrderedDict, defaultdict
from datetime import datetime

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
OUT = os.environ.get("BRIEF_OUT", "/opt/memvault/data/brief.md")

DONE = {"完成", "已完成", "结束"}
BLOCKED = {"停滞", "搁置", "卡住", "阻塞"}
SHIFTED = {"转向", "改道"}
LIVE = {"进行中", "在做", "推进中"}

def norm(s):
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-_/、·，,。:：()（）\[\]【】]+", "", s)
    s = s.replace("bootloader", "bl")
    return s

def toks(s):
    n = norm(s)
    # 中文按 2-gram，英文按词
    out = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fa5]{2}", n))
    return out

def jac(a, b):
    A, B = toks(a), toks(b)
    if not A or not B: return 0.0
    return len(A & B) / len(A | B)

def load(db):
    con = sqlite3.connect(db)
    rows = list(con.execute(
        "SELECT period_id, start_ts, end_ts, threads FROM chronicles "
        "WHERE granularity IN ('month') ORDER BY start_ts"))
    items = []
    for pid, st, en, th in rows:
        try: arr = json.loads(th) if th else []
        except Exception: continue
        for t in arr:
            if isinstance(t, str):
                name = t.split("：")[0][:40]; state = "?"; note = t
            else:
                name = (t.get("name") or "").strip()
                state = (t.get("state") or "?").strip()
                note = t.get("note") or t.get("last") or ""
            if not name: continue
            items.append(dict(period=pid, start=st, name=name, state=state, note=note))
    con.close()
    return items

def merge(items):
    """规则归并：Jaccard>=0.8 视为同一线索，保留最早的名字为主名"""
    groups = []
    for it in items:
        hit = None
        for g in groups:
            if jac(it["name"], g["name"]) >= 0.8:
                hit = g; break
        if hit:
            hit["alias"].add(it["name"])
            hit["hist"].append(it)
        else:
            groups.append(dict(name=it["name"], alias={it["name"]}, hist=[it]))
    for g in groups:
        g["hist"].sort(key=lambda x: x["start"] or "")
        g["last"] = g["hist"][-1]
        g["state"] = g["last"]["state"]
    return groups

def canon_state(s):
    if s in DONE: return "done"
    if s in BLOCKED: return "blocked"
    if s in SHIFTED: return "shifted"
    if s in LIVE: return "active"
    return "unknown"

def today_lines(db, n=8):
    """今天/昨天新增的卡 —— 供料延迟的最低成本补偿。
    编年史按月，今天的进展在月记里还没有，但卡是实时的。
    按主题聚合，而不是按 id 取最新（最新往往只是最后提炼的那一批）。"""
    import sqlite3, datetime
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    out = []
    for label, day in (("今天", 0), ("昨天", 1)):
        d = (datetime.date.today() - datetime.timedelta(days=day)).isoformat()
        total = con.execute(
            "SELECT count(*) FROM cards WHERE status='active' AND substr(created_at,1,10)=?", (d,)
        ).fetchone()[0]
        if not total:
            out.append((label, d, total, [], []))
            continue
        topics = con.execute(
            """SELECT topic, count(*) n FROM cards
               WHERE status='active' AND substr(created_at,1,10)=?
                 AND topic IS NOT NULL AND topic NOT IN ('', 'misc')
               GROUP BY 1 ORDER BY 2 DESC LIMIT 8""", (d,),
        ).fetchall()
        # 每个主题挑一张最新卡当代表
        reps = []
        for t in topics:
            r = con.execute(
                """SELECT title FROM cards WHERE status='active' AND substr(created_at,1,10)=?
                   AND topic=? ORDER BY id DESC LIMIT 1""", (d, t["topic"]),
            ).fetchone()
            reps.append((t["topic"], t["n"], r["title"] if r else ""))
        out.append((label, d, total, [(t["topic"], t["n"]) for t in topics], reps))
    return out


def render(groups, db=None, today=None):
    by = defaultdict(list)
    for g in groups:
        by[canon_state(g["state"])].append(g)
    order = [("blocked", "卡住的"), ("active", "进行中"), ("shifted", "转向了"),
             ("unknown", "状态不明"), ("done", "已完结")]
    L = []
    L.append("# 线索现状（自动生成，别手改）")
    L.append("")
    L.append(f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
             f"共 {len(groups)} 条线索")
    L.append("")
    for key, title in order:
        gs = by.get(key) or []
        if not gs: continue
        L.append(f"## {title}（{len(gs)}）")
        L.append("")
        gs.sort(key=lambda g: g["last"]["start"] or "", reverse=True)
        for g in gs:
            seen = [h["period"] for h in g["hist"]]
            star = "*" if len(g["alias"]) > 1 else ""
            L.append(f"- {g['name']}{star}  "
                     f"[{g['last']['period']} · 出现{len(seen)}次]")
            if g["last"]["note"]:
                L.append(f"  - {g['last']['note']}")
        L.append("")
    if db:
        try:
            for label, d, total, topics, reps in today_lines(db):
                if not total:
                    continue
                L.append("")
                L.append(f"## {label}新增 {total} 张 · {d}")
                if topics:
                    L.append("主题分布：" + " / ".join(f"{t}({n})" for t, n in topics))
                for topic, n, title in reps[:6]:
                    L.append(f"- [{topic}] {title[:48]}")
        except Exception as e:
            L.append(f"_(今天段生成失败: {e})_")
    L.append("---")
    L.append(f"*标记：\\* = 由多条同名记录合并而来*")
    return "\n".join(L)

if __name__ == "__main__":
    items = load(DB)
    groups = merge(items)
    md = render(groups, db=DB)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"threads {len(items)} -> groups {len(groups)} -> {OUT} ({len(md)} chars)")
