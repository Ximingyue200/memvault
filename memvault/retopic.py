"""给卡片打真正的 topic：不是目录名，是「这张卡在讲哪个东西」。

依据 prior.json 的实体白名单（库里真实出现过 3 次以上的词），
标题里出现的权重 3、正文里出现的权重 1。取前 2 个。

用法：python3 -m memvault.retopic [--dry] [--limit N]
"""
import argparse, json, os, re, sqlite3
from collections import Counter

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
PRIOR = "/opt/memvault/data/prior.json"

# 太泛的词不配当主题
_TOO_GENERIC = {"name", "max", "api", "dsh", "lxc", "nat", "wsl"}


def load_prior():
    with open(PRIOR, encoding="utf-8") as f:
        p = json.load(f)
    return {e["w"].lower(): e["df"] for e in p.get("entities") or []}


def pick(text, ents, weight):
    hits = Counter()
    low = text.lower()
    for w in ents:
        if w in _TOO_GENERIC or len(w) < 3:
            continue
        # 英文按词边界，数字/路径按子串
        if w.isdigit() or not w.isascii():
            n = low.count(w)
        else:
            n = len(re.findall(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low))
        if n:
            hits[w] += n * weight
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    ents = load_prior()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    sql = "SELECT id,title,body FROM cards WHERE status='active'"
    if a.limit:
        sql += f" LIMIT {a.limit}"
    rows = con.execute(sql).fetchall()

    changed = 0
    dist = Counter()
    for r in rows:
        h = pick(r["title"] or "", ents, 3) + pick(r["body"] or "", ents, 1)
        if not h:
            continue
        top = [w for w, _ in h.most_common(2)]
        topic = "/".join(top)[:40]
        dist[top[0]] += 1
        if not a.dry:
            con.execute("UPDATE cards SET topic=? WHERE id=? AND topic IS NOT ?", (topic, r["id"], topic))
        changed += 1

    if not a.dry:
        con.commit()
    print(f"{'DRY ' if a.dry else ''}retopic: {changed}/{len(rows)} 张有了主题", flush=True)
    print("top topics:", dict(dist.most_common(12)), flush=True)


if __name__ == "__main__":
    main()
