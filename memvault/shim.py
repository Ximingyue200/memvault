"""OpenViking API 形状 → memvault 的 shim。

插件（dsh-openviking）只会说 /api/v1/*，memvault 说的是 /find /search /memlearn。
这里做翻译，让插件一行不用改。

    python3 -m memvault.shim          # 监听 0.0.0.0:4175
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("MEMVAULT_UPSTREAM", "http://127.0.0.1:4174").rstrip("/")
PORT = int(os.environ.get("MEMVAULT_SHIM_PORT", "4175"))
HOST = os.environ.get("MEMVAULT_SHIM_HOST", "0.0.0.0")

_SELF_LAST = 0.0
_SELF_TTL = 3600.0   # 60 分钟内只注入一次（近似「新会话第一次唤醒」）

# ── 首轮判断：距上一次任何调用超过 5 分钟 = 新会话首轮 ──
_LAST_CALL = 0.0
_TURN_GAP = 300.0
# 触发回想的词：出现即视为「用户在问过去的事」，即使不是首轮也查
# 「回忆过去」的触发词。实测漏得很厉害：用户问「我什么时候问过 7840 的 CPU」
# 这句里「什么时候」「问过」一个都不在，于是整个检索被跳过、返回空——
# 而这恰恰是最该查记忆的一类问句。宁可多查一次，也不要该查的时候不查。
_RECALL_HINTS = ("上次", "刚才", "之前", "先前", "前面", "早先", "原来", "以前",
                 "前天", "昨天", "上周", "上个月", "去年", "记得", "后来",
                 "说过", "提过", "问过", "聊过", "讲过", "谈过", "那个", "当时",
                 "什么时候", "哪天", "几月", "多久", "哪次", "第一次", "最后一次")

USER_ROOT = "viking://user/da-feiyu/memories"

# ── 注入日志：每轮对话被召回了什么，全部落库，供 /admin#inject 查看 ──
import sqlite3 as _sq

_DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")

def _inject_on():
    """设置页的「注入开关」：settings 表里 inject.enabled，缺省视为开。"""
    try:
        con = _sq.connect(_DB, timeout=2)
        r = con.execute("SELECT v FROM settings WHERE k='inject.enabled'").fetchone()
        con.close()
        return (r[0] if r else "1") != "0"
    except Exception:
        return True


def _log_inject(q, mems, chars, ms, has_self):
    try:
        con = _sq.connect(_DB, timeout=3)
        con.execute("""CREATE TABLE IF NOT EXISTS injections(
            id INTEGER PRIMARY KEY, at TEXT, kind TEXT, query TEXT,
            n_cards INTEGER, card_ids TEXT, chars INTEGER, ms INTEGER, has_self INTEGER)""")
        con.execute("""CREATE INDEX IF NOT EXISTS idx_inj_at ON injections(at)""")
        ids = []
        for m in mems[:20]:
            u = m.get("uri") or ""
            if "/" in u:
                tail = u.rsplit("/", 1)[-1]
                if tail.isdigit():
                    ids.append(tail)
        con.execute("INSERT INTO injections(at,kind,query,n_cards,card_ids,chars,ms,has_self) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (__import__("datetime").datetime.now(
                        __import__("datetime").timezone(
                            __import__("datetime").timedelta(hours=8))).isoformat(timespec="seconds"),
                     "recall", q, len(mems), ",".join(ids), chars, ms, 1 if has_self else 0))
        con.commit(); con.close()
    except Exception:
        pass


def _recent_focus(limit=4):
    """从 focus 表取最近的会话焦点，拼成一段「刚做的」。"""
    try:
        con = _sq.connect(_DB, timeout=3)
        con.row_factory = _sq.Row
        rows = con.execute(
            "SELECT topic, open_items, last_ts FROM focus "
            "WHERE last_ts >= datetime('now', '-1 day') "
            "ORDER BY last_ts DESC LIMIT ?",
            (limit,)).fetchall()
        con.close()
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for r in rows:
        t = (r["topic"] or "").strip()
        if not t:
            continue
        o = (r["open_items"] or "").strip()
        if o and o not in ("[]", "无"):
            try:
                import json as _j
                arr = _j.loads(o)
                o = "；".join(arr) if isinstance(arr, list) else o
            except Exception:
                pass
            lines.append(f"- {t}（还没做完：{o}）")
        else:
            lines.append(f"- {t}")
    return "\n".join(lines[:limit])


def _up(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(UPSTREAM + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _empty_search():
    return {"memories": [], "resources": [], "total": 0}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # ── 读 ───────────────────────────────────────────────────────────────
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(p.query)
        path = p.path

        if path in ("/self", "/api/v1/self"):
            try:
                d = _up("GET", "/self")
            except Exception as e:
                return self._json(500, {"error": str(e)})
            items = d.get("items") or []
            txt = "\n".join("- " + i["body"] for i in items[:40])
            return self._json(200, {"count": len(items),
                                    "uri": f"{USER_ROOT}/self/notes",
                                    "title": "【我】备忘", "content": txt or ""})

        if path in ("/health", "/api/v1/health"):
            try:
                st = _up("GET", "/health")
            except Exception:
                st = {}
            # Studio 要的是 OpenViking 的 health 形状，不是 memvault 的
            return self._json(200, {
                "status": "ok", "healthy": True,
                "version": "0.5.2.1", "auth_mode": "api_key",
                "memvault": st,
            })

        if path in ("/api/v1/stats/memories", "/api/v1/stats"):
            st = _up("GET", "/health")
            return self._json(200, {
                "memories": st.get("cards", 0),
                "total_memories": st.get("cards", 0),
                "resources": 0,
                **st,
            })

        if path in ("/api/v1/search/find", "/api/v1/search/search", "/find", "/search"):
            q = (qs.get("q") or qs.get("query") or [""])[0]
            print(f"[shim] {path} q={q!r}", flush=True)
            # 工具调用（MCP / 插件主动查）默认 force：
            # 「注入开关」管的是网关自动塞上下文，不该把检索也一起关掉。
            # 之前 inject.enabled=0 时，MCP 查询一律返回空，看着像库里没数据。
            if "force" not in qs:
                qs = dict(qs)
                qs["force"] = ["1"]
            return self._do_search(q, qs)

        if path == "/api/v1/fs/ls":
            return self._json(200, {"entries": [], "items": []})
        if path == "/api/v1/fs/stat":
            return self._json(200, {"exists": False})
        if path == "/api/v1/fs/tree":
            return self._json(200, {"tree": {}})
        if path in ("/api/v1/fs", "/api/v1/resources"):
            return self._json(200, {"resources": [], "total": 0})
        if path == "/api/v1/observer/queue":
            return self._json(200, {"pending": 0, "queue": []})
        if path == "/api/v1/skills":
            return self._json(200, {"skills": []})

        # 兜底：别的路径一律空成功，别让插件崩
        return self._json(200, {})

    # ── 写 ───────────────────────────────────────────────────────────────
    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        path = p.path
        body = self._body()

        if path in ("/api/v1/search/find", "/api/v1/search/search"):
            # GET 那侧解析了 query string，POST 这侧以前只从 body 里挑两个字段，
            # 于是 ?force=1 和非 body 参数全被丢掉。两边都收。
            _q = urllib.parse.parse_qs(p.query)
            return self._do_search(body.get("query") or _q.get("q", [""])[0], {
                "limit": [str(body.get("limit") or 10)],
                "min_score": [str(body.get("score_threshold") or 0.30)],
                "force": _q.get("force") or ([str(body["force"])] if "force" in body else [""]),
            })

        if path == "/api/v1/content/write":
            text = body.get("content") or ""
            uri = body.get("uri") or ""
            r = _up("POST", "/memlearn", {"content": text, "uri": uri})
            return self._json(200, {"ok": True, "uri": uri, **r})

        if path in ("/api/v1/sessions",):
            return self._json(200, {"session_id": "shim", "status": "ok"})

        return self._json(200, {})

    # ── 公共搜索 ─────────────────────────────────────────────────────────
    def _do_search(self, q, qs):
        import time as _t2
        _t0 = _t2.time()
        global _LAST_CALL
        now_t = _t2.time()
        is_first = (now_t - _LAST_CALL) > _TURN_GAP
        gap = now_t - _LAST_CALL if _LAST_CALL else -1
        _LAST_CALL = now_t
        print(f"[shim] do_search q={q!r} first={is_first} gap={gap:.0f}s", flush=True)
        if not q:
            return self._json(200, _empty_search())

        # ── 总开关：只管「网关自动注入」，工具主动查询（force）照常 ──
        _force_early = str((qs or {}).get("force", [""])[0]).lower() in ("1", "true", "yes")
        if not _force_early and not _inject_on():
            _log_inject(q, [], 0, 0, False)
            return self._json(200, _empty_search())

        # force=1：调用方明确要查（用户主动问「我以前是不是…」、或 agent 自己查证），
        # 不该被「首轮/触发词」这道门拦住。retrieve 是 shim 的事，注不注入是插件的事。
        _force = str((qs or {}).get("force", [""])[0]).lower() in ("1", "true", "yes")
        want_recall = _force or is_first or any(h in q for h in _RECALL_HINTS)

        # ── 非首轮且没问过去的事：不注入，但要如实说明是「跳过」而不是「没查到」 ──
        if not want_recall:
            _log_inject(q, [], 0, int((_t2.time() - _t0) * 1000), False)
            d = _empty_search()
            d["skipped"] = "no_trigger"
            d["reason"] = "非首轮且未命中触发词；加 force=1 可强制检索"
            return self._json(200, d)

        # 以前写死 "6 if is_first else 4"，调用方传 limit=20 也拿不到 20 条。
        # 现在优先用请求里的值，没传才退回首轮/非首轮的默认。
        limit = str((qs or {}).get("limit", [""])[0] or ("6" if is_first else "4"))
        # 0.55 是拍的，没有任何标注集支撑；实测它把正确答案整批砍掉
        # （问「什么时候聊过 7840」时正确的三张卡是 0.512/0.505/0.494，全在 0.55 以下），
        # 结果只剩靠 recall 加成冲上来的无关卡。回归 memvault 默认 0.30。
        mins = (qs or {}).get("min_score", ["0.30"])[0] or "0.30"
        # 问「什么时候/哪次/问过」时走 timeline：那是「哪几段对话提到过」的问题，
        # 不是「哪张卡最像」。向量检索对这类问题给的是规格卡的时间戳，答非所问。
        _ASK_TIME = ("什么时候", "哪次", "哪天", "几月", "多久", "第一次", "最后一次",
                     "问过", "聊过", "讲过", "谈过", "说过", "提过")
        _mode = (qs or {}).get("mode", [""])[0].strip()
        if not _mode and any(w in q for w in _ASK_TIME):
            _mode = "timeline"

        _pd = {"q": q, "limit": limit, "min_score": mins,
               "hop": os.environ.get("MEMVAULT_HOP", "1")}
        if _mode:
            _pd["mode"] = _mode
        params = urllib.parse.urlencode(_pd)
        try:
            d = _up("GET", "/find?" + params)
        except Exception as e:
            return self._json(200, {**_empty_search(), "error": str(e)[:120]})
        mems = d.get("memories") or []

        # selfNote：只在「新会话」注入一次（60 分钟窗口），不每轮灌
        global _SELF_LAST
        import time as _t
        # 主动查询（force）是「我就想知道这件事」，
        # 塞三条固定内容进来只会挤掉真正的答案。
        if not _force and _t.time() - _SELF_LAST > _SELF_TTL:
            try:
                sd = _up("GET", "/self")
                items = sd.get("items") or []
                if items:
                    _SELF_LAST = _t.time()
                    txt = "\n".join("- " + i["body"] for i in items[:20])
                    mems.insert(0, {
                        "uri": f"{USER_ROOT}/self/notes",
                        "title": "【我】这 18 个月里我变了几处",
                        "content": txt[:1200],
                        "abstract": "selfNote：我对自己变化的记录",
                        "score": 0.0, "kind": "selfnote", "owner": "agent", "priority": "context",
                        "claim_type": "unknown", "evidence": [],
                    })
            except Exception:
                pass
        # 首轮：加一段「最近刚做的」（来自 focus 表，不占检索名额）
        if is_first and not _force:
            recent = _recent_focus(4)
            if recent:
                mems.insert(0, {
                    "uri": f"{USER_ROOT}/focus/recent",
                    "title": "【最近】这几个会话在做什么",
                    "content": recent,
                    "abstract": recent[:200],
                    "score": 0.0, "kind": "focus", "priority": "context",
                })
        bh = d.get("brief_hint")
        if bh and not _force:
            names = "、".join(bh.get("names") or [])
            mems.insert(0, {
                "uri": f"{USER_ROOT}/brief/threads",
                "title": "【线索】%d 件卡着 · %d 件进行中" % (bh.get("blocked", 0), bh.get("active", 0)),
                "content": ("当前卡住的：" + names) if names else "无线索记录。",
                "abstract": ("当前卡住的：" + names) if names else "无线索记录。",
                "score": 0.0,
                "kind": "brief",
                "priority": "context",
            })
        for m in mems:
            if not str(m.get("uri", "")).startswith("viking://"):
                # timeline 回来的是原文片段，没有 card_id，别拼出 card/None
                if m.get("kind") == "span" and m.get("span_id"):
                    m["uri"] = f"{USER_ROOT}/span/{m.get('span_id')}"
                elif m.get("card_id"):
                    m["uri"] = f"{USER_ROOT}/card/{m.get('card_id')}"
                else:
                    m["uri"] = f"{USER_ROOT}/{m.get('kind') or 'misc'}/{m.get('title') or ''}"[:120]
            # 让模型看得见「这是计划还是事实」
            ct = m.get("claim_type")
            if ct and ct != "unknown":
                tag = {"plan": "【计划/未执行】", "question": "【开放问题】"}.get(ct, "")
                if tag:
                    m["title"] = tag + str(m.get("title") or "")
            # 有原文的卡：注入原话，不只是转述
            evs = [e for e in (m.get("evidence") or []) if (e.get("quote") or "").strip()]
            if evs:
                # 注意：这里必须用独立变量。以前写成 q = ... 会把入参 query 覆盖掉，
                # 导致 _log_inject 记的是最后一条卡的原文，injections 表从此不可用于
                # 「用户问了什么 → 召回了什么」的分析。
                _evq = evs[0]["quote"].strip()
                if len(_evq) > 400:
                    _evq = _evq[:400] + "…"
                body = str(m.get("content") or m.get("abstract") or "")
                m["content"] = f"原文：{_evq}\n摘要：{body}"[:700]
                m["abstract"] = m["content"]
        # 背景信息（brief/focus/selfnote）不参与排名，但也不能挡在答案前面。
        # score 设成 0 只是数字，数组顺序还是插入顺序，所以这里显式排一次。
        mems.sort(key=lambda m: (1 if m.get("priority") == "context" else 0,
                                 -(m.get("score") or 0)))
        d["memories"] = mems
        try:
            _body_len = sum(len(str(m.get("title") or "")) + len(str(m.get("abstract") or m.get("content") or ""))
                            for m in mems)
            _log_inject(q, mems, _body_len,
                        int((_t2.time() - _t0) * 1000),
                        _t2.time() - _SELF_LAST < 5)
        except Exception:
            pass
        d.setdefault("resources", [])
        d["total"] = len(mems)
        return self._json(200, d)


def main():
    srv = ThreadingHTTPServer((HOST, PORT), H)
    print(f"shim {HOST}:{PORT} -> {UPSTREAM}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
