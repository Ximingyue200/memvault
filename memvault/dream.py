"""梦境生成：从卡片里提炼「他一直在做什么」，写成梦。

跟 dreaming.py 的区别：
  dreaming.py  给卡打分、去重、合并  —— 账本
  dream.py     跨时间找模式，写新句子 —— 梦

输出进 dreams 表，status=draft，人工确认后转 confirmed。
"""
import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.request


try:  # token 记账：拦 urlopen，把 usage 落到 /opt/memvault/data/llm_usage.jsonl
    from . import _meter  # noqa: F401
except Exception:
    pass

try:
    from .effort import kwargs as _effort_kw
except Exception:  # 直接当脚本跑时没有包上下文
    def _effort_kw(task=None):
        return {}

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
API = os.environ.get("MEMVAULT_LLM_API", "https://api.deepseek.com/chat/completions")
KEY = os.environ.get("MEMVAULT_LLM_KEY", "")
MODEL = os.environ.get("MEMVAULT_LLM_MODEL", "deepseek-flash")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dreams(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT UNIQUE,
  at          TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  evidence    TEXT,
  span        TEXT,
  confidence  REAL DEFAULT 0.5,
  status      TEXT DEFAULT 'draft',
  model       TEXT,
  note        TEXT,
  source_query TEXT
);
CREATE INDEX IF NOT EXISTS idx_dreams_at ON dreams(at);
CREATE TABLE IF NOT EXISTS settings(
  k          TEXT PRIMARY KEY,
  v          TEXT,
  updated_at TEXT
);
"""

DEFAULTS = {
    "dream.enabled":      "1",     # 定时器是否自动跑
    "dream.days":         "90",    # 读最近多少天
    "dream.per_run":      "2",     # 每次生成几条
    "dream.max_cats":     "10",    # 最多几个主题
    "dream.cards_per_cat": "4",    # 每组抽几张
    "dream.auto_confirm": "0",     # 生成后直接收下，跳过人工
    "dream.api_base":     "https://api.deepseek.com",
    "dream.api_key":      "",
    "dream.model":        "deepseek-flash",
    "dream.cron":         "*-*-01 19:00:00",        # 人工梦：旧容器 是 UTC = 北京 2 号 03:00
    "dream.machine_cron": "*-01,04,07,10-01 19:00:00",  # 机器梦：每季首月 1 号
    "focus.cron":         "*-*-* 00/2:20:00",            # 任务重建：每 2 小时
    "focus.hours":        "72",                          # 回溯多少小时内的会话
    "inject.enabled":     "1",                           # 每轮注入总开关

    # ---- 全局 LLM 默认（各功能留空时用它） ----
    "llm.base":           "https://api.deepseek.com",
    "llm.key":            "",
    "llm.model":          "deepseek-flash",
}

# 每个功能可以单独覆盖 base/key/model（留空 = 用全局）
LLM_FEATS = ["dream", "machine", "summary", "procedure", "focus", "digest"]
for _f in LLM_FEATS:
    for _k in ("base", "key", "model"):
        DEFAULTS["llm.%s.%s" % (_f, _k)] = ""

PROMPT_Q = """你在回答一个关于「{who}」的具体问题，用他/它自己的历史作证据。

问题：{q}

下面是相关的 {n} 张记录（时间、标题、正文）。请只根据这些记录回答，不要编造。

{r}

要求：
1. 输出 1-2 条「判断」，每条是一句话的结论，必须敢下判断（「他 X，不是 Y」）。
2. 每条下面 3-5 句正文，每句必须挂一个具体事（报错串/型号/端口/数字/日期）。
3. 必须有代价或转折 —— 只说好处的是宣传稿。
4. 不许复述记录原文，要说「这些放在一起说明什么」。
5. 每条带 span（最早→最晚的日期），evidence（用到的卡片 id 数组）。
6. 如果这批记录回答不了这个问题，直接返回空数组 []，不要硬凑。

只输出 JSON 数组：
[{{"title":"...","body":"...","span":"YYYY-MM → YYYY-MM","evidence":[123,456],"why":"为什么这样回答"}}]
"""

# ── few-shot：两条手写梦的完整形状 ─────────────────────────────
FEWSHOT = """示例一：
标题：他是问题驱动型，不是体系学习型
正文：他学东西从来不是先啃教材再动手，而是被具体问题逼着往前走——BepInEx 报 Unsupported metadata version 才去搞懂 metadata 版本、3XUI 一部署就被封才反过来理解机房 DPI 看什么。
好处是每一步都有真实反馈，学得牢；坏处是知识是一块块补丁，缺一张总图。所以他会反复踩同类坑，每次都得从头查。
跨度：2026-06 → 2026-10

