"""月 → 季 → 年 → 总纲。只读月记，不重读原文（C1 单向流）。"""
import json, os, re, sqlite3, sys, time
from collections import OrderedDict

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

DS_BASE = os.environ.get("MEMVAULT_LLM_BASE", "https://api.deepseek.com/v1")
DS_KEY = os.environ.get("MEMVAULT_LLM_KEY", "")
DS_MODEL = os.environ.get("MEMVAULT_LLM_MODEL", "deepseek-chat")
DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")

Q_PROMPT = """下面是一个季度的逐月记事。请把它们压成一条季度纪录。

要求：
1. 300-500 字。按时间顺序写：这个季度一开始在忙什么、中途转向了什么、结束时停在什么状态。
2. 必须保留具体锚点：型号、端口、IP、文件名、版本号、金额、软件名。没有锚点的概括一律不要。
3. 写"他在做什么"，不写"这段时期很重要"这类空话。禁止抽象化。
4. 同一个东西反复折腾的，写成一条演进（"先 X，失败，改 Y"），不要分三条。
5. 只输出 JSON：{"title": "10字以内的季度标题", "summary": "...", "threads": [{"name": "线索名", "state": "进行中|完成|搁置", "last": "最后停在哪"}], "entities": ["出现的关键名词"]}

{months}"""

Y_PROMPT = """下面是逐季度的纪要。请压成一条年度纪录。

要求：
1. 600-900 字。写这一年在折腾什么、主线是什么、哪些事收尾了、哪些还挂着。
2. 保留具体锚点，禁止抽象化。
3. 只输出 JSON：{"title": "年度标题", "summary": "...", "threads": [...], "entities": [...]}

{quarters}"""

CORE_PROMPT = """下面是逐年的纪要。请压成一份「我自己」的当前状态说明。

要求：
1. 800-1200 字。这是给未来的我看的开场：我是谁、在做什么方向的事、手上有什么设备/服务、关心什么问题、什么在推进什么卡住了。
2. 具体，不写性格形容。可以写"他倾向于先动手再理解"这类，但必须紧跟一条证据（哪件事体现的）。
3. 只输出 JSON：{"title": "当前状态", "summary": "...", "threads": [...], "entities": [...]}

{years}"""


def _llm(prompt, max_tokens=2000):
    body = json.dumps({
        "model": DS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        **_effort_kw("summary"),
    }).encode()
    req = urllib.request.Request(
        DS_BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + DS_KEY})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=240) as r:
                _d = json.loads(r.read())["choices"][0]["message"]
            raw = (_d.get("content") or "").strip()
            if not raw:
                raw = (_d.get("reasoning_content") or "").strip()
            try:
                return json.loads(raw)
            except Exception:
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    return json.loads(m.group(0))
                raise
        except Exception as e:
            print("  retry", i, e, flush=True)
            time.sleep(2 * (i + 1))
    return None


def q_of(ym):
    y, m = ym.split("-")
    return f"{y}-Q{(int(m) - 1) // 3 + 1}"


def q_range(q):
    y, qq = q.split("-Q")
    m0 = (int(qq) - 1) * 3 + 1
    return f"{y}-{m0:02d}", f"{y}-{m0 + 3:02d}"


def build(con, level):
    cur = con.cursor()
    if level == "quarter":
        rows = cur.execute(
            "SELECT period_id, start_ts, end_ts, msg_count, title, summary "
            "FROM chronicles WHERE granularity='month' ORDER BY start_ts").fetchall()
        groups = OrderedDict()
        for r in rows:
            groups.setdefault(q_of(r[1][:7]), []).append(r)
        for q, ms in groups.items():
            pid = q
            if cur.execute("SELECT 1 FROM chronicles WHERE period_id=?", (pid,)).fetchone():
                print(f"  {pid} exists, skip"); continue
            text = "\n\n".join(f"【{m[4]}】{m[5]}" for m in ms)
            data = _llm(Q_PROMPT.replace("{months}", text), 3000)
            if not data: print(f"  {pid} FAILED"); continue
            total = sum(m[3] for m in ms)
            cur.execute(
                "INSERT OR REPLACE INTO chronicles(period_id,granularity,start_ts,end_ts,msg_count,title,summary,threads,entities,turns,carries_over,built_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, "quarter", ms[0][1], ms[-1][2], total, data.get("title", ""),
                 data.get("summary", ""), json.dumps(data.get("threads", []), ensure_ascii=False),
                 json.dumps(data.get("entities", []), ensure_ascii=False), "[]", "", time.strftime("%F %T")))
            con.commit()
            print(f"  -> {data.get('title','')}")
    elif level == "year":
        rows = cur.execute(
            "SELECT period_id, start_ts, end_ts, msg_count, title, summary "
            "FROM chronicles WHERE granularity='quarter' ORDER BY start_ts").fetchall()
        groups = OrderedDict()
        for r in rows:
            groups.setdefault(r[1][:4], []).append(r)
        for y, qs in groups.items():
            if cur.execute("SELECT 1 FROM chronicles WHERE period_id=?", (y,)).fetchone():
                print(f"  {y} exists, skip"); continue
            text = "\n\n".join(f"【{q[4]}】{q[5]}" for q in qs)
            data = _llm(Y_PROMPT.replace("{quarters}", text), 8000)
            if not data: print(f"  {y} FAILED"); continue
            total = sum(q[3] for q in qs)
            cur.execute(
                "INSERT OR REPLACE INTO chronicles(period_id,granularity,start_ts,end_ts,msg_count,title,summary,threads,entities,turns,carries_over,built_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (y, "year", qs[0][1], qs[-1][2], total, data.get("title", ""),
                 data.get("summary", ""), json.dumps(data.get("threads", []), ensure_ascii=False),
                 json.dumps(data.get("entities", []), ensure_ascii=False), "[]", "", time.strftime("%F %T")))
            con.commit()
            print(f"  -> {data.get('title','')}")
    else:  # core
        if cur.execute("SELECT 1 FROM chronicles WHERE period_id='core'").fetchone():
            print("  core exists, skip"); return
        rows = cur.execute(
            "SELECT period_id, start_ts, end_ts, msg_count, title, summary "
            "FROM chronicles WHERE granularity='year' ORDER BY start_ts").fetchall()
        text = "\n\n".join(f"【{r[4]}】{r[5]}" for r in rows)
        data = _llm(CORE_PROMPT.replace("{years}", text), 12000)
        if not data: print("  core FAILED"); return
        cur.execute(
            "INSERT OR REPLACE INTO chronicles(period_id,granularity,start_ts,end_ts,msg_count,title,summary,threads,entities,turns,carries_over,built_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("core", "core", rows[0][1], rows[-1][2], sum(r[3] for r in rows), data.get("title", ""),
             data.get("summary", ""), json.dumps(data.get("threads", []), ensure_ascii=False),
             json.dumps(data.get("entities", []), ensure_ascii=False), "[]", "", time.strftime("%F %T")))
        con.commit()
        print(f"  -> {data.get('title','')}")


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    levels = sys.argv[1:] or ["quarter", "year", "core"]
    for lv in levels:
        print(f"[{lv}]", flush=True)
        build(con, lv)


if __name__ == "__main__":
    main()
