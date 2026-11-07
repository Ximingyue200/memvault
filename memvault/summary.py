"""知识摘要：带问题的持久化快照。

与 dream 的区别：
  dream   = 一条判断（「他 X，不是 Y」），短、锋利、可反驳
  summary = 一段回答（可以分段、可以长），持久、可刷新、可标脏

用法：
  python3 -m memvault.summary --list
  python3 -m memvault.summary --refresh "记忆库现在做到哪一步"
  python3 -m memvault.summary --all          # 重算所有 stale
"""
import json
import re
import sys
import time

from .dream import con, get_settings, call, _pick_by_query, llm_cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS summaries(
  id            INTEGER PRIMARY KEY,
  query         TEXT UNIQUE NOT NULL,
  content       TEXT,
  source_cards  TEXT,
  n_cards       INTEGER DEFAULT 0,
  refreshed_at  TEXT,
  is_stale      INTEGER DEFAULT 0,
  created_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sum_stale ON summaries(is_stale);
"""

PROMPT_S = """你在给「这个人」写一份持久的档案条目，回答一个具体问题。

问题：{q}

下面是相关的 {n} 条记录（时间、标题、正文）：

{r}

要求：
1. 直接回答问题。第一句就要给出结论，不要「让我们来分析」这种开场。
2. 3-6 段，每段一个要点，段与段之间空一行。
3. 每段至少挂一个具体值 —— 型号 / 端口 / 数字 / 日期 / 报错串 / 文件名。
4. 记录里没有的，直接说「这里没有记录」，一个字都不要编。
5. 不要写「根据以上记录」「综上所述」这类套话。
6. 最后单独一行写：跨度 YYYY-MM → YYYY-MM
7. 只输出正文。不要 JSON，不要 markdown 标题（不要 # 开头）。
8. 只写与问题直接相关的段落。沾边但回答不了问题的一律整段略过，不要为凑长度把检索到的都写进去。宁可 3 段说透，不要 7 段稀释。"""


def ensure(c):
    c.executescript(SCHEMA)
    c.commit()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def refresh(c, q, cfg=None, apply=True):
    """重算一个问题。返回 dict。"""
    ensure(c)
    if cfg is None:
        cfg = llm_cfg(c, "summary")
    q = (q or "").strip()
    if not q:
        return {"ok": False, "error": "empty query"}

    t0 = time.time()
    groups = _pick_by_query(c, q, limit=60)
    rows = []
    for _, items in groups:
        rows.extend(items)
    if not rows:
        return {"ok": False, "error": "no cards matched", "query": q}

    lines = []
    for r in rows:
        body = re.sub(r"\s+", " ", (r["body"] or ""))[:220]
        dt = (r["occurred_at"] or "")[:10] or "????-??-??"
        lines.append("- [%s] #%d %s ｜ %s" % (dt, r["id"], r["title"], body))
    try:
        from .focus import inject as _inject
        _fx = _inject(c) or ""
        if _fx:
            lines.insert(0, _fx)
    except Exception:
        pass
    prompt = PROMPT_S.format(q=q, n=len(rows), r="\n".join(lines))

    t1 = time.time()
    raw = call(prompt, tries=3, cfg=cfg)
    t2 = time.time()
    content = (raw or "").strip()
    content = re.sub(r"^#+\s*", "", content, flags=re.M)

    ids = [str(r["id"]) for r in rows]
    if apply and content:
        c.execute(
            """INSERT INTO summaries(query, content, source_cards, n_cards,
                                     refreshed_at, is_stale, created_at)
               VALUES(?,?,?,?,?,0,?)
               ON CONFLICT(query) DO UPDATE SET
                 content=excluded.content,
                 source_cards=excluded.source_cards,
                 n_cards=excluded.n_cards,
                 refreshed_at=excluded.refreshed_at,
                 is_stale=0""",
            (q, content, json.dumps(ids), len(ids), _now(), _now()),
        )
        c.commit()

    return {
        "ok": True,
        "query": q,
        "cards": len(rows),
        "prompt_chars": len(prompt),
        "llm_s": round(t2 - t1, 1),
        "total_s": round(time.time() - t0, 1),
        "applied": bool(apply and content),
        "content": content,
        "sample_ids": ids[:8],
    }


def listing(c):
    ensure(c)
    rows = c.execute(
        """SELECT id, query, content, n_cards, refreshed_at, is_stale
           FROM summaries ORDER BY is_stale DESC, refreshed_at DESC"""
    ).fetchall()
    return [
        {"id": r[0], "query": r[1], "content": r[2], "n_cards": r[3],
         "refreshed_at": r[4], "is_stale": bool(r[5])}
        for r in rows
    ]


def get(c, q):
    ensure(c)
    r = c.execute(
        "SELECT id,query,content,source_cards,n_cards,refreshed_at,is_stale "
        "FROM summaries WHERE query=?", (q,)
    ).fetchone()
    if not r:
        return None
    return {"id": r[0], "query": r[1], "content": r[2],
            "source_cards": json.loads(r[3] or "[]"), "n_cards": r[4],
            "refreshed_at": r[5], "is_stale": bool(r[6])}


def mark_stale(c, q=None):
    """标脏。q 为空则全部标脏（有新卡入库后调用）。"""
    ensure(c)
    if q:
        c.execute("UPDATE summaries SET is_stale=1 WHERE query=?", (q,))
    else:
        c.execute("UPDATE summaries SET is_stale=1")
    c.commit()
    return c.total_changes


def refresh_all(c, cfg=None, limit=10):
    ensure(c)
    if cfg is None:
        cfg = llm_cfg(c, "summary")
    rows = c.execute(
        "SELECT query FROM summaries WHERE is_stale=1 "
        "ORDER BY refreshed_at ASC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for (q,) in rows:
        try:
            r = refresh(c, q, cfg=cfg, apply=True)
            out.append({"query": q, "ok": r.get("ok"),
                        "cards": r.get("cards"), "llm_s": r.get("llm_s"),
                        "error": r.get("error")})
        except Exception as e:
            out.append({"query": q, "ok": False, "error": str(e)})
    return out


if __name__ == "__main__":
    c = con()
    if "--list" in sys.argv:
        for s in listing(c):
            flag = "STALE" if s["is_stale"] else "ok   "
            print(f"[{flag}] {s['n_cards']:>3}张  {s['query']}  ({s['refreshed_at']})")
        sys.exit(0)
    if "--all" in sys.argv:
        for r in refresh_all(c):
            print(r)
        sys.exit(0)
    if "--refresh" in sys.argv:
        i = sys.argv.index("--refresh")
        q = sys.argv[i + 1] if len(sys.argv) > i + 1 else ""
        r = refresh(c, q, apply=True)
        print({k: v for k, v in r.items() if k != "content"})
        print("\n" + (r.get("content") or ""))
        sys.exit(0)
    print(__doc__)
