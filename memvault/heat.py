"""记忆温度：半遗忘。

温度 = 存入时的热度 heat（0..1）按半衰期指数衰减。
被检索命中 -> 升温；长期不碰 -> 自然冷却。
冷到 drowsy 以下就不再主动注入，但仍可被精确检索命中（不是删除）。

所有参数都走 settings 表，可在页面上改。
"""
import math
import sqlite3
import time
from datetime import datetime, timezone, timedelta

_TZ = timezone(timedelta(hours=8))

DEFAULTS = {
    "memory.half_life_days": 7.0,      # 半衰期（天）
    "memory.heat_boost": 0.25,         # 每次命中升温幅度
    "memory.heat_cap": 1.0,            # 温度上限
    "memory.drowsy": 0.15,             # 低于此值 = 困了（不再主动注入）
    "memory.cold": 0.04,               # 低于此值 = 冷了（只在精确命中时出来）
    "memory.cold_penalty": 0.55,       # 检索时的冷卡折扣（1.0 = 不折扣）
    "memory.enabled": 1,               # 总开关
    "memory.pinned_half_life_days": 30.0,  # 刻骨铭心：置顶/核心卡的半衰期
    "memory.soften_enabled": 0,        # 自动软化开关
    "memory.soften_min_age_days": 90,  # 软化最小年龄（天）
    "memory.soften_budget": 90,
    "memory.soften_daily_cap": 20,      # 每天软化上限（0=不限）
}


def _num(v, d):
    try:
        return float(v)
    except Exception:
        return float(d)


def params(con):
    p = dict(DEFAULTS)
    try:
        for k, v in con.execute(
            "SELECT k,v FROM settings WHERE k LIKE 'memory.%'"
        ).fetchall():
            if k in p:
                cur = p[k]
                p[k] = v if isinstance(cur, str) else _num(v, cur)
    except Exception:
        pass
    return p


def now_str():
    return datetime.now(_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _parse(ts):
    if not ts:
        return None
    s = str(ts).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=_TZ)
        except Exception:
            continue
    return None


def half_life_for(p, pinned=0, heat_stored=0.0):
    """重要记忆衰退得慢：pinned 或起手就极热的卡，用更长的半衰期。"""
    base = _num(p.get("memory.half_life_days"), 3.0)
    long_hl = _num(p.get("memory.pinned_half_life_days"), 30.0)
    try:
        if int(pinned or 0) == 1:
            return long_hl
    except Exception:
        pass
    return base


def live_heat(stored, last_hit_at, half_life_days):
    """已存热度按半衰期衰减到今天。"""
    h = _num(stored, 0.0)
    if h <= 0:
        return 0.0
    t = _parse(last_hit_at)
    if not t:
        return h
    days = max(0.0, (datetime.now(_TZ) - t).total_seconds() / 86400.0)
    hl = max(0.05, _num(half_life_days, 3.0))
    return h * (0.5 ** (days / hl))


def band(live, p, last_hit_at=None):
    """温度分档：hot / warm / drowsy / cold / dead / never"""
    if (not last_hit_at) and live <= 0.001:
        return "never"
    if live >= 0.60:
        return "hot"
    if live >= p["memory.drowsy"]:
        return "warm"
    if live >= p["memory.cold"]:
        return "drowsy"
    if live > 0.001:
        return "cold"
    return "dead"


def bump(con, card_ids, p=None):
    """命中升温：先按半衰期把旧热度衰减到此刻，再加 boost，封顶。"""
    if not card_ids:
        return 0
    p = p or params(con)
    boosts = _num(p.get("memory.heat_boost"), 0.25)
    cap = _num(p.get("memory.heat_cap"), 1.0)
    hl = _num(p.get("memory.half_life_days"), 3.0)
    ts = now_str()
    n = 0
    for cid in card_ids:
        row = con.execute("SELECT heat,last_hit_at FROM cards WHERE id=?", (cid,)).fetchone()
        if not row:
            continue
        cur = live_heat(row[0], row[1], hl)
        new = min(cap, cur + boosts)
        con.execute("UPDATE cards SET heat=?, last_hit_at=? WHERE id=?", (new, ts, cid))
        n += 1
    con.commit()
    return n


def backfill(con, p=None):
    """从 card_recall 反推初始热度：命中次数越多、问法越杂，起手越热。"""
    p = p or params(con)
    hl = _num(p.get("memory.half_life_days"), 3.0)
    cap = _num(p.get("memory.heat_cap"), 1.0)
    rows = con.execute(
        "SELECT card_id, COUNT(*) n, COUNT(DISTINCT query_hash) uq, MAX(at) last_at"
        " FROM card_recall GROUP BY card_id"
    ).fetchall()
    n_set = 0
    for cid, cnt, uq, last_at in rows:
        seed = 0.18 * math.log1p(cnt) + 0.22 * math.log1p(uq)
        seed = min(cap, seed)
        live = live_heat(seed, last_at, hl)
        con.execute(
            "UPDATE cards SET heat=?, last_hit_at=? WHERE id=? AND (heat IS NULL OR heat=0)",
            (live, last_at, cid),
        )
        n_set += 1
    con.commit()
    return n_set


def stats(con, p=None, sample=2000):
    """温度分布（抽样，避免 1.2 万行每次都全扫）。"""
    p = p or params(con)
    hl = _num(p.get("memory.half_life_days"), 3.0)
    rows = con.execute(
        "SELECT id,title,heat,last_hit_at,occurred_at,kind,category,pinned"
        " FROM cards WHERE status='active' LIMIT ?", (sample,)
    ).fetchall()
    buckets = {"hot": 0, "warm": 0, "drowsy": 0, "cold": 0, "dead": 0, "never": 0}
    tot = 0.0
    hot_list = []
    cold_list = []
    for cid, title, h, lh, occ, kind, cat, pin in rows:
        live = live_heat(h, lh, half_life_for(p, pin, h))
        b = band(live, p, lh)
        buckets[b] = buckets.get(b, 0) + 1
        tot += live
        item = {"id": cid, "title": (title or "")[:60], "heat": round(live, 3),
                "band": b, "last_hit_at": lh or "", "kind": kind or "",
                "category": cat or "", "pinned": int(pin or 0)}
        if b in ("hot", "warm"):
            hot_list.append(item)
        elif b in ("cold", "dead"):
            cold_list.append(item)
    hot_list.sort(key=lambda x: -x["heat"])
    cold_list.sort(key=lambda x: x["heat"])
    n = len(rows) or 1
    return {
        "sample": len(rows),
        "buckets": buckets,
        "avg_heat": round(tot / n, 4),
        "params": {k: v for k, v in p.items()},
        "hot": hot_list[:20],
        "cold": cold_list[:20],
    }
