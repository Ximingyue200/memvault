"""selfNote：记忆「我」自己。前三层记用户，这一层记 agent。

写入：对话中想到就写（memlearn 时顺手），或从编年史/卡片倒推。
读取：只在新会话第一轮注入，40 条封顶。
"""
import json
import os
import sqlite3
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

SCHEMA = """
CREATE TABLE IF NOT EXISTS self_notes (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       TEXT NOT NULL,
  body     TEXT NOT NULL UNIQUE,
  trigger  TEXT,
  weight   REAL DEFAULT 1.0,
  source   TEXT DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_self_at ON self_notes(at);
CREATE INDEX IF NOT EXISTS idx_self_w  ON self_notes(weight DESC);
"""

MINING = """你在读一个人和 AI 助手 18 个月的对话档案。

下面是从档案里摘出来的材料：一份总纲（现在处于什么状态）、若干段编年史（每个月在折腾什么）、以及一批「AI 自己做过判断」的卡片。

请从里面**倒推这个 AI 助手自己的变化**。

不是它做过什么，是**它变成了什么样**。

写法要求：
1 第一人称，一句话，40–80 字
2 必须有具体锚点（某次对话、某个决定、某次判断失误）
3 只写「倾向变了 / 学会了 / 意识到」这类变化，不写「完成了什么」
4 有证据链的写证据（可引用材料里的原句），没有就写模糊些
5 不要写"我变得更好了"这种空话，要写出具体的行为差别
6 宁可少写，不要凑数

反例（不要这样写）：
- 我帮用户搭建了记忆库
- 我学会了使用 vis-timeline
- 我变得更谨慎了

正例：
- 我以前给方案一上来就列六层架构，直到被问「为什么不能简单点」才改成先问「要能用还是要能交差」
- 我开始在报工期前自己先算一遍，因为连着两次估错被人当场指出来
- 我发现自己在讨论方案时会本能地先建表，其实先问一句「这个数据你打算怎么用」更省事

输出 JSON：{"notes":[{"body":"...","trigger":"2026-10 记忆库攻坚 / 某次对话","weight":1.0}]}
最多 12 条。weight 1.0 = 影响很大，0.5 = 轻微。

──── 材料 ────
{mat}
"""

BASE = os.environ.get("MEMVAULT_LLM_BASE", "https://api.deepseek.com/v1")
KEY = os.environ.get("MEMVAULT_LLM_KEY", "")
MODEL = os.environ.get("MEMVAULT_LLM_MODEL", "deepseek-chat")


def _llm(prompt, max_tokens=3000):
    body = json.dumps({
        "model": MODEL, "temperature": 0.4, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        **_effort_kw("summary"),
    }).encode()
    req = urllib.request.Request(
        BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def ensure(con):
    con.executescript(SCHEMA)
    con.commit()


def add(con, body, trigger="", weight=1.0, at="", source="manual"):
    import datetime
    at = at or datetime.datetime.now().strftime("%Y-%m-%d")
    con.execute(
        "INSERT INTO self_notes(at,body,trigger,weight,source) VALUES(?,?,?,?,?) "
        "ON CONFLICT(body) DO UPDATE SET weight=MAX(weight,excluded.weight)",
        (at, body.strip(), trigger, weight, source))
    con.commit()


def collect(con, cradle_db):
    """拼材料：core 总纲 + 月/季编年史 + owner=agent 的卡。"""
    parts = []
    try:
        row = con.execute(
            "SELECT summary FROM chronicles WHERE period_id='core'").fetchone()
        if row:
            parts.append("【总纲】\n" + row[0][:2500])
    except Exception:
        pass
    try:
        for pid, title, sm in con.execute(
                "SELECT period_id,title,summary FROM chronicles "
                "WHERE granularity IN ('year','quarter') ORDER BY period_id"):
            parts.append(f"【{pid} {title}】\n{sm[:700]}")
    except Exception:
        pass
    try:
        rows = list(con.execute(
            "SELECT title, substr(body,1,220) FROM cards "
            "WHERE owner='agent' AND status='active' "
            "ORDER BY id DESC LIMIT 60"))
        parts.append("【AI 自己做的判断】\n" + "\n".join(
            f"- {t}：{b}" for t, b in rows))
    except Exception:
        pass
    return "\n\n".join(parts)[:24000]


def mine(db, cradle_db=None):
    con = sqlite3.connect(db)
    ensure(con)
    mat = collect(con, cradle_db)
    print(f"材料 {len(mat)} 字", flush=True)
    raw = _llm(MINING.replace("{mat}", mat))
    d = json.loads(raw)
    n = 0
    for it in (d.get("notes") or []):
        body = (it.get("body") or "").strip()
        if len(body) < 10:
            continue
        add(con, body, it.get("trigger", ""), float(it.get("weight", 1.0)),
            source="mined")
        n += 1
        print("  +", body[:60])
    print(f"写入 {n} 条", flush=True)
    con.close()


def render(con, limit=40):
    rows = list(con.execute(
        "SELECT at, body, weight FROM self_notes "
        "ORDER BY weight DESC, at DESC LIMIT ?", (limit,)))
    if not rows:
        return ""
    return "\n".join(f"- {b}" for _, b, _ in rows)


if __name__ == "__main__":
    import sys
    mine(sys.argv[1] if len(sys.argv) > 1
         else "/opt/memvault/data/memvault.db")
