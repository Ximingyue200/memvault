"""从 spans.ts 重算 occurred_at。

旧回填按 source_path 的 md 文件日期取，但一个 span 可能被多个 md 引用
（不同时段提炼的卡引用了同一段对话），于是同一段对话被填了不同日期。
实测 741 个 span 有冲突，最严重的一个 span 派生 23 张卡、10 种日期。

真值来自 spans.ts（每条消息的真实时间戳，53911 条里 29022 有值）。
规则：
  1. 一张卡有多条证据时，取最早的那条 span 的 ts；
  2. 优先取带具体时分秒的（spans.ts 里有 60% 是 00:00:00，那是估算的日期不是真时间）；
  3. 同 span 的卡必然拿到同一个值（同一条 SQL 算出来）。

用法：
  python3 -m memvault.fix_occurred          # 只看会改多少
  python3 -m memvault.fix_occurred --apply  # 真写
"""
import os
import sys

from .store import connect

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")


SQL_CALC = """
SELECT ce.card_id,
       MIN(CASE WHEN s.ts LIKE '%00:00:00%' THEN NULL ELSE s.ts END) AS best_ts,
       MIN(s.ts) AS any_ts
FROM card_evidence ce
JOIN spans s ON s.span_id = ce.span_id
WHERE s.ts IS NOT NULL AND s.ts <> ''
GROUP BY ce.card_id
"""


def run(apply=False):
    c = connect(DB)
    rows = c.execute(SQL_CALC).fetchall()
    fixed = skipped = 0
    samples = []
    for r in rows:
        card_id = r["card_id"]
        # 有精确时间就用户精确时间，否则退回任意 ts（可能是 00:00:00 的估算日）
        ts = r["best_ts"] or r["any_ts"]
        if not ts:
            skipped += 1
            continue
        cur = c.execute("SELECT occurred_at, title FROM cards WHERE id=?",
                        (card_id,)).fetchone()
        if not cur:
            continue
        old = cur["occurred_at"] or ""
        # 只改「差的」：空、或与真值日期不同
        if old[:10] == ts[:10]:
            skipped += 1
            continue
        fixed += 1
        if len(samples) < 12:
            samples.append((card_id, (cur["title"] or "")[:34], old[:10] or "-", ts[:10]))
        if apply:
            c.execute("UPDATE cards SET occurred_at=? WHERE id=?", (ts, card_id))
    if apply:
        c.commit()

    print("扫描 %d 张有证据的卡" % len(rows))
    print("需修正 %d / 跳过 %d" % (fixed, skipped))
    for s in samples:
        print("  #%s %-34s %s -> %s" % s)

    # 复查：同 span 的卡时间是否还有冲突
    conflict = c.execute("""
        SELECT COUNT(*) FROM (
          SELECT ce.span_id FROM card_evidence ce
          JOIN cards c ON c.id = ce.card_id
          WHERE c.occurred_at IS NOT NULL AND c.occurred_at <> ''
          GROUP BY ce.span_id HAVING COUNT(DISTINCT substr(c.occurred_at,1,10)) > 1
        )""").fetchone()[0]
    print("同 span 时间冲突组：%d" % conflict)
    c.close()
    return {"scanned": len(rows), "fixed": fixed, "skipped": skipped,
            "conflict": conflict, "apply": apply}


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
