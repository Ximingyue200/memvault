"""supersede 边：找出「旧卡说待办 / 新卡说已完成」的对，建立取代关系。

不删旧卡，只标记。检索时被取代的降权但仍可追溯。

三档判定（成本从低到高）：
  1. 规则：同 kind + 余弦 ≥0.86 + 一侧含完成词一侧含待办词 + 新卡时间更晚
  2. LLM：余弦 0.80–0.86 的候选，逐对问一句「B 是否取代 A」
  3. 其余不动
"""
import difflib, json, math, os, re, sqlite3, sys, time
import numpy as np

try:
    from .effort import kwargs as _effort_kw
except Exception:  # 直接当脚本跑时没有包上下文
    def _effort_kw(task=None):
        return {}

try:  # token 记账
    from . import _meter  # noqa: F401
except Exception:
    pass

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
KEY = os.environ.get("MEMVAULT_LLM_KEY", "")
BASE = os.environ.get("MEMVAULT_LLM_BASE", "https://api.deepseek.com/v1")
MODEL = os.environ.get("MEMVAULT_LLM_MODEL", "deepseek-chat")

DONE = re.compile(r"(已完成|已经完成|已跑完|跑完了|搞定了|已解决|已修复|成功了|已落地|已上线|已接上|已通)")
TODO = re.compile(r"(还没|未跑|待办|待跑|需要跑|下一步|尚未|没有跑|待落地|待接|未完成|计划中)")


def load(con):
    rows = con.execute(
        "SELECT id,title,body,kind,created_at,supersedes,occurred_at "
        "FROM cards WHERE status='active'"
    ).fetchall()
    return [dict(r) for r in rows]


def load_vecs(con):
    ids, vecs = [], []
    for cid, dim, blob in con.execute("SELECT card_id, dim, vec FROM vectors"):
        v = np.frombuffer(blob, dtype=np.float32)
        if v.size != dim:
            continue
        ids.append(cid); vecs.append(v)
    if not ids:
        return [], None
    m = np.vstack(vecs)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return ids, m / n


def llm_judge(a, b):
    """问：B 是否取代 A（B 是同一件事的更新状态）"""
    if not KEY:
        return None
    prompt = (
        "判断 B 是否取代 A。取代的含义：同一件事，B 是 A 的更新版本（A 说的状态已过时）。\n"
        '只回答 JSON：{"supersede": true|false, "why": "一句话"}\n\n'
        f"A: {a['title']}\n{a['body'][:300]}\n\nB: {b['title']}\n{b['body'][:300]}"
    )
    import urllib.request
    body = json.dumps({
        "model": MODEL, "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "max_tokens": 200,
        **_effort_kw("learn"),
    }).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        txt = d["choices"][0]["message"]["content"]
        return json.loads(txt)
    except Exception as e:
        return {"supersede": False, "why": f"err:{e}"[:60]}


def main(dry=True, cos_min=0.80, rule_cos=0.86, topk=3, use_llm=False, max_llm=150):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cards = {c["id"]: c for c in load(con)}
    ids, mat = load_vecs(con)
    keep = [(i, c) for i, c in enumerate(ids) if c in cards]
    ids = [c for _, c in keep]
    if mat is None or not ids:
        print("no vectors"); return
    mat = mat[[i for i, _ in keep]]
    pos = {c: i for i, c in enumerate(ids)}

    # 时间序：新卡 > 旧卡
    def t(card):
        return card.get("occurred_at") or card.get("created_at") or ""

    pairs = []
    K = min(topk + 1, len(ids))
    for chunk_start in range(0, len(ids), 512):
        block = ids[chunk_start:chunk_start + 512]
        bm = mat[chunk_start:chunk_start + 512]
        sim = bm @ mat.T                      # (512, N)
        for r, cid in enumerate(block):
            row = sim[r]
            top = np.argpartition(-row, K - 1)[:K]
            for j in top:
                if j == chunk_start + r:
                    continue
                s = float(row[j])
                if s < cos_min:
                    continue
                other = ids[j]
                a, b = cards[cid], cards[other]
                if a["kind"] != b["kind"]:
                    continue
                # 方向：时间晚的取代早的
                if t(b) < t(a):
                    a, b = b, a
                if t(a) == t(b):
                    continue
                pairs.append((a, b, s))

    # 去重（同一对只留最高分）
    seen = {}
    for a, b, s in pairs:
        k = (a["id"], b["id"])
        if k not in seen or s > seen[k][2]:
            seen[k] = (a, b, s)
    cands = sorted(seen.values(), key=lambda x: -x[2])
    print(f"候选对 {len(cands)} 个（余弦 ≥{cos_min}）", flush=True)
    if cands:
        print("  最高分:", [(round(x[2],3), x[0]['title'][:18], x[1]['title'][:18]) for x in cands[:5]], flush=True)

    out = []
    llm_calls = 0
    for a, b, s in cands:
        ta, tb = a["title"] + a["body"], b["title"] + b["body"]
        a_done, a_todo = bool(DONE.search(ta)), bool(TODO.search(ta))
        b_done, b_todo = bool(DONE.search(tb)), bool(TODO.search(tb))
        verdict, why = None, ""
        # 严格对立才算规则命中：一侧待办、另一侧已完成，且标题讲的是同一件事。
        # 原来是「状态不同 + 余弦≥0.86」，噪音太大（把"定义卡 A"当成了"B 取代了 A"）。
        tr = difflib.SequenceMatcher(None, a["title"], b["title"]).ratio()
        strict = ((a_todo and b_done and not a_done and not b_todo)
                  or (b_todo and a_done and not b_done and not a_todo))
        if strict and tr >= 0.55:
            verdict, why = True, "rule: 待办→已完成（标题相似 %.2f）" % tr
        elif use_llm and s >= 0.90 and llm_calls < max_llm:  # 只让高相似度的进 LLM，省钱
            llm_calls += 1
            r = llm_judge(a, b)
            if r and r.get("supersede"):
                verdict, why = True, "llm: " + str(r.get("why"))[:60]
        if verdict:
            out.append((a, b, s, why))

    print(f"判定取代 {len(out)} 对", flush=True)
    for a, b, s, why in out[:20]:
        print(f"  {s:.3f}  [{a['id']}] {a['title'][:28]}  →  [{b['id']}] {b['title'][:28]}   {why}")

    if dry:
        print("(dry-run，未写库)")
        return out

    n = 0
    for a, b, s, why in out:
        con.execute("UPDATE cards SET supersedes=? WHERE id=? AND (supersedes IS NULL OR supersedes=?)",
                    (b["id"], a["id"], a["id"]))
        con.execute(
            "INSERT OR REPLACE INTO edges(src,dst,kind,weight,edge_type,label) "
            "VALUES(?,?,?,?,?,?)",
            (b["id"], a["id"], "supersedes", float(s), "supersede", why)
        )
        n += 1
    con.commit()
    print(f"写入 {n} 对")


if __name__ == "__main__":
    dry = "--write" not in sys.argv
    use_llm = "--llm" in sys.argv
    maxn = 150
    for a in sys.argv:
        if a.startswith("--max="):
            maxn = int(a.split("=", 1)[1])
    main(dry=dry, use_llm=use_llm, max_llm=maxn)
