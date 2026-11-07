"""给旧卡填 owner。

旧卡没有 evidence（source_range 全空），所以只能按文本迹象推断，
并且统一记为推断值，不冒充事实：
  正文里出现「用户/他/你」-> human 倾向
  出现「我/助手/AI」-> agent 倾向
  两者都有 -> both
  判不出 -> human（默认：这些卡来自以用户提问为轴的对话）
"""
import sys, json, re, time
sys.path.insert(0, "/opt/memvault")
from memvault import store

con = store.connect("/opt/memvault/data/memvault.db")
H = re.compile(r"(用户|他觉得|他想|你的|他把|你要)")
A = re.compile(r"(助手|我判断|我建议|我的判断|AI |模型|我把它|我先|我发现)")
now = time.strftime("%Y-%m-%dT%H:%M:%S")
n = {"human":0,"agent":0,"both":0}
for r in con.execute("SELECT id,body,owner FROM cards WHERE extractor_version='legacy-v1'").fetchall():
    b = r["body"] or ""
    h, a = bool(H.search(b)), bool(A.search(b))
    owner = "both" if (h and a) else ("agent" if a else "human")
    con.execute("UPDATE cards SET owner=?, updated_at=? WHERE id=?", (owner, now, r["id"]))
    n[owner] += 1
con.commit()
print("backfilled:", n)
print("stats:", json.dumps(store.stats(con), ensure_ascii=False))
