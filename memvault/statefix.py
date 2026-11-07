"""实体状态机：只处理「待办 → 完成」这一种取代。

不做全库余弦，不做 supersede 判定（余弦量话题不量互斥）。
只对含状态词的卡抽 (entity, state)，同实体聚合，找状态迁移，输出**提议**供人确认。
"""
import json, os, re, sqlite3, sys, time

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

DONE = re.compile(r"(已完成|已经完成|已跑完|跑完了|搞定了|已解决|已修复|成功了|已落地|已上线|已接上|已通|搞完了)")
TODO = re.compile(r"(还没|未跑|待办|待跑|需要跑|下一步|尚未|没有跑|待落地|待接|未完成|计划中|要做|得做)")

PROMPT = """从这张记忆卡里抽出「一个实体 + 它的状态」。

实体：这张卡主要在说哪个东西/哪件事。用最短的名词短语，6–20 字。
      不要写成句子，不要带"用户"，不要带时间。
      例：「U30 Air 刷机」「PVE 核显直通」「OpenViking 记忆服务」
状态：pending（还没做）/ active（在做）/ done（做完了）/ dropped（放弃了）

只输出 JSON：{"entity": "...", "state": "pending|active|done|dropped"}

标题：{title}
正文：{body}"""


def llm(title, body):
    if not KEY:
        return None
    import urllib.request
    p = PROMPT.replace("{title}", title[:100]).replace("{body}", body[:400])
    req = urllib.request.Request(
        BASE + "/chat/completions",
        data=json.dumps({"model": MODEL, "temperature": 0,
                         **_effort_kw("learn"),
                         "messages": [{"role": "user", "content": p}],
                         "response_format": {"type": "json_object"},
                         "max_tokens": 120}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {KEY}"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read())
            return json.loads(d["choices"][0]["message"]["content"])
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def main(write=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = [r for r in con.execute(
        "SELECT id,title,body,kind,occurred_at,created_at FROM cards WHERE status='active'")
        if DONE.search(r["title"] + r["body"]) or TODO.search(r["title"] + r["body"])]
    print(f"含状态词的卡 {len(rows)} 张", flush=True)

    items = []
    for i, r in enumerate(rows, 1):
        v = llm(r["title"], r["body"])
        if not v or not v.get("entity"):
            continue
        items.append({"id": r["id"], "title": r["title"], "entity": v["entity"].strip(),
                      "state": v.get("state", "pending"),
                      "ts": r["occurred_at"] or r["created_at"] or ""})
        if i % 20 == 0:
            print(f"  {i}/{len(rows)}", flush=True)

    print(f"抽出 {len(items)} 条实体状态", flush=True)

    # 实体归一（去空格/标点，小写）
    def nk(s):
        return re.sub(r"[^\w\u4e00-\u9fff]", "", s).lower()

    groups = {}
    for it in items:
        groups.setdefault(nk(it["entity"]), []).append(it)

    props = []
    for k, g in groups.items():
        if len(g) < 2:
            continue
        g.sort(key=lambda x: x["ts"])
        done = [x for x in g if x["state"] == "done"]
        open_ = [x for x in g if x["state"] in ("pending", "active")]
        for d in done:
            for o in open_:
                if o["ts"] < d["ts"]:
                    props.append({"old": o, "new": d, "entity": g[0]["entity"]})

    print(f"\n状态迁移提议 {len(props)} 条：", flush=True)
    for p in props:
        print(f"  [{p['old']['id']}] {p['old']['title'][:30]} ({p['old']['state']})"
              f"  →  [{p['new']['id']}] {p['new']['title'][:30]} (done)   entity={p['entity']}",
              flush=True)

    if write and props:
        n = 0
        for p in props:
            con.execute("UPDATE cards SET supersedes=? WHERE id=? AND supersedes IS NULL",
                        (p["new"]["id"], p["old"]["id"]))
            con.execute(
                "INSERT OR REPLACE INTO edges(src,dst,kind,weight,edge_type,label) "
                "VALUES(?,?,?,?,?,?)",
                (p["new"]["id"], p["old"]["id"], "supersedes", 1.0, "supersede",
                 f"状态迁移 {p['old']['state']}→done"))
            n += 1
        con.commit()
        print(f"\n写入 {n} 条")
    else:
        print("\n(dry-run)")


if __name__ == "__main__":
    main(write="--write" in sys.argv)
