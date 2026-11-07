"""memvault 真相层 —— cradle 的下游，只读它，从不回写。

五条契约写在 schema 注释里，不写在文档里（文档会烂，约束不会）：

  C1 单向流       本库只读 cradle；从不回写上游。任何"优化"= 重新 ingest。
  C2 不可变+版本   content_hash 定身份；schema_version / extractor_version 随卡走，
                   换抽取器不改旧卡，新旧并存。
  C3 证据闭合      每张卡写入时必须有 >=1 条 evidence，且 quote 必须能在 span 原文里
                   逐字命中（content[char_start:char_end] == quote）。
  C4 拒收可观测    不合契约的写入进 cards_rejected，带原因，永静默丢弃。
  C5 supersede     观点变迁用 supersedes 指向旧卡，旧卡改 status='superseded'，不删。
"""

import array
import hashlib
import sqlite3
import time
from pathlib import Path

SCHEMA_VERSION = 2

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

-- C1/C2: 卡片是派生物，不可变身份 + 版本
CREATE TABLE IF NOT EXISTS cards(
  id                INTEGER PRIMARY KEY,
  content_hash      TEXT UNIQUE NOT NULL,
  title             TEXT NOT NULL,
  body              TEXT NOT NULL,
  kind              TEXT NOT NULL,            -- definition | event | note
  owner             TEXT NOT NULL DEFAULT 'human',  -- human | agent | both
  topic             TEXT,
  schema_version    INTEGER NOT NULL DEFAULT 2,
  extractor_version TEXT,
  supersedes        INTEGER,                  -- C5: 被本卡取代的旧卡 id
  source_path       TEXT,
  source_range      TEXT,
  created_at        TEXT,
  updated_at        TEXT,
  status            TEXT DEFAULT 'active'      -- active | superseded | archived | partial
);

CREATE INDEX IF NOT EXISTS idx_cards_kind  ON cards(kind);
CREATE INDEX IF NOT EXISTS idx_cards_topic ON cards(topic);
CREATE INDEX IF NOT EXISTS idx_cards_time  ON cards(created_at);

CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
  title, body,
  content=cards, content_rowid=id,
  tokenize=trigram
);

-- C3: 证据。role 是硬字段，不是标签。
--   user / user_note  = 人说的，可支撑 definition
--   assistant         = 模型说的，不能单独支撑 definition
--   assistant_thinking= 模型当时的推理，只能当上下文
CREATE TABLE IF NOT EXISTS card_evidence(
  card_id    INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  span_id    TEXT NOT NULL,
  role       TEXT NOT NULL,
  quote      TEXT NOT NULL,
  char_start INTEGER,
  char_end   INTEGER,
  weight     REAL DEFAULT 1.0,
  created_at TEXT,
  PRIMARY KEY(card_id, span_id)
);

CREATE INDEX IF NOT EXISTS idx_ev_span ON card_evidence(span_id);
CREATE INDEX IF NOT EXISTS idx_ev_role ON card_evidence(role);

