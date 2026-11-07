"""建库：一份完整 DDL，空库第一次连上就能跑。

为什么单独一个文件：这套 schema 是历年增量补出来的，store.SCHEMA 只覆盖 v1 那几张表，
spans / card_recall / learn_seen / providers 这些从没写进代码（只存在于作者那台机器的库里）。
本文件是从线上库的 sqlite_master 原样导出后再规范化的，所以它和真实库一致。

store.connect() 会调用 ensure()，因此任何新库都是完整的。
"""
import sqlite3

SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS _dream_kind_mig(k TEXT);

CREATE TABLE IF NOT EXISTS card_evidence (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id    INTEGER NOT NULL,
  span_id    TEXT NOT NULL,
  quote      TEXT NOT NULL,
  char_start INTEGER NOT NULL DEFAULT 0,
  char_end   INTEGER NOT NULL DEFAULT 0,
  role       TEXT,
  weight     REAL DEFAULT 1.0,
  created_at TEXT NOT NULL,
  UNIQUE(card_id, span_id)
);

CREATE TABLE IF NOT EXISTS card_recall(
  card_id    INTEGER NOT NULL,
  query_hash TEXT NOT NULL,
  at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cards(
  id            INTEGER PRIMARY KEY,
  content_hash  TEXT UNIQUE NOT NULL,
  title         TEXT,
  body          TEXT NOT NULL,
  kind          TEXT,
  topic         TEXT,
  source_path   TEXT,
  source_range  TEXT,
  created_at    TEXT,
  status        TEXT DEFAULT active
, schema_version INTEGER DEFAULT 1, extractor_version TEXT, supersedes INTEGER, updated_at TEXT, owner TEXT DEFAULT 'human', occurred_at TEXT, claim_type TEXT DEFAULT 'unknown', category TEXT, promoted_at TEXT, importance INTEGER DEFAULT 5, consolidate_of TEXT, related_card INTEGER, heat REAL DEFAULT 0, last_hit_at TEXT, pinned INTEGER DEFAULT 0, softened_at TEXT, body_orig TEXT, scope text not null default 'global', project_id text, hits INTEGER DEFAULT 0, last_seen TEXT);

CREATE VIRTUAL TABLE IF NOT EXISTS cards_fts USING fts5(
  title, body,
  tokenize=trigram
);

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

CREATE TABLE IF NOT EXISTS conv_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conv_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS dream_runs(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  at           TEXT NOT NULL,
  phase        TEXT NOT NULL,
  n_candidates INTEGER DEFAULT 0,
  n_promoted   INTEGER DEFAULT 0,
  n_merged     INTEGER DEFAULT 0,
  duration_ms  INTEGER,
  report       TEXT
);

CREATE TABLE IF NOT EXISTS dreams(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT UNIQUE,
  at          TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  evidence    TEXT,
  span        TEXT,
  confidence  REAL DEFAULT 0.5,
  status      TEXT DEFAULT 'draft',
  model       TEXT,
  note        TEXT
, kind TEXT DEFAULT 'human', source_query TEXT, consolidate_of TEXT);

CREATE TABLE IF NOT EXISTS edges(
        src INTEGER, dst INTEGER, kind TEXT, weight REAL,
        edge_type TEXT, label TEXT,
        PRIMARY KEY(src, dst, kind));

CREATE TABLE IF NOT EXISTS focus(
  id INTEGER PRIMARY KEY,
  conv_id TEXT UNIQUE NOT NULL,
  topic TEXT,
  open_items TEXT,
  span_ids TEXT,
  last_ts TEXT,
  n_spans INTEGER DEFAULT 0,
  model TEXT,
  built_at TEXT
, done_items TEXT, status TEXT DEFAULT 'open', done_at TEXT);

CREATE TABLE IF NOT EXISTS injections(
            id INTEGER PRIMARY KEY, at TEXT, kind TEXT, query TEXT,
            n_cards INTEGER, card_ids TEXT, chars INTEGER, ms INTEGER, has_self INTEGER);

CREATE TABLE IF NOT EXISTS learn_seen (
    span_id TEXT PRIMARY KEY,
    at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS map2d(
        card_id INTEGER PRIMARY KEY, x REAL, y REAL,
        px REAL, py REAL, ring REAL, angle REAL);

CREATE TABLE IF NOT EXISTS map3d(
    card_id INTEGER PRIMARY KEY,
    x REAL, y REAL, z REAL,
    tz REAL,          -- 时间在 [0,1] 的位置，便于前端做时间轴
    month TEXT
);

CREATE TABLE IF NOT EXISTS memory_tombstones(
  id     TEXT PRIMARY KEY,
  kind   TEXT,
  reason TEXT,
  at     TEXT
);

CREATE TABLE IF NOT EXISTS procedures(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE,
  category TEXT,
  situation TEXT,
  action TEXT,
  result TEXT,
  evidence TEXT,
  n_support INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft',
  model TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS provider_models(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          provider_id INTEGER NOT NULL,
          model_id TEXT NOT NULL,
          kind TEXT DEFAULT 'chat',
          created_at TEXT,
          UNIQUE(provider_id, model_id, kind)
        );

CREATE TABLE IF NOT EXISTS providers(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT UNIQUE NOT NULL,
          base_url TEXT NOT NULL,
          api_key TEXT,
          kind TEXT DEFAULT 'openai',
          enabled INTEGER DEFAULT 1,
          created_at TEXT
        );

CREATE TABLE IF NOT EXISTS scenes(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  slug        TEXT UNIQUE,
  at          TEXT NOT NULL,
  title       TEXT NOT NULL,
  narrative   TEXT NOT NULL,
  facts       TEXT,
  foresight   TEXT,
  span        TEXT,
  evidence    TEXT,
  status      TEXT DEFAULT 'draft',
  model       TEXT,
  source_query TEXT
, merged_into INTEGER);

CREATE TABLE IF NOT EXISTS self_notes (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  at       TEXT NOT NULL,
  body     TEXT NOT NULL UNIQUE,
  trigger  TEXT,
  weight   REAL DEFAULT 1.0,
  source   TEXT DEFAULT 'manual'
);

CREATE TABLE IF NOT EXISTS session_digest(
  conv_id TEXT PRIMARY KEY, ts_start TEXT, ts_end TEXT, msgs INTEGER,
  topic TEXT, open_items TEXT, stuck TEXT, digest TEXT, built_at TEXT);

CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS spans (
  span_id      TEXT PRIMARY KEY,
  conv_id      TEXT NOT NULL,
  msg_id       INTEGER NOT NULL,
  seq          INTEGER NOT NULL,
  role         TEXT NOT NULL,
  ts           TEXT,
  char_start   INTEGER NOT NULL DEFAULT 0,
  char_end     INTEGER NOT NULL,
  quote        TEXT NOT NULL,
  text_hash    TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spans_seen(
        conv_id TEXT NOT NULL,
        conv_log_id INTEGER NOT NULL,
        span_id TEXT NOT NULL,
        PRIMARY KEY(conv_id, conv_log_id));

CREATE TABLE IF NOT EXISTS summaries(
  id            INTEGER PRIMARY KEY,
  query         TEXT UNIQUE NOT NULL,
  content       TEXT,
  source_cards  TEXT,
  n_cards       INTEGER DEFAULT 0,
  refreshed_at  TEXT,
  is_stale      INTEGER DEFAULT 0,
  created_at    TEXT
);

CREATE TABLE IF NOT EXISTS thread_alias(
    alias       TEXT PRIMARY KEY,
    thread_id   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_state(
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL,
    state       TEXT NOT NULL,
    note        TEXT DEFAULT '',
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,                            -- NULL = 当前
    source_period TEXT
);

CREATE TABLE IF NOT EXISTS threads(
    thread_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'issue',   -- issue | progress
    state       TEXT NOT NULL DEFAULT 'active',  -- active | blocked | shifted | done | unknown
    note        TEXT DEFAULT '',
    cursor      TEXT DEFAULT NULL,               -- progress 类的游标（JSON）
    first_period TEXT,                           -- 最早出现在哪个 period
    last_period  TEXT,                           -- 最近出现在哪个 period
    valid_from  TEXT,                            -- 当前状态从什么时候开始
    last_touched TEXT,                           -- 最近一次被提到
    seen        INTEGER DEFAULT 1,               -- 被几个 period 提到过
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS vectors(
  card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
  dim     INTEGER NOT NULL,
  vec     BLOB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cards_kind  ON cards(kind);

CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);

CREATE INDEX IF NOT EXISTS idx_cards_time  ON cards(created_at);

CREATE INDEX IF NOT EXISTS idx_cards_topic ON cards(topic);

CREATE INDEX IF NOT EXISTS idx_ce_card ON card_evidence(card_id);

CREATE INDEX IF NOT EXISTS idx_ce_span ON card_evidence(span_id);

CREATE INDEX IF NOT EXISTS idx_chr_gran ON chronicles(granularity);

CREATE INDEX IF NOT EXISTS idx_chr_start ON chronicles(start_ts);

CREATE INDEX IF NOT EXISTS idx_conv_log ON conv_log(conv_id, id);

CREATE INDEX IF NOT EXISTS idx_cr_card ON card_recall(card_id);

CREATE INDEX IF NOT EXISTS idx_cr_hash ON card_recall(query_hash);

CREATE INDEX IF NOT EXISTS idx_dreams_at ON dreams(at);

CREATE INDEX IF NOT EXISTS idx_ev_role ON card_evidence(role);


CREATE INDEX IF NOT EXISTS idx_focus_ts ON focus(last_ts);

CREATE INDEX IF NOT EXISTS idx_inj_at ON injections(at);

CREATE INDEX IF NOT EXISTS idx_proc_cat ON procedures(category);

CREATE INDEX IF NOT EXISTS idx_proc_status ON procedures(status);

CREATE INDEX IF NOT EXISTS idx_rej_reason ON cards_rejected(reason);

CREATE INDEX IF NOT EXISTS idx_scenes_at ON scenes(at);

CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status);

CREATE INDEX IF NOT EXISTS idx_self_at ON self_notes(at);

CREATE INDEX IF NOT EXISTS idx_self_w  ON self_notes(weight DESC);

CREATE INDEX IF NOT EXISTS idx_spans_conv ON spans(conv_id, seq);

CREATE INDEX IF NOT EXISTS idx_spans_msg  ON spans(msg_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_spans_msgid ON spans(msg_id);

CREATE INDEX IF NOT EXISTS idx_sum_stale ON summaries(is_stale);

CREATE INDEX IF NOT EXISTS idx_threads_state ON threads(state);

CREATE INDEX IF NOT EXISTS idx_threads_touch ON threads(last_touched);

CREATE INDEX IF NOT EXISTS idx_tstate_tid ON thread_state(thread_id, valid_from);
"""


def ensure(con: sqlite3.Connection) -> None:
    """幂等地把库补到完整形态；老库只补缺的表/索引，不动数据。"""
    con.executescript(SCHEMA_SQL)
    con.commit()
    # token 与暂存队列：新装即具备（老库也补上）
    try:
        from . import auth as _a
        from . import qflush as _q
        _a.ensure(con)
        _q.ensure(con)
    except Exception:
        pass
