"""场景（MemScene）：一件事从起因到收口的完整叙事。

跟其他生成器的区别：
  chronicle.py  按月/季/年 —— 「那段时间发生了什么」
  summary.py    按问题    —— 「这个问题的答案是什么」
  dream.py      按人      —— 「他是个什么样的人」
  scene.py      按事      —— 「这件事是怎么走过来、走到哪一步的」

场景是唯一带「前瞻」的产物：下次遇到同类事该注意什么。

输出进 scenes 表，status=draft，人工确认后 confirmed 才进注入。
"""
import hashlib
import json
import os
import re
import time
import urllib.request

from . import store

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")


def _cfg(c=None):
    from .dream import llm_cfg
    if c is None:
        c = store.connect(DB)
    return llm_cfg(c, "dream")


def _call(prompt, cfg, tries=3):
    """reasoning 模型要留足 token，否则 content 为空。"""
    from .dream import call
    return call(prompt, tries=tries, cfg=cfg, max_tokens=16000)


def parse(txt):
    """从 LLM 输出里抠 JSON 数组。"""
    if not txt:
        return []
    s = txt.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    m = re.search(r"\[.*\]", s, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or "").strip()
        narr = (it.get("narrative") or "").strip()
        if not title or len(narr) < 30:
            continue
        facts = [str(x).strip() for x in (it.get("facts") or []) if str(x).strip()][:6]
        fore = []
        for f in (it.get("foresight") or []):
            if isinstance(f, dict) and (f.get("content") or "").strip():
                fore.append({
                    "content": str(f["content"]).strip()[:300],
                    "valid_until": (f.get("valid_until") or "")[:10],
                })
            elif isinstance(f, str) and f.strip():
                fore.append({"content": f.strip()[:300], "valid_until": ""})
        out.append({
            "title": title[:120],
            "narrative": narr[:2000],
            "facts": facts,
            "foresight": fore[:3],
            "span": (it.get("span") or "")[:40],
            "evidence": [int(x) for x in (it.get("evidence") or []) if str(x).isdigit()][:20],
        })
    return out


PROMPT = """你在给一个人整理「记忆场景」。

不是日记，不是梦。场景是「一件事从开始到收口」的完整叙事——有起点、有转折、有当前状态。

要求：

1. 标题是一件具体的事，不是判断。
   ✅「从 CentOS 迁到 Debian 的全过程」「傲腾 M10 16G 排障」
   ❌「他是个爱折腾的人」「硬件的偏好」——那是梦干的事，不是场景。

2. 叙事 3-6 句，必须走完「起因 → 过程 → 结果/现状」。
   每句都要挂具体东西：型号、端口、报错串、路径、数字。
   没有具体东西的句子不要写。

3. 要点 2-5 条，每条是**一句能独立成立的事实**——单独拿出来也能用，不依赖上下文。
   例：「NAS 管理口从万兆光口 nic2 改到 2.5G 电口 nic0，IP 192.168.1.20」
   例：「旧容器 内存 2G 会 OOM，改成 4G + 2G swap」

4. 前瞻 0-2 条，只写**下次遇到同类事该注意什么**，不要写感想。
   ✅「换 PVE 管理口前先确认新口的驱动在 initramfs 里」
   ❌「以后要多测试」
   有效期按事情的性质估：硬件经验 1 年、软件版本 半年、临时方案 3 个月。

5. 跨度写清楚：素材最早到最晚的时间（YYYY-MM → YYYY-MM）。

6. 如果这批素材凑不成「一件事」——只是零散的知识点、没有过程和结论——返回空数组 []。
   宁可少一条，也不要硬凑。

只输出 JSON 数组，不要 markdown：
[{{"title":"...","narrative":"...","facts":["..."],"foresight":[{{"content":"...","valid_until":"YYYY-MM-DD"}}],"span":"YYYY-MM → YYYY-MM","evidence":[卡片id,...]}}]

下面是素材，按主题分组，每组按时间排好了：

{groups}
"""


