"""自动软化：冷掉的记忆细节褪色，要点保留。

温度降到冷阈值以下的卡 -> 正文压缩成一句话（存 body_orig 留原文），
证据链（card_evidence / spans）完全不动 —— 检索出来是一句模糊的回忆，
点开仍能看到当时原话。

用法：
  python3 -m memvault.soften --limit 20 --dry     # 先看会改什么
  python3 -m memvault.soften --limit 20           # 真改
  python3 -m memvault.soften --restore <id>       # 还原某一张
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

try:  # token 记账：拦 urlopen，把 usage 落到 /opt/memvault/data/llm_usage.jsonl
    from . import _meter  # noqa: F401
except Exception:
    pass

from datetime import datetime, timezone, timedelta

from . import heat

try:
    from .effort import kwargs as _effort_kw
except Exception:  # 直接当脚本跑时没有包上下文
    def _effort_kw(task=None):
        return {}

_TZ = timezone(timedelta(hours=8))
DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")

API = os.environ.get("SOFTEN_API", "https://api.deepseek.com")
KEY = os.environ.get("SOFTEN_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
MODEL = os.environ.get("SOFTEN_MODEL", "deepseek-flash")

PROMPT = """把下面这段记忆正文压缩成一句话（不超过 40 字），只保留最核心的事实或结论。

要求：
- 只输出那一句话本身，不要解释、不要前缀、不要引号
- 保留具体的值（型号、端口、版本号、路径），不要笼统化
- 如果原文本身就是一句话，原样返回

