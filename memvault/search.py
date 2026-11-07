"""检索层 —— 绝对分，不用 min-max。

min-max 的病：第一名永远 1.000、最后一名永远 0.000、中间线性铺满，
所以"这条不相关"无法表达，阈值形同虚设（之前的 `姜文` 返回 8 条、
第 2–8 名 score 0.834–0.974 就是这么来的）。

现在：
  vec_score = cos(q, c)              归一化后属于 [-1, 1]，实际集中在 0.2–0.7
  bm25_score = 归一化到 [0,1] 的惩罚项（sigmoid 而非 min-max）
  final = 0.65*vec + 0.35*bm25
  再乘 status/kind/role 的硬过滤（不是加分，是过滤）
"""
import hashlib, json
import math
import os
import re
import re
import time

import numpy as np

from . import embed

try:
    from . import heat as _heat
except Exception:
    _heat = None

# 硬过滤：intent -> 允许的 kind
# 默认检索必须覆盖库里所有 kind —— 原来只放 definition/event/note，
# 结果 fact(2967) / experience(1171) / preference(426) / tool(305) 共 4,869 张卡
# 在普通查询里永远搜不到。
ALL_KINDS = ("definition", "event", "note", "fact", "experience", "preference", "tool")
INTENT_KINDS = {
    "definition": ("definition",),
    "event": ("event",),
    "note": ("note",),
    None: ALL_KINDS,
}

# definition 意图只认人类证据
INTENT_ROLES = {
    "definition": ("user", "user_note"),
    None: None,
}


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

# LIKE 兜底时给的伪 bm25 原始分：标题命中 > 正文命中。
# 不能用 1.0 —— logistic(1.0; mid=8, scale=4) 只算出 0.15，会被 _bm25_floor 直接丢掉，
# 这就是「两个字的词搜不到东西」的真正原因（FTS5 的 unicode61 把连续汉字当成一个整词，
# 两字查询永远没法和库里的长词对齐）。
_LIKE_HIT_TITLE = 16.0
_LIKE_HIT_BODY = 12.0


def _like_rows(con, q, limit=100):
    """中文短查询 / FTS 打空时的兜底：直接 LIKE 扫，按命中位置给伪分。"""
    rows = con.execute(
        "SELECT c.id, c.title, c.body, (c.title LIKE ?) AS t_hit "
        "FROM cards c WHERE c.status='active' AND (c.title LIKE ? OR c.body LIKE ?) "
        "LIMIT ?",
        (f"%{q}%", f"%{q}%", f"%{q}%", limit),
    ).fetchall()
    return [(r["id"], _LIKE_HIT_TITLE if r["t_hit"] else _LIKE_HIT_BODY) for r in rows]


def _bm25(con, q, limit=100):
    """FTS5 + 中文兜底。连续汉字在 unicode61 里是整词，短查询必须退到 LIKE。"""
    q = q.strip()
    if not q:
        return []
    if len(q) < 3 or (_CJK.search(q) and len(q) <= 4):
        # 中文两三字（内存 / 摇篮 / 网卡驱动）先走 LIKE，比 FTS 里的整词匹配可靠
        rows = _like_rows(con, q, limit)
        if rows:
            return rows
    try:
        rows = con.execute(
            # 标题权重 3 : 正文权重 1
            # 卡片常「一卡两事」，正文里顺带提到的东西不该跟标题同级。
            """SELECT f.rowid id, bm25(cards_fts, 3.0, 1.0) s FROM cards_fts f
               JOIN cards c ON c.id=f.rowid
               WHERE cards_fts MATCH ? AND c.status='active'
               ORDER BY s LIMIT ?""",
            (q, limit),
        ).fetchall()
    except Exception:
        rows = []
    if not rows and _CJK.search(q):
        return _like_rows(con, q, limit)
    return [(r["id"], -r["s"]) for r in rows]  # bm25 越小越相关


