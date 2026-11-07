"""用户画像：把散落在各处的「关于他」聚成一页。

不调模型，纯聚合。四个块：
  他是谁      → dreams（关于人的判断）
  他怎么做事  → procedures（遇到这类问题他就这么做）
  他偏好什么  → cards where kind='preference'
  正在做什么  → focus where status='open'
"""
import json
import sqlite3


def _rows(c, sql, args=()):
    try:
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    except Exception:
        return []


def collect(c, limit_dreams=20, limit_proc=40, limit_pref=60, limit_focus=20):
    """返回画像的全部素材。"""
    stats = {}
    for k, sql in (
        ("cards", "SELECT COUNT(*) FROM cards WHERE status='active'"),
        ("preferences", "SELECT COUNT(*) FROM cards WHERE kind='preference' AND status='active'"),
        ("dreams", "SELECT COUNT(*) FROM dreams"),
        ("procedures", "SELECT COUNT(*) FROM procedures"),
        ("tasks_open", "SELECT COUNT(*) FROM focus WHERE COALESCE(status,'open')='open'"),
        ("tasks_done", "SELECT COUNT(*) FROM focus WHERE status='done'"),
        ("summaries", "SELECT COUNT(*) FROM summaries"),
    ):
        try:
            stats[k] = c.execute(sql).fetchone()[0]
        except Exception:
            stats[k] = 0

    # ① 他是谁：关于人的梦（手写 + 自动），未合并的
    dreams = _rows(c, """
        SELECT id, slug, title, body, span, evidence, confidence, kind, status
        FROM dreams
        WHERE COALESCE(kind,'human') IN ('hand','human') AND COALESCE(status,'draft') != 'merged'
        ORDER BY CASE WHEN kind='hand' THEN 0 ELSE 1 END, COALESCE(at,'') DESC
        LIMIT ?""", (limit_dreams,))

    # ② 他怎么做事
    procs = _rows(c, """
        SELECT id, category, situation, action, result, evidence, status
        FROM procedures
        WHERE COALESCE(status,'draft') != 'rejected'
        ORDER BY category, id
        LIMIT ?""", (limit_proc,))

    # ③ 他偏好什么：按分类分组
    prefs = _rows(c, """
        SELECT id, title, body, category, topic
        FROM cards
        WHERE kind='preference' AND status='active'
        ORDER BY CASE WHEN category='其它' THEN 1 ELSE 0 END, category, COALESCE(occurred_at,'') DESC
        LIMIT ?""", (limit_pref,))

    # ④ 正在做什么
    focus = _rows(c, """
        SELECT conv_id, topic, open_items, last_ts, n_spans
        FROM focus
        WHERE COALESCE(status,'open')='open'
        ORDER BY last_ts DESC
        LIMIT ?""", (limit_focus,))

    # 解析 JSON 字段
    for d in dreams:
        try:
            d["evidence"] = json.loads(d.get("evidence") or "[]")
        except Exception:
            d["evidence"] = []
    for p in prefs:
        pass
    for f in focus:
        try:
            f["open_items"] = json.loads(f.get("open_items") or "[]")
        except Exception:
            f["open_items"] = []
    for x in procs:
        try:
            x["evidence"] = json.loads(x.get("evidence") or "[]")
        except Exception:
            x["evidence"] = []

    # 偏好按 category 归组
    by_cat = {}
    for p in prefs:
        by_cat.setdefault(p.get("category") or "其它", []).append(p)
    pref_groups = [{"category": k, "items": v} for k, v in
                   sorted(by_cat.items(), key=lambda kv: (kv[0] == "其它", -len(kv[1])))]

    # 程序按 category 归组
    by_cat2 = {}
    for p in procs:
        by_cat2.setdefault(p.get("category") or "其它", []).append(p)
    proc_groups = [{"category": k, "items": v} for k, v in
                   sorted(by_cat2.items(), key=lambda kv: (kv[0] == "其它", kv[0]))]

    return {"stats": stats, "dreams": dreams, "proc_groups": proc_groups,
            "pref_groups": pref_groups, "focus": focus,
            "built_at": None}


if __name__ == "__main__":
    from .dream import con
    r = collect(con())
    print("stats:", r["stats"])
    print("dreams:", len(r["dreams"]), "| proc groups:", len(r["proc_groups"]),
          "| pref groups:", len(r["pref_groups"]), "| focus:", len(r["focus"]))
