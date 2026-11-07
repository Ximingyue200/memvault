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
        "description": "检索摇篮里的记忆卡片。返回标题、正文、时间、分数。",
        "inputSchema": {
            "type": "object",
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
        "description": "把一条事实写进摇篮。会自动生成 span 作为证据，保持可追溯。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "要记住的内容，一句话或一小段"},
                "title": {"type": "string", "description": "可选标题，不填取正文前 24 字"},
                "kind": {"type": "string", "description": "definition / event / preference / plan，默认 event"},
                "conv_id": {"type": "string", "description": "可选会话标识，默认 mcp"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "stats",
        "description": "库的概览：卡片数、span 数、待学习量、热度和取代边数量。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "report",
        "description": "摇篮的最新健康日报：卡片/原文/注入次数/余额，纯读本地文件。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _con():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def tool_recall(args):
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
        fn = {"recall": tool_recall, "retain": tool_retain, "stats": tool_stats, "report": tool_report}.get(name)
        if not fn:
            return (None, {"code": -32602, "message": "unknown tool: %s" % name})
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

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
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


if __name__ == "__main__":
    main()