def _vector(con, q, limit=100):
    from . import store
    ids, mat = store.load_vectors(con)
    if not ids:
        return []
    qv = np.array(embed.embed([q])[0], dtype=np.float32)
    n = np.linalg.norm(qv)
    if n:
        qv = qv / n
    sims = mat @ qv
    order = np.argsort(-sims)[:limit]
    return [(ids[i], float(sims[i])) for i in order]


BRIEF_PATH = "/opt/memvault/data/brief.md"
PRIOR_PATH = "/opt/memvault/data/prior.json"

_prior_cache = {"mtime": 0, "ents": None}


def prior_entities():
    """库里真实出现过的实体白名单。prior.json 变了会重载。"""
    try:
        mt = os.path.getmtime(PRIOR_PATH)
    except OSError:
        return None
    if _prior_cache["ents"] is not None and _prior_cache["mtime"] == mt:
        return _prior_cache["ents"]
    try:
        with open(PRIOR_PATH, encoding="utf-8") as f:
            p = json.load(f)
        ents = {e["w"].lower() for e in p.get("entities") or []}
    except Exception:
        return None
    _prior_cache["mtime"] = mt
    _prior_cache["ents"] = ents
    return ents

def brief_hint(max_items=4, con=None):
    """线索摘要。会话启动时的恒定背景层，不走检索。

    优先读 threads 表（能算「多久没动」「状态变了几次」），
    表不在才退回 brief.md。"""
    import os as _os, re as _re, json as _json

    # ── 优先：threads 表 ──────────────────────────────
    if con is not None:
        try:
            rows = con.execute(
                """SELECT name, state, last_period, seen FROM threads
                   WHERE state IN ('blocked','active')
                   ORDER BY CASE state WHEN 'blocked' THEN 0 ELSE 1 END,
                            last_period DESC LIMIT ?""", (max_items * 3,)
            ).fetchall()
            if rows:
                blocked_n = con.execute(
                    "SELECT count(*) FROM threads WHERE state='blocked'").fetchone()[0]
                active_n = con.execute(
                    "SELECT count(*) FROM threads WHERE state='active'").fetchone()[0]
                return {"blocked": blocked_n, "active": active_n,
                        "names": [r[0] for r in rows[:max_items]],
                        "detail": [{"name": r[0], "state": r[1], "last": r[2], "seen": r[3]}
                                   for r in rows[:max_items]],
                        "source": "threads"}
        except Exception:
            pass

    path = _os.environ.get("MEMVAULT_BRIEF", BRIEF_PATH)
    try:
        text = open(path, encoding="utf-8").read()
    except Exception:
        return None
    # 抓 "## 卡住的（N）" 段
    m = _re.search(r"^## 卡住的（(\d+)）", text, _re.M)
    blocked_n = int(m.group(1)) if m else 0
    m2 = _re.search(r"^## 进行中（(\d+)）", text, _re.M)
    active_n = int(m2.group(1)) if m2 else 0
    # 取卡住段的条目名
    sec = _re.split(r"^## ", text, flags=_re.M)
    names = []
    for chunk in sec:
        if chunk.startswith("卡住的"):
            names = _re.findall(r"^- (.+?)  \[", chunk, _re.M)
            break
    if not blocked_n and not active_n:
        return None
    return {"blocked": blocked_n, "active": active_n,
            "names": names[:max_items], "path": path}



_HARD = re.compile(
    r"(?<!\d)\d{3,5}(?!\d)"                    # 8006 / 1024（3位数起）
    r"|/[a-z0-9_/.-]{3,}"                 # 路径
    r"|\b[a-z_]+[.-][a-z0-9-]{2,}\b"   # 文件名 / 服务名
    r"|\b[A-Z][A-Z0-9]{1,}\b"           # SSH / PVE / HTTP
    r"|\b[a-z]+[A-Z][a-zA-Z]+\b")       # camelCase

