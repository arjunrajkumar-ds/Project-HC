"""
test_train_recency.py — Train page recency, e1RM and previous-session history
=============================================================================
Cover for the three queries the redesigned /train page leans on:

  * get_exercise_recency()      — the "8d" / "21d" staleness readouts on the
                                  coverage strip,
  * get_e1rm_bests()            — the PR badge on the exercise card,
  * get_previous_session_sets() — the PREV column of the set table.

All three are pure reads, but all three compare dates, which is exactly where
this page has been bitten before (see docs/REVIEW-train-hardening.md §1.1 and
tests/test_train_session_day.py). The risks pinned here are:

  1. the 4 AM training-day boundary — a set logged at 2 AM belongs to the
     previous training day, so "days since" must not count it as today;
  2. legacy rows imported by import_history.py, which carry session_id IS NULL,
     a bare 'YYYY-MM-DD' recorded_at and sometimes sets > 1 — the oldest
     history there is, and precisely what "last time" should surface;
  3. Epley's divergence on high-rep work: 20 kg x 21 reps estimates a 34 kg
     single and would outrank a genuine heavy set if it were not capped.

Run:
    TZ=Australia/Melbourne python -m tests.test_train_recency
"""
import os
import sys
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta

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
            notes TEXT,
            reps_min INTEGER, reps_max INTEGER, sets INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_weighted INTEGER NOT NULL DEFAULT 1,
            tracking_type TEXT NOT NULL DEFAULT 'weight_reps',
            archived_at TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            exercise_class TEXT NOT NULL DEFAULT 'strength');
        CREATE TABLE gym_progression (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL,
            weight_kg REAL, sets INTEGER, reps INTEGER,
            successful INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER,
            duration_s INTEGER, distance_m INTEGER, setting TEXT);
        CREATE TABLE gym_functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            muscle_group TEXT NOT NULL, key TEXT NOT NULL,
            label TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE(muscle_group, key));
    """)
    conn.execute("INSERT INTO gym_exercises (id, name, tier, muscle_group, function) "
                 "VALUES (1, 'Bench', 1, 'chest', 'press')")
    conn.execute("INSERT INTO gym_exercises (id, name, tier, muscle_group, function) "
                 "VALUES (2, 'Curl', 3, 'arms', 'biceps')")
    conn.execute("INSERT INTO gym_exercises (id, name, tier, muscle_group, function) "
                 "VALUES (3, 'Never Done', 3, 'core', 'flexion')")
    conn.commit()
    gym_bank.DB_PATH = path
    return conn, path


def _utc(local_dt):
    """Local naive datetime -> the UTC wall-clock string SQLite would store."""
    return datetime.utcfromtimestamp(local_dt.timestamp()).strftime('%Y-%m-%d %H:%M:%S')


def _log(conn, ex, when_local, weight, reps, session_id=None, sets=1):
    conn.execute(
        "INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, "
        "recorded_at, session_id) VALUES (?, ?, ?, ?, ?, ?)",
        (ex, weight, sets, reps, _utc(when_local), session_id))
    conn.commit()


def main():
    conn, path = _mkdb()
    now = datetime.now()
    today = (now - timedelta(hours=gym_bank.ROLLOVER_HOUR)).date()

    # Three outings for exercise 1: eight days ago, three days ago, and today.
    eight = datetime.combine(today - timedelta(days=8), datetime.min.time()) + timedelta(hours=10)
    three = datetime.combine(today - timedelta(days=3), datetime.min.time()) + timedelta(hours=10)
    todayish = datetime.combine(today, datetime.min.time()) + timedelta(hours=10)

    _log(conn, 1, eight, 60.0, 8, session_id=1)
    _log(conn, 1, eight, 60.0, 7, session_id=1)
    _log(conn, 1, three, 62.5, 8, session_id=2)
    _log(conn, 1, three, 62.5, 8, session_id=2)
    _log(conn, 1, three, 62.5, 6, session_id=2)

    print("\n[recency] days since last performed")
    rec = gym_bank.get_exercise_recency()
    check("exercise logged 3 days ago reads days_since == 3",
          rec.get(1, {}).get('days_since') == 3)
    check("exercise never logged is absent from the map", 3 not in rec)

    print("\n[previous] the most recent PRIOR outing, not today's")
    _log(conn, 1, todayish, 65.0, 5, session_id=3)
    prev = gym_bank.get_previous_session_sets(1)
    check("previous outing is the 3-days-ago session, not today's",
          prev['day'] == (today - timedelta(days=3)).isoformat())
    check("previous outing reports days_since == 3", prev['days_since'] == 3)
    check("returns that session's 3 sets in order",
          [s['reps'] for s in prev['sets']] == [8, 8, 6])
    check("today's 65kg set is excluded",
          all(s['weight_kg'] != 65.0 for s in prev['sets']))

    print("\n[previous] recency now reads today, previous still reads back")
    rec2 = gym_bank.get_exercise_recency()
    check("after logging today, days_since == 0", rec2.get(1, {}).get('days_since') == 0)
    check("previous outing is unaffected by today's set",
          gym_bank.get_previous_session_sets(1)['day'] == prev['day'])

    print("\n[previous] exercise with no history at all")
    none = gym_bank.get_previous_session_sets(3)
    check("returns an empty, well-formed payload",
          none == {'day': None, 'days_since': None, 'sets': []})

    print("\n[legacy] import_history rows: session_id NULL, bare date, sets > 1")
    conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, "
                 "recorded_at, session_id) VALUES (2, 20.0, 3, 10, ?, NULL)",
                 ((today - timedelta(days=12)).isoformat(),))
    conn.commit()
    legacy = gym_bank.get_previous_session_sets(2)
    check("a bare YYYY-MM-DD row is found despite session_id IS NULL",
          legacy['day'] == (today - timedelta(days=12)).isoformat())
    check("a bare date is NOT shifted by 'localtime' (would read 11d or 13d)",
          legacy['days_since'] == 12)
    check("sets > 1 is expanded into 3 comparable set rows",
          len(legacy['sets']) == 3 and all(s['reps'] == 10 for s in legacy['sets']))

    print("\n[boundary] a 2 AM set belongs to the previous training day")
    conn.execute("DELETE FROM gym_progression WHERE exercise_id = 2")
    two_am = datetime.combine(today + timedelta(days=1), datetime.min.time()) + timedelta(hours=2)
    _log(conn, 2, two_am, 30.0, 10, session_id=9)
    rec3 = gym_bank.get_exercise_recency()
    check("2 AM tomorrow still counts as today's training day (days_since == 0)",
          rec3.get(2, {}).get('days_since') == 0)

    print("\n[e1rm] Epley, capped at 12 reps")
    conn.execute("DELETE FROM gym_progression WHERE exercise_id = 2")
    _log(conn, 2, three, 40.0, 5, session_id=2)      # 40 * (1+5/30)  = 46.67
    _log(conn, 2, three, 20.0, 21, session_id=2)     # 20 * (1+21/30) = 34.0, excluded
    conn.commit()
    bests = gym_bank.get_e1rm_bests()
    check("best e1RM is the heavy 5-rep set", abs(bests.get(2, 0) - 40 * (1 + 5 / 30.0)) < 0.01)
    _log(conn, 2, three, 20.0, 12, session_id=2)     # 20 * (1+12/30) = 28.0, at the cap
    check("a 12-rep set is inside the cap and still loses to the heavy single",
          abs(gym_bank.get_e1rm_bests().get(2, 0) - 40 * (1 + 5 / 30.0)) < 0.01)
    check("raising the cap lets the 21-rep set in",
          gym_bank.get_e1rm_bests(max_reps=30).get(2, 0) >= 34.0)
    check("bodyweight-only work is absent rather than zero", 3 not in gym_bank.get_e1rm_bests())

    print("\n[live] session payload carries the set ordinal and successful flag")
    conn.execute("INSERT INTO gym_sessions (id, performed_at) VALUES (77, ?)",
                 (_utc(todayish),))
    conn.execute("DELETE FROM gym_progression")
    conn.commit()
    _log(conn, 1, todayish, 60.0, 8, session_id=77)
    _log(conn, 1, todayish, 60.0, 7, session_id=77)
    _log(conn, 2, todayish, 20.0, 12, session_id=77)
    live = gym_bank.get_live_session_state()
    ns = [s['n'] for s in live['sets']]
    check("ordinals number per exercise, not across the session", ns == [1, 2, 1])
    check("successful is surfaced on every set",
          all('successful' in s for s in live['sets']))

    conn.close()
    os.unlink(path)

    print("\n" + "=" * 60)
    if FAILS:
        print("✗ %d check(s) failed:" % len(FAILS))
        for f in FAILS:
            print("   - " + f)
        return 1
    print("✓ All Train recency / e1RM / previous-session checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
