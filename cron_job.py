#!/usr/bin/env python3
"""统一定时任务入口。

用法：cron_job.py <task>
task ∈ collect | learn | dream | machine | focus | procedure | scene

开关关着就静默退出（exit 0），不报错，不写日志噪音。
dream 任务额外做「料够才跑」判断：新卡数或距上次天数不够就直接跳过。
每个任务跑完打一行 JSON 摘要。
"""
import json
import sqlite3
import sys
import time

DB = "/opt/memvault/data/memvault.db"

# task -> (开关 key, 开关默认值)
TASKS = {
    "collect":   ("collect.enabled", "1"),   # 纯 SQL，不花钱，默认开
    "learn":     ("learn.enabled",   "0"),   # 会调 LLM 花钱，默认关
    "dream":     ("dream.enabled",   "0"),
    "machine":   ("dream.enabled",   "0"),
    "focus":     ("focus.enabled",   "0"),
    "procedure": ("procedure.enabled", "0"),
    "scene":     ("scene.enabled",   "0"),
    "supersede": ("supersede.enabled", "1"),   # 纯规则，不调模型，默认开
}

TRUTHY = ("1", "true", "True", "on", "yes")


def _get(key, default=""):
    try:
        c = sqlite3.connect(DB, timeout=10)
        row = c.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        c.close()
        if row and row[0] is not None:
            return str(row[0]).strip()
    except Exception:
        pass
    return default


def _num(key, default):
    try:
        return float(_get(key, str(default)))
    except Exception:
        return float(default)


def _on(key, default="0"):
    v = _get(key, default)
    return v in TRUTHY


def _should_dream():
    """料够才跑。返回 (bool, reason)。"""
    min_fresh = int(_num("dream.min_fresh", 30))
    max_gap = _num("dream.max_gap_days", 7)
    try:
        c = sqlite3.connect(DB, timeout=10)
        row = c.execute("SELECT MAX(at) FROM dreams").fetchone()
        last = (row[0] if row and row[0] else "") or ""
        fresh = c.execute(
            "SELECT COUNT(*) FROM cards WHERE status='active' "
            "AND COALESCE(NULLIF(occurred_at,''), NULLIF(created_at,'')) > ?",
            (last,)).fetchone()[0] or 0
        if last:
            gap = c.execute(
                "SELECT julianday('now') - julianday(?)", (last,)).fetchone()[0]
            gap = float(gap or 0)
        else:
            gap = 999.0
        c.close()
    except Exception as e:
        return True, "check-failed(%s)" % type(e).__name__

    if fresh >= min_fresh:
        return True, "fresh=%d>=%d" % (fresh, min_fresh)
    if gap >= max_gap:
        return True, "gap=%.1fd>=%.0fd" % (gap, max_gap)
    return False, "fresh=%d<%d gap=%.1fd<%.0fd" % (fresh, min_fresh, gap, max_gap)


def _run(task):
    if task == "collect":
        from memvault import collect as m
        from memvault.store import connect
        c = connect(DB)
        try:
            added, skipped = m.collect(c, limit=500, dry=False)
            return {"added": added, "skipped": skipped}
        finally:
            c.close()

    if task == "learn":
        from memvault import learn as m
        from memvault.store import connect
        c = connect(DB)
        try:
            # 必须在 import extract 之前设 env（learn._env_first 的注释说明）
            m._env_first(c)
            saved, rejected, seen, err = m.learn(c, limit=120, dry=False)
            return {"saved": saved, "rejected": rejected, "seen": seen,
                    "err": (err or "")[:120]}
        finally:
            c.close()

    if task == "dream":
        from memvault import dream as m
        return m.run(apply=True, manual=False)

    if task == "machine":
        from memvault import dream as m
        return m.run_machine(apply=True)

    if task == "focus":
        from memvault import focus as m
        from memvault.store import connect
        c = connect(DB)
        try:
            return m.build_recent(c, hours=72, apply=True, limit=20)
        finally:
            c.close()

    if task == "procedure":
        from memvault import procedure as m
        try:
            return m.run(apply=True)
        except TypeError:
            from memvault.store import connect
            c = connect(DB)
            try:
                return m.run(c, apply=True)
            finally:
                c.close()

    if task == "scene":
        from memvault import scene as m
        return m.run(apply=True)

    if task == "supersede":
        from memvault import supersede as m
        out = m.main(dry=False, use_llm=False)   # 只用规则档，零成本
        return {"pairs": len(out or [])}

    raise SystemExit("unknown task: %s" % task)


def main():
    if len(sys.argv) < 2:
        print("usage: cron_job.py <%s>" % "|".join(TASKS), file=sys.stderr)
        return 2

    task = sys.argv[1]
    spec = TASKS.get(task)
    if not spec:
        print("unknown task: %s" % task, file=sys.stderr)
        return 2
    key, dflt = spec

    if not _on(key, dflt):
        print("%s disabled (%s=0), skip" % (task, key))
        return 0

    if task == "dream":
        ok, why = _should_dream()
        if not ok:
            print("dream skipped (not enough material): %s" % why)
            return 0
        print("dream run: %s" % why)

    if task == "machine":
        max_gap = _num("dream.machine_max_gap_days", 14)
        try:
            c = sqlite3.connect(DB, timeout=10)
            row = c.execute(
                "SELECT MAX(at) FROM dreams WHERE kind='machine'").fetchone()
            last = (row[0] if row and row[0] else "") or ""
            gap = c.execute(
                "SELECT julianday('now') - julianday(?)", (last,)).fetchone()[0] if last else 999.0
            c.close()
            gap = float(gap or 0)
        except Exception:
            gap = 999.0
        if gap < max_gap:
            print("machine skipped: gap=%.1fd<%.0fd" % (gap, max_gap))
            return 0
        print("machine run: gap=%.1fd>=%.0fd" % (gap, max_gap))

    t0 = time.time()
    try:
        r = _run(task) or {}
    except Exception as e:
        print("FAILED %s: %s: %s" % (task, type(e).__name__, str(e)[:300]), file=sys.stderr)
        return 1

    brief = {}
    for k, v in r.items():
        if k in ("dreams", "scenes", "items", "done", "list"):
            brief[k + "_n"] = len(v or [])
        elif isinstance(v, (str, int, float, bool)) or v is None:
            brief[k] = v
    print(json.dumps(brief, ensure_ascii=False)[:1500])
    print("%s done in %.1fs" % (task, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