# 抽硬词时丢掉这些：年份、月份、日期、太泛的词
_STOP = {
    "2024", "2025", "2026", "2027", "12", "11", "10", "01", "02", "03",
    "ai", "the", "and", "for", "not", "you", "all", "one", "two",
    "http", "https", "www", "com", "md", "json", "txt",
}


def _quotes(con, card_id, limit=2, max_chars=400):
    """取这张卡最相关的原文引用。没有就返回空。"""
    try:
        rows = con.execute(
            "SELECT quote FROM card_evidence WHERE card_id=? "
            "ORDER BY weight DESC, length(quote) ASC LIMIT ?", (card_id, limit)
        ).fetchall()
    except Exception:
        return []
    _sup_map = {}
    try:
        for _r in con.execute("SELECT id, supersedes FROM cards WHERE supersedes IS NOT NULL AND supersedes<>0"):
            _sup_map[_r[1]] = _r[0]
    except Exception:
        pass
    out = []
    for r in rows:
        q = (r[0] or "").strip()
        if not q:
            continue
        if len(q) > max_chars:
            q = q[:max_chars] + "…"
        out.append(q)
    return out


def hard_words(text, limit=6, whitelist=None):
    seen, out = set(), []
    for m in _HARD.finditer(text or ""):
        w = m.group(0)
        lw = w.lower()
        if len(w) < 3 or lw in _STOP or lw in seen:
            continue
        if w.isdigit() and len(w) < 3:
            continue
        if whitelist is not None and lw not in whitelist:
            continue
        seen.add(w.lower()); out.append(w)
    return out[:limit]


# 纯 BM25 强命中的绝对底线：logistic(8,4) 反解 b=0.62 ≈ bm25 原始分 10.6
_BM25_FLOOR = 0.62          # 长查询用的严格门槛
_BM25_FLOOR_SHORT = 0.30    # 短查询（中文两三个字）用宽松门槛，否则恒被丢弃


def _bm25_floor(q):
    """中文短查询的 BM25 分天然很低（两字词约 0.15），按查询长度自适应门槛。"""
    q = (q or "").strip()
    if len(q) <= 6:
        return _BM25_FLOOR_SHORT
    if len(q) <= 12:
        return (_BM25_FLOOR_SHORT + _BM25_FLOOR) / 2
    return _BM25_FLOOR


def _bm25_score(pairs, mid=8.0, scale=4.0):
    """bm25 原始分 -> [0,1]，用 logistic 而不是 min-max。

    min-max 的病：候选集里第一名永远是 1.0，哪怕它只是"矮子里拔将军"。
    logistic 保留了绝对量：s=8 附近得 0.5，s=4 只有 0.27，弱匹配不会被抬满。
    """
    if not pairs:
        return {}
    return {i: 1.0 / (1.0 + math.exp(-(v - mid) / scale)) for i, v in pairs}


