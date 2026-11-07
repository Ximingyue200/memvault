"""Dreaming：把 OpenClaw 的三阶段梦境搬到 memvault。

light  -> 挑候选、六信号打分、暂存（不写卡）
rem    -> 找重复组（同主题 + 标题相似）
deep   -> 每组选代表、合并其余、提升代表（写 promoted_at / importance / consolidate_of）

用法:
    python3 dreaming.py light          # 只出候选，不写
    python3 dreaming.py rem            # 出重复组
    python3 dreaming.py deep --apply   # 真合并、真提升
    python3 dreaming.py run            # 三阶段全跑（dry-run）
"""
import json
import math
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
_TZ = timezone(timedelta(hours=8))

# 六信号权重（照抄 OpenClaw dreaming）
W_RELEVANCE = 0.30
W_FREQUENCY = 0.24
W_DIVERSITY = 0.15   # 我们暂时用「不同会话数」替代「不同查询数」
W_RECENCY = 0.15
W_CONSOLID = 0.10
W_RICHNESS = 0.06

# 三道门（必须同时通过）
MIN_SCORE = 0.55
MIN_EVIDENCE = 2
MIN_CONVS = 1

# 重复检测用更低的门——重复的卡往往分数低（因为分散），
# 但"同一件事被写了 3 遍"本身就是信号。
REM_MIN_SCORE = 0.30

# 重复组判定
TITLE_JACCARD = 0.62


def now():
    return datetime.now(_TZ).isoformat(timespec="seconds")


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


# ---------- 工具 ----------
def grams(s, n=3):
    s = (s or "").strip().lower()
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def clamp(x):
    return max(0.0, min(1.0, x))


