# -*- coding: utf-8 -*-
"""MCP 写入的暂存队列。

agent 调 retain 时**不立刻**入库，先落这张表；攒到阈值再一次性：
抽取 → 逐张判重（余弦）→ 合并/新建/取代 → 写向量 → 标记已处理。

阈值（都可环境变量覆盖）：
  MEMVAULT_Q_MIN      条数下限，默认 5
  MEMVAULT_Q_CHARS    字数下限，默认 1500
  MEMVAULT_Q_AGE_MIN  最老一条的年龄（分钟），默认 30
  MEMVAULT_Q_CAP      硬上限，到 50 条必刷
"""
import hashlib
import os
import sqlite3
import sys
import time

QMIN = int(os.environ.get("MEMVAULT_Q_MIN", "5"))
QCHARS = int(os.environ.get("MEMVAULT_Q_CHARS", "1500"))
QAGE = int(os.environ.get("MEMVAULT_Q_AGE_MIN", "30"))
QCAP = int(os.environ.get("MEMVAULT_Q_CAP", "50"))
BATCH = int(os.environ.get("MEMVAULT_Q_BATCH", "20"))
SIM_DUP = float(os.environ.get("MEMVAULT_Q_SIM_DUP", "0.93"))
SIM_NEW = float(os.environ.get("MEMVAULT_Q_SIM_NEW", "0.80"))

DDL = """CREATE TABLE IF NOT EXISTS retain_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL DEFAULT 'unknown',
  source TEXT NOT NULL DEFAULT 'mcp',
  context TEXT,
  text TEXT NOT NULL,
  chars INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending',
  tries INTEGER NOT NULL DEFAULT 0,
  flushed_at TEXT,
  card_ids TEXT
)"""
IDX = "CREATE INDEX IF NOT EXISTS idx_queue_state ON retain_queue(state, id)"


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def ts():
    return time.time()


def ensure(con):
    con.execute(DDL)
    con.execute(IDX)
    con.commit()


def enqueue(con, text, agent="unknown", source="mcp", context=None):
    ensure(con)
    text = (text or "").strip()
    if not text:
        return None
    cur = con.execute(
        "INSERT INTO retain_queue(agent, source, context, text, chars, created_at) VALUES(?,?,?,?,?,?)",
        (agent or "unknown", source or "mcp", context, text, len(text), now()),
    )
    con.commit()
    return cur.lastrowid


def _pending(con, limit=1000):
    return con.execute(
        "SELECT id, agent, text, chars, created_at FROM retain_queue WHERE state='pending' ORDER BY id LIMIT ?",
        (limit,),
    ).fetchall()


def depth(con):
    ensure(con)
    n = con.execute("SELECT COUNT(*) FROM retain_queue WHERE state='pending'").fetchone()[0]
    chars = con.execute(
        "SELECT COALESCE(SUM(chars),0) FROM retain_queue WHERE state='pending'"
    ).fetchone()[0]
    oldest = con.execute(
        "SELECT MIN(created_at) FROM retain_queue WHERE state='pending'"
    ).fetchone()[0]
    by = con.execute(
        "SELECT agent, COUNT(*), COALESCE(SUM(chars),0) FROM retain_queue "
        "WHERE state='pending' GROUP BY agent ORDER BY 2 DESC"
    ).fetchall()
    return {
        "pending": n,
        "chars": chars,
        "oldest": oldest,
        "agents": [{"agent": a, "items": c, "chars": ch} for a, c, ch in by],
    }


