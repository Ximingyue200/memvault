"""把 occurred_at 补上：新卡从 spans.ts，旧卡从 source_path 目录名。"""
import re, sqlite3, sys

DB = "/opt/memvault/data/memvault.db"


def main(db=DB):
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode=WAL")

    # 1) 新卡：取它所有证据里最早的 span 时间
    n1 = con.execute("""
        UPDATE cards SET occurred_at = (
            SELECT MIN(s.ts) FROM card_evidence ce
            JOIN spans s ON s.span_id = ce.span_id
            WHERE ce.card_id = cards.id AND s.ts IS NOT NULL AND s.ts <> ''
        )
        WHERE (occurred_at IS NULL OR occurred_at = '')
          AND EXISTS (SELECT 1 FROM card_evidence ce WHERE ce.card_id = cards.id)
    """).rowcount
    con.commit()
    print(f"证据回填: {n1}")

    # 2) 旧卡：从 source_path 里的 YYYY/MM/DD 或 YYYY-MM-DD 提
    rows = con.execute("""
        SELECT id, source_path FROM cards
        WHERE (occurred_at IS NULL OR occurred_at = '') AND source_path IS NOT NULL
    """).fetchall()
    pat = re.compile(r"(20\d\d)[/-](\d\d)[/-](\d\d)")
    upd = []
    for cid, sp in rows:
        m = pat.search(sp or "")
        if m:
            upd.append((f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00+08:00", cid))
    con.executemany("UPDATE cards SET occurred_at=? WHERE id=?", upd)
    con.commit()
    print(f"路径回填: {len(upd)}")

    tot = con.execute("SELECT count(*) FROM cards").fetchone()[0]
    have = con.execute("SELECT count(*) FROM cards WHERE occurred_at IS NOT NULL AND occurred_at <> ''").fetchone()[0]
    print(f"覆盖: {have}/{tot} = {have/tot*100:.1f}%")

    print("--- 按年 ---")
    for y, n in con.execute("""
        SELECT substr(occurred_at,1,4) y, count(*) FROM cards
        WHERE occurred_at IS NOT NULL AND occurred_at <> ''
        GROUP BY y ORDER BY y
    """):
        print(f"  {y}  {n}")
    con.close()


if __name__ == "__main__":
    main(*sys.argv[1:])