def search(con, q, topk=10, min_score=0.30, intent=None, role_filter=None, owner=None, claim=None, scope=None):
    """返回 EvidenceBundle 形态。owner ∈ {human, agent, both} 或 None(=不限)。"""
    kinds = INTENT_KINDS.get(intent, INTENT_KINDS[None])
    try:
        _heat_p = _heat.params(con) if _heat else None
    except Exception:
        _heat_p = None
    _vec_err = None
    try:
        vrows = _vector(con, q, limit=200)
    except Exception as e:
        # embedding 是唯一外部依赖（HTTP 服务）。它挂了不该让整条检索挂掉——
        # BM25 不依赖它，降级成单路至少还能召回关键词命中。
        vrows = []
        _vec_err = str(e)[:120]
    brows = _bm25(con, q, limit=200)
    vmap = dict(vrows)
    bmap = _bm25_score(brows)

    cand = set(vmap) | set(bmap)
    if not cand:
        return {"query": q, "intent": intent, "cards": [], "count": 0,
                "note": ("no_candidates(vec_failed:%s)" % _vec_err) if _vec_err else "no_candidates",
                "vec_ok": not _vec_err}

    ph = ",".join("?" * len(cand))
    kp = ",".join("?" * len(kinds))
    extra = ""
    args = list(cand) + list(kinds)
    # owner 语义：None/'both' = 不限；'human'/'agent' = 精确。
    # 以前把 'both' 当成普通值塞进 IN(...)，而 cards.owner 只有 human/agent/both 三种，
    # 结果 'both' 变成「只匹配 owner 列字面值为 both 的那 511 张」，调用方以为放开了、实际收窄了。
    if owner and owner != "both":
        vals = list(owner) if isinstance(owner, (list, tuple)) else [owner]
        if "both" not in vals:
            extra = f" AND owner IN ({','.join('?' * len(vals))})"
            args += vals
    if claim:
        cl = claim if isinstance(claim, (list, tuple)) else [claim]
        extra += f" AND claim_type IN ({','.join('?' * len(cl))})"
        args += list(cl)
    if scope:
        # 四级作用域的落地：global 永远可见，加上指定的这一级
        extra += " AND (scope IS NULL OR scope='global' OR scope=?)"
        args.append(scope)
    rows = con.execute(
        f"""SELECT id,title,body,kind,owner,topic,created_at,occurred_at,status,supersedes,claim_type,heat,last_hit_at
            FROM cards WHERE id IN ({ph}) AND status='active' AND kind IN ({kp}){extra}""",
        (*args,),
    ).fetchall()
    byid = {r["id"]: r for r in rows}

    # 证据数：被几条原文撑着。只用小幅加权（log 压平，上限 +12%），
    # 不然新卡会把旧卡全挤出去 —— 旧卡占 70%，里面大概率有真相关的。
    ev_count = {}
    if byid:
        ids = list(byid)
        chunks = [ids[i:i+500] for i in range(0, len(ids), 500)]
        for cl in chunks:
            q2 = ",".join("?" * len(cl))
            for row in con.execute(
                f"SELECT card_id, count(*) n FROM card_evidence WHERE card_id IN ({q2}) GROUP BY 1",
                cl,
            ):
                ev_count[row["card_id"]] = row["n"]

    # 启动效应：被召回过的卡更容易被再次召回（priming）。
    # 频次贡献小、多样性贡献大 —— 被 5 种不同问题都要过的卡，比被同一个问题问 20 次的有用。
    rec_stats = {}
    if byid:
        ids = list(byid)
        chunks = [ids[i:i+500] for i in range(0, len(ids), 500)]
        for cl in chunks:
            q2 = ",".join("?" * len(cl))
            try:
                for row in con.execute(
                    f"SELECT card_id, count(*) n, count(DISTINCT query_hash) uq "
                    f"FROM card_recall WHERE card_id IN ({q2}) GROUP BY 1",
                    cl,
                ):
                    rec_stats[row["card_id"]] = (row["n"], row["uq"])
            except Exception:
                pass

    out = []
    # 旧卡 → 新卡 映射（supersedes 是「新卡指向旧卡」，所以反查得到谁被取代）
    _sup_map = {}
    try:
        # supersedes 是「新卡.supersedes = 旧卡.id」；这里要建的是 旧→新，
        # 之前写反了：结果被降权的是刚写进来的新卡，新卡一入库就搜不到。
        for _new_id, _old_id in con.execute(
            "SELECT id, supersedes FROM cards WHERE supersedes IS NOT NULL AND supersedes<>0"
        ):
            _sup_map[int(_old_id)] = int(_new_id)
    except Exception:
        _sup_map = {}

    for cid in cand:
        r = byid.get(cid)
        if not r:
            continue
        v = vmap.get(cid, 0.0)
        b = bmap.get(cid, 0.0)
        score = 0.65 * v + 0.35 * b
        proof = ev_count.get(cid, 0)
        if proof:
            score *= (1.0 + 0.04 * math.log1p(proof))
        # 启动效应：被反复检索、且被不同问法命中过的卡，说明它「有用」。
        # 但必须是微调，不能翻盘——实测乘 1.26 会让 recall=25 的万金油卡
        # 把语义更准的卡挤到后面（问 8845 时 MT7922 靠加成从 0.455 冲到 0.573）。
        # 改成加法，上限 0.05，只做同分附近的 tie-break。
        n_rec, n_uq = rec_stats.get(cid, (0, 0))
        if n_rec:
            score += min(0.05, 0.01 * math.log1p(n_rec) + 0.02 * math.log1p(n_uq))
        # min_score 语义 = 融合分底线（保持原义，调用方调高就真的更严）。
        # 但纯 BM25 命中的融合分上限只有 0.35*b，永远够不着 0.30 这条线，
        # 双路的「关键词精确命中」会整批消失。所以给 BM25 单独一条绝对底线：
        # b >= _BM25_FLOOR（对应 bm25 原始分约 -10）就当作强命中放行。
        if score < min_score and b < _bm25_floor(q):
            continue
        try:
            _lh = r["last_hit_at"]
            _live = _heat.live_heat(r["heat"], _lh, _heat_p["memory.half_life_days"]) if _heat and _heat_p else 0.0
            _band = _heat.band(_live, _heat_p, _lh) if _heat and _heat_p else "never"
        except Exception:
            _live, _band = 0.0, "never"
        # 被取代的是「旧卡」：别的卡把 supersedes 指向我，我才该降权。
        if cid in _sup_map:
            score *= 0.55
            superseded_by = _sup_map[cid]
        else:
            superseded_by = None
        out.append({
            "id": cid, "score": round(score, 4), "proof": proof, "superseded_by": superseded_by,
            "recall": n_rec, "uq": n_uq,
            "vec": round(v, 4), "bm25": round(b, 4),
            "title": r["title"], "body": r["body"],
            "kind": r["kind"], "claim_type": r["claim_type"], "owner": r["owner"], "topic": r["topic"],
            "created_at": r["created_at"], "occurred_at": r["occurred_at"], "status": r["status"],
            "heat": round(_live, 4), "band": _band,
        })

    out.sort(key=lambda x: -x["score"])
    out = out[:topk]

    # 附证据（带 role，调用方要知道"这是人说的还是模型想的"）
    for c in out:
        evs = con.execute(
            "SELECT span_id,role,quote,char_start,char_end FROM card_evidence WHERE card_id=?",
            (c["id"],),
        ).fetchall()
        if role_filter:
            evs = [e for e in evs if e["role"] in role_filter]
        c["evidence"] = [dict(e) for e in evs[:3]]
        c["has_human_evidence"] = any(e["role"] in ("user", "user_note") for e in evs)

    # 命中升温：热度是短期记忆的载体，检索一次就记一次。
    # 只热 topk 里的前几条，避免把整个候选池都烤热。
    if _heat_p is not None and out:
        try:
            _heat.bump(con, [c["id"] for c in out[:topk]], _heat_p)   # heat_bump_on_hit
            # 命中留痕：以前只有 4174 面板写 card_recall，网关检索一次也不记，
            # 于是「97% 的卡从没被想起」这个数字一直被低估。
            try:
                qh = hashlib.sha1((q or "").encode("utf-8")).hexdigest()[:16]
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                for c in out[:topk]:
                    con.execute("INSERT INTO card_recall(card_id,query_hash,at) VALUES(?,?,?)",
                                (c["id"], qh, ts))
                con.commit()
            except Exception:
                pass
        except Exception:
            pass

    note = None
    if intent == "definition" and not out:
        alt = con.execute(
            "SELECT COUNT(*) FROM cards WHERE status='active' AND kind='event'"
        ).fetchone()[0]
        note = f"没有定义卡；库里有 {alt} 条事件卡"
    res = {
        "query": q, "intent": intent, "owner": owner,
        "cards": out, "count": len(out), "note": note,
    }
    bh = brief_hint(con=con)
    if bh:
        res["brief_hint"] = bh
    return res

