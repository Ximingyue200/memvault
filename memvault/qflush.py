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
QAGE = int(os.environ.get("MEMVAULT_Q_AGE_MIN", "0"))   # 0 = 不按时间触发
QCAP = int(os.environ.get("MEMVAULT_Q_CAP", "50"))
BATCH = int(os.environ.get("MEMVAULT_Q_BATCH", "20"))
SIM_DUP = float(os.environ.get("MEMVAULT_Q_SIM_DUP", "0.93"))
SIM_NEW = float(os.environ.get("MEMVAULT_Q_SIM_NEW", "0.80"))
SIM_CHANGE = float(os.environ.get("MEMVAULT_Q_SIM_CHANGE", "0.55"))
# 「改主意」的说法：这类句子即便相似度不高，也应该去找旧卡来取代
_CHANGE_RE = __import__("re").compile(r"(改成|改为|改到|换成|换成用|不再|取消|以后|现在用|调整成|更正|修正)")

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
    if QAGE > 0 and _age_min(d["oldest"]) >= QAGE:
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


def _iter_cards(con):
    for r in con.execute("SELECT c.id, v.vec FROM cards c JOIN vectors v ON v.card_id=c.id WHERE c.status IN ('active','proposal')"):
        try:
            yield r[0], np.frombuffer(r[1], dtype=np.float32)
        except Exception:
            continue


def _cos(a, b):
    na, nb = float(np.linalg.norm(a)) or 1.0, float(np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / (na * nb))


def _share_word(x, y, n=2):
    xs = {x[i:i + n] for i in range(max(0, len(x) - n + 1)) if "\u4e00" <= x[i] <= "\u9fff"}
    ys = {y[i:i + n] for i in range(max(0, len(y) - n + 1)) if "\u4e00" <= y[i] <= "\u9fff"}
    return len(xs & ys) >= 2


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


LOCK_PATH = os.environ.get("MEMVAULT_QLOCK", "/opt/memvault/data/.qflush.lock")


def _flock(blocking=True):
    """跨进程互斥：MCP 进程与定时器抢同一把锁，否则会各建一张重复卡。"""
    import fcntl
    fh = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return fh
    except BlockingIOError:
        fh.close()
        return None


def flush(con, force=False, limit=None, dry=False, blocking=True):
    if not dry:
        _lk = _flock(blocking)
        if _lk is None:
            return {"flushed": False, "reason": "另一处正在提炼", **depth(con)}
        try:
            return _flush_locked(con, force=force, limit=limit, dry=False)
        finally:
            try:
                import fcntl
                fcntl.flock(_lk.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            _lk.close()
    return _flush_locked(con, force=force, limit=limit, dry=dry)


def _flush_locked(con, force=False, limit=None, dry=False):
    from memvault import embed as emb
    from memvault import extract as ex
    from memvault import store

    ok, why = should_flush(con, force=force)
    if not ok:
        return {"flushed": False, "reason": why, **depth(con)}

    rows = _pending(con, limit or BATCH)
    if rows:
        # 原子领取：谁先改到 processing 谁负责，避免两个 flush 处理同一批
        ids = [r[0] for r in rows]
        con.executemany(
            "UPDATE retain_queue SET state='processing' WHERE id=? AND state='pending'",
            [(i,) for i in ids],
        )
        con.commit()
        claimed = {r[0] for r in con.execute(
            "SELECT id FROM retain_queue WHERE state='processing' AND id IN (%s)" % ",".join("?" * len(ids)), ids
        )}
        rows = [r for r in rows if r[0] in claimed]
    if not rows:
        return {"flushed": False, "reason": "empty", **depth(con)}

    text = "\n".join("记录: " + (r[2] or "").strip() for r in rows)
    agents = sorted({r[1] for r in rows})
    tag = ",".join(agents)

    cards = []
    try:
        cards = ex.extract(text, max_cards=8)
    except Exception as e:
        # 抽取挂了不该整批作废：交给下面的本地规则兜底
        print("[qflush] 抽取异常，转本地兜底：%s" % str(e)[:120])

    if not cards:
        try:
            from memvault.demoextract import split_cards
            cards = split_cards(text, max_cards=8)
            if cards:
                print("[qflush] LLM 没给出可用结果，用本地规则切了 %d 条" % len(cards))
        except Exception as e:
            print("[qflush] 本地兜底也失败：%s" % e)

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

        # 改主意的句子：放宽相似度门槛，但要求共享关键词，避免误取代
        if not (best_id and SIM_NEW <= cos < SIM_DUP) and _CHANGE_RE.search(text or ""):
            alt_id, alt_c = None, 0.0
            for cid0, c0 in _iter_cards(con):
                c2 = _cos(v, c0) if v is not None else 0.0
                if c2 > alt_c and c2 >= SIM_CHANGE and _share_word(text, c0):
                    alt_id, alt_c = cid0, c2
            if alt_id:
                best_id, cos = alt_id, max(cos, alt_c)

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
    import json
    if "--requeue" in argv:
        n = con.execute("UPDATE retain_queue SET state='pending', tries=0 WHERE state IN ('dead','processing')").rowcount
        con.commit()
        print(json.dumps({"requeued": n}, ensure_ascii=False))
        return
    if "--status" in argv:
        import json
        print(json.dumps(depth(con), ensure_ascii=False))
        return
    r = flush(con, force="--force" in argv)
    import json
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()

_CACHE = {}


def _vec_of(text):
    """给队列里的文本算向量（进程内缓存，队列最多几十条）。"""
    key = hashlib.sha1(text.encode("utf-8")).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    try:
        from memvault import embed as emb
        v = emb.embed([text])[0]
    except Exception:
        v = None
    if len(_CACHE) > 500:
        _CACHE.clear()
    _CACHE[key] = v
    return v


def search(con, query, limit=5, min_score=0.35):
    """在**还没出库**的暂存里找（跨 agent 可见）。返回 list[dict]。"""
    import numpy as np

    ensure(con)
    rows = con.execute(
        "SELECT id, agent, source, context, text, chars, created_at FROM retain_queue "
        "WHERE state='pending' ORDER BY id DESC LIMIT 200"
    ).fetchall()
    if not rows:
        return []
    qv = _vec_of(query)
    out = []
    for r in rows:
        text = r[4] or ""
        score = 0.0
        if qv is not None:
            iv = _vec_of(text)
            if iv is not None:
                a = np.asarray(qv, dtype=np.float32)
                b = np.asarray(iv, dtype=np.float32)
                na, nb = float(np.linalg.norm(a)) or 1.0, float(np.linalg.norm(b)) or 1.0
                score = float(np.dot(a, b) / (na * nb))
        # 关键词兜底：查询里的词直接出现在正文里
        hit = sum(1 for w in _tokens(query) if w and w in text)
        if hit:
            score = max(score, min(0.9, 0.35 + 0.15 * hit))
        if score >= min_score:
            out.append({
                "id": r[0], "agent": r[1], "source": r[2], "context": r[3],
                "text": text, "chars": r[5], "created_at": r[6],
                "score": round(score, 4), "pending": True,
            })
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def _tokens(q):
    """粗分词：中文按 2-4 字滑窗，英文按词。"""
    q = (q or "").strip()
    out = []
    for m in __import__("re").finditer(r"[A-Za-z0-9_.-]{2,}", q):
        out.append(m.group(0))
    zh = "".join(ch for ch in q if "\u4e00" <= ch <= "\u9fff")
    for n in (4, 3, 2):
        for i in range(0, max(0, len(zh) - n + 1)):
            out.append(zh[i:i + n])
    return out[:60]
