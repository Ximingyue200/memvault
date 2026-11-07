"""摇篮的 MCP 服务端（Streamable HTTP + JSON-RPC 2.0）。

给「走不了 Gateway 的 Agent」一个标准入口：任何 MCP 客户端都能直接连，
initialize → tools/list → tools/call，不需要改它的模型配置。

工具三个：
  recall  按语义+关键词检索卡片
  retain  写入一条记忆（自动落 span 作为证据，保持可追溯）
  stats   库的健康度概览

只依赖标准库 + memvault 自身模块。监听 0.0.0.0:4181。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time

from memvault import auth as _auth
from memvault import qflush as _q

_CTX = {"who": "anonymous"}
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, "/opt/memvault")

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
PORT = int(os.environ.get("MEMVAULT_MCP_PORT", "4181"))
NAME = "memvault-mcp"
VERSION = "0.1.0"
PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "recall",
        "description": "检索摇篮里的记忆卡片。返回标题、正文、时间、分数。\n参数（JSON，可选字段可省略）：\n- query   string  必填  要查的内容，用自然语言，例如 \"我的内存条是什么牌子\"\n- topk    integer 可选  返回条数，默认 6\n- min_score number 可选 最低相关度 0~1，默认 0.30，调低更宽",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "description": "要查的内容，用自然语言"},
                "topk": {"type": "integer", "default": 6},
                "min_score": {"type": "number", "default": 0.30},
            },
            "required": ["query"],
        },
    },
    {
        "name": "retain",
        "description": "把一条事实写进摇篮。会自动生成 span 作为证据，保持可追溯。\n写入后状态是 proposal，需人工批准（mvapprove）才会进检索。\n参数（JSON，可选字段可省略）：\n- text    string 必填  要记住的内容，一句话或一小段\n- title   string 可选  标题，不填取正文前 24 字\n- kind    string 可选  definition / event / preference / plan，默认 event\n- conv_id string 可选  会话标识，默认 mcp\n- context string 可选  上下文/来源说明\n写入方式：先进暂存队列，攒够阈值再统一提炼（pending / queue_stats / flush 可查看与触发）",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "description": "要记住的内容，一句话或一小段"},
                "title": {"type": "string", "description": "可选标题，不填取正文前 24 字"},
                "kind": {"type": "string", "description": "definition / event / preference / plan，默认 event"},
                "conv_id": {"type": "string", "description": "可选会话标识，默认 mcp"},
                "context": {"type": "string", "description": "可选：上下文/来源说明"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "stats",
        "description": "库的概览：卡片数、span 数、待学习量、热度和取代边数量。\n参数：无（调用时传空对象 {} 即可）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "report",
        "description": "摇篮的最新健康日报：卡片/原文/注入次数/余额，纯读本地文件。\n注意是每日 08:00 生成的快照，不含当天此后的新增。\n参数：无（调用时传空对象 {} 即可）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


TOOLS.extend([
    {
        "name": "pending",
        "description": "看暂存队列里尚未提炼的片段：谁写的、多久前、正文前 200 字。用于多 agent 协作时了解别人还没入库的东西。\n\n参数：\n- limit：可选，整数，默认 20",
        "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                        "properties": {"limit": {"type": "integer", "description": "返回条数，默认 20"}},
                        "additionalProperties": False},
    },
    {
        "name": "queue_stats",
        "description": "暂存队列概况：待提炼条数、总字数、最老一条时间，以及按 agent 分组。\n\n无参数。",
        "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                        "properties": {}, "additionalProperties": False},
    },
    {
        "name": "flush",
        "description": "立刻把暂存队列提炼入库（抽取 → 判重 → 合并/取代）。\n\n参数：\n- force：可选，布尔，忽略阈值强制刷",
        "inputSchema": {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
                        "properties": {"force": {"type": "boolean", "description": "忽略阈值强制提炼"}},
                        "additionalProperties": False},
    },
])


def _con():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _tool_recall_base(args):
    from memvault import search

    q = (args.get("query") or "").strip()
    if not q:
        return {"error": "query 不能为空"}
    con = _con()
    try:
        d = search.search(
            con, q, topk=int(args.get("topk") or 6), min_score=float(args.get("min_score") or 0.30)
        )
        cards = []
        for c in d.get("cards", []):
            cards.append(
                {
                    "id": c.get("id"),
                    "title": c.get("title"),
                    "body": (c.get("body") or "")[:600],
                    "kind": c.get("kind"),
                    "when": c.get("occurred_at") or c.get("created_at"),
                    "score": round(float(c.get("final") or c.get("score") or 0), 3),
                }
            )
        return {"query": q, "count": len(cards), "cards": cards, "note": d.get("note")}
    finally:
        con.close()


def tool_retain(args):
    from memvault import store

    text = (args.get("text") or "").strip()
    if len(text) < 4:
        return {"error": "text 太短"}
    text = text[:8000]
    conv = (args.get("conv_id") or "mcp")[:64]
    kind = args.get("kind") or "event"
    title = (args.get("title") or text[:24]).strip()
    now = _now()

    con = _con()
    try:
        msg_id = con.execute("SELECT COALESCE(MAX(msg_id),0)+1 FROM spans").fetchone()[0]
        span_id = "sp_" + hashlib.sha1(("%s:%s" % (conv, msg_id)).encode()).hexdigest()[:16]
        th = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        con.execute(
            "INSERT INTO spans(span_id,conv_id,msg_id,seq,role,ts,char_start,char_end,quote,text_hash,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (span_id, conv, msg_id, 0, "assistant", now, 0, len(text), text, th, now),
        )
        con.commit()

        card = {
            "title": title,
            "body": text,
            "kind": kind,
            "owner": "agent",
            # 防污染：agent 写的东西先落 proposal，人工确认才转正（MEMVAULT_AGENT_PROPOSAL=0 可关）
            "status": "proposal" if os.environ.get("MEMVAULT_AGENT_PROPOSAL", "1") != "0" else "active",
            "source_path": "mcp/%s" % conv,
            "extractor_version": "mcp-v1",
        }
        evs = [
            {
                "span_id": span_id,
                "role": "assistant",
                "quote": text[:800],
                "char_start": 0,
                "char_end": min(len(text), 800),
                "weight": 0.8,
            }
        ]

        def lookup(sid):
            return {"quote": text, "role": "assistant"} if sid == span_id else None

        cid, reason = store.ingest_card(con, card, evs, span_lookup=lookup)
        con.commit()
        if cid:
            try:
                from memvault import embed as _emb
                _v = _emb.embed([(title + " " + text)[:1200]])[0]
                store.put_vector(con, cid, _v)
                con.commit()
            except Exception as e:
                sys.stderr.write("[mcp] embed failed: %s\n" % e)
            return {"saved": True, "card_id": cid, "span_id": span_id, "title": title,
                    "status": "proposal" if os.environ.get("MEMVAULT_AGENT_PROPOSAL", "1") != "0" else "active",
                    "note": "proposal 需要人工批准才进检索（mvapprove --approve <id>）"}
        return {"saved": False, "reason": reason}
    finally:
        con.close()


def tool_stats(_args):
    con = _con()
    try:
        one = lambda q: con.execute(q).fetchone()[0]
        return {
            "cards": one("SELECT count(*) FROM cards"),
            "cards_active": one("SELECT count(*) FROM cards WHERE status='active'"),
            "spans": one("SELECT count(*) FROM spans"),
            "pending_learn": one(
                "SELECT count(*) FROM spans s WHERE NOT EXISTS "
                "(SELECT 1 FROM learn_seen z WHERE z.span_id=s.span_id)"
            ),
            "supersedes": one("SELECT count(*) FROM cards WHERE supersedes IS NOT NULL"),
            "heat_avg": round(one("SELECT COALESCE(avg(heat),0) FROM cards"), 4),
            "db_bytes": one("SELECT page_count*page_size FROM pragma_page_count(), pragma_page_size()"),
        }
    finally:
        con.close()


def tool_report(_args):
    for path in ("/opt/memvault/reports/latest.md",):
        try:
            with open(path, encoding="utf-8") as f:
                return {"ok": True, "report": f.read()}
        except FileNotFoundError:
            continue
        except Exception as e:
            return {"ok": False, "reason": str(e)[:120]}
    return {"ok": False, "reason": "还没有日报，先跑一次 /usr/local/bin/mvhealth.py"}


def tool_retain_q(args):
    """retain 改成入队：攒够再一起提炼，避免碎片写成几十张卡。"""
    text = (args.get("text") or "").strip()
    if not text:
        return {"queued": False, "reason": "text 为空"}
    con = _con()
    try:
        qid = _q.enqueue(con, text, agent=_CTX.get("who") or "anonymous",
                         source="mcp", context=args.get("context"))
        ok, why = _q.should_flush(con)
        auto = None
        if ok:
            try:
                auto = _q.flush(con, force=False)   # autoflush
            except Exception as _e:
                auto = {"flushed": False, "reason": "%s: %s" % (type(_e).__name__, _e)}
        d = _q.depth(con)
    finally:
        con.close()
    return {"queued": True, "id": qid, "agent": _CTX.get("who"),
            "trigger": why if ok else None, "auto": auto, "queue": d,
            "note": "已进暂存队列，攒够阈值再提炼；要立刻提炼请调 flush"}


def tool_pending(args):
    con = _con()
    try:
        items = _q.pending_items(con, int(args.get("limit") or 20))
        d = _q.depth(con)
    finally:
        con.close()
    return {"count": len(items), "queue": d, "items": items}


def tool_queue_stats(_args):
    con = _con()
    try:
        return _q.depth(con)
    finally:
        con.close()


def tool_flush(args):
    con = _con()
    try:
        return _q.flush(con, force=bool(args.get("force")))
    except Exception as e:
        return {"flushed": False, "reason": "%s: %s" % (type(e).__name__, e)}
    finally:
        con.close()


def dispatch(method, params):
    """返回 (result, error)。"""
    if method == "initialize":
        return (
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": NAME, "version": VERSION},
            },
            None,
        )
    if method in ("notifications/initialized", "notifications/cancelled"):
        return (None, None)
    if method == "ping":
        return ({}, None)
    if method == "tools/list":
        return ({"tools": TOOLS}, None)
    if method == "tools/call":
        name = (params or {}).get("name")
        args = (params or {}).get("arguments") or {}
        fn = {"recall": tool_recall, "retain": tool_retain_q, "stats": tool_stats, "report": tool_report,
              "pending": tool_pending, "queue_stats": tool_queue_stats, "flush": tool_flush}.get(name)
        if not fn:
            return (None, {"code": -32602, "message": "unknown tool: %s" % name})
        # 入参严格校验：schema 里写了 additionalProperties=false，服务端就得真的拒
        _spec = next((t for t in TOOLS if t.get("name") == name), {})
        _schema = _spec.get("inputSchema") or {}
        _props = list((_schema.get("properties") or {}).keys())
        _extra = sorted(set(args) - set(_props))
        if _extra:
            return (None, {"code": -32602,
                           "message": "%s 不认识参数 %s（可用：%s）" % (name, ", ".join(_extra), ", ".join(_props) or "无")})
        _miss = [r for r in (_schema.get("required") or []) if args.get(r) in (None, "")]
        if _miss:
            return (None, {"code": -32602, "message": "%s 缺少必填参数：%s" % (name, ", ".join(_miss))})
        try:
            out = fn(args)
        except Exception as e:
            traceback.print_exc()
            out = {"error": "%s: %s" % (type(e).__name__, e)}
        return ({"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}]}, None)
    if method in ("resources/list", "prompts/list"):
        return ({}, None)
    return (None, {"code": -32601, "message": "method not found: %s" % method})


class Handler(BaseHTTPRequestHandler):
    server_version = NAME + "/" + VERSION

    def log_message(self, fmt, *a):
        sys.stderr.write("[mcp] " + (fmt % a) + "\n")

    def _send(self, code, payload, extra=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/health", "/"):
            self._send(200, {"ok": True, "server": NAME, "version": VERSION, "protocol": PROTOCOL,
                             "tools": [t["name"] for t in TOOLS]})
        elif path == "/mcp":
            self._sse_stream()
        elif path == "/registry":
            # 能力登记簿：任何 agent 一条 GET 就知道这台机器上有哪些技能和 MCP，
            # 需要哪个再自己去 /registry/skill/<名字> 取正文 —— 按需取用，不预装。
            try:
                with open("/opt/memvault/registry/index.json", encoding="utf-8") as f:
                    self._send(200, json.load(f))
            except Exception as e:
                self._send(500, {"error": str(e)[:120]})
        elif path.startswith("/registry/skill/"):
            name = path[len("/registry/skill/"):].strip("/")
            if not name or "/" in name or ".." in name:
                self._send(400, {"error": "bad name"})
                return
            try:
                with open("/opt/memvault/registry/skills/%s.md" % name, encoding="utf-8") as f:
                    body = f.read()
            except FileNotFoundError:
                self._send(404, {"error": "no such skill"})
                return
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self._send(404, {"error": "not found"})

    def do_DELETE(self):
        """MCP 会话收尾：规范要求 204，之前没实现会回 501，客户端判定异常。"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _sse_stream(self):
        """GET /mcp —— server→client 的 SSE 通道。

        本服务是请求-响应式的，不会主动推送内容；但协议允许客户端开这条流，
        不开就会让一些客户端（OpenClaw）误判连接已关闭。这里挂住并定期发心跳。
        """
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("mcp-session-id", self.headers.get("mcp-session-id") or "")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            for _ in range(240):  # 最长挂 60 分钟
                time.sleep(15)
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except Exception:
            return  # 客户端断开

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"

        _ok, _who, _why = False, "", ""
        try:
            _c = _con()
            _ok, _who, _why = _auth.verify(_c, self.headers.get("Authorization"))
            _c.close()
        except Exception as _e:
            _ok, _why = False, "auth error: %s" % _e
        if not _ok:
            self._send(401, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32001, "message": "unauthorized: " + _why}})
            return
        _CTX["who"] = _who
        try:
            msg = json.loads(raw or b"{}")
        except Exception:
            self._send(400, {"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32700, "message": "parse error"}})
            return

        session = self.headers.get("mcp-session-id") or hashlib.sha1(
            ("%s%s" % (time.time(), os.getpid())).encode()
        ).hexdigest()[:32]
        extra = {"mcp-session-id": session}

        if isinstance(msg, list):  # batch
            out = []
            for m in msg:
                res, err = dispatch(m.get("method"), m.get("params"))
                if m.get("id") is None:
                    continue
                out.append({"jsonrpc": "2.0", "id": m.get("id"),
                            **({"result": res} if err is None else {"error": err})})
            self._send(200, out, extra)
            return

        res, err = dispatch(msg.get("method"), msg.get("params"))
        if msg.get("id") is None:  # notification：202 空体
            self.send_response(202)
            for k, v in extra.items():
                self.send_header(k, v)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        payload = {"jsonrpc": "2.0", "id": msg.get("id")}
        payload.update({"result": res} if err is None else {"error": err})
        self._send(200, payload, extra)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    sys.stderr.write("[mcp] %s v%s listening on :%d (db=%s)\n" % (NAME, VERSION, PORT, DB))
    srv.serve_forever()



def tool_recall(args):
    """检索已入库的卡；顺带把**还没出库**的暂存也搜一遍（跨 agent 可见）。"""
    out = _tool_recall_base(args)
    try:
        con = _con()
        hits = _q.search(con, args.get("query") or "", limit=3)
        con.close()
    except Exception as e:
        hits = []
        print("[mcp] 暂存检索失败：%s" % e)
    if isinstance(out, dict):
        out["pending_hits"] = hits
        if hits and not out.get("cards"):
            out["note"] = "已入库的卡里没有，但暂存队列里有 %d 条还没提炼的，见 pending_hits" % len(hits)
    return out


if __name__ == "__main__":
    main()