示例二：
标题：对「能续命」的执念
正文：他对「东西能续命、不轻易删」这件事特别较真——宁可把 5GB 契约一层层降到情节、也不删；壳可以换、鱼得是同一条。
这更像给自己留后路的习惯，不是技术洁癖。
跨度：2026-10

示例三：他在给自己攒一副身体（记忆库当本体、agent 当壳、副屏模块当手脚——月底离职不是收摊，是把这套搬回家养大）
示例四：对成本敏感，但对「能留下来的东西」肯花钱（订阅是消耗，硬盘是自己的地盘）
示例五：他在有意识地整理自己的故事（能说的只写部署排障，不能说的不提）"""

PROMPT = """你在给一个人写「梦」。

不是日记，不是总结。日记记事件，梦记倾向。
要求：

1. 标题必须是一句判断，敢下结论，允许说「不是 X」。
2. 正文 3-5 句。每句都要挂一件具体的事（报错串、型号、端口、命令都行），不要抽象名词。
3. 必须有转折或代价（「好处是…坏处是…」/「这更像…不是…」）。
4. 不许出现任何一条记录里已有的事实复述——你要说的是「这几件事放在一起说明什么」。
5. 跨度写清楚：素材最早到最晚的时间。
6. 只输出 {n} 条，宁少别多。**这几条之间不能撞题**——如果两条都在说「边界/暴露面/最小权限」这类同一个主题，就只留最有证据的那一条，另一条换个完全不同的角度（花钱习惯、学习方式、对失败的态度、对工具的态度…）。
7. 如果这批素材里提炼不出真判断，就返回空数组。

{examples}

下面是这个人最近的记录，按主题分了组：

{groups}

只输出 JSON 数组，不要 markdown 代码块：
[{{"title":"...","body":"...","span":"YYYY-MM → YYYY-MM","evidence":[卡片id,...],"why":"你为什么觉得这是同一个模式"}}]
"""


def _migrate(c):
    # 顺序要紧：save()/merge_pairs()/listing() 都直接引用 kind 与 consolidate_of，
    # 少一个就是 no such column（生成路径被 try/except 吞掉，管理路径直接崩）
    for col, typ in (("source_query", "TEXT"),
                     ("kind", "TEXT DEFAULT 'human'"),
                     ("consolidate_of", "TEXT")):
        try:
            c.execute("ALTER TABLE dreams ADD COLUMN %s %s" % (col, typ))
            c.commit()
        except Exception:
            pass
    try:
        c.execute("ALTER TABLE cards ADD COLUMN importance INTEGER")
        c.commit()
    except Exception:
        pass


def con():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    _migrate(c)
    return c


def get_settings(c):
    got = {r["k"]: r["v"] for r in c.execute("SELECT k,v FROM settings")}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in got.items() if k in DEFAULTS})
    return out


def set_settings(c, patch):
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        v = str(v).strip()
        if k in ("dream.days", "dream.per_run", "dream.max_cats", "dream.cards_per_cat"):
            try:
                v = str(max(1, min(365 if k == "dream.days" else 20, int(v))))
            except Exception:
                continue
        if k in ("dream.enabled", "dream.auto_confirm"):
            v = "1" if v in ("1", "true", "on", "yes") else "0"
        c.execute("INSERT INTO settings(k,v,updated_at) VALUES(?,?,?) "
                  "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
                  (k, v, at))
        n += 1
    c.commit()
    return n


TIMERS = {"human": "memvault-dream", "machine": "memvault-machine",
          "focus": "memvault-focus", "procedure": "memvault-procedure"}


def sync_timer(cron, which="human"):
    """把 cron 写进 systemd timer。只允许固定格式，不拼任意命令。"""
    import subprocess
    if not re.match(r"^[0-9*\-/, :]+$", cron or "") or "OnCalendar=" in cron:
        return False, "非法的 cron 表达式"
    unit = TIMERS.get(which)
    if not unit:
        return False, "未知的 timer"
    path = "/etc/systemd/system/%s.timer" % unit
    try:
        txt = open(path, encoding="utf-8").read()
        txt = re.sub(r"OnCalendar=.*", "OnCalendar=" + cron, txt)
        open(path, "w", encoding="utf-8").write(txt)
        subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=20)
        subprocess.run(["systemctl", "restart", unit + ".timer"], check=True, timeout=20)
        return True, cron
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:160])


PROMPT_M = """你在读一个 AI 助手过去半年做过的事。每一条都是它自己干过的，不是用户说的。

