#!/usr/bin/env python3
"""learn 修 LLM_KEY：extract 模块在 import 时就把 key 读进模块常量，
所以必须在 import extract **之前**设置环境变量。"""
import os
import sqlite3
import sys
import time

sys.path.insert(0, "/opt/memvault")

WINDOW = 6
MIN_CHARS = 200
CURSOR_KEY = "learn.cursor"


def _cfg(con, key, default=""):
    r = con.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return r[0] if r and r[0] else default


def _cursor(con):
    r = con.execute("SELECT v FROM settings WHERE k=?", (CURSOR_KEY,)).fetchone()
    return int(r[0]) if r and r[0] else 0


def _set_cursor(con, v):
    con.execute(
        "INSERT INTO settings(k,v,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
        (CURSOR_KEY, str(v), time.strftime("%Y-%m-%dT%H:%M:%S")))
    con.commit()


def _llm_url_of(base):
    """把面板里填的基地址拼成 chat/completions 端点。

    演示端点（pollinations）本身就是完整端点，不能再拼 /v1/chat/completions。
    """
    b = (base or "").strip().rstrip("/")
    if not b:
        return ""
    if b.endswith("/chat/completions"):
        return b
    if "pollinations" in b:
        return b
    return b + "/v1/chat/completions"


def _env_first(con):
    """必须在 import extract 之前调用。

    只覆盖「库里真的配了」的项；没配就交给 extract 自己的默认值（演示端点）。
    """
    key = _cfg(con, "dream.api_key")
    if key:
        os.environ["MEMVAULT_LLM_KEY"] = key
    model = _cfg(con, "llm.model") or _cfg(con, "dream.model")
    if model:
        os.environ["MEMVAULT_LLM_MODEL"] = model
    url = _llm_url_of(_cfg(con, "llm.base") or _cfg(con, "dream.api_base"))
    if url:
        os.environ["MEMVAULT_LLM_URL"] = url



def _mark_seen(con, group):
    """把已处理的 span 记进 learn_seen，避免重复 learn。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    for s in group:
        con.execute("INSERT OR IGNORE INTO learn_seen(span_id, at) VALUES(?,?)", (s[0], now))


def learn(con, limit=120, dry=False, window=WINDOW, max_cards=4):
    from memvault import extract as ex
    from memvault import store

    cur = _cursor(con)
    since = (con.execute("SELECT v FROM settings WHERE k='learn.since'").fetchone() or ["2026-11-01"])[0]
    rows = con.execute(
        """SELECT s.span_id, s.conv_id, s.msg_id, s.role, s.quote, s.ts
           FROM spans s
           WHERE NOT EXISTS (SELECT 1 FROM learn_seen z WHERE z.span_id = s.span_id)
             AND s.ts >= ?
           ORDER BY COALESCE(s.ts,''), s.msg_id
           LIMIT ?""",
        (since, limit)).fetchall()
    if not rows:
        return 0, 0, 0, "no new spans"

    cache = {}

    def lookup(sid):
        if sid not in cache:
            r = con.execute("SELECT quote, role FROM spans WHERE span_id=?", (sid,)).fetchone()
            cache[sid] = {"quote": r[0], "role": r[1]} if r else None
        return cache[sid]

    saved = rejected = seen = 0
    last_err = ""
    _vecq = []   # 本轮新卡，flush 末尾批量补向量

    def flush(group):
        nonlocal saved, rejected, last_err
        if not group:
            return
        text = "\n".join(("用户: " if s[3] == "user" else "AI: ") + s[4] for s in group)
        if len(text) < MIN_CHARS:
            return
        try:
            cards = ex.extract(text, max_cards=max_cards)
        except Exception as e:
            last_err = str(e)[:100]
            return
        for c in cards:
            # 证据优先挑 user/user_note 的 span（definition 卡要求 human 支撑）
            human = [s for s in group if s[3] in ("user", "user_note") and s[4].strip()]
            src = max(human or group, key=lambda s: len(s[4]))
            raw_quote = (src[4] or "").strip()
            quote = raw_quote[:120]
            if not quote:
                continue
            # 不再往正文里塞「原文：」——正文是模型写的结论，证据在 card_evidence 里
            c["body"] = c["body"][:2000]
            c["title"] = c["title"][:80]
            c["owner"] = "human" if src[3] in ("user", "user_note") else "agent"
            c["extractor_version"] = "learn-v1"
            c["source_path"] = "conv_log/%s" % group[0][1]
            # 偏移尽量用 span 内的真实位置；定位不到就退回整段（不再伪造 0/len）
            _off = raw_quote.find(quote)
            evs = [{"span_id": src[0], "role": src[3], "quote": quote,
                    "char_start": _off if _off >= 0 else 0,
                    "char_end": (_off + len(quote)) if _off >= 0 else len(quote),
                    "weight": 0.8}]
            cid, reason = store.ingest_card(con, c, evs, span_lookup=lookup)
            if cid:
                saved += 1
                _vecq.append((cid, (c["title"] + " " + c["body"])[:1200]))
            else:
                rejected += 1
                if not last_err:
                    last_err = "reject:%s" % reason
        if _vecq:
            try:
                from memvault import embed as _emb
                _vs = _emb.embed([t for _, t in _vecq])
                for (_cid, _t), _v in zip(_vecq, _vs):
                    store.put_vector(con, _cid, _v)
                con.commit()
            except Exception as e:
                # 向量服务没起不该卡住游标：卡片已经入库了，缺向量只是检索退化。
                # 用一条 warn 记一次，别让 learn 每次重跑同一批 span。
                print("[warn] 向量补写失败（不影响入库）：%s" % str(e)[:80], flush=True)
            _vecq.clear()

    batch = []
    for r in rows:
        batch.append(tuple(r))
        seen += 1
        if len(batch) >= window:
            flush(batch)
            if not dry:
                _mark_seen(con, batch)
            batch = []
            if not dry:
                con.commit()
                # 出错（比如 401 / 超时）就别把游标推过去，下一轮重试这批
                if not last_err:
                    _set_cursor(con, rows[seen - 1][2])
    if batch:
        flush(batch)
        if not dry:
            _mark_seen(con, batch)
    if not dry:
        con.commit()
        if not last_err:
            _set_cursor(con, rows[-1][2])
        else:
            print("learn 出错，游标停在 %s 不回退" % _cursor(con))
    return saved, rejected, seen, last_err


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MEMVAULT_DB") or "data/memvault.db"
    dry = "--dry" in sys.argv
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    _env_first(con)          # 先设 env
    s, r, n, err = learn(con, dry=dry)
    print("learn%s：处理 %d span，新增 %d 卡，拒绝 %d%s" %
          ("(dry)" if dry else "", n, s, r, ("  末次: " + err) if err else ""))
