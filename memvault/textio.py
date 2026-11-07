#!/usr/bin/env python3
"""记忆库原始文本导出/导入。

导出格式（人可读，可回灌）：

    ================================================================
    CONV: <conv_id>  MSGS: <n>  FROM: <ts>  TO: <ts>
    ================================================================

    --- [2026-10-31T07:34:07] user ---
    正文...

    --- [2026-10-31T07:34:30] assistant ---
    正文...

导入：按同样格式解析，写回 conv_log（at 转 Unix 秒），再跑 collect 生成 spans。
"""
import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time

DB = "/opt/memvault/data/memvault.db"

HDR = re.compile(r"^CONV:\s*(\S+)\s+MSGS:\s*(\d+)\s+FROM:\s*(\S*)\s+TO:\s*(\S*)\s*$")
MSG = re.compile(r"^--- \[([^\]]*)\] (\w+) ---$")
SEP = "=" * 64


def export_text(db, out_path, conv_limit=None, min_len=0):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    q = ("SELECT conv_id, COUNT(*) n, MIN(ts) t0, MAX(ts) t1 "
         "FROM spans GROUP BY conv_id ORDER BY MAX(ts) DESC")
    rows = list(c.execute(q))
    if conv_limit:
        rows = rows[:conv_limit]

    wrote_conv = wrote_msg = wrote_bytes = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            msgs = list(c.execute(
                "SELECT ts, role, quote FROM spans WHERE conv_id=? "
                "AND LENGTH(quote)>=? ORDER BY seq, msg_id",
                (r["conv_id"], min_len)))
            if not msgs:
                continue
            f.write(SEP + "\n")
            f.write("CONV: %s  MSGS: %d  FROM: %s  TO: %s\n"
                    % (r["conv_id"], len(msgs), r["t0"] or "", r["t1"] or ""))
            f.write(SEP + "\n\n")
            wrote_conv += 1
            for m in msgs:
                f.write("--- [%s] %s ---\n%s\n\n"
                        % (m["ts"] or "", m["role"], (m["quote"] or "").rstrip()))
                wrote_msg += 1
    wrote_bytes = os.path.getsize(out_path)
    c.close()
    return {"convs": wrote_conv, "msgs": wrote_msg, "bytes": wrote_bytes,
            "path": out_path}


def import_text(db, in_path, conv_prefix="", dry=False):
    c = sqlite3.connect(db)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    cur_conv = None
    cur_ts = ""
    cur_role = None
    buf = []
    stats = {"convs": 0, "msgs": 0, "skipped_dup": 0, "bytes": 0}
    seen = set()

    def flush():
        nonlocal buf, cur_role, cur_ts, cur_conv
        if cur_conv is None or cur_role is None:
            buf, cur_role = [], None
            return
        body = "".join(buf).strip()
        role = cur_role          # 先存下来，下面 cur_role 会被清空
        buf, cur_role = [], None
        if not body:
            return
        if _is_noise(body):
            stats["skipped_noise"] = stats.get("skipped_noise", 0) + 1
            return
        at = _to_unix(cur_ts)
        h = hashlib.sha1((cur_conv + "|" + role + "|" + body).encode("utf-8")).hexdigest()
        if h in seen:
            stats["skipped_dup"] += 1
            return
        seen.add(h)
        if not dry:
            c.execute("INSERT INTO conv_log(conv_id, role, content, at) VALUES(?,?,?,?)",
                      (cur_conv, role, body[:20000], at))
        stats["msgs"] += 1

    with open(in_path, "r", encoding="utf-8") as f:
        for line in f:
            stats["bytes"] += len(line.encode("utf-8"))
            s = line.rstrip("\n")
            m = HDR.match(s)
            if m:
                flush()
                raw = m.group(1)
                # 已经带前缀的不重复加（回灌自己导出的文件不会变成 imp_imp_xxx）
                cur_conv = raw if (conv_prefix and raw.startswith(conv_prefix)) else conv_prefix + raw
                stats["convs"] += 1
                continue
            m = MSG.match(s)
            if m:
                flush()
                cur_ts = m.group(1)
                cur_role = m.group(2)
                continue
            if s.startswith(SEP):
                continue
            if cur_role is not None:
                buf.append(line)
    flush()
    if not dry:
        c.commit()
    c.close()
    return stats


def _is_noise(body):
    """过滤网关转发时混进 conv_log 的运行时前缀。"""
    head = body[:200].lstrip()
    for p in ("Conversation info:", "Runtime: agent=", "⟦openclaw:"):
        if head.startswith(p):
            return True
    # 整段只有前缀没有实际内容
    if len(body) < 60 and "openclaw" in body:
        return True
    return False


def _to_unix(ts):
    if not ts:
        return str(int(time.time()))
    if ts.isdigit():
        return ts
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return str(int(time.mktime(time.strptime(ts[:19], fmt[:len(fmt)]))))
        except Exception:
            continue
    return str(int(time.time()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("path")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--convs", type=int, default=0, help="只导出最近 N 个会话")
    ap.add_argument("--min-len", type=int, default=0, help="跳过短于此的消息")
    ap.add_argument("--prefix", default="imp_", help="导入时 conv_id 前缀")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    if a.action == "export":
        r = export_text(a.db, a.path, a.convs or None, a.min_len)
    else:
        r = import_text(a.db, a.path, a.prefix, a.dry)
    for k, v in r.items():
        print("%-10s %s" % (k, v))


if __name__ == "__main__":
    main()