def _age_min(oldest):
    if not oldest:
        return 0
    try:
        t = time.mktime(time.strptime(oldest[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0
    return max(0.0, (ts() - t) / 60.0)


def should_flush(con, force=False):
    d = depth(con)
    if force:
        return True, "force"
    if d["pending"] == 0:
        return False, "empty"
    if d["pending"] >= QCAP:
        return True, "cap(%d)" % QCAP
    if d["pending"] >= QMIN:
        return True, "count(%d)" % d["pending"]
    if d["chars"] >= QCHARS:
        return True, "chars(%d)" % d["chars"]
    if _age_min(d["oldest"]) >= QAGE:
        return True, "age(%.0fm)" % _age_min(d["oldest"])
    return False, "below-threshold"


def pending_items(con, limit=20):
    ensure(con)
    rows = con.execute(
        "SELECT id, agent, source, context, chars, created_at, text FROM retain_queue "
        "WHERE state='pending' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "agent": r[1],
                "source": r[2],
                "context": r[3],
                "chars": r[4],
                "created_at": r[5],
                "preview": (r[6] or "").replace("\n", " ")[:200],
            }
        )
    return out


# ---------- 判重 ----------

def _matrix(con):
    """{card_id: np.array}，只取有向量的卡。"""
    import numpy as np

    m = {}
    for cid, dim, blob in con.execute("SELECT card_id, dim, vec FROM vectors"):
        if not dim:
            continue
        m[cid] = np.frombuffer(blob, dtype=np.float32)
    return m


def _best(vec, mat, con):
    import numpy as np

    best_id, best = None, -1.0
    v = np.asarray(vec, dtype=np.float32)
    nv = float(np.linalg.norm(v)) or 1.0
    for cid, a in mat.items():
        na = float(np.linalg.norm(a)) or 1.0
        c = float(np.dot(v, a) / (nv * na))
        if c > best:
            best_id, best = cid, c
    if best_id is None:
        return None, 0.0
    return best_id, best


def _span_for(con, text, tag):
    """给证据造一行 span（learn 也是这么做的），返回 span_id。"""
    sid = "sp_q_" + hashlib.sha1((tag + text[:200]).encode("utf-8")).hexdigest()[:16]
    q = text[:800]
    con.execute(
        "INSERT OR IGNORE INTO spans(span_id, conv_id, msg_id, seq, role, ts, char_start, char_end, quote, text_hash, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (sid, "mcpq", 0, 0, "assistant", now(), 0, len(q), q,
         hashlib.sha1(q.encode("utf-8")).hexdigest(), now()),
    )
    return sid


def flush(con, force=False, limit=None, dry=False):
    from memvault import embed as emb
    from memvault import extract as ex
    from memvault import store

    ok, why = should_flush(con, force=force)
    if not ok:
        return {"flushed": False, "reason": why, **depth(con)}

    rows = _pending(con, limit or BATCH)
    if not rows:
        return {"flushed": False, "reason": "empty", **depth(con)}

    text = "\n".join("记录: " + (r[2] or "").strip() for r in rows)
    agents = sorted({r[1] for r in rows})
    tag = ",".join(agents)

    try:
        cards = ex.extract(text, max_cards=8)
    except Exception as e:
        for r in rows:
            con.execute("UPDATE retain_queue SET tries=tries+1, state=CASE WHEN tries>=2 THEN 'dead' ELSE 'pending' END WHERE id=?", (r[0],))
        con.commit()
        return {"flushed": False, "reason": "extract: %s" % str(e)[:80], **depth(con)}

    if not cards:
        for r in rows:
            con.execute("UPDATE retain_queue SET tries=tries+1, state=CASE WHEN tries>=2 THEN 'dead' ELSE 'pending' END WHERE id=?", (r[0],))
        con.commit()
        return {"flushed": False, "reason": "no cards", **depth(con)}

    mat = _matrix(con)
    sid = _span_for(con, text, tag)

    def lookup(s):
        return {"quote": text[:800], "role": "assistant"} if s == sid else None

    saved = merged = dup = 0
    ids = []
    for c in cards:
        title = (c.get("title") or "")[:80]
        body = (c.get("body") or "")[:2000]
        if not body:
            continue
        try:
            v = emb.embed([title + " " + body])[0]
        except Exception:
            v = None
        best_id, cos = _best(v, mat, con) if v else (None, 0.0)

        if best_id and cos >= SIM_DUP:
            dup += 1
            con.execute(
                "UPDATE cards SET heat=COALESCE(heat,0)+0.05, updated_at=? WHERE id=?",
                (now(), best_id),
            )
            continue

        card = {
            "title": title,
            "body": body,
            "kind": c.get("kind") or "note",
            "topic": c.get("topic"),
            "owner": "agent",
            "status": "active",
            "source_path": "mcpq/%s" % tag,
            "extractor_version": "mcpq-v1",
        }
        if best_id and SIM_NEW <= cos < SIM_DUP:
            card["supersedes"] = best_id
        evs = [{"span_id": sid, "role": "assistant", "quote": text[:800],
                "char_start": 0, "char_end": min(len(text), 800), "weight": 0.7}]
        cid, reason = store.ingest_card(con, card, evs, span_lookup=lookup)
        if cid:
            saved += 1
            ids.append(cid)
            if v is not None:
                try:
                    store.put_vector(con, cid, v)
                    mat[cid] = __import__("numpy").asarray(v, dtype="float32")
                except Exception:
                    pass
            if card.get("supersedes"):
                merged += 1
        else:
            dup += 1

    if not dry:
        for r in rows:
            con.execute(
                "UPDATE retain_queue SET state='done', flushed_at=?, card_ids=? WHERE id=?",
                (now(), ",".join(map(str, ids)), r[0]),
            )
        con.commit()

    return {"flushed": True, "reason": why, "items": len(rows), "cards": saved,
            "superseded": merged, "dups": dup, "ids": ids, **depth(con)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    db = os.environ.get("MEMVAULT_DB") or "data/memvault.db"
    if argv and not argv[0].startswith("-"):
        db = argv.pop(0)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    ensure(con)
    if "--status" in argv:
        import json
        print(json.dumps(depth(con), ensure_ascii=False))
        return
    r = flush(con, force="--force" in argv)
    import json
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