正文：
%s"""


_GEN_WORDS = (
    "关系", "平台", "规格", "清单", "内容", "要点", "情况", "方法", "方式", "问题",
    "分析", "总结", "记录", "介绍", "说明", "配置", "设置", "使用", "支持", "对比",
    "差异", "区别", "现状", "方案", "步骤", "流程", "结构", "原理", "定义", "概念",
)


def _title_keys(title):
    """从标题里抽「必须出现在结果里」的关键词：型号、专有缩写、核心名词。"""
    import re as _re
    t = title or ""
    keys = []
    # 1) ASCII 型号/缩写：SMR / PMR / XG76 / 7840HS / PCIe / eGPU / USB4
    for m in _re.findall(r"[A-Za-z][A-Za-z0-9._\-]{1,}", t):
        if m.lower() not in ("the", "and", "for", "of", "to"):
            keys.append(m)
    # 2) 数字型号：780M / 7600 / XXXX
    for m in _re.findall(r"\d{3,4}[A-Za-z]{0,2}", t):
        if m not in keys:
            keys.append(m)
    # 只校验 ASCII 型号 / 纯数字型号 —— 中文名丢了顶多是缩略，不算压歪
    return [k for k in keys if _re.search(r"[A-Za-z0-9]", k)]


def _split_arrow(txt):
    """超长句内部按「→」拆步骤。"""
    bits, cur = [], ""
    for ch in txt:
        cur += ch
        if ch == "→":
            bits.append(cur)
            cur = ""
    if cur.strip():
        bits.append(cur)
    return bits if len(bits) >= 2 else []


def _split_parallel(txt):
    """括号外按「；」拆并列项。"""
    parts, cur, depth = [], "", 0
    for ch in txt:
        cur += ch
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif ch in "；;" and depth == 0:
            parts.append(cur[:-1])
            cur = ""
    if cur.strip():
        parts.append(cur)
    return [x.strip() for x in parts if x.strip()]


def _clip_parallel(s, n, first=False):
    """裁到 n 字：括号内允许截断但补回 )，首项不切引导冒号。"""
    if len(s) <= n:
        return s
    depth, cut = 0, -1
    for i, ch in enumerate(s):
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif ch in "，、," and depth == 0 and i <= n:
            cut = i
        elif ch == "：" and depth == 0 and i <= n and not first:
            cut = i
    out = s[:cut] if cut > 0 else s[:n]
    out = out.rstrip("，、 ：")
    if out.count("（") > out.count("）"):
        out += "）"
    return out


def _heuristic(text):
    """不调模型：按分句贪心取，保证每句完整、能读懂。

    规则：
    - 先按句末标点切句（。！？；换行）
    - 贪心累加整句，直到接近 budget
    - 最后一句如果太长，退回到上一个完整分句（，、）而不是切字
    - 绝不出现半句（"ABB A…" / "隐私政策 h…"）
    """
    import re
    t = " ".join((text or "").split())
    if not t:
        return ""
    BUDGET = max(70, min(140, int(len(t) * 0.60)))

    def _split_sents(txt):
        """按句末标点切句，但括号内不切。"""
        out, cur, depth = [], "", 0
        for ch in txt:
            cur += ch
            if ch in "（(":
                depth += 1
            elif ch in "）)":
                depth = max(0, depth - 1)
            elif ch in "。！？!?；;" and depth == 0:
                out.append(cur)
                cur = ""
        if cur.strip():
            out.append(cur)
        return [x for x in out if x.strip()]

    # 并列型：≥3 项、或 2 项且都很短（真列表，不是「一句话；另一句话」）
    _par = _split_parallel(t)
    _is_list = len(_par) >= 3 or (len(_par) == 2 and all(len(x) <= 60 for x in _par))
    if _is_list:
        _per = max(26, BUDGET // len(_par))
        _bits = [_clip_parallel(x, _per, i == 0).rstrip("。；;，、 ") for i, x in enumerate(_par)]
        return "；".join(_bits) + "。"

    sents = _split_sents(t)
    if not sents:
        sents = [t]

    # 超长「一句到底」的操作路径：按 → 拆步骤，保证不会被整条丢弃
    if sents and len(sents[0]) > BUDGET:
        _acc = ""
        for _x in _split_arrow(sents[0]):
            if len((_acc + _x).rstrip()) > BUDGET:
                break
            _acc += _x
        if _acc.strip():
            return _acc.rstrip("→，、 ：") + "。"
    out = ""
    n_keep = 0
    min_sents = 1 if len(sents) <= 1 else min(2, len(sents))
    for s0 in sents:
        cand = out + s0
        if len(cand) > BUDGET and out and n_keep >= min_sents:
            break
        out = cand
        n_keep += 1
        if len(out) >= BUDGET * 0.85 and n_keep >= min_sents:
            break
    out = out.strip()
    # 仍然超预算（第一句就特别长）：退到最后一个「括号外」的完整分句
    if len(out) > BUDGET:
        parts, cur, depth = [], "", 0
        for ch in out:
            cur += ch
            if ch in "（(":
                depth += 1
            elif ch in "）)":
                depth = max(0, depth - 1)
            elif ch in "，、：," and depth == 0:
                parts.append(cur)
                cur = ""
        if cur.strip():
            parts.append(cur)
        acc = ""
        for pp in parts:
            if len(acc + pp) > BUDGET and acc:
                break
            acc += pp
        out = (acc or parts[0]).strip()
    # 去掉结尾的悬空标点/连接词
    out = re.sub(r"[，、：,;；]+$", "", out).strip()

    # 太短说明只留了个标题级空话 —— 至少补一个带具体值的分句
    if len(out) < 24:
        import re as _re
        # 原文里带数字/路径/命令的分句
        frags = _re.split(r"(?<=[。！？；;])", t)
        best = ""
        for fr in frags:
            if _re.search(r"[/\\:：]|\d|[A-Za-z]{3,}", fr) and len(fr) > len(best):
                best = fr
        if best and best not in out and out not in best:
            out = (out + ("；" if out and out[-1] not in "。！？" else "") + best).strip()
            if len(out) > BUDGET:
                out = out[:BUDGET].rsplit("，", 1)[0] if "，" in out[:BUDGET] else out[:BUDGET]
                out = out.rstrip("，、；")

    if out and out[-1] not in "。！？!?":
        out += "。"
    return out


def _llm(text, timeout=90):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT % text[:3000]}],
        "max_tokens": 3000,
        "temperature": 0.1,
        **_effort_kw("summary"),
    }).encode()
    req = urllib.request.Request(
        API.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    msg = d["choices"][0]["message"]
    out = (msg.get("content") or "").strip()
    # 不拿 reasoning_content 兜底 —— 那是模型的思考过程，不是答案
    return out.strip().strip('"').strip("「」").strip()


def candidates(con, p, limit=200):
    """冷透、还没软化过、正文够长的卡。"""
    hl_default = float(p.get("memory.half_life_days") or 3.0)
    # 两条路：
    #  1) 命中过但冷透了（cold / dead）
    #  2) 从没被检索过（never）且已经放了超过 --min-age 天
    #     后者是绝大多数（库里 92% 是 never），它们才是真正「没人碰」的老碎片。
    min_age = int(p.get("memory.soften_min_age_days") or 90)  # 保留：never 分支已停用
    rows = con.execute(
        "SELECT id,title,body,heat,last_hit_at,pinned,category,occurred_at,created_at "
        "FROM cards WHERE status='active' AND softened_at IS NULL "
        "AND body IS NOT NULL AND LENGTH(body) > 60 "
        # 只要「用过又忘了」的：last_hit_at 非空。否则 2900+ 张 never（heat=0）
        # 会按 heat ASC 永远排在前面，把真正冷掉的卡挤出 LIMIT 窗口。
        "AND last_hit_at IS NOT NULL "
        "ORDER BY heat ASC, last_hit_at ASC LIMIT ?",
        (min(limit * 8, 4000),),
    ).fetchall()
    # 只按温度系统筛：用过的、又忘掉的（drowsy / cold / dead）。
    # 「从没被检索过」不软化 —— 没被问过 ≠ 不重要，和检索时 never 不降权保持一致。
    out = []
    for r in rows:
        live = heat.live_heat(r["heat"], r["last_hit_at"],
                              heat.half_life_for(p, r["pinned"], r["heat"]))
        b = heat.band(live, p, r["last_hit_at"])
        if b in ("drowsy", "cold", "dead"):
            out.append((r, live, b))
        if len(out) >= limit:
            break
    return out[:limit]


def _cutoff(days):
    return (datetime.now(_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")


def softened_today(con):
    """今天（本地日期）已经软化了多少张。"""
    r = con.execute(
        # softened_at 存的是北京时间（_TZ=UTC+8），所以按 +8 小时取
        "SELECT COUNT(*) FROM cards WHERE date(softened_at) = date('now','+8 hours')"
    ).fetchone()
    return int(r[0] or 0)


def cap_left(con, p):
    """今天还剩多少额度；cap=0 表示不限，返回 None。"""
    cap = int(p.get("memory.soften_daily_cap", 30) or 0)
    if cap <= 0:
        return None
    return max(0, cap - softened_today(con))


def soften_one(con, card, live, band_name, dry=False, engine="heuristic"):
    orig = card["body"] or ""
    try:
        short = _heuristic(orig) if engine == "heuristic" else _llm(orig)
    except Exception as e:
        return {"id": card["id"], "ok": False, "err": str(e)[:120]}
    # 只匹配「模型在复述指令」的模式，不要匹配正文里正常的「输出」「字数」等词
    BAD = ("只输出那一句", "只输出结果", "不要输出", "直接输出结果", "保留一个具体路径",
           "输出结果，不要", "可输出：", "Count:", "计数：", "如果保留", "压缩到", "字数限制")
    if any(b in short for b in BAD):
        return {"id": card["id"], "ok": False, "err": "输出含指令残渣，跳过"}
    if short.endswith("？") or short.endswith("?"):
        return {"id": card["id"], "ok": False, "err": "输出是反问句，跳过"}
    if len(orig) <= 80:
        return {"id": card["id"], "ok": False, "err": "正文太短，不值得软化"}
    if not short or len(short) >= len(orig) * 0.8:
        return {"id": card["id"], "ok": False, "err": "压缩比不足，跳过"}
    if not dry:
        con.execute(
            "UPDATE cards SET body_orig=?, body=?, softened_at=? WHERE id=?",
            (orig, short, datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S"), card["id"]),
        )
        # FTS 同步（否则索引里留着软化前的词，搜得到看不着）
        try:
            _t = card["title"] or ""
            con.execute("INSERT INTO cards_fts(cards_fts, rowid, title, body) VALUES('delete', ?, ?, ?)",
                        (card["id"], _t, orig))
            con.execute("INSERT INTO cards_fts(rowid, title, body) VALUES(?, ?, ?)",
                        (card["id"], _t, short))
        except Exception:
            pass
        con.commit()
    # 标题对齐兜底：结果里缺了标题的关键词 → 判定压歪，回滚跳过
    keys = _title_keys(card["title"])
    missing = [k for k in keys if k not in short]
    if missing:
        if not dry:
            try:
                con.execute("UPDATE cards SET body_orig=NULL, body=?, softened_at=NULL WHERE id=?",
                            (orig, card["id"]))
                con.commit()          # ← 漏了这句，回滚没生效
            except Exception:
                pass
        return {"id": card["id"], "ok": False,
                "err": "压歪了：结果里缺标题关键词 %s" % ",".join(missing[:4])}

    return {
        "id": card["id"], "ok": True, "title": card["title"],
        "heat": round(live, 4), "band": band_name,
        "before": len(orig), "after": len(short),
        "short": short,
    }


def restore(con, cid):
    old_factory = con.row_factory
    try:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT body_orig FROM cards WHERE id=?", (cid,)).fetchone()
    finally:
        con.row_factory = old_factory
    if not r or not r["body_orig"]:
        return False
    r = dict(r)
    con.execute("UPDATE cards SET body=body_orig, body_orig=NULL, softened_at=NULL WHERE id=?", (cid,))
    con.commit()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--restore", type=int)
    ap.add_argument("--engine", choices=["heuristic", "llm"], default="heuristic")
    a = ap.parse_args()

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    p = heat.params(con)

    if a.restore:
        print("还原:", a.restore, restore(con, a.restore))
        return

    if a.engine == "llm" and not KEY:
        print("缺少 SOFTEN_KEY / DEEPSEEK_API_KEY", file=sys.stderr)
        return

    cands = candidates(con, p, limit=max(a.limit * 5, 100))[:a.limit]
    print(f"候选 {len(cands)} 张（冷透且未软化）", "· DRY" if a.dry else "")
    ok = 0
    for card, live, bnd in cands:
        r = soften_one(con, card, live, bnd, dry=a.dry, engine=a.engine)
        if r.get("ok"):
            ok += 1
            print(f"  [{bnd}] {r['heat']} {r['before']}→{r['after']} | {r['title'][:26]}")
            print(f"        → {r['short']}")
        else:
            print(f"  ✗ {card['id']} {r.get('err')}")
        time.sleep(0.3)
    print(f"完成：{ok}/{len(cands)}")


if __name__ == "__main__":
    main()