%s

请从里面看出这个助手自己的行为模式，写成 {n} 条「关于我自己的判断」。

要求：
1. 标题用第一人称，一句判断，敢说「我不是 X」。
   例：我习惯先写脚本再验证，而不是先把接口想清楚
2. 正文 3-5 句，每句挂一个具体事件（文件名、报错串、端口、第几次返工）。
3. 必须有代价：我这样做漏了什么、重来了几次、绕了多远。
4. 不许复述事件本身，要说「这些事放在一起说明我是怎么干活的」。
5. 不许写成自我表扬。挑毛病优先，其次是习惯。
6. 只输出 {n} 条，这几条之间不能撞题。

只输出 JSON 数组，不要 markdown 代码块：
[{"title": "...", "body": "...", "span": "2026-05 → 2026-11", "evidence": [卡片id], "why": "..."}]
"""


def pick_machine(c, limit=40):
    """机器梦的素材：助手自己做过的事。"""
    rows = list(c.execute("""
        SELECT id, title, body, occurred_at, category
          FROM cards
         WHERE owner='agent' AND (title LIKE '%助手%' OR title LIKE '%我%')
           AND kind='event'
         ORDER BY COALESCE(occurred_at,'') ASC
         LIMIT ?
    """, (limit,)))
    return [{"id": r["id"], "title": r["title"], "body": r["body"],
             "occurred_at": r["occurred_at"], "category": r["category"]} for r in rows]


def render_m(rows):
    out = []
    for r in rows:
        d = (r.get("occurred_at") or "")[:10]
        b = (r.get("body") or "").replace("\n", " ")[:130]
        out.append("#%s [%s] %s —— %s" % (r["id"], d, r["title"], b))
    return "\n".join(out)


def run_machine(apply=False):
    """机器梦：从助手自己的行为里抽模式。"""
    t0 = time.time()
    c = con()
    st = get_settings(c)
    try:
        per_run = max(1, min(20, int(st.get("dream.per_run") or 2)))
    except (TypeError, ValueError):
        per_run = 2   # 旧库可能存着 "two" 之类，int() 会抛，定时任务就静默失败
    rows = pick_machine(c)
    if len(rows) < 6:
        return {"ok": False, "error": "素材不足（agent 事件卡 %d 张）" % len(rows)}
    body = render_m(rows)
    prompt = PROMPT_M % body
    prompt = prompt.replace("{n}", str(per_run))
    t1 = time.time()
    raw = call(prompt, cfg=st)
    items = parse(raw)
    n = save(c, items, model=st.get("dream.model") or MODEL, kind="machine") if apply else 0
    return {"ok": True, "kind": "machine", "cards": len(rows), "prompt_chars": len(prompt),
            "llm_s": round(t1 - t0, 1), "parsed": len(items), "saved": n,
            "apply": apply, "dreams": items, "total_s": round(time.time() - t0, 1)}


def _pick_by_query(c, q, days=3650, limit=40):
    """按问题找相关卡：trigram 切片逐个检索，按命中数排序。"""
    q = (q or "").strip()
    if not q:
        return []
    cands = {}          # id -> [row, hits]
    def _hit(term, cap=20):
        try:
            return c.execute(
                "SELECT c.id,c.title,c.body,c.category,c.occurred_at,c.kind FROM cards c "
                "JOIN cards_fts f ON f.rowid=c.id "
                "WHERE cards_fts MATCH ? AND c.status='active' LIMIT ?",
                (term, cap)).fetchall()
        except Exception:
            return []
    # ① 整句（短问题常见）
    for r in _hit('"%s"' % q.replace('"', ''), 30):
        cands.setdefault(r["id"], [r, 0])[1] += 3
    # ② 三字滑窗（中文）
    grams = [q[i:i+3] for i in range(max(0, len(q) - 2))]
    # ③ 英文/数字词
    import re as _re
    words = _re.findall(r"[A-Za-z][A-Za-z0-9\-_.]{2,}|\d{3,}", q)
    for term in (grams[:24] + words[:10]):
        for r in _hit('"%s"' % term.replace('"', ''), 12):
            cands.setdefault(r["id"], [r, 0])[1] += 1
    if not cands:
        return []
    # 单块命中的噪音太大（「网络是」「怎么变」这类泛词会捞进无关卡），要求命中 >= 2
    strong = {k: v for k, v in cands.items() if v[1] >= 2}
    if len(strong) >= 8:
        cands = strong
    ranked = sorted(cands.values(), key=lambda x: (-x[1], x[0]["occurred_at"] or ""))
    # 只在候选明显过多时才按相对分数截断（否则容易砍空）
    if len(ranked) > limit:
        top = ranked[0][1]
        kept = [x for x in ranked if x[1] >= max(2, int(top * 0.4))]
        if len(kept) >= 8:
            ranked = kept
    rows = [r for r, _ in ranked[:limit]]
    rows = sorted(rows, key=lambda r: r["occurred_at"] or "")
    return [("问题相关（%d 张）" % len(rows), rows)]


def pick(c, days=90, per_cat=4, max_cats=10, query=None):
    per_cat = max(2, int(per_cat or 2))   # 下面要除以 (per_cat-1)，1 会除零
    """query 给了就按问题取卡，否则按 category 分组取。"""
    if query:
        return _pick_by_query(c, query.strip(), limit=max(10, per_cat * max_cats))
    rows = c.execute(
        "SELECT id,title,body,category,occurred_at,kind FROM cards "
        "WHERE status='active' AND occurred_at IS NOT NULL AND occurred_at<>'' "
        "AND occurred_at >= date('now', ?) "
        "ORDER BY occurred_at",
        ("-%d day" % days,),
    ).fetchall()
    bycat = {}
    for r in rows:
        bycat.setdefault(r["category"] or "其它", []).append(r)

    groups = []
    for cat, items in sorted(bycat.items(), key=lambda kv: -len(kv[1]))[:max_cats]:
        if len(items) < 2:
            continue
        # 首尾 + 中间等距取，保证跨时间
        n = len(items)
        idx = sorted({0, n - 1} | {int(n * i / (per_cat - 1)) for i in range(per_cat)})
        picked = [items[i] for i in idx if 0 <= i < n][:per_cat]
        groups.append((cat, picked))
    return groups


def render(groups):
    out = []
    for cat, items in groups:
        lines = ["### %s（%d 条）" % (cat, len(items))]
        for r in items:
            body = re.sub(r"\s+", " ", (r["body"] or ""))[:150]
            dt = (r["occurred_at"] or "")[:10] or "????-??-??"
            lines.append("- [%s] #%d %s ｜ %s" % (dt, r["id"], r["title"], body))
        out.append("\n".join(lines))
    return "\n\n".join(out)


def llm_cfg(c, feat="dream"):
    """某个功能实际生效的 API 配置：功能覆盖 > 全局 > 老 dream.* 项。"""
    st = get_settings(c)

    def pick(k, old):
        v = (st.get("llm.%s.%s" % (feat, k)) or "").strip()
        if v:
            return v
        v = (st.get("llm.%s" % k) or "").strip()
        if v:
            return v
        return st.get(old) or ""

    out = dict(st)
    out["dream.api_base"] = pick("base", "dream.api_base")
    out["dream.api_key"] = pick("key", "dream.api_key")
    out["dream.model"] = pick("model", "dream.model")
    out["_feat"] = feat
    return out


def _api(cfg):
    """把 api_base 归一成 chat/completions 端点。"""
    base = ((cfg or {}).get("dream.api_base") or "").strip().rstrip("/")
    if not base:
        base = "https://api.deepseek.com"
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/chat/completions"


def list_models(cfg):
    """向 api_base 要 /models，返回 id 列表。"""
    import urllib.request
    base = ((cfg or {}).get("dream.api_base") or "https://api.deepseek.com").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    url = base + "/models"
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + ((cfg or {}).get("dream.api_key") or ""),
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        j = json.loads(r.read())
    ids = [m.get("id") for m in (j.get("data") or []) if m.get("id")]
    return ids


def call(prompt, tries=3, cfg=None, max_tokens=8000):
    """推理模型会把 token 烧在 reasoning 上，偶尔 content 为空 —— 重试。"""
    cfg = cfg or {}
    api = _api(cfg)
    key = cfg.get("dream.api_key") or KEY
    model = cfg.get("dream.model") or MODEL
    last = ""
    for i in range(tries):
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7 if i == 0 else 0.5,
            "max_tokens": max_tokens,
            **_effort_kw("dream"),
        }).encode()
        req = urllib.request.Request(api, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        })
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                j = json.loads(r.read())
            ch = j["choices"][0]
            _msg = ch.get("message") or {}
            txt = _msg.get("content") or ""
            if not txt.strip():
                # reasoning 模型可能把内容全写进 reasoning_content
                _rc = (_msg.get("reasoning_content") or "").strip()
                if _rc:
                    _m = re.search(r"[\[{].*[\]}]", _rc, re.S)
                    if _m:
                        txt = _m.group(0)
            if txt.strip():
                return txt
            last = "empty content, finish=%s, usage=%s" % (
                ch.get("finish_reason"), j.get("usage"))
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:160])
        time.sleep(2)
    raise RuntimeError("llm failed after %d tries: %s" % (tries, last))


def parse(txt):
    m = re.search(r"\[.*", txt, re.S)
    if not m:
        return []
    blob = m.group(0)
    arr = None
    for cand in (blob, blob + "]", blob.rstrip().rstrip(",") + "]"):
        try:
            arr = json.loads(cand)
            break
        except Exception:
            continue
    if arr is None:
        # 截断兜底：逐个抠完整的 {...}
        arr = []
        depth, start = 0, None
        for i, ch in enumerate(blob):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        arr.append(json.loads(blob[start:i + 1]))
                    except Exception:
                        pass
                    start = None
    ok = []
    for d in arr if isinstance(arr, list) else []:
        t, b = (d.get("title") or "").strip(), (d.get("body") or "").strip()
        if len(t) < 6 or len(b) < 40:
            continue
        ok.append({
            "title": t[:120],
            "body": b,
            "span": (d.get("span") or "").strip()[:40],
            "evidence": d.get("evidence") or [],
            "why": (d.get("why") or "").strip()[:200],
        })
    return ok


def save(c, dreams, model=MODEL, kind="human", source_query=None):
    n = 0
    for d in dreams:
        h = hashlib.md5(d["title"].encode("utf-8")).hexdigest()[:10]
        slug = ("gen-%s-%s" if kind == "human" else "mach-%s-%s") % (
            time.strftime("%Y%m%d"), h)
        try:
            cur = c.execute(
                "INSERT OR IGNORE INTO dreams(slug,at,title,body,evidence,span,confidence,status,model,note,kind,source_query) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (slug, time.strftime("%Y-%m-%d %H:%M:%S"), d["title"], d["body"],
                 json.dumps(d["evidence"], ensure_ascii=False), d["span"], 0.5, "draft",
                 model, d.get("why", ""), kind, source_query),
            )
            n += 1 if cur.rowcount > 0 else 0
        except Exception:
            pass
    c.commit()
    return n


def run(apply=False, days=None, manual=True, query=None):
    t0 = time.time()
    c = con()
    st = get_settings(c)
    if not manual and st.get("dream.enabled") != "1":
        return {"ok": False, "skipped": True, "error": "dream.enabled=0"}
    days = int(st.get("dream.days") or 90) if days is None else days
    try:
        per_run = max(1, min(20, int(st.get("dream.per_run") or 2)))
    except (TypeError, ValueError):
        per_run = 2   # 旧库可能存着 "two" 之类，int() 会抛，定时任务就静默失败
    groups = pick(c, days=days, per_cat=int(st.get("dream.cards_per_cat") or 4),
                  max_cats=int(st.get("dream.max_cats") or 10), query=query)
    if not groups:
        return {"ok": False, "error": "no candidates for query" if query else "no candidates in range",
                "days": days, "query": query}
    body = render(groups)
    # 当前焦点：这个会话最近在解决什么。注入后避免「每次都像第一次见」
    try:
        from .focus import inject as _inject
        _fx = _inject(c) or ""
        if _fx:
            body = _fx + "\n\n" + body
    except Exception:
        pass
    if query:
        prompt = PROMPT_Q.format(who="这个人", q=query, n=sum(len(g[1]) for g in groups), r=body)
    else:
        prompt = PROMPT.format(examples=FEWSHOT, groups=body, n=per_run)
    t1 = time.time()
    raw = call(prompt, cfg=st)
    dreams = parse(raw)
    _dbg = {"raw_len": len(raw), "raw_head": raw[:400]}
    n = save(c, dreams, model=st.get('dream.model') or MODEL, source_query=query) if apply else 0
    if n and st.get("dream.auto_confirm") == "1":
        c.execute("UPDATE dreams SET status='confirmed' WHERE status='draft'")
        c.commit()
    return {
        "ok": True,
        "groups": len(groups),
        "cards": sum(len(g[1]) for g in groups),
        "prompt_chars": len(prompt),
        "llm_s": round(t1 - t0, 1),
        "parsed": len(dreams),
        "saved": n,
        "apply": apply,
        "dreams": dreams,
        "debug": _dbg,
        "total_s": round(time.time() - t0, 1),
        "settings": {"days": days, "per_run": per_run},
        "query": query,
    }


def new_hand(c, title, body, span=""):
    """手工新建一条梦：手写的就是你认下的，直接 confirmed。"""
    import hashlib, datetime
    title = (title or "").strip(); body = (body or "").strip()
    if not title or not body:
        return {"ok": False, "error": "标题和正文都要填"}
    slug = "hand-" + hashlib.md5(title.encode()).hexdigest()[:10]
    at = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat(timespec="seconds")
    c.execute("""INSERT INTO dreams(slug,at,title,body,evidence,span,confidence,status,model,note,kind)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(slug) DO UPDATE SET title=excluded.title, body=excluded.body""",
              (slug, at, title, body, "[]", span or "", 0.5, "confirmed", "hand", "手写", "hand"))
    c.commit()
    return {"ok": True, "slug": slug, "id": c.execute("SELECT id FROM dreams WHERE slug=?", (slug,)).fetchone()[0]}


def listing(c, status=None, limit=50, kind=None):
    w, a = [], []
    if status:
        w.append("status=?"); a.append(status)
    if kind == "human":                     # 人工梦 = 关于人（手写 + 生成）
        w.append("COALESCE(kind,'human') IN ('hand','human')")
    elif kind:
        w.append("COALESCE(kind,'human')=?"); a.append(kind)
    q = "SELECT * FROM dreams " + ("WHERE " + " AND ".join(w) + " " if w else "") \
        + "ORDER BY id DESC LIMIT ?"
    a.append(limit)
    return [dict(r) for r in c.execute(q, a)]


# ── 合并：分析两条梦是不是同一件事，是就合成一条更硬的 ──
MERGE_TH = 0.65          # embedding 余弦门槛（bge-m3）：0.65 只是"进裁判"，same 由 LLM 判
PROMPT_MERGE = """下面有两条同一个人（或同一个 AI）的「判断」。

