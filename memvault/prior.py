"""检索先验：从库里统计「真实存在的实体/主题/节律/排斥项」。

产出的 prior.json 给 /find 用：
  entities  → 硬词白名单（多跳只认这些）
  topics    → 查询扩展
  rhythm    → 时间先验
  negative  → 排序降权

用法：python3 -m memvault.prior [--db PATH] [--out PATH]
"""
import argparse, json, math, os, re, sqlite3, sys
from collections import Counter, defaultdict

from .search import hard_words

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
OUT = "/opt/memvault/data/prior.json"

# 数字噪声：容量/速度/常见值，不是实体
_NUM_NOISE = {
    "100", "200", "300", "400", "500", "600", "700", "800", "900", "1000",
    "128", "256", "512", "1024", "2048", "4096", "168", "192", "127", "0", "10",
}

# 明确排斥/负面表达
NEG = re.compile(r"(不要|别用|讨厌|排斥|不想|不喜欢|拒绝|反感|受不了|坚决不|再也不|别再|avoid|no longer)")


def connect(p):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def build(db=DB):
    con = connect(db)
    n_cards = con.execute("SELECT count(*) FROM cards WHERE status='active'").fetchone()[0]

    # 1) 实体：从 evidence 原文里抽硬词，按文档频率（df）算
    df = Counter()
    rows = con.execute("""
        SELECT ce.quote AS q FROM card_evidence ce
        JOIN cards c ON c.id = ce.card_id
        WHERE c.status='active' AND length(ce.quote) > 10
    """).fetchall()
    for r in rows:
        for w in set(x.lower() for x in hard_words(r["q"] or "", limit=20)):
            df[w] += 1

    # 2) 主题：kind 分布 + 高频标题词
    kinds = dict(con.execute(
        "SELECT kind, count(*) FROM cards WHERE status='active' GROUP BY 1 ORDER BY 2 DESC"))
    title_tok = Counter()
    for r in con.execute("SELECT title FROM cards WHERE status='active'"):
        t = (r["title"] or "").strip()
        if 2 <= len(t) <= 24:
            title_tok[t] += 1

    # 3) 节律：按月/按小时的消息量
    by_month = dict(con.execute("""
        SELECT substr(ts,1,7) m, count(*) FROM spans
        WHERE ts IS NOT NULL GROUP BY 1 ORDER BY 1"""))
    by_hour = dict(con.execute("""
        SELECT substr(ts,12,2) h, count(*) FROM spans
        WHERE ts IS NOT NULL GROUP BY 1 ORDER BY 1"""))

    # 4) 排斥项：正文含否定词的偏好卡
    neg = []
    for r in con.execute("""SELECT id, title, body FROM cards
                            WHERE status='active' AND kind='preference'"""):
        m = NEG.search(r["body"] or "")
        if m:
            neg.append({"id": r["id"], "title": r["title"][:40], "cue": m.group(1)})

    total = max(1, len(rows))
    ents = []
    for w, c in df.most_common(600):
        if c < 3:
            continue
        if w.isdigit():
            # 只留 4 位以上、且不是常见容量值的数字（8845 / 8006 / 9210）
            if len(w) < 4 or w in _NUM_NOISE:
                continue
        ents.append({"w": w, "df": c, "idf": round(math.log(total / (1 + c)), 3)})
        if len(ents) >= 400:
            break
    prior = {
        "schema": 1,
        "cards": n_cards,
        "evidence_scanned": len(rows),
        "entities": ents,
        "kinds": kinds,
        "top_titles": [{"t": t, "n": n} for t, n in title_tok.most_common(80) if n >= 3],
        "rhythm": {"by_month": by_month, "by_hour": by_hour},
        "negative": neg[:60],
    }
    return prior


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    p = build(a.db)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False, indent=1)
    print(f"entities={len(p['entities'])} titles={len(p['top_titles'])} "
          f"neg={len(p['negative'])} -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
