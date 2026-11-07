"""思维导图数据：支持按多个维度组织。"""
import sqlite3

KIND_CN = {
    "definition": "定义", "fact": "事实", "event": "事件",
    "experience": "经验", "tool": "工具", "preference": "偏好",
    "note": "笔记", "rule": "规则",
}
OWNER_CN = {"human": "你说的", "agent": "我判断的", "both": "两边都有"}
CLAIM_CN = {"plan": "计划/未执行", "question": "开放问题", "fact": "已成立", "unknown": "普通"}


def _con(db):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    return c


def _label(dim, v):
    if dim == "kind":
        return KIND_CN.get(v, v or "未分类")
    if dim == "owner":
        return OWNER_CN.get(v, v or "未知")
    if dim == "claim":
        return CLAIM_CN.get(v, v or "未知")
    return v or "未知"


def _cat_tree(db, per=40):
    """一级（硬件/系统/开发/应用/其它）→ 二级（CPU/存储/…）→ topic → 卡片。"""
    con = _con(db)
    try:
        rows = con.execute(
            "SELECT COALESCE(NULLIF(category,''),'其它') c, "
            "COALESCE(NULLIF(topic,''),'(无主题)') t, count(*) n "
            "FROM cards WHERE status='active' GROUP BY c, t"
        ).fetchall()
    finally:
        con.close()

    tree = {}
    total = 0
    for r in rows:
        cat, topic, n = r["c"], r["t"], r["n"]
        total += n
        top, _, sub = cat.partition("/")
        sub = sub or "其它"
        tree.setdefault(top, {}).setdefault(sub, []).append((topic, n))

    children = []
    for top in sorted(tree, key=lambda k: -sum(n for subs in tree[k].values() for _, n in subs)):
        subs = tree[top]
        n_top = sum(n for s2 in subs.values() for _, n in s2)
        skids = []
        for sub in sorted(subs, key=lambda k: -sum(n for _, n in subs[k])):
            arr = sorted(subs[sub], key=lambda x: -x[1])
            n_sub = sum(n for _, n in arr)
            shown = arr[:per]
            tkids = [{"id": f"t:category:{top}/{sub}:{t}", "name": t, "n": n,
                      "category": f"{top}/{sub}", "topic": t} for t, n in shown]
            if len(arr) > len(shown):
                tkids.append({"id": f"t:category:{top}/{sub}:__other__",
                              "name": f"其它 {len(arr)-len(shown)} 个", "n":
                              sum(n for _, n in arr[len(shown):]),
                              "category": f"{top}/{sub}", "topic": "__other__"})
            skids.append({"id": f"s:category:{top}/{sub}", "name": sub, "n": n_sub,
                          "category": f"{top}/{sub}", "children": tkids})
        children.append({"id": f"g:category:{top}", "name": top, "n": n_top,
                         "cat1": top, "children": skids})
    return {"id": "root", "name": "cradle", "n": total, "by": "category",
            "children": children}


def _time_tree(db, per=40):
    """年 → 月 → 周 → 日 四层时间树。"""
    con = _con(db)
    try:
        rows = con.execute(
            "SELECT substr(COALESCE(NULLIF(occurred_at,''), created_at),1,10) d, "
            "count(*) n FROM cards WHERE status='active' "
            "AND COALESCE(NULLIF(occurred_at,''), created_at) IS NOT NULL "
            "GROUP BY d ORDER BY d"
        ).fetchall()
    finally:
        con.close()

    years = {}
    total = 0
    for r in rows:
        day = (r["d"] or "")[:10]
        if len(day) < 10:
            continue
        y, m, dd = day[:4], day[:7], day
        try:
            dom = int(day[8:10])
        except ValueError:
            continue
        wk = (dom - 1) // 7 + 1
        n = r["n"]
        total += n
        Y = years.setdefault(y, {"n": 0, "months": {}})
        Y["n"] += n
        M = Y["months"].setdefault(m, {"n": 0, "weeks": {}})
        M["n"] += n
        W = M["weeks"].setdefault(wk, {"n": 0, "days": []})
        W["n"] += n
        W["days"].append({"id": f"d:{day}", "name": day[5:], "n": n, "day": day})

    children = []
    for y in sorted(years, reverse=True):
        Y = years[y]
        mkids = []
        for m in sorted(Y["months"], reverse=True):
            M = Y["months"][m]
            wkids = []
            for wk in sorted(M["weeks"]):
                W = M["weeks"][wk]
                wkids.append({
                    "id": f"w:{m}:{wk}", "name": f"第{wk}周", "n": W["n"],
                    "month": m, "week": wk,
                    "children": sorted(W["days"], key=lambda x: -x["n"])[:12],
                })
            mkids.append({"id": f"m:{m}", "name": m, "n": M["n"],
                          "month": m, "children": wkids})
        children.append({"id": f"y:{y}", "name": y, "n": Y["n"],
                         "year": y, "children": mkids})
    return {"id": "root", "name": "cradle", "n": total, "by": "time",
            "children": children}


