# -*- coding: utf-8 -*-
"""pitfall.py —— 从原文里把"踩过的坑"抽成可检索的卡。

背景：learn 抽的是"关于用户的结论"，排障过程会被概括成「用户修好了 X」，
错误串与修法一起丢掉。这个模块单独扫 span，要求「错误特征 + 修复标记」
同时出现才出卡，错误串归一化后存进 cards.topic 作为检索签名。

用法：
    python3 -m memvault.pitfall --scan [--days N] [--dry]
    python3 -m memvault.pitfall --search "listen tcp4"
"""
import argparse
import os
import re
import sqlite3
import sys

DB = os.environ.get("MEMVAULT_DB") or "/opt/memvault/data/memvault.db"

# 错误特征：宁窄勿宽，避免把普通讨论当成坑
ERR = re.compile(
    r"(Traceback \(most recent call last\)"
    r"|SyntaxError|IndentationError|NameError|TypeError|ValueError|KeyError|AttributeError"
    r"|DatabaseError|OperationalError|psycopg2\.|sqlite3\."
    r"|EADDRINUSE|ECONNREFUSED|ENOENT|EACCES|ETIMEDOUT"
    r"|\bfailed to\b|\bfailed:|\brefused\b|\bdenied\b"
    r"|cannot assign requested address|no such (?:file|column|table|module)"
    r"|database disk image is malformed"
    r"|exit code [1-9]|Exited \(\d+\)"
    r"|Error starting userland proxy|HTTP (?:400|401|403|404|422|500|502|503)\b)",
    re.I,
)

# 修复标记：出现这些词，说明这段在讲"怎么解决的"
FIX = re.compile(
    r"(改成|改为|改到|换成|修法|修正|解决|根因|原因是|问题出|绕开|绕过"
    r"|之后就好了|就好了|不再|不用了|加上.{0,12}(就好|解决)"
    r"|fixed by|because|root cause|turned out|solution)",
    re.I,
)

CARD_KIND = "event"          # 坑是"某次发生的事"，不新造 kind
CARD_CATEGORY = "pitfall"    # 展示用分类名
CARD_MARK = "pitfall-v1"     # 真正的识别标记，写在 extractor_version
MAX_BODY = 2000


