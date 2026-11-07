"""会话焦点（工作记忆的替代品）。

真正的「工作记忆」是运行时状态（此刻手里翻着哪几页），批处理系统无法拥有。
这里做的是它的**外部近似**：把每个会话压缩成「在解决什么问题 / 还差什么」，
供三处使用：
  1. dream / summary 生成时注入，当作「当前焦点」，治盲抽导致的撞题
  2. 新会话开始时注入，治「上下文压缩后丢线」
  3. /admin 的「现在在干什么」页
"""
import json, re, time
from .dream import con, get_settings, call

SCHEMA = """
CREATE TABLE IF NOT EXISTS focus(
  id INTEGER PRIMARY KEY,
  conv_id TEXT UNIQUE NOT NULL,
  topic TEXT,
  open_items TEXT,
  done_items TEXT,
  status TEXT DEFAULT 'open',
  done_at TEXT,
  span_ids TEXT,
  last_ts TEXT,
  n_spans INTEGER DEFAULT 0,
  model TEXT,
  built_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_focus_ts ON focus(last_ts);
"""

PROMPT_F = """你在给一个会话写「工作记忆」：这个会话此刻在解决什么问题、还差什么没做完。

你看到的是这个会话最近 {n} 条消息（时间正序，最后一条是最近说的）。

另外，这是上次给这个会话写的待办（**可能已经做完了，需要你判断**）：
{prev}

输出严格四行，不要别的：
topic: 一句话说清「在解决什么问题」。用动宾短语，不要「讨论」「交流」这类空话。
open: 还没做完的事。**上次待办里已经做完的必须剔除**，不要重复报。都做完了写「无」。
closed: 上次待办里哪些已经做完了，用分号隔开；没有就写「无」。
diff: 卡在哪 / 下一步该做什么。如果没有卡点写「无」。

要求：
- 只根据这些消息判断，不要推测。
- topic 不超过 30 字。
- 不要 markdown，不要代码块，就是三行文本。

消息：
{msgs}"""


def _migrate(c):
    cols = {r[1] for r in c.execute("PRAGMA table_info(focus)")}
    for k, t in (("done_items", "TEXT"), ("status", "TEXT DEFAULT 'open'"), ("done_at", "TEXT")):
        if k not in cols:
            c.execute("ALTER TABLE focus ADD COLUMN %s %s" % (k, t))
    c.commit()


def ensure(c):
    c.executescript(SCHEMA)
    _migrate(c)
    c.commit()


def _recent_spans(c, conv_id, n=15):
    """spans 表的正文列叫 quote，不是 content；按 seq 取最近的更准。"""
    return c.execute("""
        SELECT span_id, role, ts, quote AS content, seq FROM spans
        WHERE conv_id=? AND quote IS NOT NULL AND quote<>''
        ORDER BY COALESCE(seq, 0) DESC LIMIT ?""",
        (conv_id, n)).fetchall()[::-1]


def _parse(txt):
    out = {"topic": "", "open": "", "closed": "", "diff": ""}
    for line in (txt or "").splitlines():
        m = re.match(r"^\s*(topic|open|closed|diff)\s*[:：]\s*(.+)$", line.strip(), re.I)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
    return out


def build(c, conv_id, apply=True, cfg=None):
    ensure(c)
    rows = _recent_spans(c, conv_id, 15)
    if len(rows) < 3:
        return {"ok": False, "error": "too_few_spans", "n": len(rows)}
    cfg = cfg or get_settings(c)
    msgs = "\n".join(
        f"[{(r['ts'] or '')[:16]}] {r['role']}: "
        + " ".join((r["content"] or "").split())[:220]
        for r in rows)
    prev = c.execute("SELECT open_items FROM focus WHERE conv_id=?", (conv_id,)).fetchone()
    prev_open = []
    if prev and prev["open_items"]:
        try:
            prev_open = [x for x in json.loads(prev["open_items"]) if x]
        except Exception:
            prev_open = []
    prompt = PROMPT_F.replace("{n}", str(len(rows))).replace("{msgs}", msgs)
    prompt = prompt.replace("{prev}", "\n".join("- " + x for x in prev_open) if prev_open else "（无）")
    txt = call(prompt, tries=2, cfg=cfg)
    d = _parse(txt)
    if not d["topic"]:
        return {"ok": False, "error": "parse_failed", "raw": (txt or "")[:200]}
    open_items = [x for x in re.split(r"[；;、]", d["open"]) if x.strip() and x.strip() != "无"]
    last_ts = (rows[-1]["ts"] or "")[:19]
    if apply:
        st_ = "open" if open_items else "done"
        c.execute("""INSERT INTO focus(conv_id,topic,open_items,span_ids,last_ts,n_spans,model,built_at,status)
                     VALUES(?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(conv_id) DO UPDATE SET
                       topic=excluded.topic, open_items=excluded.open_items,
                       span_ids=excluded.span_ids, last_ts=excluded.last_ts,
                       n_spans=excluded.n_spans, model=excluded.model, built_at=excluded.built_at,
                       status=excluded.status""",
                  (conv_id, d["topic"], json.dumps(open_items, ensure_ascii=False),
                   json.dumps([r["span_id"] for r in rows], ensure_ascii=False),
                   last_ts, len(rows), cfg.get("dream.model") or "deepseek-flash",
                   time.strftime("%Y-%m-%dT%H:%M:%S"), st_))
        c.commit()
    if apply:
        now_ = time.strftime("%Y-%m-%dT%H:%M:%S")
        with c:
            if open_items:
                c.execute("UPDATE focus SET status='open', done_at=NULL WHERE conv_id=?", (conv_id,))
            else:
                c.execute("UPDATE focus SET status='done', done_at=COALESCE(done_at, ?) WHERE conv_id=?",
                          (now_, conv_id))
    return {"ok": True, "conv_id": conv_id, "n": len(rows), **d,
            "open_items": open_items, "closed": d.get("closed", ""), "prev_open": prev_open,
            "prompt": len(prompt), "applied": apply}


