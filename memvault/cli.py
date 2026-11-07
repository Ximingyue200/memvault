"""memvault —— md 真相 + SQLite 索引 + bge-m3 向量 + RRF 融合。

用法:
    python -m memvault.cli init
    python -m memvault.cli import-md /path/to/memories
    python -m memvault.cli search "记忆卡抽象化"
    python -m memvault.cli stats
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import embed, search as search_mod, store

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.M)
MEMFIELDS_RE = re.compile(r"<!--\s*MEMORY_FIELDS.*?-->", re.S)

KIND_BY_DIR = {
    "entities": "entity",
    "tools": "tool",
    "events": "event",
    "preferences": "preference",
    "experiences": "experience",
    "dreams": "dream",
    "rules": "rule",
    "archive": "archive",
}


def parse_md(path: Path) -> dict | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    fm = {}
    m = FM_RE.match(raw)
    body = raw
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"').strip("'")
        body = raw[m.end():]
    body = MEMFIELDS_RE.sub("", body).strip()
    if not body:
        return None
    tm = TITLE_RE.search(body)
    title = tm.group(1).strip() if tm else (fm.get("title") or path.stem)
    if tm:
        body = body[tm.end():].strip() or body
    parts = path.parts
    kind = fm.get("card_type") or ""
    for p in parts:
        if p in KIND_BY_DIR:
            kind = kind or KIND_BY_DIR[p]
            break
    return {
        "title": title,
        "body": body,
        "kind": kind or None,
        "topic": parts[-2] if len(parts) >= 2 else None,
        "source_path": str(path),
        "created_at": fm.get("occurred_at") or fm.get("date") or None,
        "status": fm.get("status") or "active",
    }


def cmd_init(args):
    con = store.connect(DB)
    print(json.dumps(store.stats(con), ensure_ascii=False))
    con.close()


def cmd_import_md(args):
    root = Path(args.dir)
    files = sorted(root.rglob("*.md"))
    files = [f for f in files if not f.name.startswith(".")]
    print("files:", len(files))
    con = store.connect(DB)
    created = skipped = failed = 0
    batch, pending = [], []

    def flush():
        nonlocal created, pending
        if not pending:
            return
        vecs = embed.embed([p[1] for p in pending])
        for (cid, _), v in zip(pending, vecs):
            store.put_vector(con, cid, v)
        con.commit()
        created += len(pending)
        pending = []

    for i, f in enumerate(files, 1):
        try:
            card = parse_md(f)
            if not card:
                failed += 1
                continue
            cid, is_new = store.upsert(con, card)
            if is_new:
                pending.append((cid, (card["title"] + "\n" + card["body"])[:3000]))
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print("ERR", f, type(e).__name__, e)
        if len(pending) >= args.batch:
            flush()
            print("  embedded %d / %d" % (created, len(files)), flush=True)
    flush()
    con.commit()
    print(json.dumps({"created": created, "skipped": skipped, "failed": failed,
                      **store.stats(con)}, ensure_ascii=False))
    con.close()


def cmd_search(args):
    con = store.connect(DB)
    for r in search_mod.search(con, args.query, topk=args.topk, min_score=args.min_score):
        print("%.3f  [%s] %s" % (r["score"], r["kind"] or "-", r["title"]))
        print("       " + r["body"][:120].replace("\n", " "))
    con.close()


def cmd_stats(args):
    con = store.connect(DB)
    print(json.dumps(store.stats(con), ensure_ascii=False))
    for row in con.execute("SELECT kind, COUNT(*) c FROM cards GROUP BY kind ORDER BY c DESC LIMIT 12"):
        print("  %-12s %d" % (row["kind"], row["c"]))
    con.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="memvault")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    ip = sub.add_parser("import-md")
    ip.add_argument("dir")
    ip.add_argument("--batch", type=int, default=8)
    ip.set_defaults(func=cmd_import_md)

    sp = sub.add_parser("search")
    sp.add_argument("query")
    sp.add_argument("--topk", type=int, default=5)
    sp.add_argument("--min-score", type=float, default=0.0)
    sp.set_defaults(func=cmd_search)

    sub.add_parser("stats").set_defaults(func=cmd_stats)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