# ── chrono_hint：/find 只返回信号，不返回编年史正文 ──────────────────
import re as _re

_TIME_WORDS = ("今年", "去年", "前年", "上个月", "上个星期", "上周", "最近", "这段时间",
               "前阵子", "之前", "那时候", "当时", "在干嘛", "干什么了", "做了什么",
               "忙什么", "在折腾什么", "这几年", "这一年", "这几个月", "回顾", "总结一下")
_HARD_ENTITY = _re.compile(
    r"(?<!\d)\d{2,5}(?!\d)"                      # 8006 / 1024 / 8845
    r"|/[a-z0-9_/.-]{3,}"                 # 路径
    r"|\b[a-z_]+[.-][a-z0-9-]{2,}\b"     # 文件名 / 包名
    r"|\b[A-Z]{2,}\b"                   # SSH / PVE / DNS
    r"|\b[a-z]+[A-Z][a-zA-Z]+\b"         # camelCase
)


def _num_hit(quote, word):
    """数字词要求十六进制边界，否则 8845 会命中图片 URL 里的 ...0c8845e3bf..."""
    if not any(c.isdigit() for c in word):
        return word in quote
    pat = re.compile(r"(?<![0-9a-fA-F])" + re.escape(word) + r"(?![0-9a-fA-F])")
    return bool(pat.search(quote))