def _norm_sig(line):
    """把错误行归一化成检索签名：去掉时间、IP、端口、路径、十六进制、数字。"""
    s = (line or "").strip()
    s = re.sub(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?", " ", s)
    s = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", " ", s)
    s = re.sub(r"[A-Za-z]:?[\w./\\-]{4,}\.\w{1,6}", " ", s)   # 路径/文件名
    s = re.sub(r"\b[0-9a-f]{8,}\b", " ", s)
    s = re.sub(r"\b\d+\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" :-|")
    return s[:140]


def _pick(text):
    """返回 (错误行, 修复行) 或 (None, None)。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    err = None
    for l in lines:
        if ERR.search(l):
            err = l
            break
    if not err:
        return None, None
    fix = None
    for l in lines:
        if l == err:
            continue
        if FIX.search(l) and len(l) >= 8:
            fix = l
            break
    if not fix:
        return None, None       # 只有报错没有修法 → 不算"坑"，可能是贴日志
    return err, fix


def _title(err, fix):
    core = re.sub(r"\s+", " ", fix).strip(" ：:，,。.")
    core = re.sub(r"^(然后|但是|所以|因为|另外|还有|最后|于是)", "", core)
    core = core[:26]
    if len(core) < 4:
        core = _norm_sig(err)[:26]
    return ("踩坑：" + core)[:80]


def _body(err, fix, extra, conv):
    return (
        "现象：%s\n"
        "修法：%s\n"
        "线索：%s\n"
        "来源：%s"
        % (err[:400], fix[:400], extra[:600], conv)
    )[:MAX_BODY]


def _exists(con, sig):
    if not sig:
        return True
    r = con.execute(
        "select id from cards where extractor_version=? and topic=? limit 1", (CARD_MARK, sig)
    ).fetchone()
    return bool(r)


def scan(con, days=2, dry=False, max_cards=40):
    from memvault import store

    since = "datetime('now','-%d day')" % int(days)
    rows = con.execute(
        "select span_id, conv_id, seq, role, quote, created_at from spans "
        "where created_at >= %s order by conv_id, seq" % since
    ).fetchall()
    print("[scan] 近 %d 天 span：%d 条" % (days, len(rows)), flush=True)

    made = skipped = 0
    by_conv = {}
    for r in rows:
        by_conv.setdefault(r[1], []).append(r)

    for conv, sp in by_conv.items():
        for i in range(0, len(sp), 3):
            win = sp[i:i + 6]
            if not win:
                continue
            text = "\n".join(w[4] or "" for w in win)
            err, fix = _pick(text)
            if not err:
                continue
            sig = _norm_sig(err)
            if len(sig) < 8 or _exists(con, sig):
                skipped += 1
                continue
            if made >= max_cards:
                break

            src = win[0]
            for w in win:
                if (w[4] or "").strip() and (err[:40] in (w[4] or "")):
                    src = w
                    break
            quote = (src[4] or "").strip()[:120]
            card = {
                "title": _title(err, fix),
                "body": _body(err, fix, win[0][4] or "", conv),
                "kind": CARD_KIND,
                "category": CARD_CATEGORY,
                "extractor_version": CARD_MARK,
                "topic": sig,
                "owner": "agent",
                "source_path": "pitfall/scan",
                "extractor_version": "pitfall-v1",
            }
            if dry:
                print("  [dry] %s  ← %s" % (card["title"], sig[:70]), flush=True)
                made += 1
                continue

            evs = [{"span_id": src[0], "role": src[3] or "assistant", "quote": quote,
                    "char_start": 0, "char_end": len(quote), "weight": 0.9}]
            cid, reason = store.ingest_card(con, card, evs, span_lookup=None)
            if cid:
                made += 1
                try:
                    from memvault import embed
                    vec = embed.embed([card["title"] + " " + card["body"]])[0]
                    store.put_vector(con, cid, vec)
                except Exception as e:
                    print("  [warn] 向量失败（不影响入库）：%s" % str(e)[:80], flush=True)
                print("  + #%s %s" % (cid, card["title"]), flush=True)
            else:
                skipped += 1
                print("  - 拒收(%s) %s" % (reason, sig[:60]), flush=True)

    con.commit()
    print("[done] 新增 %d 张，跳过 %d" % (made, skipped), flush=True)
    return made, skipped



def _semantic(con, q, limit=2, min_score=0.55):
    """语义兜底：拿 query 向量与坑卡向量算余弦。"""
    import math
    try:
        from . import embed
        qv = embed.embed([q])[0]
    except Exception:
        return []
    rows = con.execute(
        "select c.id, c.title, c.topic, substr(c.body,1,400), c.created_at, v.vec "
        "from cards c join vectors v on v.card_id=c.id "
        "where c.extractor_version=? order by c.id",
        (CARD_MARK,),
    ).fetchall()
    if not rows:
        return []
    import array
    qn = math.sqrt(sum(x * x for x in qv)) or 1.0
    scored = []
    for r in rows:
        try:
            vec = array.array("f")
            vec.frombytes(r[5])
        except Exception:
            continue
        vn = math.sqrt(sum(x * x for x in vec)) or 1.0
        dot = sum(a * b for a, b in zip(qv, vec))
        scored.append((dot / (qn * vn), r))
    scored.sort(key=lambda t: -t[0])
    if not scored:
        return []
    best = scored[0][0]
    if best < min_score:
        return []
    # 短而含糊的查询（≤5 字）语义不可靠，要求更高分，否则宁可说没命中
    if len(q) <= 5 and best < 0.62:
        return []
    # 只留「够近的」：高于阈值，且不差于最佳 0.10
    keep = [r for sc, r in scored if sc >= max(min_score, best - 0.10)]
    return keep[:limit]


def search(con, q, limit=10):
    q = (q or "").strip()
    if not q:
        return []
    sig = _norm_sig(q)
    rows = con.execute(
        "select id, title, topic, substr(body,1,400), created_at from cards "
        "where extractor_version=? and (topic like ? or topic like ? or body like ? or title like ?) "
        "order by created_at desc limit ?",
        (CARD_MARK, q + "%", "%" + sig + "%", "%" + q + "%", "%" + q + "%", limit),
    ).fetchall()
    if not rows and sig:
        rows = con.execute(
            "select id, title, topic, substr(body,1,400), created_at from cards "
            "where extractor_version=? and body like ? order by created_at desc limit ?",
            (CARD_MARK, "%" + sig.split(" ")[0] + "%", limit),
        ).fetchall()
    if not rows:
        rows = _semantic(con, q, limit=min(limit, 5))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--search", default="")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--db", default=DB)
    a = ap.parse_args()

    con = sqlite3.connect(a.db, timeout=10)
    try:
        if a.search:
            rows = search(con, a.search)
            if not rows:
                print("没有匹配的踩坑记录。")
            for r in rows:
                print("\n#%s  %s\n  %s\n  %s" % (r[0], r[1], (r[2] or "")[:100], (r[4] or "")[:10]))
                for line in (r[3] or "").splitlines():
                    print("  " + line)
            return 0
        if a.scan:
            scan(con, days=a.days, dry=a.dry)
            return 0
    finally:
        con.close()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
