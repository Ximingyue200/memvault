# -*- coding: utf-8 -*-
"""pitfallauto.py —— 自动从原文里捞「踩坑」，写成 pitfall 卡。

两个来源：
  1) spans 表（走网关的 agent：Eta / OpenClaw / 任何 base_url 指过来的客户端）
  2) 本地 jsonl（DSH 会话同步过来的原文，可选目录）

规约：一条坑 = 「报错行」+「同一会话里随后的修法」。
打分（≥3 才入库）：
  +2  报错行带真实错误特征（Error/Exception/Traceback/errno/exit code/bind ...）
  +2  报错行来自 tool/result（真跑过）而不是人在转述
  +1  同一会话随后的文本里出现修法词（改成/修法/根因/加一行/fixed by…）
  +1  同一签名在语料里出现 ≥2 次
  -3  签名像「文档示例」（含 示例/比如/假设/if you see）

用法：
  python3 -m memvault.pitfallauto --spans [--days 30] [--dry]
  python3 -m memvault.pitfallauto --jsonl <目录或文件> [--days 30] [--dry]
字表存在 settings.pitfallauto.cursor.*，增量跑。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ERR = re.compile(
    r"(?:\b\w*(?:Error|Exception)\b\s*[:：]|Traceback \(most recent call last\)"
    r"|EADDRINUSE|ECONNREFUSED|ECONNRESET|ENOENT|EACCES|ETIMEDOUT"
    r"|cannot assign requested address|no such (?:file|column|table|module)"
    r"|database disk image is malformed|permission denied"
    r"|command not found|exit code [1-9]|Exited \(\d+\)"
    r"|listen tcp4? [^ ]+ bind|401 Unauthorized|403 Forbidden|404 Not Found"
    r"|422 Unprocessable|500 Internal Server Error|502 Bad Gateway"
    r"|ModuleNotFoundError|ImportError|SyntaxError|IndentationError|NameError"
    r"|UnboundLocalError|TypeError|ValueError|KeyError|AttributeError"
    r"|OperationalError|IntegrityError|DatabaseError)",
    re.I,
)
FIX = re.compile(
    r"(改成|改为|改到|换成|修法|根因|原因是|原因是|解决办法|解决方式|改用|加一行|删掉|"
    r"调成|收紧|放宽|换成用|重启后|更正|修正|补上|去掉|加上|fixed by|root cause|"
    r"the fix|workaround|patch)", re.I)
DOC = re.compile(r"(示例|比如|例如|假设|if you see|for example|e\.g\.|文档里)", re.I)
TOOLISH = re.compile(r"(?:^|\n)\s*(?:\$|#|>)\s|exit code|stderr|stdout", re.I)


ERR_HARD = re.compile(
    r"(?:(?:^|[:：]\s*)[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)\b\s*[:：])"
    r"|(?:command not found|No such file or directory|Permission denied|Operation not permitted)"
    r"|(?:\bno such (?:column|table|module|file|function)\b)"
    r"|(?:\bdatabase disk image is malformed\b)"
    r"|(?:\bbind: (?:Address already in use|cannot assign requested address)\b)"
    r"|(?:\bexit code [1-9]\b)"
    r"|(?:\b(?:EADDRINUSE|ECONNREFUSED|ECONNRESET|ENOENT|EACCES|ETIMEDOUT)\b)"
    r"|(?:\bTraceback \(most recent call last\))"
    r"|(?:\b(?:ModuleNotFoundError|ImportError|SyntaxError|IndentationError|UnboundLocalError)\b)"
    r"|(?:\b(?:cannot|can't) access local variable\b)"
    r"|(?:\bMissingGreenlet\b)",
)
CJK = re.compile(r"[\u4e00-\u9fff]")
CODEY = re.compile(r"(=>|\?\?|===|!==|function\s*\(|\{\s*\}|\.map\(|console\.|\bconst\b|\blet\b|\bvar\b|;\s*$)")

CARD_MARK = "pitfall-v1"
MAX_CARDS = 40          # 一次最多入库多少张
WINDOW_EVENTS = 60      # 报错后多少条事件内找修法
WINDOW_CHARS = 1200


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------- 归一化：把可变的片段抹掉，留下可检索的骨架 ----------
_NOISE = [
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"), " "),
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "IP"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "IP"),
    (re.compile(r":\d{2,5}\b"), ":PORT"),
    (re.compile(r"(?:[\w.-]+/)+([\w.-]{2,})"), r"\1"),
    (re.compile(r"\b0x[0-9a-f]{4,}\b", re.I), "HEX"),
    (re.compile(r"\b[0-9a-f]{32,}\b", re.I), "HASH"),
    (re.compile(r"\b\d{3,}\b"), "N"),
]


def norm_sig(line, limit=140):
    s = (line or "").strip()
    s = re.sub(r"^\s*(?:\$|#|>)\s*", "", s)
    for rx, rep in _NOISE:
        s = rx.sub(rep, s)
    s = re.sub(r"\s+", " ", s).strip(" :：-—")
    return s[:limit]


def _err_line(text):
    """从一段文本里挑出最像「报错那一行」的一行（严格口径）。"""
    if not text:
        return None
    best, best_w = None, -1
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        if len(line) < 8 or len(line) > 400:
            continue
        if not ERR_HARD.search(line):
            continue
        w = 2
        # 真跑出来的输出里通常带命令提示符或 stderr
        if TOOLISH.search(line):
            w += 1
        # 中文散文多半是人在解释，不是报错本身
        cjk = len(CJK.findall(line)) / max(1, len(line))
        if cjk > 0.10:
            w -= 3
        if CODEY.search(line):
            w -= 3
        if "=" in line and not re.search(r"(?:command not found|No such file|Permission denied)", line):
            w -= 2
        if DOC.search(line):
            w -= 3
        if w > best_w:
            best, best_w = line, w
    return best if best_w >= 1 else None


FIXISH = re.compile(r"(`|docker|systemctl|curl|ssh|sed|grep|python|node |npm |apt|"
                    r"rm |mv |cp |chmod|iptables|sqlite|ALTER|CREATE|export |env |"
                    r"\.py|\.sh|\.js|/etc/|/opt/|:\\d+|--\w+)")


def _fix_snippet(text):
    if not text:
        return None
    m = FIX.search(text)
    if not m:
        return None
    start = max(0, m.start() - 60)
    frag = re.sub(r"\s+", " ", text[start:m.start() + 240]).strip()
    frag = re.sub(r"^(?:[，,。.、；;]|所以|然后|那么|因此|但是|不过)+", "", frag)
    if not FIXISH.search(frag):
        return None
    return frag[:240]


# ---------- 来源 1：spans 表（网关流量） ----------
def harvest_spans(con, days=30, dry=False, max_cards=MAX_CARDS):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = con.execute(
        "select span_id, conv_id, seq, role, quote, created_at from spans "
        "where created_at >= ? order by conv_id, seq", (since,)
    ).fetchall()
    return _harvest_rows(rows, con, dry, max_cards, label="spans")


# ---------- 来源 2：jsonl（DSH 原文） ----------
def _iter_jsonl(path):
    if path.endswith(".zstd"):
        p = subprocess.run(["zstd", "-dc", path], capture_output=True)
        if p.returncode != 0:
            return []
        raw = p.stdout.decode("utf-8", "replace")
    else:
        try:
            raw = open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            return []
    out = []
    for line in raw.splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _text_of(ev):
    """把一条 DSH 事件里所有可见文本拼出来（含 tool 结果）。"""
    d = ev.get("data") or {}
    buf = []

    def walk(x, depth=0):
        if depth > 6 or x is None:
            return
        if isinstance(x, str):
            buf.append(x)
        elif isinstance(x, dict):
            for k in ("text", "content", "stdout", "stderr", "output", "error", "message"):
                if k in x:
                    walk(x[k], depth + 1)
            for k, v in x.items():
                if k not in ("text", "content", "stdout", "stderr", "output", "error", "message"):
                    walk(v, depth + 1)
        elif isinstance(x, list):
            for it in x:
                walk(it, depth + 1)

    walk(d)
    walk(ev.get("error"))
    return "\n".join(x for x in buf if isinstance(x, str))


def harvest_jsonl(paths, con, days=30, dry=False, max_cards=MAX_CARDS):
    rows = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    for f in paths:
        seq = 0
        session = os.path.basename(f).split(".")[0]
        ts = None
        for ev in _iter_jsonl(f):
            seq += 1
            et = ev.get("type") or ""
            if et == "session":
                ts = (ev.get("createdAt") or "")[:19]
            if ts and ts < cutoff[:19]:
                continue
            if et not in ("tool/result", "assistant/message", "user/message", "step/end"):
                continue
            quote = _text_of(ev)[:4000]
            if not quote:
                continue
            role = "assistant" if et.startswith("assistant") else (
                "user" if et.startswith("user") else "tool")
            rows.append((session, session, seq, role, quote, ts or _now()))
    return _harvest_rows(rows, con, dry, max_cards, label="jsonl")


# ---------- 共用：配对 + 打分 + 入库 ----------
def _harvest_rows(rows, con, dry, max_cards, label="spans"):
    from . import pitfall as pf

    cands = []
    i = 0
    n = len(rows)
    while i < n:
        _sid, conv, seq, role, quote, created = rows[i]
        line = _err_line(quote)
        if not line:
            i += 1
            continue
        sig = norm_sig(line)
        if re.search(r"(No such file or directory|Permission denied)$", sig) and len(sig) < 24:
            i += 1
            continue
        if sig in ("Traceback (most recent call last)", "Error", "Exception"):
            i += 1
            continue
        fix = None
        chars = 0
        for j in range(i + 1, min(n, i + WINDOW_EVENTS)):
            if rows[j][1] != conv:
                break
            t = rows[j][4] or ""
            chars += len(t)
            if chars > WINDOW_CHARS:
                break
            fix = _fix_snippet(t)
            if fix:
                break
        score = 2
        if role == "tool":
            score += 2
        elif TOOLISH.search(quote):
            score += 1
        if fix:
            score += 1
        if DOC.search(line):
            score -= 3
        cands.append({
            "sig": sig, "line": line, "fix": fix, "score": score,
            "conv": conv, "seq": seq, "created": created, "role": role,
        })
        i += 1

    # 同签名出现 ≥2 次 +1
    from collections import Counter
    cnt = Counter(c["sig"] for c in cands)
    for c in cands:
        if cnt[c["sig"]] >= 2:
            c["score"] += 1

    seen = set()
    kept = []
    for c in sorted(cands, key=lambda x: -x["score"]):
        # 自动入库只收「有修法」或「明确 command not found 且被重复遇到」的
        has_fix = c["fix"] is not None
        repeated = cnt[c["sig"]] >= 2
        if c["score"] < 5 or c["sig"] in seen:
            continue
        if not (has_fix or ("command not found" in c["sig"] and repeated)):
            continue
            continue
        seen.add(c["sig"])
        kept.append(c)
        if len(kept) >= max_cards:
            break

    made = skipped = 0
    for c in kept:
        if con.execute(
            "select 1 from cards where extractor_version=? and topic=? limit 1",
            (CARD_MARK, c["sig"]),
        ).fetchone():
            skipped += 1
            continue
        body = "现象：%s" % c["line"]
        if c["fix"]:
            body += "\n修法（原文摘录）：%s" % c["fix"]
        body += "\n来源：%s %s#%s" % (label, c["conv"][:12], c["seq"])
        if dry:
            print("[%d] %s\n    %s\n" % (c["score"], c["sig"][:90], (c["fix"] or "—")[:90]))
            made += 1
            continue

        sid = "sp_pa_" + __import__("hashlib").sha1(
            (c["sig"] + str(c["seq"])).encode("utf-8")).hexdigest()[:16]
        con.execute(
            "insert or ignore into spans(span_id,conv_id,msg_id,seq,role,ts,char_start,char_end,quote,text_hash,created_at) "
            "values(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, "pitfallauto", 0, c["seq"], "assistant", c["created"],
             0, len(c["line"]), c["line"][:800],
             __import__("hashlib").sha1(c["line"].encode("utf-8")).hexdigest(), _now()),
        )
        card = {
            "status": "active" if c["score"] >= 5 else "proposal",
            "title": ("踩坑：" + c["sig"])[:80],
            "body": body[:2000],
            "kind": "event",
            "owner": "agent",
            "source_path": ("pitfallauto/%s" % label)[:200],
            "extractor_version": CARD_MARK,
            "topic": c["sig"],
        }
        evs = [{"span_id": sid, "role": "assistant", "quote": c["line"][:120],
                "char_start": 0, "char_end": min(120, len(c["line"])), "weight": 0.9}]
        cid, reason = __import__("memvault.store", fromlist=["x"]).ingest_card(
            con, card, evs, span_lookup=None)
        if not cid:
            skipped += 1
            print("  - 拒收(%s) %s" % (reason, c["sig"][:60]))
            continue
        try:
            from . import embed
            from . import store
            vec = embed.embed([card["title"] + " " + card["body"]])[0]
            store.put_vector(con, cid, vec)
        except Exception as e:
            print("  [warn] 向量失败：%s" % str(e)[:60])
        made += 1
        print("  + #%s %s" % (cid, c["sig"][:70]))

    con.commit()
    print("[%s] 候选 %d，入库 %d，已存在/失败 %d" % (label, len(cands), made, skipped))
    return made


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    days = 30
    for i, a in enumerate(args):
        if a == "--days" and i + 1 < len(args):
            days = int(args[i + 1])
    db = os.environ.get("MEMVAULT_DB") or "/opt/memvault/data/memvault.db"
    con = sqlite3.connect(db, timeout=30)

    if "--spans" in args:
        harvest_spans(con, days=days, dry=dry)
    elif "--jsonl" in args:
        tgt = args[args.index("--jsonl") + 1]
        files = []
        if os.path.isdir(tgt):
            for root, _d, fs in os.walk(tgt):
                for f in fs:
                    if f.endswith(".jsonl") or f.endswith(".jsonl.zstd"):
                        files.append(os.path.join(root, f))
        else:
            files = [tgt]
        print("[jsonl] %d 个文件" % len(files))
        harvest_jsonl(files, con, days=days, dry=dry)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