def _pick_groups(c, days=365, per_group=8, max_groups=8):
    """按 topic 分组取卡，每组要在时间上有跨度（最早/最晚/中间）。"""
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    rows = list(c.execute("""
        SELECT id, title, substr(body,1,300) AS ab, topic,
               COALESCE(occurred_at, created_at) AS ts
        FROM cards
        WHERE status='active' AND topic IS NOT NULL AND topic<>''
          AND COALESCE(occurred_at, created_at) >= ?
        ORDER BY topic, ts
    """, (since,)))
    by = {}
    for r in rows:
        by.setdefault(r[3], []).append(r)
    # 只要「有过程」的组：卡数 >=3 且时间跨度 >=7 天
    good = []
    for topic, items in by.items():
        if len(items) < 3:
            continue
        t0, t1 = items[0][4][:10], items[-1][4][:10]
        try:
            d0 = time.mktime(time.strptime(t0, "%Y-%m-%d"))
            d1 = time.mktime(time.strptime(t1, "%Y-%m-%d"))
        except Exception:
            continue
        if (d1 - d0) < 7 * 86400:
            continue
        good.append((len(items), topic, items))
    good.sort(key=lambda x: -x[0])
    out = []
    for _, topic, items in good[:max_groups]:
        n = len(items)
        if n <= per_group:
            picked = items
        else:
            idx = sorted({0, n - 1} | {int(n * i / (per_group - 1)) for i in range(per_group)})
            picked = [items[i] for i in idx if 0 <= i < n]
        out.append((topic, picked))
    return out


def render(groups):
    parts = []
    for topic, items in groups:
        lines = ["### 主题：%s（%d 条，%s → %s）" % (
            topic, len(items), items[0][4][:10], items[-1][4][:10])]
        for r in items:
            lines.append("- #%d [%s] %s ｜ %s" % (r[0], r[4][:10], r[1], (r[2] or "")[:200]))
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


SCHEMA = """
CREATE TABLE IF NOT EXISTS scenes(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT UNIQUE,
  at          TEXT NOT NULL,
  title       TEXT NOT NULL,
  narrative   TEXT NOT NULL,
  facts       TEXT,
  foresight   TEXT,
  span        TEXT,
  evidence    TEXT,
  status      TEXT DEFAULT 'draft',
  model       TEXT,
  source_query TEXT
);
CREATE INDEX IF NOT EXISTS idx_scenes_at ON scenes(at);
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status);
"""


def ensure(c):
    c.executescript(SCHEMA)
    cols = [r[1] for r in c.execute("PRAGMA table_info(scenes)").fetchall()]
    if "merged_into" not in cols:
        c.execute("ALTER TABLE scenes ADD COLUMN merged_into INTEGER")
        c.commit()


def save(c, scenes, model="", source_query=None):
    ensure(c)
    n = 0
    for s in scenes:
        slug = "sc_" + hashlib.md5((s["title"] + s["span"]).encode("utf-8")).hexdigest()[:12]
        try:
            cur = c.execute("""INSERT INTO scenes(slug, at, title, narrative, facts, foresight, span, evidence, status, model, source_query)
                         VALUES(?,?,?,?,?,?,?,?, 'draft', ?, ?)
                         ON CONFLICT(slug) DO NOTHING""", (
                slug, time.strftime("%Y-%m-%d %H:%M:%S"),
                s["title"], s["narrative"],
                json.dumps(s["facts"], ensure_ascii=False),
                json.dumps(s["foresight"], ensure_ascii=False),
                s["span"], json.dumps(s["evidence"]),
                model, source_query,
            ))
            if cur.rowcount > 0:
                n += 1
        except Exception:
            pass
    c.commit()
    return n


def listing(c, status=None, limit=50):
    ensure(c)
    q = "SELECT id, at, title, narrative, facts, foresight, span, evidence, status FROM scenes"
    args = []
    if status:
        q += " WHERE status=?"
        args.append(status)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    out = []
    for r in c.execute(q, args):
        out.append({
            "id": r[0], "at": r[1], "title": r[2], "narrative": r[3],
            "facts": json.loads(r[4] or "[]"),
            "foresight": json.loads(r[5] or "[]"),
            "span": r[6], "evidence": json.loads(r[7] or "[]"),
            "status": r[8],
        })
    return out