LAYERS = [
    ("客观知识", "definition,fact,tool", "#5aa9ff"),
    ("交互记录", "event,experience,note",           "#ffc857"),
    ("沉淀认知", "__inner__",                        "#c084fc"),
    ("知识摘要", "__chronicles__",                   "#5fd48a"),
]


def _layer_tree(db, per=40):
    """四层：客观知识 / 交互记录 / 沉淀认知 / 知识摘要。
    前两层来自 cards（按 category 再分），后两层来自 dreams+self_notes / chronicles。"""
    con = _con(db)
    try:
        rows = con.execute(
            "SELECT CASE WHEN kind IN ('definition','fact','tool') THEN '客观知识' "
            "            WHEN kind IN ('event','experience','note') THEN '交互记录' "
            "            WHEN kind='preference' THEN '沉淀认知' "
            "            ELSE '客观知识' END layer, "
            "  COALESCE(NULLIF(category,''),'其它') c, "
            "  COALESCE(NULLIF(topic,''),'(无主题)') t, count(*) n "
            "FROM cards WHERE status='active' GROUP BY layer, c, t"
        ).fetchall()
        dr = con.execute("SELECT COALESCE(kind,'human') k, count(*) n FROM dreams GROUP BY k").fetchall()
        sn = con.execute("SELECT count(*) n FROM self_notes").fetchone()["n"]
        ch = con.execute("SELECT period_id p, COALESCE(msg_count,0) m FROM chronicles "
                         "ORDER BY period_id DESC LIMIT 24").fetchall()
    finally:
        con.close()

    by_layer = {}
    for r in rows:
        by_layer.setdefault(r["layer"], []).append(r)
    dmap = {r["k"]: r["n"] for r in dr}

    children = []
    total = 0
    for name, _, color in LAYERS:
        if name in ("客观知识", "交互记录"):
            items = by_layer.get(name, [])
            if not items:
                continue
            n_g = sum(i["n"] for i in items)
            total += n_g
            cats = {}
            for i in items:
                cats[i["c"]] = cats.get(i["c"], 0) + i["n"]
            ranked = sorted(cats.items(), key=lambda kv: -kv[1])
            kids = [{"id": f"t:layer:{name}:{c}", "name": c, "n": n,
                     "group": name, "cat": c} for c, n in ranked[:per]]
            if len(ranked) > per:
                kids.append({"id": f"t:layer:{name}:__other__", "name": f"其它 {len(ranked)-per}",
                             "n": sum(n for _, n in ranked[per:]), "group": name, "other": True})
            children.append({"id": f"g:layer:{name}", "name": name, "group": name,
                             "n": n_g, "topics": len(ranked), "color": color, "children": kids})
        elif name == "沉淀认知":
            kids = []
            n_g = 0
            n_pref = sum(i["n"] for i in by_layer.get(name, []))
            if n_pref:
                kids.append({"id": "t:layer:pref", "name": f"偏好 {n_pref}",
                             "n": n_pref, "group": name, "dk": "pref"})
                n_g += n_pref
            for k, label in (("hand", "手写梦"), ("human", "人工梦"), ("machine", "机器梦")):
                if dmap.get(k):
                    kids.append({"id": f"t:layer:dream:{k}", "name": f"{label} {dmap[k]}",
                                 "n": dmap[k], "group": name, "dk": k})
                    n_g += dmap[k]
            if sn:
                kids.insert(0, {"id": "t:layer:note", "name": f"自我笔记 {sn}",
                                "n": sn, "group": name, "dk": "note"})
                n_g += sn
            if n_g:
                total += n_g
                children.append({"id": f"g:layer:{name}", "name": name, "group": name,
                                 "n": n_g, "topics": len(kids), "color": color, "children": kids})
        else:
            if not ch:
                continue
            n_g = len(ch)
            total += n_g
            kids = [{"id": f"t:layer:chron:{r['p']}", "name": r["p"], "n": r["m"],
                     "group": name, "period": r["p"]} for r in ch[:per]]
            children.append({"id": f"g:layer:{name}", "name": name, "group": name,
                             "n": n_g, "topics": len(ch), "color": color, "children": kids})

    return {"id": "root", "name": "cradle", "n": total, "by": "layer", "children": children}