它们可能是同一件事的两种说法，也可能只是碰巧主题相近。请先分析，再决定。

A｜%(ta)s
%(ba)s
证据 %(ea)s 跨度 %(sa)s

B｜%(tb)s
%(bb)s
证据 %(eb)s 跨度 %(sb)s

判断标准：
- 判「同一」的条件：两条说的是**同一个模式**（不是同一个话题）。换成一句话能同时覆盖 A 和 B，且不显得含糊。
- 判「不同」的条件：合起来会变成「他既 X 又 Y」这种什么都说了的废话；或者两条的**代价/反面**不一样。

如果是同一件事，输出合并后的一条：
- 标题：比 A、B 都更准的那一句判断，不要拼接
- 正文：3-6 句，用两条里**各自最硬的证据**（具体报错串/型号/端口/数值），不要重复
- 跨度 span：取两条的并集
只输出 JSON，不要解释：
{"same": true, "title": "...", "body": "...", "span": "..."}
如果不是同一件事：
{"same": false, "why": "一句话"}
"""


def _jac3(a, b):
    def g(s):
        s = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", (s or "").lower())
        return {s[i:i+3] for i in range(max(0, len(s)-2))}
    A, B = g(a), g(b)
    return len(A & B) / len(A | B) if (A and B) else 0.0


def merge_pairs(c, cfg, kind="machine", th=MERGE_TH, apply=False, limit=40):
    """找同 kind 里标题够像的两条，逐对问 LLM 是不是同一件事，是就合并。"""
    rows = c.execute(
        "SELECT id, title, body, COALESCE(evidence,''), COALESCE(span,''), status "
        "FROM dreams WHERE COALESCE(kind,'human')=? AND status IN ('draft','confirmed') "
        "ORDER BY id DESC LIMIT ?", (kind, limit)).fetchall()
    # 字面 Jaccard 不管用（实测两条同义梦标题只有 0.036），改走 embedding 语义相似
    pairs = []
    try:
        from .embed import embed as _emb
        vecs = _emb([(r[1] or "") + "。" + (r[2] or "")[:400] for r in rows])
        def _cos(a, b): return sum(x*y for x, y in zip(a, b))
        for i in range(len(rows)):
            for j in range(i+1, len(rows)):
                s = _cos(vecs[i], vecs[j])
                if s >= th:
                    pairs.append((round(s, 3), rows[i], rows[j]))
        pairs.sort(key=lambda x: -x[0])
    except Exception as e:
        return [{"ok": False, "err": "embed 不可用: %s" % str(e)[:120]}]
    import concurrent.futures as _cf

    def _judge(pair):
        # 每对独立判断，4 路并发：串行 4 对要 2 分钟，并发约 30 秒
        sm, A, B = pair
        prompt = PROMPT_MERGE % {
            "ta": A[1], "ba": (A[2] or "")[:600], "ea": A[3], "sa": A[4],
            "tb": B[1], "bb": (B[2] or "")[:600], "eb": B[3], "sb": B[4]}
        try:
            r = parse(call(prompt, cfg=cfg))
            one = r[0] if isinstance(r, list) and r else (r if isinstance(r, dict) else {})
            return (sm, A, B, one, None)
        except Exception as e:
            return (sm, A, B, {}, str(e)[:120])

    out = []
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        judged = list(ex.map(_judge, pairs[:6]))
    for sm, A, B, one, err in judged:
        s = sm
        if err:
            out.append({"sim": s, "a": A[1], "b": B[1], "ok": False, "err": err}); continue
        rec = {"sim": s, "a": A[1], "b": B[1], "same": bool(one.get("same"))}
        if one.get("same") and one.get("title"):
            rec["title"] = one["title"]
            ev = []
            for x in (A[3], B[3]):
                for t in re.findall(r"\d+", x or ""):
                    if t not in ev: ev.append(t)
            rec["evidence"] = ",".join(ev[:16])
            rec["span"] = (one.get("span") or "").strip() or (A[4] if A[4] == B[4] else "%s / %s" % (A[4], B[4]))
            if apply:
                slug = "mrg-" + hashlib.md5(one["title"].encode()).hexdigest()[:10]
                c.execute(
                    "INSERT OR IGNORE INTO dreams(slug,at,title,body,evidence,span,confidence,status,model,note,kind)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (slug, time.strftime("%Y-%m-%d %H:%M:%S"), one["title"], one.get("body") or "",
                     rec["evidence"], rec["span"], 0.5, "draft", cfg.get("dream.model") or MODEL,
                     "合并 %s+%s" % (A[0], B[0]), kind))
                for old in (A[0], B[0]):
                    c.execute("UPDATE dreams SET status='merged', consolidate_of=? WHERE id=?",
                              (c.execute("SELECT id FROM dreams WHERE slug=?", (slug,)).fetchone()[0], old))
                c.commit()
                rec["merged_id"] = c.execute("SELECT id FROM dreams WHERE slug=?", (slug,)).fetchone()[0]
        out.append(rec)
    return out


if __name__ == "__main__":
    import sys
    do = "--apply" in sys.argv
    if "--merge" in sys.argv:
        c = con(); st = get_settings(c)
        k = "machine" if "--machine" in sys.argv else "human"
        r = {"ok": True, "merge": merge_pairs(c, st, kind=k, apply=do)}
    elif "--machine" in sys.argv:
        r = run_machine(apply=do)
    else:
        r = run(apply=do, manual=False)
    if not r.get("dreams"):
        print(json.dumps({k: v for k, v in r.items() if k != "dreams"}, ensure_ascii=False))
    else:
        print(json.dumps({k: v for k, v in r.items() if k != "dreams"}, ensure_ascii=False))
        for d in r["dreams"]:
            print("\n── " + d["title"])
            print(d["body"])
            print("   跨度 %s ｜ 证据 %s" % (d["span"], d["evidence"]))
            print("   理由 " + d["why"])