def run(apply=True, days=365, max_groups=8):
    c = store.connect(DB)
    ensure(c)
    groups = _pick_groups(c, days=days, max_groups=max_groups)
    if not groups:
        return {"ok": False, "msg": "没有满足条件的主题组（需要 >=3 张卡且跨度 >=7 天）"}
    cfg = _cfg(c)
    prompt = PROMPT.format(groups=render(groups))
    txt = _call(prompt, cfg)
    scenes = parse(txt)
    if not apply:
        return {"ok": True, "dry": True, "n": len(scenes), "scenes": scenes, "groups": len(groups)}
    n = save(c, scenes, model=(cfg or {}).get("model", ""))
    return {"ok": True, "n": n, "groups": len(groups), "scenes": scenes}


def _grams(s, n=3):
    s = re.sub(r"\s+", "", s or "")
    return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}


def scene_block(q="", limit=2, budget=700, status=None):
    """按问题挑最多 limit 个场景注入。q 为空时取最新的。

    只注入 confirmed（人工确认过的）；status 显式传可覆盖。
    """
    st = status or "confirmed"
    c = store.connect(DB)
    ensure(c)
    rows = listing(c, status=st, limit=120)
    if not rows:
        return ""
    if q:
        qg = _grams(q)
        scored = []
        for r in rows:
            hay = (r["title"] or "") + " " + (r["narrative"] or "") + " " + " ".join(r["facts"] or [])
            hg = _grams(hay)
            if not qg:
                continue
            hit = len(qg & hg)
            if hit < 2:
                continue
            scored.append((hit / max(1, len(qg)), r))
        scored.sort(key=lambda x: -x[0])
        rows = [r for _, r in scored[:limit]]
    else:
        rows = rows[:limit]

    if not rows:
        return ""

    parts = []
    used = 0
    for r in rows:
        seg = ["<scene title=\"%s\" span=\"%s\">" % (r["title"], r["span"] or "")]
        if r["narrative"]:
            seg.append(r["narrative"][:400])
        if r["facts"]:
            seg.append("要点：")
            for f in r["facts"][:5]:
                seg.append("· " + f[:160])
        live = [f for f in (r["foresight"] or []) if f.get("content")]
        if live:
            seg.append("下次注意：")
            for f in live[:2]:
                vu = f.get("valid_until") or ""
                seg.append("· " + f["content"][:160] + (("（有效至 %s）" % vu) if vu else ""))
        seg.append("</scene>")
        txt = "\n".join(seg)
        if used + len(txt) > budget and parts:
            break
        parts.append(txt)
        used += len(txt)

    if not parts:
        return ""
    return "<scenes note=\"这是过去某些事的完整经过。与当前问题相关时才参考；无关就当它不存在\">\n" + "\n\n".join(parts) + "\n</scenes>"


def _cos(a, b):
    """两边都已 L2 归一化，点积即余弦。"""
    return sum(x * y for x, y in zip(a, b))


def dedupe(c, thr=0.85, dry=False):
    """同一个 `at` 顺序里，新的留、旧的并。

    按 at DESC 遍历；每条跟「已保留的」比余弦，> thr 就判为同一件事，
    当前这条（更旧）标 status='merged' 并写 merged_into=<保留的 id>。
    """
    from .embed import embed
    ensure(c)
    rows = c.execute(
        "SELECT id, title, narrative, at, status FROM scenes "
        "WHERE status <> 'merged' "
        "ORDER BY (CASE WHEN status='confirmed' THEN 0 ELSE 1 END), at DESC, id DESC").fetchall()
    if not rows:
        return {"kept": 0, "merged": 0, "pairs": []}
    texts = [((r["title"] or "") + "。" + (r["narrative"] or "")[:300]) for r in rows]
    vecs = embed(texts)
    kept, pairs = [], []
    for r, v in zip(rows, vecs):
        dup = None
        for kid, kv in kept:
            if _cos(v, kv) > thr:
                dup = kid
                break
        if dup is None:
            kept.append((r["id"], v))
        else:
            pairs.append((r["id"], dup))
    if not dry and pairs:
        for old, new in pairs:
            c.execute("UPDATE scenes SET status=?, merged_into=? WHERE id=?",
                      ("merged", new, old))
        c.commit()
    return {"kept": len(kept), "merged": len(pairs), "pairs": pairs}