def build_recent(c, hours=72, apply=True, limit=20):
    ensure(c)
    convs = c.execute("""
        SELECT conv_id, MAX(ts) mx, COUNT(*) n FROM spans
        WHERE quote IS NOT NULL AND quote<>'' AND ts >= datetime('now', ?)
        GROUP BY conv_id HAVING n >= 5 ORDER BY mx DESC LIMIT ?""",
        (f"-{hours} hours", limit)).fetchall()
    out = []
    for r in convs:
        try:
            out.append(build(c, r["conv_id"], apply=apply))
        except Exception as e:
            out.append({"ok": False, "conv_id": r["conv_id"], "error": str(e)[:100]})
    return {"ok": True, "hours": hours, "convs": len(convs), "results": out}


def close(c, conv_id):
    """把「还没做完」移进 done_items，状态改 done（不丢记录）。"""
    ensure(c)
    row = c.execute("SELECT open_items, done_items FROM focus WHERE conv_id=?", (conv_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "not_found", "conv_id": conv_id}
    try:
        cur_open = [x for x in json.loads(row["open_items"] or "[]") if x]
    except Exception:
        cur_open = []
    try:
        done = [x for x in json.loads(row["done_items"] or "[]") if x]
    except Exception:
        done = []
    merged = list(dict.fromkeys(done + cur_open))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with c:
        cur = c.execute("UPDATE focus SET open_items='[]', done_items=?, status='done', done_at=? WHERE conv_id=?",
                        (json.dumps(merged, ensure_ascii=False), now, conv_id))
    return {"ok": True, "conv_id": conv_id, "moved": len(cur_open),
            "done_total": len(merged), "changed": cur.rowcount}


def reopen(c, conv_id):
    """已完成改回未完成（done_items 全部回到 open）。"""
    ensure(c)
    row = c.execute("SELECT done_items FROM focus WHERE conv_id=?", (conv_id,)).fetchone()
    if not row:
        return {"ok": False, "error": "not_found", "conv_id": conv_id}
    try:
        done = [x for x in json.loads(row["done_items"] or "[]") if x]
    except Exception:
        done = []
    with c:
        c.execute("UPDATE focus SET open_items=?, status='open', done_at=NULL WHERE conv_id=?",
                  (json.dumps(done, ensure_ascii=False), conv_id))
    return {"ok": True, "conv_id": conv_id, "restored": len(done)}


def listing(c, limit=40, status=None):
    ensure(c)
    if status in ("open", "done"):
        rows = c.execute("SELECT * FROM focus WHERE COALESCE(status,'open')=? "
                         "ORDER BY last_ts DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows = c.execute("SELECT * FROM focus ORDER BY last_ts DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try: d["open_items"] = json.loads(d.get("open_items") or "[]")
        except Exception: d["open_items"] = []
        out.append(d)
    return out


def inject(c, conv_id=None, hours=24):
    """给 dream/summary 用的「当前焦点」前缀。conv_id 为空则取最近一个。"""
    ensure(c)
    if conv_id:
        r = c.execute("SELECT * FROM focus WHERE conv_id=?", (conv_id,)).fetchone()
    else:
        r = c.execute("""SELECT * FROM focus WHERE last_ts >= datetime('now', ?)
                         ORDER BY last_ts DESC LIMIT 1""", (f"-{hours} hours",)).fetchone()
    if not r:
        return ""
    items = []
    try: items = json.loads(r["open_items"] or "[]")
    except Exception: pass
    s = f"【当前焦点】{r['topic']}（最后活动 {(r['last_ts'] or '')[:16]}）"
    if items:
        s += "\n【还没做完】" + "；".join(items[:4])
    return s


if __name__ == "__main__":
    import sys
    c = con()
    if "--all" in sys.argv:
        do = "--apply" in sys.argv
        try:
            from .dream import get_settings as _gs
            _hrs = int(_gs(c).get("focus.hours") or 72)
        except Exception:
            _hrs = 72
        r = build_recent(c, hours=_hrs, apply=do)
        for x in r["results"]:
            if x.get("ok"):
                print(f"  {x['conv_id'][:24]:26} {x['topic'][:40]}")
            else:
                print(f"  {x.get('conv_id','?')[:24]:26} ERR {x.get('error')}")
        print(f"{r['convs']} 个会话  apply={do}")
    else:
        for x in listing(c, 20):
            print(f"{x['last_ts'][:16]}  {x['topic'][:50]}")
