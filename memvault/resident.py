# -*- coding: utf-8 -*-
"""常驻记忆层：2,200 字的「他是谁 + 现在在干什么」，每轮注入。

与检索层的分工：
  - 常驻层：固定预算、每次全给、不检索、答「这个人是谁、在推什么」
  - 检索层：按需、topk、答「某件具体的事」

预算（字）：
  画像 500 / 当前状态 400 / 最近一月 300 / 未完成 400 / 自我笔记 300 / 今天 300 = 2,200
"""
import json
import sqlite3

BUDGET = {
    "profile": 500,
    "core": 400,
    "month": 300,
    "threads": 400,
    "notes": 300,
    "today": 300,
}


def _clip(txt, n):
    """截到 n 字以内的最后一个句号/分号，找不到就加省略号。"""
    txt = (txt or "").strip()
    if len(txt) <= n:
        return txt
    head = txt[:n]
    cut = max(head.rfind("。"), head.rfind("；"), head.rfind("\n"), head.rfind(". "))
    if cut > n * 0.4:
        return head[:cut + 1].rstrip()
    return head.rstrip() + "…"


def _setting(con, key, default=""):
    try:
        r = con.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        return (r[0] if r and r[0] is not None else default)
    except Exception:
        return default


def _profile(con, n):
    """画像：优先 profile.note（JSON: {summary, points|blocks}），退化成纯文本。"""
    raw = _setting(con, "profile.note", "")
    if not raw:
        return ""
    try:
        d = json.loads(raw)
    except Exception:
        return _clip(raw, n)
    parts = []
    s = (d.get("summary") or "").strip()
    if s:
        parts.append(s)
    blocks = d.get("blocks") or {}
    if isinstance(blocks, dict):
        for k in ("basic", "insights", "recent", "values"):
            for line in (blocks.get(k) or []):
                line = (line or "").strip()
                if line:
                    parts.append("· " + line)
    elif isinstance(d.get("points"), list):
        for p in d["points"]:
            if isinstance(p, dict):
                k, v = p.get("k", ""), p.get("v", "")
                if k or v:
                    parts.append("· %s：%s" % (k, v))
    return _clip("\n".join(parts), n)


def _chronicle(con, n, gran):
    try:
        r = con.execute(
            "SELECT title, summary FROM chronicles WHERE granularity=? "
            "ORDER BY end_ts DESC LIMIT 1", (gran,)).fetchone()
        if r and r[1]:
            return _clip(r[1], n)
    except Exception:
        pass
    return ""


def _threads(con, n):
    """未完成：active + blocked，按 last_touched 倒序，每条一行。"""
    try:
        rows = con.execute(
            "SELECT name, state, note FROM threads WHERE state IN ('active','blocked') "
            "ORDER BY COALESCE(last_touched, updated_at) DESC LIMIT 14").fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    out, used = [], 0
    for name, state, note in rows:
        tag = "在推" if state == "active" else "卡住"
        line = "· [%s] %s" % (tag, (name or "").strip())
        tail = _clip((note or "").strip(), 40)
        if tail:
            line += " —— " + tail
        if used + len(line) > n:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _notes(con, n):
    """自我笔记：weight 高的优先。"""
    try:
        rows = con.execute(
            "SELECT body FROM self_notes ORDER BY COALESCE(weight,0) DESC, at DESC LIMIT 6").fetchall()
    except Exception:
        return ""
    out, used = [], 0
    for (b,) in rows:
        b = (b or "").strip()
        if not b:
            continue
        line = "· " + _clip(b, 110)
        if used + len(line) > n:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _today(con, n):
    """今天新增：标题 + 分类。"""
    try:
        rows = con.execute(
            "SELECT title, category FROM cards WHERE status='active' "
            "AND substr(COALESCE(occurred_at, created_at), 1, 10) = date('now','+8 hours') "
            "ORDER BY COALESCE(heat,0) DESC, id DESC LIMIT 20").fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    out, used = [], 0
    for title, cat in rows:
        line = "· " + (title or "").strip()
        if cat:
            line += "（%s）" % cat
        if used + len(line) > n:
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def resident_block(con=None, budget=None):
    """返回常驻记忆块（约 2,200 字）。失败返回空串，绝不抛。"""
    b = dict(BUDGET)
    if budget:
        b.update(budget)
    own = con is None
    try:
        if own:
            import os
            from .store import connect
            con = connect(os.environ.get("GW_DB", "/opt/memvault/data/memvault.db"))
        secs = [
            ("他是谁", _profile(con, b["profile"])),
            ("当前状态", _chronicle(con, b["core"], "core")),
            ("最近一个月", _chronicle(con, b["month"], "month")),
            ("手上没完的", _threads(con, b["threads"])),
            ("他怎么说自己", _notes(con, b["notes"])),
            ("今天新记的", _today(con, b["today"])),
        ]
        secs = [(k, v) for k, v in secs if v]
        if not secs:
            return ""
        out = ['<resident note="这是关于该用户的常驻背景，每轮都在。'
               '文中「他」指用户本人；「我」出现在总结里也是记录者口吻，不是你。'
               '这是背景不是事实源，具体细节以检索到的记忆为准">']
        for k, v in secs:
            out.append("【%s】" % k)
            out.append(v)
            out.append("")
        out.append("</resident>")
        return "\n".join(out)
    except Exception as e:
        try:
            from .gateway import log
            log("resident_block err %s" % e)
        except Exception:
            pass
        return ""
    finally:
        if own and con is not None:
            try:
                con.close()
            except Exception:
                pass


if __name__ == "__main__":
    import sys
    blk = resident_block()
    print("长度:", len(blk))
    print(blk[:1500])