-- C4: 拒收记录。拒收率是这个库唯一的健康指标。
CREATE TABLE IF NOT EXISTS cards_rejected(
  id           INTEGER PRIMARY KEY,
  content_hash TEXT,
  title        TEXT,
  body         TEXT,
  kind         TEXT,
  reason       TEXT NOT NULL,
  detail       TEXT,
  rejected_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_rej_reason ON cards_rejected(reason);


CREATE TABLE IF NOT EXISTS chronicles(
  period_id    TEXT PRIMARY KEY,
  granularity  TEXT NOT NULL,          -- month | quarter | year | all
  start_ts     TEXT,
  end_ts       TEXT,
  msg_count    INTEGER,
  title        TEXT,
  summary      TEXT,
  threads      TEXT,                   -- JSON [{name,state,note}]
  entities     TEXT,                   -- JSON [..]
  turns        TEXT,                   -- JSON [{ts,what}]
  carries_over TEXT,                   -- JSON [..] 从上一期带过来的
  built_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_chr_gran ON chronicles(granularity);
CREATE INDEX IF NOT EXISTS idx_chr_start ON chronicles(start_ts);

CREATE TABLE IF NOT EXISTS vectors(
  card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
  dim     INTEGER NOT NULL,
  vec     BLOB NOT NULL
);
"""

KINDS = ("definition", "event", "note")
HUMAN_ROLES = ("user", "user_note")
ALL_ROLES = ("user", "user_note", "assistant", "assistant_thinking")

# 旧体系（memvault v1 / OpenViking）到新体系的映射。
# 旧 kind 是"内容类型"，新 kind 是"认知地位"：
#   definition = 这东西是什么（稳定陈述）
#   event      = 发生过什么（时间点/过程）
#   note       = 我当时的想法（非事实）
LEGACY_KIND_MAP = {
    "fact": "definition", "entity": "definition", "rule": "definition",
    "tool": "definition", "profile": "definition", "preference": "definition",
    "event": "event", "experience": "event", "mixed": "event",
    "archive": "event", "dream": "note",
}

def normalize_kind(kind: str) -> str:
    k = (kind or "").strip().lower()
    return k if k in KINDS else LEGACY_KIND_MAP.get(k, k)

BAD_TITLES = {"", "summary", "摘要", "untitled"}

CHATLOG_MARKERS = (
    "# ChatLog",
    "**我：**",
    "### 🧑 用户",
    "### 🤖 DeepSeek",
    "(Day) ChatLog:",
)


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    # 完整 DDL（从线上库导出）：spans / card_recall / learn_seen 等都在这
    from . import initdb
    initdb.ensure(con)
    _migrate(con)
    # 依赖 _migrate 补的列，必须在 migrate 之后建（v1 库直接执行会 no such column）
    con.execute("CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status)")
    con.commit()
    return con


def _migrate(con):
    """给旧库补列（幂等）。第一版 6867 张卡没有这些字段。"""
    have = {r["name"] for r in con.execute("PRAGMA table_info(cards)")}
    for col, decl in (
        ("schema_version", "INTEGER DEFAULT 1"),
        ("extractor_version", "TEXT"),
        ("supersedes", "INTEGER"),
        ("updated_at", "TEXT"),
        ("status", "TEXT DEFAULT 'active'"),
        ("owner", "TEXT DEFAULT 'human'"),
        ("claim_type", "TEXT DEFAULT 'unknown'"),
        ("occurred_at", "TEXT"),
        ("category", "TEXT"),
        ("promoted_at", "TEXT"),
        ("importance", "INTEGER DEFAULT 5"),
        ("consolidate_of", "TEXT"),
        ("related_card", "INTEGER"),
        ("heat", "REAL DEFAULT 0"),
        ("last_hit_at", "TEXT"),
        ("pinned", "INTEGER DEFAULT 0"),
        ("softened_at", "TEXT"),
        ("body_orig", "TEXT"),
        ("scope", "TEXT NOT NULL DEFAULT 'global'"),
        ("project_id", "TEXT"),
    ):
        if col not in have:
            con.execute(f"ALTER TABLE cards ADD COLUMN {col} {decl}")
    _fts_standalone(con)
    con.commit()


def _fts_standalone(con):
    """把 cards_fts 从「外部内容表」转成独立表。

    外部内容表只支持用 'delete' 命令维护；代码里那句
    `DELETE FROM cards_fts WHERE rowid=?` 在行还没进索引时直接报
    `database disk image is malformed`（首次写入必踩）。独立表没这问题。
    """
    row = con.execute("SELECT sql FROM sqlite_master WHERE name='cards_fts'").fetchone()
    if not row or not row[0] or "content=" not in row[0]:
        return
    con.executescript("""
        DROP TABLE IF EXISTS cards_fts;
        CREATE VIRTUAL TABLE cards_fts USING fts5(title, body, tokenize=trigram);
    """)
    con.execute("INSERT INTO cards_fts(rowid,title,body) SELECT id,title,body FROM cards")
    con.commit()


def content_hash(title: str, body: str) -> str:
    h = hashlib.sha256()
    h.update((title or "").strip().encode())
    h.update(b"\x00")
    h.update(body.strip().encode())
    return h.hexdigest()[:32]


# ---------------------------------------------------------------- C3/C4 校验门

def _reject(con, card, reason, detail=""):
    con.execute(
        """INSERT INTO cards_rejected(content_hash,title,body,kind,reason,detail,rejected_at)
           VALUES(?,?,?,?,?,?,?)""",
        (
            card.get("content_hash") or content_hash(card.get("title", ""), card.get("body", "")),
            card.get("title"),
            (card.get("body") or "")[:4000],
            card.get("kind"),
            reason,
            str(detail)[:500],
            time.strftime("%Y-%m-%dT%H:%M:%S"),
        ),
    )
    return None, reason


def _has_chatlog(body: str) -> bool:
    return any(m in body for m in CHATLOG_MARKERS)


def ingest_card(con, card: dict, evidences: list, span_lookup=None):
    """唯一的写入口。返回 (card_id, None) 或 (None, reason)。

    span_lookup(span_id) -> dict|None，至少含 {'quote': str, 'role': str}
    没有 span_lookup 时退化为只做结构校验（用于离线迁移旧卡）。
    """
    title = (card.get("title") or "").strip()
    body = (card.get("body") or "").strip()
    kind = normalize_kind(card.get("kind"))
    card["kind"] = kind
    evidences = evidences or []

    # 1 标题
    if title.lower() in BAD_TITLES:
        return _reject(con, card, "bad_title", title)

    # 2 正文是 chatlog 原文
    if _has_chatlog(body):
        return _reject(con, card, "chatlog_paste", body[:120])

    # 3 正文太短（一句话标签，没有内容）
    if len(body) < 15:
        return _reject(con, card, "body_too_short", f"{len(body)}c")

    # 4 kind 非法
    if kind not in KINDS:
        return _reject(con, card, "bad_kind", kind)

    # 5 证据存在性
    if not evidences:
        return _reject(con, card, "no_evidence", "")

    bad_role = [e.get("role") for e in evidences if e.get("role") not in ALL_ROLES]
    if bad_role:
        return _reject(con, card, "bad_evidence_role", bad_role)

    # 6 证据闭合：quote 必须逐字出现在 span 原文里
    if span_lookup is not None:
        for ev in evidences:
            span = span_lookup(ev.get("span_id"))
            if span is None:
                return _reject(con, card, "span_missing", ev.get("span_id"))
            q = (ev.get("quote") or "").strip()
            if not q:
                return _reject(con, card, "empty_quote", ev.get("span_id"))
            if q not in (span.get("quote") or ""):
                return _reject(con, card, "quote_not_in_span", q[:80])

    # 7 definition 卡：按 owner 分别要求证据来源
    #    人类定义卡必须有 user/user_note 支撑
    #    智能体定义卡必须有 assistant/thinking 支撑（但永不算"事实来源"）
    if kind == "definition":
        roles = {e.get("role") for e in evidences}
        owner = (card.get("owner") or "human").strip()
        human_ok = bool(roles & set(HUMAN_ROLES))
        agent_ok = bool(roles & {"assistant", "assistant_thinking"})
        if owner == "human" and not human_ok:
            return _reject(con, card, "definition_not_grounded_human", sorted(roles))
        if owner == "agent" and not agent_ok:
            return _reject(con, card, "definition_not_grounded_agent", sorted(roles))
        if owner == "both" and not (human_ok and agent_ok):
            return _reject(con, card, "definition_not_grounded_both", sorted(roles))

    # ---- 通过，写入 ----
    ch = card.get("content_hash") or content_hash(title, body)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    row = con.execute("SELECT id FROM cards WHERE content_hash=?", (ch,)).fetchone()
    if row:
        cid, created = row["id"], False
    else:
        cur = con.execute(
            """INSERT INTO cards(content_hash,title,body,kind,owner,topic,schema_version,
                                 extractor_version,supersedes,source_path,source_range,
                                 created_at,updated_at,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ch, title, body, kind,
                card.get("owner") or "human",
                card.get("topic"),
                card.get("schema_version") or SCHEMA_VERSION,
                card.get("extractor_version"),
                card.get("supersedes"),
                card.get("source_path"),
                card.get("source_range"),
                card.get("created_at") or now,
                now,
                card.get("status") or "active",
            ),
        )
        cid, created = cur.lastrowid, True
        con.execute("INSERT INTO cards_fts(rowid,title,body) VALUES(?,?,?)", (cid, title, body))

    # C5: supersede 生效后旧卡退役（不删）
    sup = card.get("supersedes")
    if sup:
        con.execute("UPDATE cards SET status='superseded', updated_at=? WHERE id=? AND status='active'", (now, sup))

    for ev in evidences:
        con.execute(
            """INSERT OR IGNORE INTO card_evidence
               (card_id,span_id,role,quote,char_start,char_end,weight,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                cid, ev["span_id"], ev["role"], ev["quote"],
                ev.get("char_start"), ev.get("char_end"),
                ev.get("weight", 1.0), now,
            ),
        )
    return cid, None


def rejection_stats(con) -> dict:
    rows = con.execute(
        "SELECT reason, COUNT(*) c FROM cards_rejected GROUP BY reason ORDER BY c DESC"
    ).fetchall()
    total = sum(r["c"] for r in rows)
    return {"total": total, "by_reason": {r["reason"]: r["c"] for r in rows}}


# ---------------------------------------------------------------- 向量

def upsert(con, card: dict):
    """写一张卡（幂等）。返回 (card_id, created)。

    server.py 的 /memlearn 用它。质检门在 ingest_card 里，
    这里是内部调用路径，仍走 content_hash 去重 + FTS 同步。
    """
    title = (card.get("title") or "").strip() or "(无标题)"
    body = (card.get("body") or card.get("content") or "").strip()
    kind = normalize_kind(card.get("kind") or card.get("type") or "fact")
    topic = card.get("topic") or ""
    owner = card.get("owner") or "both"
    h = content_hash(title, body)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")

    row = con.execute("SELECT id FROM cards WHERE content_hash=?", (h,)).fetchone()
    if row:
        cid = row[0]
        con.execute(
            "UPDATE cards SET kind=COALESCE(NULLIF(?,''),kind), "
            "topic=COALESCE(NULLIF(?,''),topic) WHERE id=?", (kind, topic, cid))
        con.commit()
        return cid, False

    cur = con.execute(
        """INSERT INTO cards(title, body, kind, topic, owner, content_hash,
                            source_path, created_at, status, schema_version,
                            extractor_version, claim_type)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (title, body, kind, topic, owner, h, card.get("source_path") or "memlearn",
         now, card.get("status") or "active", SCHEMA_VERSION,
         card.get("extractor_version") or "memlearn-v1",
         card.get("claim_type") or "unknown"))
    cid = cur.lastrowid
    con.execute("DELETE FROM cards_fts WHERE rowid=?", (cid,))
    con.execute("INSERT INTO cards_fts(rowid,title,body) VALUES(?,?,?)", (cid, title, body))
    con.commit()
    return cid, True


def put_vector(con, card_id: int, vec) -> None:
    a = array.array("f", vec)
    con.execute("INSERT OR REPLACE INTO vectors(card_id,dim,vec) VALUES(?,?,?)",
                (card_id, len(vec), a.tobytes()))


def load_vectors(con):
    import numpy as np
    rows = con.execute(
        """SELECT v.card_id, v.dim, v.vec FROM vectors v
           JOIN cards c ON c.id=v.card_id
           WHERE c.status='active'"""
    ).fetchall()
    if not rows:
        return [], None
    ids, mats = [], []
    for r in rows:
        a = array.array("f")
        a.frombytes(r["vec"])
        ids.append(r["card_id"])
        mats.append(np.frombuffer(a, dtype=np.float32).copy())
    return ids, np.vstack(mats)


def stats(con) -> dict:
    q = lambda s: con.execute(s).fetchone()[0]
    return {
        "cards": q("SELECT COUNT(*) FROM cards"),
        "active": q("SELECT COUNT(*) FROM cards WHERE status='active'"),
        "definition": q("SELECT COUNT(*) FROM cards WHERE kind='definition'"),
        "event": q("SELECT COUNT(*) FROM cards WHERE kind='event'"),
        "evidence": q("SELECT COUNT(*) FROM card_evidence"),
        "rejected": q("SELECT COUNT(*) FROM cards_rejected"),
        "vectors": q("SELECT COUNT(*) FROM vectors"),
        "owner_human": q("SELECT COUNT(*) FROM cards WHERE owner='human'"),
        "owner_agent": q("SELECT COUNT(*) FROM cards WHERE owner='agent'"),
        "owner_both": q("SELECT COUNT(*) FROM cards WHERE owner='both'"),
    }
