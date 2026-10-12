"""
test_train_session_day.py — Train page live-session day boundary
================================================================
Regression cover for the bug where the Train page silently lost a morning
workout.

gym_progression.recorded_at and gym_sessions.performed_at are written by
SQLite's CURRENT_TIMESTAMP, which is UTC. The live-session helpers used to
compare DATE(recorded_at) — a UTC date — against DATE('now','localtime'),
a local one. East of Greenwich those disagree for every set logged before
UTC midnight rolls over locally (before ~10 AM in Melbourne), so:

  * get_live_session_state() found no session -> the page rendered the
    "no sets yet today" picker while sets were being written,
  * get_or_create_today_gym_session() never matched -> a fresh session row
    per set,
  * get_today_tally() / undo_last_today() silently no-opped.

These tests pin the boundary itself rather than the wall clock: they insert
rows at known UTC instants and assert which training day each lands on,
under a fixed TZ.

Run:
    TZ=Australia/Melbourne python -m tests.test_train_session_day
"""
import os
import sys
import sqlite3
import tempfile
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# Pin the zone BEFORE sqlite resolves 'localtime'.
os.environ['TZ'] = os.environ.get('TRAIN_TEST_TZ', 'Australia/Melbourne')
try:
    time.tzset()
except AttributeError:      # Windows
    pass

from gymtracker import gym_bank  # noqa: E402

FAILS = []


def check(label, cond):
    print(("  ok  — " if cond else "  FAIL — ") + label)
    if not cond:
        FAILS.append(label)


def _mkdb():
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE gym_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            performed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished INTEGER NOT NULL DEFAULT 0,
            ended_at TEXT);
        CREATE TABLE gym_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            tier INTEGER NOT NULL,
            muscle_group TEXT NOT NULL,
            function TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            engagement TEXT NOT NULL DEFAULT '{}',
            notes TEXT, reps_min INTEGER, reps_max INTEGER, sets INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_weighted INTEGER NOT NULL DEFAULT 1,
            tracking_type TEXT NOT NULL DEFAULT 'weight_reps',
            archived_at TEXT, sort_order INTEGER NOT NULL DEFAULT 0,
            exercise_class TEXT NOT NULL DEFAULT 'strength');
        CREATE TABLE gym_functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            muscle_group TEXT NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0, UNIQUE(muscle_group, key));
        CREATE TABLE gym_progression (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL REFERENCES gym_exercises(id) ON DELETE CASCADE,
            weight_kg REAL, sets INTEGER, reps INTEGER,
            successful INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER REFERENCES gym_sessions(id) ON DELETE SET NULL,
            duration_s INTEGER, distance_m INTEGER, setting TEXT);
        INSERT INTO gym_functions (muscle_group, key, label) VALUES ('chest','press','Press');
        INSERT INTO gym_exercises (name, tier, muscle_group, function)
            VALUES ('Bench Press', 1, 'chest', 'press');
    """)
    conn.commit()
    conn.close()
    return path


def training_day_of(conn, ts):
    """What training day does this stored timestamp fall on?"""
    expr = gym_bank._training_day('?')
    return conn.execute(
        "SELECT %s AS d" % expr, (ts,) * expr.count('?')).fetchone()[0]


def main():
    path = _mkdb()
    gym_bank.DB_PATH = path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    print("\n[train] training-day boundary (TZ=%s)" % os.environ['TZ'])

    # 07:30 Melbourne on 2 Oct is 21:30 UTC on 1 Oct. The old code read that
    # as "1 Oct" and so never matched the local today.
    check("a 07:30 local set belongs to that local day, not the UTC one",
          training_day_of(conn, '2026-10-01 21:30:00') == '2026-10-02')

    # 22:00 Melbourne on 2 Oct is 12:00 UTC the same day — the evening case
    # that happened to work before, and must keep working.
    check("a 22:00 local set stays on the same local day",
          training_day_of(conn, '2026-10-02 12:00:00') == '2026-10-02')

    # 01:00 Melbourne on 3 Oct (15:00 UTC 2 Oct) is still the 2 Oct session —
    # that is what ROLLOVER_HOUR is for.
    check("a 01:00 local finish still counts as the previous training day",
          training_day_of(conn, '2026-10-02 15:00:00') == '2026-10-02')

    # 05:00 Melbourne on 3 Oct (19:00 UTC 2 Oct) is past the 4 AM roll-over.
    check("a 05:00 local start is a new training day",
          training_day_of(conn, '2026-10-02 19:00:00') == '2026-10-03')

    # Legacy import_history rows store a bare local date with no time, so
    # 'localtime' must not be applied to them.
    check("a date-only legacy row keeps its own date",
          training_day_of(conn, '2026-07-14') == '2026-07-14')

    print("\n[train] live session survives a morning log")
    # Simulate 07:30 local: one session + one set written with a UTC stamp
    # whose UTC date is yesterday.
    conn.execute("INSERT INTO gym_sessions (id, performed_at, finished) VALUES (1, ?, 0)",
                 ('2026-10-01 21:30:00',))
    conn.execute("INSERT INTO gym_progression (exercise_id, session_id, weight_kg, sets, reps, recorded_at) "
                 "VALUES (1, 1, 60, 1, 5, ?)", ('2026-10-01 21:35:00',))
    conn.commit()

    today_expr = gym_bank._training_day('performed_at')
    same_day = conn.execute(
        "SELECT COUNT(*) FROM gym_sessions WHERE %s = ?" % today_expr,
        ('2026-10-02',)).fetchone()[0]
    check("the morning session resolves under its local training day", same_day == 1)

    # And the same session is NOT claimed by the following day.
    next_day = conn.execute(
        "SELECT COUNT(*) FROM gym_sessions WHERE %s = ?" % today_expr,
        ('2026-10-03',)).fetchone()[0]
    check("it is not also claimed by the next training day", next_day == 0)

    print("\n[train] one session per training day")
    first = gym_bank.get_or_create_today_gym_session()
    second = gym_bank.get_or_create_today_gym_session()
    check("repeat calls reuse the open session rather than opening a new one",
          first == second)
    total = conn.execute("SELECT COUNT(*) FROM gym_sessions").fetchone()[0]
    check("no runaway session rows", total <= 2)

    print("\n[train] undo is scoped to its session")
    sid = gym_bank.get_or_create_today_gym_session()
    gym_bank.log_gym_set(1, 60, 5, session_id=sid)
    gym_bank.log_gym_set(1, 62.5, 5, session_id=sid)
    before = gym_bank.get_today_tally(1)['sets']
    check("today's tally sees sets logged just now", before >= 2)
    check("undo removes one", gym_bank.undo_last_today(1, session_id=sid) is True)
    after = gym_bank.get_today_tally(1)['sets']
    check("the tally drops by exactly one", after == before - 1)
    check("undo scoped to an unrelated session removes nothing",
          gym_bank.undo_last_today(1, session_id=999999) is False)

    print("\n[train] roll-over stamps only a genuinely stale session")
    conn.execute("UPDATE gym_sessions SET finished = 0")
    conn.commit()
    gym_bank.apply_session_rollover()
    open_now = conn.execute(
        "SELECT COUNT(*) FROM gym_sessions WHERE finished = 0").fetchone()[0]
    check("today's session is left open by the roll-over", open_now >= 1)

    conn.close()
    os.unlink(path)

    print("\n" + "=" * 60)
    if FAILS:
        print("✗ %d check(s) failed:" % len(FAILS))
        for f in FAILS:
            print("   - " + f)
        return 1
    print("✓ All Train session-day checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