def timeline(con, q, limit=20, per_conv=2, max_days=6, per_day=2):
    """问「什么时候/哪次」时用：原文全文命中 -> 按时间排 -> 按会话去重。

    为什么不走向量：问「8845 是什么」和问「什么时候聊过 8845」是两个问题。
    前者要语义最近的卡，后者只要「哪几段对话里提到过它」，按时间给。
    用向量答后者，返回的是规格卡的时间戳，答非所问。

    为什么稀有词必须全中、通用词只加分：问「什么时候问过 8845 的 CPU」时，
    「CPU」单独出现有几千条，「8845」只有一千多条，要求两者同现才是那个问题。
    """
    words = hard_words(q, limit=4)
    if not words:
        return []

    rare = [w for w in words if any(c.isdigit() for c in w)]
    base = rare or words[:1]

    conds = " AND ".join(["quote LIKE ?"] * len(base))
    params = ["%" + w + "%" for w in base]
    try:
        rows = con.execute(
            "SELECT span_id, conv_id, seq, role, ts, quote FROM spans "
            "WHERE (" + conds + ") AND ts IS NOT NULL AND ts <> '' "
            "ORDER BY ts ASC LIMIT 600", params).fetchall()
    except Exception:
        return []

    # 同会话内 user 优先：问「我问过什么」时，AI 的回复只是佐证，用户那句才是答案。
    grouped = {}
    for r in rows:
        quote = (r[5] or "").strip()
        # SQL 的 LIKE 做不到词边界，这里补一刀
        if not all(_num_hit(quote, w) for w in base):
            continue
        grouped.setdefault(r[1] or "", []).append(r)

    # 按「天」分散，不是按「会话」。同一天可能开了五段对话，全给出来会把
    # 别的日期挤掉——问「什么时候」要的是「有几个时间点」。
    by_day = {}
    for cid, rs in grouped.items():
        day = (rs[0][4] or "")[:10]
        by_day.setdefault(day, []).append((cid, rs))

    picked = []
    for day in sorted(by_day)[:max_days]:
        for cid, rs in by_day[day][:per_day]:
            rs.sort(key=lambda x: (0 if x[3] == "user" else 1, x[4] or ""))
            picked.extend(rs[:per_conv])
    picked.sort(key=lambda x: x[4] or "")

    out = []
    for r in picked:
        quote = (r[5] or "").strip()
        out.append({
            "conv_id": r[1] or "",
            "span_id": r[0],
            "seq": r[2],
            "role": r[3],
            "when": (r[4] or "")[:10],
            "ts": r[4],
            "title": quote[:60].replace("\n", " "),
            "abstract": quote[:240],
            "content": quote[:600],
            "kind": "span",
            "score": 1.0 - len(out) * 0.01,
            "evidence": [{"span_id": r[0], "role": r[3], "quote": quote[:400]}],
        })
    return out[:limit]


