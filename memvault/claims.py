"""给卡片打 claim_type：fact / plan / question / unknown。

不做 LLM 判定（entity 方案已证明那条路走不通）。
只用规则抓明确信号；抓不到就留 unknown——unknown 不参与「待办」推断。
"""
import os, re, sqlite3, sys

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")

# 强信号：只在标题里出现才判 plan（这些词出现在标题里，几乎肯定是提议）
PLAN_TITLE = re.compile(
    r"(提议|打算|计划做|建议|要不要|待确认|待定|未定|待拍板|等你决定|"
    r"两个选择|三选一|备选|方案讨论|方案提议|待办)")
# 弱信号：正文里出现，且正文不长（长正文多是记录，不是提议）
PLAN_BODY = re.compile(
    r"(可以考虑|可以试试|你可以|建议你|我建议|下一步可以|"
    r"要不要|是否要|等你拍板|等你决定|由你决定)")
QUESTION = re.compile(r"[?？]\s*$|^(如何|怎么|为什么|是否|要不要|能不能)")
HEDGE = re.compile(r"(可能|也许|大概|或许|估计|似乎|应该是)")
DONE_STRONG = re.compile(r"(已完成|已经完成|已跑完|跑完了|搞定了|已解决|已修复|已上线|已落地|已接上|实测通过|验证通过)")


def classify(title, body):
    title = title or ""
    body = body or ""
    t = title + "\n" + body
    if QUESTION.search(title.strip()):
        return "question", "title?"
    if DONE_STRONG.search(t) and not PLAN_TITLE.search(title):
        return "fact", "done-word"
    if PLAN_TITLE.search(title):
        return "plan", "title-plan"
    if PLAN_BODY.search(body) and len(body) < 800:
        return "plan", "body-plan"
    return "unknown", "no-signal"


def main(write=False):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cols = [r[1] for r in con.execute("PRAGMA table_info(cards)")]
    if "claim_type" not in cols:
        con.execute("ALTER TABLE cards ADD COLUMN claim_type TEXT DEFAULT 'unknown'")
        con.commit()
        print("added column claim_type", flush=True)

    rows = con.execute(
        "SELECT id,title,body FROM cards WHERE status='active' "
        "AND (claim_type IS NULL OR claim_type='unknown')").fetchall()
    print(f"待分类 {len(rows)} 张", flush=True)

    from collections import Counter
    cnt = Counter()
    plan_ids, q_ids = [], []
    for r in rows:
        c, why = classify(r["title"], r["body"])
        cnt[c] += 1
        if c == "plan":
            plan_ids.append(r["id"])
        elif c == "question":
            q_ids.append(r["id"])

    print(dict(cnt), flush=True)

    if write:
        con.executemany("UPDATE cards SET claim_type='plan' WHERE id=?",
                        [(i,) for i in plan_ids])
        con.executemany("UPDATE cards SET claim_type='question' WHERE id=?",
                        [(i,) for i in q_ids])
        con.commit()
        print(f"写入 plan {len(plan_ids)} / question {len(q_ids)}", flush=True)

    # 抽样看 plan 判得准不准
    print("\nplan 抽样 12 张：", flush=True)
    for i in plan_ids[:12]:
        r = con.execute("SELECT title FROM cards WHERE id=?", (i,)).fetchone()
        print("  ", i, r["title"][:46], flush=True)


if __name__ == "__main__":
    main(write="--write" in sys.argv)
