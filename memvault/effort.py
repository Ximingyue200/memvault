"""后台任务的推理等级（reasoning_effort）解析。

三个旋钮，都在 settings 表里（面板「记忆库参数」可改）：
    llm.reasoning_effort      全局默认；留空 = 跟随上游模型自身默认
    dream.reasoning_effort    做梦（dream / dreaming）
    learn.reasoning_effort    卡片提炼（extract / categorize / supersede / statefix）
    llm.summary.reasoning_effort  摘要类（chronicle / rollup / selfnote / soften）

取值 none | minimal | low | medium | high | max；也接受 off（自动换成 none）。
留空串表示「不传这个字段」，让上游按模型默认跑。
"""
import os
import sqlite3
import time

DB = os.environ.get("MEMVAULT_DB") or "/opt/memvault/data/memvault.db"

# 任务 -> 设置键
TASK_KEYS = {
    "dream": "dream.reasoning_effort",
    "learn": "learn.reasoning_effort",
    "summary": "llm.summary.reasoning_effort",
}
GLOBAL_KEY = "llm.reasoning_effort"

# 没有配任何键时的建议默认（None = 不传，跟随上游）
DEFAULTS = {
    "dream": "high",     # 联想值得多想
    "learn": "low",      # 结构化抽取，脑子不用太用力
    "summary": "low",
}

ALIAS = {"off": "none", "disabled": "none", "false": "none", "0": "none",
         "on": "high", "true": "high", "1": "high", "auto": "medium", "default": "high"}
VALID = ("none", "minimal", "low", "medium", "high", "max")

_cache = {"at": 0.0, "v": {}}


def _settings():
    now = time.time()
    if _cache["v"] and now - _cache["at"] < 60:
        return _cache["v"]
    d = {}
    try:
        con = sqlite3.connect(DB, timeout=3)
        try:
            for k, v in con.execute(
                "select k, v from settings where k in (?,?,?,?)",
                (GLOBAL_KEY, TASK_KEYS["dream"], TASK_KEYS["learn"], TASK_KEYS["summary"]),
            ):
                d[k] = (v or "").strip()
        finally:
            con.close()
    except Exception:
        pass
    _cache.update(at=now, v=d)
    return d


def get(task=None):
    d = _settings()
    for key in ((TASK_KEYS.get(task) if task else None), GLOBAL_KEY):
        if key and d.get(key):
            return _normalize(d[key])
    return _normalize(DEFAULTS.get(task) or "")


def _normalize(v):
    v = (v or "").strip().lower()
    if not v:
        return ""
    v = ALIAS.get(v, v)
    return v if v in VALID else ""


def kwargs(task=None):
    """给 payload 用的 kwargs：{'reasoning_effort': 'high'} 或 {}"""
    e = get(task)
    return {"reasoning_effort": e} if e else {}


def put(payload, task=None):
    """就地写入 payload 并返回它（拿不到值就不动）。"""
    e = get(task)
    if e and isinstance(payload, dict) and not payload.get("reasoning_effort"):
        payload["reasoning_effort"] = e
    return payload


def summary():
    return {t: get(t) for t in ("dream", "learn", "summary")} | {"global": get(None)}
