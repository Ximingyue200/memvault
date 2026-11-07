"""collect：conv_log → spans

把网关实时写入的 conv_log 转成 spans（带 char_start/char_end/quote/text_hash）。

msg_id 是**全局单调编号**：cradle 历史导入占了 1..54402，新对话从 MAX(msg_id)+1 起，
不是 conv_log.id（那样会撞 idx_spans_msgid 唯一索引）。

幂等：靠 (conv_id, 来源 conv_log id) 记在 text_hash 前缀里不够用，
改为维护 settings 'collect.cursor'（conv_log.id）+ spans 表 spans_seen 记录。
"""
import hashlib
import sqlite3
import sys
import time

MIN_LEN = 6   # 12 太狠："只回两个字：摇篮" 这种短句被静默丢掉
MAX_LEN = 8000
CURSOR_KEY = "collect.cursor"

# 网关转发时混进 conv_log 的运行时垃圾，直接跳过
NOISE_HEADS = (
    "OpenClaw runtime context:",
    "Runtime: agent=",
    "Conversation info:",
    "\u27e6openclaw:",
    "Background skill review.",
    "Reply with the single word",
    "Continue the current task from the existing transcript",
)

# 行内运行时尾巴，切掉只留正文
TAIL_MARKERS = ("\n\nRuntime: agent=", "\nRuntime: agent=",
                "\n\nConversation info:", "\nConversation info:")

_TS_PREFIX = __import__("re").compile(
    r"^\[[A-Z][a-z]{2} \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\]\s*")


def clean(text):
    """剥掉时间戳前缀和行内运行时尾巴。"""
    m = _TS_PREFIX.match(text)
    if m:
        text = text[m.end():]
    for mk in TAIL_MARKERS:
        i = text.find(mk)
        if i > 0:
            text = text[:i]
            break
    return text.strip()


def is_noise(text):
    head = text[:120].lstrip()
    for pfx in NOISE_HEADS:
        if head.startswith(pfx):
            return True
    return False


def is_junk(text):
    """纯测试语料：只有一句问句、没有上下文。"""
    t = text.strip()
    if len(t) < 40:
        return True
    if t in ("我上次提过的内存条是哪款？直接说型号", "我上次提过的内存条是哪款？"):
        return True
    return False


def _hash(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _ensure_seen(con):
    con.execute("""CREATE TABLE IF NOT EXISTS spans_seen(
        conv_id TEXT NOT NULL,
        conv_log_id INTEGER NOT NULL,
        span_id TEXT NOT NULL,
        PRIMARY KEY(conv_id, conv_log_id))""")


def _cursor(con):
    r = con.execute("SELECT v FROM settings WHERE k=?", (CURSOR_KEY,)).fetchone()
    return int(r[0]) if r and r[0] else 0


def _set_cursor(con, v):
    con.execute(
        "INSERT INTO settings(k,v,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
        (CURSOR_KEY, str(v), time.strftime("%Y-%m-%dT%H:%M:%S")))


def collect(con, limit=500, dry=False):
    """返回 (新增 span 数, 跳过数)。"""
    _ensure_seen(con)
    cur = _cursor(con)
    rows = con.execute(
        "SELECT id, conv_id, role, content, at FROM conv_log WHERE id > ? ORDER BY id LIMIT ?",
        (cur, limit)).fetchall()
    if not rows:
        return 0, 0

    seen = {(r[0], r[1]) for r in
            con.execute("SELECT conv_id, conv_log_id FROM spans_seen")}
    nxt = con.execute("SELECT COALESCE(MAX(msg_id),0) FROM spans").fetchone()[0]
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    added = skipped = 0

    for r in rows:
        cid_log, cid = r[0], r[1]   # r[0]=conv_log.id, r[1]=conv_id
        if (cid, cid_log) in seen:
            skipped += 1
            continue
        text = clean((r["content"] or ""))
        if len(text) < MIN_LEN or is_noise(text) or is_junk(text):
            skipped += 1
            continue
        text = text[:MAX_LEN]
        nxt += 1
        sid = "sp_" + hashlib.sha1(("%s:%s" % (cid, nxt)).encode()).hexdigest()[:16]
        try:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S+08:00",
                               time.localtime(int(r["at"])))
        except Exception:
            ts = r["at"]
        if not dry:
            con.execute(
                """INSERT OR IGNORE INTO spans
                   (span_id,conv_id,msg_id,seq,role,ts,char_start,char_end,
                    quote,text_hash,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, cid, nxt, 0, r["role"], ts, 0, len(text), text,
                 _hash(text), now))
            con.execute(
                "INSERT OR IGNORE INTO spans_seen(conv_id,conv_log_id,span_id) VALUES(?,?,?)",
                (cid, cid_log, sid))
        added += 1

    if not dry:
        _set_cursor(con, rows[-1][0])
        con.commit()
    return added, skipped


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "data/memvault.db"
    dry = "--dry" in sys.argv
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    a, s = collect(con, dry=dry)
    print("collect%s：新增 %d，跳过 %d" % ("(dry)" if dry else "", a, s))
