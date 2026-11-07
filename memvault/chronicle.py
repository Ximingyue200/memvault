"""编年史生成 —— 按时间窗口读原文，一段一段分析。

跟卡片抽取的区别：
  卡片 = 按主题切，回答「X 是什么」
  编年史 = 按时间切，回答「那段时间在干什么」

两级压缩：
  窗口内分块（每块 LLM 一次，出 100 字）
  → 合并成月记（LLM 一次，出 300 字 + threads）

输入带上一期的 threads，所以能写出「还在做 / 停了 / 又捡起来」。
"""
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict

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

CHUNK_MSGS = 45
MAX_CHARS_PER_MSG = 320

BLOCK_PROMPT = """你在读一个人 18 个月聊天记录的一部分。下面是按时间排列的消息，编号 [n]。

请写一段 80-140 字的中文，说明**这段时间他在做什么**。要求：
1 只说这段里能看出来的一件事或一条线，不要罗列
2 必须带上具体锚点（软件名、机器型号、端口号、报错原文、数字）
3 禁止抽象化：写「把 8006 改成 80 因为嫌输端口麻烦」，不要写「简化了配置」
4 如果这段没有实质内容（寒暄、确认、闲聊），输出 NOISE
5 不要推断动机，只写看到了什么

只输出那段文字，不要 JSON，不要标题。"""

WINDOW_PROMPT = """下面是同一个月里若干段时间的观察记录（按时间顺序）。请综合成一条月记。

输出 JSON：
{
  "title": "10字内，这个月在做什么",
  "summary": "200-350字，按时间顺序写这一个月。必须保留具体锚点（型号/端口/文件名/数字）。写清楚：开始时在做什么，中间发生了什么转折，结束时停在什么状态。禁止抽象化。",
  "threads": [{"name":"在做的事","state":"进行中|停滞|完成|转向","note":"一句话"}],
  "entities": ["这个月反复出现的实体"],
  "turns": [{"when":"大概日期","what":"什么让它转向了"}],
  "carries_over": ["上个月遗留、这个月还在处理的"]
}

只输出 JSON。"""


def _llm(prompt, max_tokens=1200, json_mode=False):
    body = {
        "model": DS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
        **_effort_kw("summary"),
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        DS_BASE.rstrip("/") + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {DS_KEY}"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            return d["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))


def _msg_text(row):
    t = (row["content"] or "").strip()
    t = re.sub(r"<[^>]{0,40}>.*?</[^>]{0,40}>", " ", t, flags=re.S)
    t = re.sub(r"\s+", " ", t)
    return t[:MAX_CHARS_PER_MSG]


def load_window(con, start, end):
    rows = con.execute(
        """SELECT m.seq, m.ts, m.role, m.content
           FROM messages m
           WHERE m.ts >= ? AND m.ts < ?
             AND m.role IN ('user','assistant')
             AND length(m.content) > 20
           ORDER BY m.ts, m.seq""",
        (start, end),
    ).fetchall()
    return rows


def analyze_window(rows, prev_threads=None):
    """分块 -> 块摘要 -> 月记。返回 dict 或 None（全噪音）。"""
    if not rows:
        return None
    blocks = []
    for i in range(0, len(rows), CHUNK_MSGS):
        part = rows[i:i + CHUNK_MSGS]
        lines = []
        for n, r in enumerate(part, 1):
            who = "他" if r["role"] == "user" else "AI"
            lines.append(f"[{n}] ({r['ts'][:16]}) {who}: {_msg_text(r)}")
        try:
            out = _llm(BLOCK_PROMPT + "\n\n" + "\n".join(lines), max_tokens=400).strip()
        except Exception as e:
            out = f"(块失败 {type(e).__name__})"
        if out and out.upper() != "NOISE" and not out.startswith("(块失败"):
            blocks.append(f"[{part[0]['ts'][:10]}~{part[-1]['ts'][:10]}] {out}")
    if not blocks:
        return None

    ctx = ""
    if prev_threads:
        ctx = "上一期还在做的事：" + json.dumps(prev_threads, ensure_ascii=False)[:600] + "\n\n"
    try:
        raw = _llm(ctx + WINDOW_PROMPT + "\n\n" + "\n\n".join(blocks[:40]),
                   max_tokens=1500, json_mode=True)
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


def periods(con):
    """按月分窗；单月 > 3000 条则按半月切。"""
    rows = con.execute(
        """SELECT substr(ts,1,7) ym, COUNT(*) c FROM messages
           WHERE ts IS NOT NULL AND role IN ('user','assistant') AND length(content)>20
           GROUP BY ym ORDER BY ym"""
    ).fetchall()
    out = []
    for r in rows:
        ym, c = r["ym"], r["c"]
        if c <= 3000:
            out.append((ym, ym + "-01", None))
        else:
            out.append((ym + "A", ym + "-01", ym + "-16"))
            out.append((ym + "B", ym + "-16", None))
    return out


def next_period_start(ym, start):
    """粗略推下一个月起点。"""
    y, m = int(start[:4]), int(start[5:7])
    m += 1
    if m > 12:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}-01"


def run(db_path, out_path=None, only=None, limit=None):
    """读 cradle（不可变），写 memvault（C1 单向流）。"""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sys.path.insert(0, "/opt/memvault")
    from memvault import store as _store
    wcon = _store.connect(out_path or "/opt/memvault/data/memvault.db")
    todo = periods(con)
    if only:
        todo = [p for p in todo if p[0].startswith(only)]
    if limit:
        todo = todo[:limit]

    prev_threads = None
    n_ok = 0
    for pid, start, boundary in todo:
        done = wcon.execute("SELECT 1 FROM chronicles WHERE period_id=?", (pid,)).fetchone()
        if done:
            print(f"skip {pid}")
            r = wcon.execute("SELECT threads FROM chronicles WHERE period_id=?", (pid,)).fetchone()
            prev_threads = json.loads(r["threads"]) if r and r["threads"] else None
            continue

        if boundary:
            end = boundary
        else:
            end = next_period_start(pid[:7], start)
        rows = load_window(con, start, end)
        if not rows:
            continue
        print(f"[{pid}] {len(rows)} msgs  {start} -> {end}", flush=True)
        t0 = time.time()
        res = analyze_window(rows, prev_threads)
        if not res:
            print(f"  -> no result", flush=True)
            continue
        wcon.execute(
            """INSERT OR REPLACE INTO chronicles
               (period_id,granularity,start_ts,end_ts,msg_count,title,summary,
                threads,entities,turns,carries_over,built_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                pid, "month", start, end, len(rows),
                res.get("title", ""), res.get("summary", ""),
                json.dumps(res.get("threads", []), ensure_ascii=False),
                json.dumps(res.get("entities", []), ensure_ascii=False),
                json.dumps(res.get("turns", []), ensure_ascii=False),
                json.dumps(res.get("carries_over", []), ensure_ascii=False),
                time.strftime("%Y-%m-%dT%H:%M:%S"),
            ),
        )
        wcon.commit()
        n_ok += 1
        print(f"  -> {res.get('title','')}  ({time.time()-t0:.0f}s)", flush=True)
        prev_threads = res.get("threads")
    print(f"done, built {n_ok}")
    return n_ok


if __name__ == "__main__":
    db = os.environ.get("CRADLE_DB", "/opt/cradle/data/cradle.db")
    out = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    run(db, out, only, lim)
