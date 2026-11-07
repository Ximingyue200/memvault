"""第一版摇篮记忆库 - 旧卡过门 + 修 401 张坏标题 + kind 归位。

不做的事：不重新提炼、不改正文、不删任何东西。
只做三件：
  1. title 是 Summary/空 -> 从正文首行/首句提取真标题
  2. 旧 kind -> 新 kind（definition/event/note）
  3. 打上 extractor_version=legacy-v1, schema_version=2
"""
import sys, re, json, time
sys.path.insert(0, "/opt/memvault")
from memvault import store

DB = "/opt/memvault/data/memvault.db"
con = store.connect(DB)
print("before:", json.dumps(store.stats(con), ensure_ascii=False))

def derive_title(body: str) -> str:
    """从正文里找一个像标题的东西。"""
    for line in body.splitlines():
        s = line.strip().lstrip("#").strip()
        if not s: continue
        s = re.sub(r"^#+\s*", "", s)
        if s.lower() in ("summary", "摘要"): continue
        if len(s) >= 4:
            return s[:60]
    m = re.split(r"[。！？\n]", body.strip())
    for s in m:
        s = s.strip()
        if len(s) >= 4:
            return s[:60]
    return ""

rows = con.execute("SELECT id,title,body,kind FROM cards").fetchall()
fixed_title = fixed_kind = 0
now = time.strftime("%Y-%m-%dT%H:%M:%S")

for r in rows:
    new_title = r["title"]
    if (r["title"] or "").strip().lower() in store.BAD_TITLES:
        t = derive_title(r["body"] or "")
        if t:
            new_title = t
            fixed_title += 1
    nk = store.normalize_kind(r["kind"])
    if nk != r["kind"]:
        fixed_kind += 1
    con.execute(
        "UPDATE cards SET title=?, kind=?, schema_version=2, extractor_version=COALESCE(extractor_version,'legacy-v1'), updated_at=? WHERE id=?",
        (new_title, nk, now, r["id"]),
    )
con.commit()

# FTS 重建（title 变了）
con.execute("INSERT INTO cards_fts(cards_fts) VALUES('rebuild')")
con.commit()

print(f"fixed_title={fixed_title} fixed_kind={fixed_kind}")
print("after:", json.dumps(store.stats(con), ensure_ascii=False))