def deep_search(con, q, topk=6, min_score=0.30, max_words=4, decay=0.85, **kw):
    """两跳检索。

    第 1 跳：原 query。
    第 2 跳：从第 1 跳命中卡的 evidence.quote 里抽硬词（数字/路径/服务名/大写词），
            每个硬词单独搜一次，只取 top 2。
    合并时第 2 跳分数乘 decay（默认 0.85），保证原 query 的排序优先。

    多跳只为**召回**，不为精排。硬词用正则抽，不用模型。
    """
    r1 = search(con, q, topk=topk, min_score=min_score, **kw)
    cards = list(r1.get("cards") or [])

    # 硬词按「在几张卡里出现过」排序：只出现一次的往往是噪声数字
    WL = prior_entities()      # None = 白名单不可用，退化为不过滤
    qlow = q.lower()
    freq = {}
    order = {}
    for ci, c in enumerate(cards[:6]):
        srcs = [e.get("quote") or "" for e in (c.get("evidence") or [])[:2]]
        if not any(srcs):
            srcs = [c.get("body") or ""]
        for src in srcs:
            for w in hard_words(src, limit=6, whitelist=WL):
                lw = w.lower()
                if lw in qlow:
                    continue
                freq[lw] = freq.get(lw, 0) + 1
                if lw not in order:
                    order[lw] = (w, ci)
    # 出现 ≥2 次的优先；不足则按出现顺序补
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], order[kv[0]][1]))
    words = [order[k][0] for k, _ in ranked if _ >= 2][:max_words]
    if len(words) < max_words:
        for k, _ in ranked:
            if len(words) >= max_words:
                break
            w = order[k][0]
            if w not in words:
                words.append(w)

    hops = []
    if words:
        seen_ids = {c["id"] for c in cards}
        for w in words:
            try:
                r2 = search(con, w, topk=2, min_score=min_score, **kw)
            except Exception:
                continue
            for c in r2.get("cards") or []:
                if c["id"] in seen_ids:
                    continue
                seen_ids.add(c["id"])
                c = dict(c)
                c["score"] = round(c["score"] * decay, 4)
                c["hop"] = w
                cards.append(c)
                hops.append({"word": w, "card": c["id"], "title": c["title"][:30]})
    cards.sort(key=lambda x: -x["score"])
    out = dict(r1)
    out["cards"] = cards[:topk + 2]
    out["count"] = len(out["cards"])
    out["hops"] = hops
    out["hop_words"] = words
    return out


def chrono_hint(q: str) -> dict:
    """判断这个问题该不该带编年史。返回信号，不带正文。"""
    q = (q or "").strip()
    t = sum(1 for w in _TIME_WORDS if w in q)
    e = len(_HARD_ENTITY.findall(q))
    if e >= 1 and t == 0:
        return {"need": False, "kind": "fact", "reason": "has_entity"}
    if t >= 1 and e == 0:
        return {"need": True, "kind": "timeline", "level": "core_abs", "reason": "time_no_entity"}
    if t >= 1 and e >= 1:
        return {"need": True, "kind": "mixed", "level": "core_abs", "reason": "time_plus_entity"}
    if t == 0 and e == 0 and len(q) <= 12:
        return {"need": True, "kind": "vague", "level": "core_abs", "reason": "no_anchor_short"}
    return {"need": False, "kind": "fact", "reason": "default"}