def load(db, by="kind", per=40):
    """root → 一级分组 → 二级 topic。by 决定一级分组。"""
    if by == "source":
        expr = ("CASE WHEN COALESCE(extractor_version,'')='legacy-v1' "
                "OR COALESCE(extractor_version,'')='' THEN '旧卡(OpenViking)' "
                "ELSE '新提炼' END")
    elif by == "month":
        expr = "substr(COALESCE(NULLIF(occurred_at,''), created_at),1,7)"
    elif by == "year":
        expr = "substr(COALESCE(NULLIF(occurred_at,''), created_at),1,4)"
    elif by == "claim":
        expr = "claim_type"
    elif by == "category":
        expr = "COALESCE(NULLIF(category,''),'其它')"
    else:
        expr = by if by in ("kind", "owner") else "kind"

    sub = None
    if by == "time":
        return _time_tree(db, per)
    if by == "category":
        return _cat_tree(db, per)
    if by == "layer":
        return _layer_tree(db, per)

    con = _con(db)
    try:
        topic_expr = sub if sub else "topic"
        rows = con.execute(
            f"SELECT {expr} AS g, kind, {topic_expr} AS topic, count(*) n FROM cards "
            "WHERE status='active' "
            f"GROUP BY g, kind, topic"
        ).fetchall()
    finally:
        con.close()

    # 一级分组
    groups = {}
    for r in rows:
        g = r["g"]
        if g is None or str(g).strip() in ("", "None", "null"):
            continue
        groups.setdefault(g, []).append(r)

    children = []
    total = 0
    for g, items in groups.items():
        if not g or str(g).strip() in ("", "None", "null"):
            continue
        n_g = sum(i["n"] for i in items)
        total += n_g
        # 二级：topic 聚合（同一 topic 跨 kind 合并）
        topics = {}
        for i in items:
            key = i["topic"] or "__none__"
            topics[key] = topics.get(key, 0) + i["n"]
        ranked = sorted(topics.items(), key=lambda kv: -kv[1])
        head, tail = ranked[:per], ranked[per:]
        kids = []
        for t, n in head:
            if t == "__none__":
                continue
            kids.append({"id": f"t:{by}:{g}:{t}", "name": t, "n": n,
                         "group": g, "kind": (items[0]["kind"] if items else "")})
        if tail:
            kids.append({"id": f"t:{by}:{g}:__other__", "name": f"其它 {len(tail)}",
                         "n": sum(n for _, n in tail), "group": g, "other": True})
        kind_of = max(items, key=lambda i: i["n"])["kind"] if items else ""
        children.append({
            "id": f"g:{by}:{g}", "name": _label(by, g), "group": g,
            "kind": kind_of, "n": n_g, "topics": len(topics),
            "children": kids,
        })
    children.sort(key=lambda c: -c["n"])
    return {"id": "root", "name": "cradle", "n": total, "by": by,
            "children": children}


def cards_of(db, group, topic, by="kind", limit=60):
    con = _con(db)
    try:
        cond, args = [], []
        if by == "kind":
            cond.append("kind=?"); args.append(group)
        elif by == "month":
            cond.append("substr(COALESCE(NULLIF(occurred_at,''), created_at),1,7)=?")
            args.append(group)
        elif by == "year":
            cond.append("substr(COALESCE(NULLIF(occurred_at,''), created_at),1,4)=?")
            args.append(group)
        elif by == "owner":
            cond.append("owner=?"); args.append(group)
        elif by == "claim":
            cond.append("claim_type=?"); args.append(group)
        elif by == "time":
            cond.append("substr(COALESCE(NULLIF(occurred_at,''), created_at),1,4)=?")
            args.append(group)
        elif by == "category":
            cond.append("COALESCE(NULLIF(category,''),'其它')=?"); args.append(group)
        elif by == "layer":
            if group == "客观知识":
                cond.append("kind IN ('definition','fact','tool')")
            elif group == "沉淀认知":
                cond.append("kind='preference'")
            else:
                cond.append("kind IN ('event','experience','note')")
            cond.append("COALESCE(NULLIF(category,''),'其它')=?"); args.append(topic)
        elif by == "source":
            cond.append("(CASE WHEN COALESCE(extractor_version,'')='legacy-v1' "
                        "OR COALESCE(extractor_version,'')='' THEN '旧卡(OpenViking)' "
                        "ELSE '新提炼' END)=?")
            args.append(group)
        if topic and topic != "__other__":
            cond.append("topic=?"); args.append(topic)
        where = " AND ".join(cond) if cond else "1=1"
        rows = con.execute(
            f"SELECT id, title, kind, topic, created_at FROM cards "
            f"WHERE status='active' AND {where} ORDER BY id DESC LIMIT ?",
            [*args, limit],
        ).fetchall()
    finally:
        con.close()
    return [{"id": f"c:{r['id']}", "name": (r["title"] or "")[:46],
             "card_id": r["id"], "kind": r["kind"], "t": r["created_at"]}
            for r in rows]
