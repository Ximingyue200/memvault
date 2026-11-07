"""最小可用 MCP 客户端（Streamable HTTP）。

只做三件事：拿到工具清单、把清单转成 OpenAI tools 格式、调工具拿文本结果。
不处理 resources / prompts / sampling，也不需要 session。
headers 用于需要 Authorization 的服务（如百度千帆）；Tavily 把 key 放在 URL 里，headers 留空。
"""
import json
import urllib.error
import urllib.request


class MCPError(RuntimeError):
    pass


class MCP(object):
    def __init__(self, url, name="mcp", timeout=15, headers=None):
        self.url = url
        self.name = name
        self.timeout = timeout
        self.headers = dict(headers or {})
        self._id = 0
        self._tools = None
        self._info = None

    # ── 底层 ────────────────────────────────────────────────
    def _rpc(self, method, params=None):
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        data = json.dumps(body).encode("utf-8")
        hdrs = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        hdrs.update(self.headers)
        req = urllib.request.Request(self.url, data=data, method="POST", headers=hdrs)
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            raise MCPError("HTTP %s: %s" % (e.code, e.read()[:200]))
        except Exception as e:
            raise MCPError("%s: %s" % (type(e).__name__, str(e)[:200]))

        ctype = (resp.headers.get("content-type") or "").lower()
        if "text/event-stream" in ctype:
            # SSE：逐行读，拿到匹配 id 的 result 立刻返回，不等流结束
            while True:
                try:
                    line = resp.readline()
                except Exception:
                    break
                if not line:
                    break
                s = line.decode("utf-8", "replace").strip()
                if not s.startswith("data:"):
                    continue
                payload = s[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    m = json.loads(payload)
                except Exception:
                    continue
                if not isinstance(m, dict):
                    continue
                if m.get("id") is not None and m.get("id") != self._id:
                    continue
                if "error" in m:
                    err = m["error"]
                    raise MCPError(str(err.get("message") or err)[:300])
                if "result" in m:
                    return m["result"]
            raise MCPError("SSE 流结束但没拿到 result")
        raw = resp.read().decode("utf-8", "replace")

        msgs = []
        if "text/event-stream" in ctype:
            for line in raw.split("\n"):
                line = line.strip()
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        msgs.append(json.loads(payload))
                    except Exception:
                        pass
        else:
            try:
                msgs = [json.loads(raw)]
            except Exception:
                raise MCPError("响应不是 JSON: %s" % raw[:200])

        for m in msgs:
            if not isinstance(m, dict):
                continue
            if m.get("id") is not None and m.get("id") != self._id:
                continue
            if "error" in m:
                err = m["error"]
                raise MCPError(str(err.get("message") or err)[:300])
            if "result" in m:
                return m["result"]
        raise MCPError("没有拿到 result（%d 条消息）" % len(msgs))

    # ── 对外 ────────────────────────────────────────────────
    def initialize(self):
        if self._info is not None:
            return self._info
        r = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "memvault-gateway", "version": "1.0"},
        })
        self._info = r or {}
        return self._info

    def tools(self):
        if self._tools is None:
            self.initialize()
            r = self._rpc("tools/list", {})
            self._tools = (r or {}).get("tools") or []
        return self._tools

    def reset(self):
        """丢掉缓存与连接状态，下次调用重新握手。"""
        self._tools = None
        self._info = None
        self._id = 0

    def openai_tools(self, prefix=None):
        """转成 OpenAI function 格式。prefix 用于给工具名加前端命名空间。"""
        out = []
        for t in self.tools():
            name = t.get("name") or ""
            if not name:
                continue
            schema = t.get("inputSchema") or {}
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object", "properties": {}}
            out.append({
                "type": "function",
                "function": {
                    "name": (prefix + name) if prefix else name,
                    "description": (t.get("description") or "")[:900],
                    "parameters": schema,
                },
            })
        return out

    def call(self, name, args, prefix=None):
        """调一个工具，返回纯文本。MCP 的 content[] 里可能有 text/image/resource。"""
        real = name[len(prefix):] if (prefix and name.startswith(prefix)) else name
        r = self._rpc("tools/call", {"name": real, "arguments": args or {}})
        parts = []
        for c in (r or {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            t = c.get("type")
            if t == "text":
                parts.append(c.get("text") or "")
            elif t == "resource":
                res = c.get("resource") or {}
                parts.append(str(res.get("text") or res.get("uri") or ""))
            elif t == "image":
                parts.append("[image %s]" % (c.get("mimeType") or ""))
        if not parts:
            return json.dumps(r, ensure_ascii=False)[:4000]
        return "\n".join(parts)
