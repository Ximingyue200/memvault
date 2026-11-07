# -*- coding: utf-8 -*-
"""API token —— 一个 token 就是一个 agent 的身份。

设计：表为空时**不校验**（便于平滑切换）；建了第一个启用中的 token 之后，
所有受保护入口都必须带 `Authorization: Bearer <token>`。
只存 sha256，不存明文。
"""
import hashlib
import secrets
import time

DDL = """CREATE TABLE IF NOT EXISTS api_tokens(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  prefix TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_used_at TEXT
)"""


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _h(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def ensure(con):
    con.execute(DDL)
    con.commit()


def create(con, name):
    ensure(con)
    tok = "mv-" + secrets.token_urlsafe(24)
    con.execute(
        "INSERT INTO api_tokens(name, token_hash, prefix, created_at) VALUES(?,?,?,?)",
        (name, _h(tok), tok[:10], now()),
    )
    con.commit()
    return tok


def listing(con):
    ensure(con)
    return con.execute(
        "SELECT id,name,prefix,enabled,created_at,last_used_at FROM api_tokens ORDER BY id"
    ).fetchall()


def set_enabled(con, key, enabled):
    """key 可以是 id 或名字。"""
    ensure(con)
    cur = con.execute(
        "UPDATE api_tokens SET enabled=? WHERE id=? OR name=?", (1 if enabled else 0, key, key)
    )
    con.commit()
    return cur.rowcount


def _bearer(h):
    if not h:
        return ""
    h = h.strip()
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return h


def verify(con, header):
    """返回 (ok, who, why)。表空 → (True,'anonymous','open')。"""
    ensure(con)
    n = con.execute("SELECT COUNT(*) FROM api_tokens WHERE enabled=1").fetchone()[0]
    if n == 0:
        return True, "anonymous", "no-token-configured"
    tok = _bearer(header)
    if not tok:
        return False, "", "缺少 Authorization: Bearer <token>"
    r = con.execute(
        "SELECT id,name FROM api_tokens WHERE token_hash=? AND enabled=1", (_h(tok),)
    ).fetchone()
    if not r:
        return False, "", "token 无效或已停用"
    con.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (now(), r[0]))
    con.commit()
    return True, r[1], "ok"