# ---------- light ----------
def light(c, days=180, limit=4000):
    """挑候选并打分。不写卡。"""
    since = (datetime.now(_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")

    # 近 30 天的主题热度
    hot = {}
    hot_since = (datetime.now(_TZ) - timedelta(days=30)).strftime("%Y-%m-%d")
    for r in c.execute("""SELECT category, COUNT(*) n FROM cards
                          WHERE status='active' AND occurred_at >= ?
                          GROUP BY category""", (hot_since,)):
        hot[r["category"]] = r["n"]

    rows = c.execute("""
        SELECT cd.id, cd.title, cd.body, cd.category, cd.topic,
               cd.occurred_at, cd.created_at,
               (SELECT COUNT(*) FROM card_evidence e WHERE e.card_id=cd.id) AS n_ev,
               (SELECT COUNT(DISTINCT s.conv_id) FROM card_evidence e
                  JOIN spans s ON s.span_id=e.span_id
                 WHERE e.card_id=cd.id) AS n_conv,
               (SELECT COUNT(DISTINCT cd2.source_path) FROM cards cd2
                 WHERE cd2.topic=cd.topic AND cd2.status='active' AND cd.topic<>'') AS n_topic_src,
               (SELECT COUNT(*) FROM card_recall cr WHERE cr.card_id=cd.id) AS n_recall,
               (SELECT COUNT(DISTINCT cr.query_hash) FROM card_recall cr
                 WHERE cr.card_id=cd.id) AS n_uq
        FROM cards cd
        WHERE cd.status='active'
          AND cd.promoted_at IS NULL
          AND (cd.occurred_at IS NULL OR cd.occurred_at >= ?)
        ORDER BY cd.occurred_at DESC, cd.id DESC
        LIMIT ?
    """, (since, limit)).fetchall()

    today = datetime.now(_TZ)
    out = []
    for r in rows:
        ts = r["occurred_at"] or r["created_at"]
        try:
            d = (today - datetime.fromisoformat(ts[:19]).replace(tzinfo=_TZ)).days
        except Exception:
            d = 365
        recency = math.exp(-max(d, 0) / 30.0)

        n_hot = hot.get(r["category"] or "", 0)
        relevance = clamp(n_hot / 400.0)

        # 有真实召回记录就用真的，没有才用证据数/会话数当代理
        n_rec, n_uq = (r["n_recall"] or 0), (r["n_uq"] or 0)
        frequency = clamp(n_rec / 10.0) if n_rec else clamp((r["n_ev"] or 0) / 5.0)
        diversity = clamp(n_uq / 5.0) if n_uq else clamp((r["n_conv"] or 0) / 3.0)
        consolid = clamp((r["n_topic_src"] or 0) / 3.0)
        richness = clamp(len(r["body"] or "") / 400.0)

        score = (W_RELEVANCE * relevance + W_FREQUENCY * frequency +
                 W_DIVERSITY * diversity + W_RECENCY * recency +
                 W_CONSOLID * consolid + W_RICHNESS * richness)

        passed = (score >= MIN_SCORE and
                  (r["n_ev"] or 0) >= MIN_EVIDENCE and
                  (r["n_conv"] or 0) >= MIN_CONVS)

        out.append({
            "id": r["id"], "title": r["title"], "category": r["category"],
            "topic": r["topic"], "score": round(score, 4),
            "pass": passed, "n_ev": r["n_ev"], "n_conv": r["n_conv"],
            "n_recall": r["n_recall"], "n_uq": r["n_uq"], "real": bool(r["n_uq"]),
            "sig": {"rel": round(relevance, 3), "freq": round(frequency, 3),
                    "div": round(diversity, 3), "rec": round(recency, 3),
                    "con": round(consolid, 3), "rich": round(richness, 3)},
        })

    out.sort(key=lambda x: -x["score"])
    return out


# ---------- rem ----------
def rem(candidates, min_score=REM_MIN_SCORE):
    """在候选里找重复组：同 category + 标题 3-gram Jaccard >= 阈值。"""
    pool = [x for x in candidates if x["score"] >= min_score]
    grams_map = {x["id"]: grams(x["title"]) for x in pool}

    groups = []
    used = set()
    by_cat = {}
    for x in pool:
        by_cat.setdefault(x["category"] or "", []).append(x)

    for cat, items in by_cat.items():
        items = sorted(items, key=lambda x: -x["score"])
        for i, a in enumerate(items):
            if a["id"] in used:
                continue
            grp = [a]
            used.add(a["id"])
            for b in items[i + 1:]:
                if b["id"] in used:
                    continue
                if jaccard(grams_map[a["id"]], grams_map[b["id"]]) >= TITLE_JACCARD:
                    grp.append(b)
                    used.add(b["id"])
            if len(grp) >= 2:
                groups.append(grp)

    groups.sort(key=lambda g: -len(g))
    return groups


# ---------- deep ----------
def deep(c, groups, apply=False):
    """每组选代表（evidence 最多，其次 score 最高），其余标 merged。"""
    res = []
    for grp in groups:
        rep = max(grp, key=lambda x: (x["n_ev"] or 0, x["score"]))
        others = [x for x in grp if x["id"] != rep["id"]]
        res.append({"rep": rep["id"], "rep_title": rep["title"],
                    "merged": [x["id"] for x in others],
                    "titles": [x["title"] for x in others]})

        if apply:
            ids = json.dumps([x["id"] for x in others], ensure_ascii=False)
            c.execute("""UPDATE cards
                            SET promoted_at=?, status='active',
                                importance=MIN(10, COALESCE(importance,5)+2),
                                consolidate_of=?
                          WHERE id=?""", (now(), ids, rep["id"]))
            c.execute(f"""UPDATE cards SET status='merged'
                           WHERE id IN ({','.join('?' * len(others))})""",
                      [x["id"] for x in others])
    if apply:
        c.commit()
    return res


# ---------- 主 ----------
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    apply = "--apply" in sys.argv
    c = conn()
    t0 = time.time()

    cands = light(c)
    n_pass = sum(1 for x in cands if x["pass"])
    groups = rem(cands)
    plan = deep(c, groups, apply=apply)

    n_merged = sum(len(p["merged"]) for p in plan)

    report = {
        "mode": mode, "apply": apply,
        "n_scanned": len(cands),
        "n_pass": n_pass,
        "n_groups": len(groups),
        "n_merged": n_merged,
        "top": [{"id": x["id"], "t": x["title"], "s": x["score"], "p": x["pass"]}
                for x in cands[:15]],
        "groups": [{"rep": p["rep"], "t": p["rep_title"], "n": len(p["merged"]),
                    "merged_titles": p["titles"][:6]} for p in plan[:20]],
    }

    c.execute("""INSERT INTO dream_runs(at, phase, n_candidates, n_promoted, n_merged, duration_ms, report)
                 VALUES(?,?,?,?,?,?,?)""",
              (now(), mode, len(cands), len(groups) if apply else 0,
               n_merged if apply else 0, int((time.time() - t0) * 1000),
               json.dumps(report, ensure_ascii=False)))
    c.commit()

    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
