#!/usr/bin/env python3
"""定时软化：先看开关，关着就直接退出。"""
import json, sqlite3, sys, urllib.request

DB = "/opt/memvault/data/memvault.db"
API = "http://127.0.0.1:4180/admin/memory-mechanism/soften"


def main():
    c = sqlite3.connect(DB)
    row = c.execute(
        "SELECT v FROM settings WHERE k='memory.soften_enabled'"
    ).fetchone()
    enabled = (row[0] if row else "0")
    if str(enabled).strip() not in ("1", "true", "True", "on"):
        print("soften disabled (memory.soften_enabled=%s), skip" % enabled)
        return 0

    req = urllib.request.Request(
        API,
        data=json.dumps({"limit": 200}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        body = urllib.request.urlopen(req, timeout=1200).read().decode()
        d = json.loads(body)
        print("candidates=%s applied=%s" % (d.get("candidates"), d.get("applied")))
        for x in (d.get("done") or [])[:20]:
            print("  %s  %s -> %s" % (x.get("title", "")[:40], x.get("before"), x.get("after")))
        return 0
    except Exception as e:
        print("soften failed: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
