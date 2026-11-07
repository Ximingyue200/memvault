"""程序性记忆：从 event/experience 里抽「情境 → 动作 → 结果」的可执行套路。

跟 dream 的区别：
  dream      一条判断（他是什么样的人）
  procedure  一个动作（遇到 X 就这么做）—— 带命令、带路径、带参数
"""
import json, re, sqlite3, time
from .dream import con, get_settings, call, llm_cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS procedures(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE,
  category TEXT,
  situation TEXT,
  action TEXT,
  result TEXT,
  evidence TEXT,
  n_support INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft',
  model TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_proc_cat ON procedures(category);
CREATE INDEX IF NOT EXISTS idx_proc_status ON procedures(status);
"""

PROMPT_P = """你在从一个人的历史记录里抽取「他遇到这类问题时怎么办」的固定套路。

下面是一个【主题簇】：同一个主题下、跨不同时间的 {n} 条记录。

每条 procedure 必须三段：
- situation：具体症状。必须能一眼认出来（报错串 / 具体数字 / 具体设备型号 / 具体现象）。
- action：他实际做了什么。必须可执行（具体命令、参数、改哪个文件、看哪里）。
- result：成没成。没成的话，下一步是什么。

硬要求：
1 只抽这个簇里反复出现的套路。只出现过一次的具体操作不算套路。
2 situation 不能是「遇到问题」「有异常」这类；必须指向一种能辨认的现象。
3 action 不能是「检查一下」「优化配置」这类；必须能照着做。
4 不许编。记录里没有的动作、命令、参数，一个字都不要写。
5 输出 JSON 数组，每项形如 {"situation":"...","action":"...","result":"...","evidence":[卡片id数字]}。
6 evidence 只填真正支撑这一条的卡片 id，最多 6 个。
7 一个簇抽 1-3 条。如果这个簇里没有反复出现的套路，返回 []。
8 只输出 JSON，不要任何解释、不要 markdown 代码块。

主题：{cat}
记录：
{records}"""


def ensure(c):
    c.executescript(SCHEMA); c.commit()


def _pick_by_cat(c, cat, limit=14):
    """一个主题簇：优先取 event/experience（有过程的），再补 fact/definition。"""
    rows = c.execute("""
        SELECT id, title, body, occurred_at, kind FROM cards
        WHERE status='active' AND category=? AND occurred_at IS NOT NULL AND occurred_at<>''
        ORDER BY CASE kind WHEN 'event' THEN 0 WHEN 'experience' THEN 1 ELSE 2 END,
                 occurred_at ASC
        LIMIT ?""", (cat, limit)).fetchall()
    return rows


def _parse(txt):
    t = (txt or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t); t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        return []
    s = m.group(0)
    for cand in (s, s.rstrip(",").rstrip() + "]", re.sub(r",\s*]", "]", s)):
        try:
            v = json.loads(cand)
            return v if isinstance(v, list) else []
        except Exception:
            continue
    return []


def _slug(cat, sit):
    import hashlib
    return "p-" + hashlib.md5((cat + "|" + sit).encode()).hexdigest()[:12]


def _save(c, cat, items, model):
    n = 0
    for it in items:
        sit = (it.get("situation") or "").strip()
        act = (it.get("action") or "").strip()
        res = (it.get("result") or "").strip()
        ev = it.get("evidence") or []
        if not (sit and act) or len(sit) < 8 or len(act) < 8:
            continue
        ev = [str(x).lstrip("#") for x in ev if str(x).lstrip("#").isdigit()][:6]
        c.execute("""INSERT INTO procedures(slug,category,situation,action,result,evidence,
                     n_support,status,model,created_at)
                     VALUES(?,?,?,?,?,?,?,'draft',?,?)
                     ON CONFLICT(slug) DO UPDATE SET
                       action=excluded.action, result=excluded.result,
                       evidence=excluded.evidence, n_support=excluded.n_support,
                       model=excluded.model""",
                  (_slug(cat, sit), cat, sit, act, res,
                   json.dumps(ev, ensure_ascii=False), len(ev), model,
                   time.strftime("%Y-%m-%dT%H:%M:%S")))
        n += 1
    c.commit()
    return n


def run(cats=None, apply=False, limit_cat=6, per_cat=14):
    c = con(); ensure(c)
    cfg = llm_cfg(c, "procedure")
    model = cfg.get("dream.model") or "deepseek-flash"
    if not cats:
        cats = [r["category"] for r in c.execute("""
            SELECT category, COUNT(*) n FROM cards
            WHERE status='active' AND category IS NOT NULL AND category<>'其它'
              AND kind IN ('event','experience')
            GROUP BY category ORDER BY n DESC LIMIT ?""", (limit_cat,))]
    t0 = time.time(); out = []
    for cat in cats:
        rows = _pick_by_cat(c, cat, per_cat)
        if len(rows) < 8:
            out.append({"cat": cat, "skipped": "too_few", "n": len(rows)}); continue
        recs = "\n".join(
            f"#{r['id']} [{(r['occurred_at'] or '')[:10]}] ({r['kind']}) {r['title']} —— "
            + " ".join((r['body'] or "").split())[:190]
            for r in rows)
        prompt = PROMPT_P.replace("{n}", str(len(rows))).replace("{cat}", cat).replace("{records}", recs)
        try:
            txt = call(prompt, tries=2, cfg=cfg)
        except Exception as e:
            out.append({"cat": cat, "error": str(e)[:120]}); continue
        items = _parse(txt)
        saved = _save(c, cat, items, model) if apply else 0
        out.append({"cat": cat, "cards": len(rows), "prompt": len(prompt),
                    "parsed": len(items), "saved": saved, "items": items})
    c.commit()
    return {"ok": True, "apply": apply, "cats": cats,
            "total_s": round(time.time() - t0, 1), "results": out}


def listing(c, status=None, category=None, limit=80):
    ensure(c)
    w = []; a = []
    if status: w.append("status=?"); a.append(status)
    if category: w.append("category=?"); a.append(category)
    where = ("WHERE " + " AND ".join(w)) if w else ""
    rows = c.execute(f"""SELECT * FROM procedures {where}
                         ORDER BY category, n_support DESC, created_at DESC LIMIT ?""",
                     (*a, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try: d["evidence"] = json.loads(d.get("evidence") or "[]")
        except Exception: d["evidence"] = []
        out.append(d)
    return out


def set_status(c, pid, status):
    c.execute("UPDATE procedures SET status=? WHERE id=?", (status, pid)); c.commit()
    return {"ok": True, "id": pid, "status": status}


if __name__ == "__main__":
    import sys
    do = "--apply" in sys.argv
    r = run(apply=do)
    for x in r["results"]:
        print(f"{x.get('cat','?'):24} cards={x.get('cards','-'):>3} parsed={x.get('parsed','-'):>2} saved={x.get('saved','-')}")
        for it in (x.get("items") or [])[:2]:
            print(f"    · {it.get('situation','')[:56]}")
    print(f"总耗时 {r['total_s']}s  apply={do}")
