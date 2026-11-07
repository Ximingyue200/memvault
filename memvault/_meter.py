"""Token 记账：拦 urllib.request.urlopen，把每次 LLM 调用的 usage 落一行账。

为什么用拦而不是改九个模块：
  各模块自己拼 urllib 请求、自己解析响应，形状各不相同；在 HTTP 层拦一次，
  谁调的、调了多少 token 都能拿到，模块一行都不用改（只加一句 import）。

账落在 /opt/memvault/data/llm_usage.jsonl，一行一次调用：
  {"ts","task","module","model","key","in","out","cached","cny","ms"}
"""
import json
import os
import sys
import time
import urllib.request

LOG = os.environ.get("MEMVAULT_USAGE_LOG", "/opt/memvault/data/llm_usage.jsonl")

# 模块 → 任务名（决定面板/日报里怎么归类）
TASK_OF = {
    "extract": "learn",
    "categorize": "learn",
    "statefix": "learn",
    "supersede": "learn",
    "dream": "dream",
    "chronicle": "summary",
    "rollup": "summary",
    "selfnote": "summary",
    "soften": "summary",
}

# 单价（元 / 1M tokens），可在 settings 里覆盖
DEF_PRICE = {"in": 2.0, "out": 3.0, "cached": 0.5}
_price_cache = {"at": 0.0, "v": dict(DEF_PRICE)}


def _prices():
    now = time.time()
    if now - _price_cache["at"] < 60:
        return _price_cache["v"]
    v = dict(DEF_PRICE)
    try:
        import sqlite3
        db = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
        con = sqlite3.connect(db, timeout=3)
        for k, name in (("llm.price_in", "in"), ("llm.price_out", "out"),
                        ("llm.price_cached", "cached")):
            row = con.execute("select v from settings where k=?", (k,)).fetchone()
            if row and str(row[0]).strip():
                try:
                    v[name] = float(row[0])
                except Exception:
                    pass
        con.close()
    except Exception:
        pass
    _price_cache.update(at=now, v=v)
    return v


def _task_of(module):
    short = module.split(".")[-1]
    if short in TASK_OF:
        return TASK_OF[short]
    return short


def _find_task():
    f = sys._getframe(2)
    while f is not None:
        m = f.f_globals.get("__name__") or ""
        if m.startswith("memvault.") and not m.endswith("_meter"):
            return _task_of(m), m
        f = f.f_back
    return "unknown", ""


def record(task, module, model, key, usage, ms=0):
    try:
        pin = int(usage.get("prompt_tokens") or 0)
        pout = int(usage.get("completion_tokens") or 0)
        cached = int(usage.get("prompt_cache_hit_tokens")
                     or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        p = _prices()
        cny = ((pin - cached) * p["in"] + cached * p["cached"] + pout * p["out"]) / 1e6
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "task": task, "module": module, "model": model or "",
            "key": (key or "")[-4:], "in": pin, "out": pout, "cached": cached,
            "cny": round(cny, 6), "ms": int(ms or 0),
        }
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:
        pass


class _Metered:
    """把 urlopen 的响应整个读下来，包一层给调用方用（这些调用都是非流式 JSON）。"""

    def __init__(self, resp, req, ms):
        self._resp = resp
        self._req = req
        self._ms = ms
        self.status = getattr(resp, "status", 200)
        self.headers = resp.headers
        self._body = resp.read()
        try:
            resp.close()
        except Exception:
            pass
        self._log()

    def _log(self):
        try:
            j = json.loads(self._body.decode("utf-8", "replace"))
        except Exception:
            return
        u = j.get("usage") if isinstance(j, dict) else None
        if not isinstance(u, dict):
            return
        model = ""
        key = ""
        try:
            body = json.loads((self._req.data or b"{}").decode("utf-8", "replace"))
            model = body.get("model") or ""
        except Exception:
            pass
        try:
            key = (self._req.headers.get("Authorization") or "").replace("Bearer ", "")
        except Exception:
            pass
        task, module = _find_task()
        record(task, module, model, key, u, self._ms)

    def read(self, n=-1):
        return self._body if n in (-1, None) else self._body[:n]

    def getcode(self):
        return self.status

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._resp, name)



# ---------------- 分任务路由 ----------------
_route_cache = {"at": 0.0, "v": {}}


def _task_conf(task):
    """读 settings 里 <task>.api_key / .model / .api_base，60 秒缓存。"""
    import time as _t
    now = _t.time()
    if now - _route_cache["at"] < 60:
        return _route_cache["v"].get(task, {})
    conf = {}
    try:
        import sqlite3
        db = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
        con = sqlite3.connect(db, timeout=3)
        for k, v in con.execute(
                "select k, v from settings where k like '%.api_key' or k like '%.api_base' "
                "or k like '%.model'"):
            if not v or not str(v).strip():
                continue
            task_name = str(k).split(".")[0]
            field = str(k).split(".")[-1]
            if task_name not in ("learn", "dream", "summary", "llm"):
                continue
            if task_name == "llm":
                continue
            conf.setdefault(task_name, {})[field] = str(v).strip()
        con.close()
    except Exception:
        pass
    _route_cache.update(at=now, v=conf)
    return conf.get(task, {})


def _apply_route(req):
    """按调用者任务改写 Request：key / model / base。失败原样返回。"""
    try:
        if not isinstance(req, urllib.request.Request):
            return req
        task, _module = _find_task()
        if task not in ("learn", "dream", "summary"):
            return req
        c = _task_conf(task)
        if not c:
            return req
        url = req.full_url
        data = req.data
        headers = dict(req.headers)
        changed = False
        if c.get("api_base"):
            base = c["api_base"].rstrip("/")
            if base.endswith("/v1"):
                url = base + "/chat/completions"
            else:
                url = base + "/v1/chat/completions"
            changed = True
        if data:
            try:
                body = json.loads(data.decode("utf-8"))
            except Exception:
                body = None
            if isinstance(body, dict):
                if c.get("model"):
                    body["model"] = c["model"]
                    data = json.dumps(body, ensure_ascii=False).encode()
                    changed = True
        if c.get("api_key"):
            headers["Authorization"] = "Bearer " + c["api_key"]
            changed = True
        if not changed:
            return req
        return urllib.request.Request(url, data=data, headers=headers, method=req.get_method())
    except Exception:
        return req


_orig_urlopen = urllib.request.urlopen


def _urlopen(req, *a, **kw):
    t0 = time.time()
    req = _apply_route(req)
    resp = _orig_urlopen(req, *a, **kw)
    try:
        if isinstance(req, urllib.request.Request):
            return _Metered(resp, req, (time.time() - t0) * 1000)
        return resp
    except Exception:
        return resp


if not getattr(urllib.request, "_memvault_metered", False):
    urllib.request.urlopen = _urlopen
    urllib.request._memvault_metered = True


def summary(hours=24):
    """按任务汇总最近 N 小时：次数 / 输入 / 输出 / 命中缓存 / 估钱。"""
    cut = time.time() - hours * 3600
    out = {}
    try:
        with open(LOG, encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ts = d.get("ts") or ""
                try:
                    t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
                except Exception:
                    continue
                if t < cut:
                    continue
                g = out.setdefault(d.get("task") or "unknown",
                                   {"calls": 0, "in": 0, "out": 0, "cached": 0, "cny": 0.0})
                g["calls"] += 1
                g["in"] += int(d.get("in") or 0)
                g["out"] += int(d.get("out") or 0)
                g["cached"] += int(d.get("cached") or 0)
                g["cny"] += float(d.get("cny") or 0)
    except FileNotFoundError:
        pass
    return out
