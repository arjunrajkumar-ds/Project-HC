"""
test_train_stage.py — stage-bar segment tagging (Feature 01)
============================================================
docs/HANDOFF-stage-bar-recap-route.md §4.1. Every live set is tagged with the
stage bar segment it belongs to so the client derivation (stageState) stays a
pure function of the set list:

  * tier 1 (any muscle)              -> 't1'   (BB OHP / heavy DB Lateral Raise
                                                are tier-1 shoulders => T1, not T3)
  * tier 2/3 chest|back|legs         -> 't2'   (the optional compound slot)
  * tier 2/3 shoulders|arms|core     -> 't3:sh' | 't3:ar' | 't3:co'
  * tier 4 (Fa Jin), cardio, stability, uncategorised, unknown -> None

get_live_session_state() carries that `stage` on every set, plus a top-level
`finished`, for today AND for a back-dated day.

Run:
    TZ=Australia/Melbourne python -m tests.test_train_stage
"""
import os
import sys
import sqlite3
import tempfile
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

os.environ['TZ'] = os.environ.get('TRAIN_TEST_TZ', 'Australia/Melbourne')
try:
    time.tzset()
except AttributeError:
    pass

_fd, _DB = tempfile.mkstemp(suffix='.db')
os.close(_fd)
os.environ['GYMTRACKER_DB'] = _DB

from gymtracker import gym_bank          # noqa: E402
gym_bank.DB_PATH = _DB
from gymtracker import database          # noqa: E402
database.DB_PATH = _DB                   # nothing here may reach tracker.db

FAILS = []


def check(label, cond):
    print(("  ok  — " if cond else "  FAIL — ") + label)
    if not cond:
        FAILS.append(label)


# name, tier, muscle_group, expected stage
EXS = [
    ('BB Overhead Press', 1, 'shoulders', 't1'),
    ('BB Squat', 1, 'legs', 't1'),
    ('DB Row', 2, 'back', 't2'),
    ('Lat Pulldown', 3, 'back', 't2'),
    ('DB Shoulder Press', 2, 'shoulders', 't3:sh'),
    ('DB Skullcrusher', 2, 'arms', 't3:ar'),
    ('Ab Wheel', 2, 'core', 't3:co'),
    ('Fa Jin : Pec Deck', 4, 'chest', None),
    ('Rowing', 3, 'cardio', None),
    ('Cable Bicep Curl', 2, 'uncategorised', None),
]


def _mkdb():
    conn = sqlite3.connect(_DB)
    conn.executescript("""
        CREATE TABLE gym_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            performed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished INTEGER NOT NULL DEFAULT 0,
            ended_at TEXT);
        CREATE TABLE gym_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            tier INTEGER NOT NULL, muscle_group TEXT NOT NULL, function TEXT,
            is_enabled INTEGER NOT NULL DEFAULT 1, engagement TEXT NOT NULL DEFAULT '{}',
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
            id INTEGER PRIMARY KEY AUTOINCREMENT, exercise_id INTEGER NOT NULL,
            weight_kg REAL, sets INTEGER, reps INTEGER,
            successful INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER, duration_s INTEGER, distance_m INTEGER, setting TEXT);
    """)
    ids = {}
    for name, tier, mg, _stage in EXS:
        cur = conn.execute(
            "INSERT INTO gym_exercises (name, tier, muscle_group, tracking_type) VALUES (?, ?, ?, ?)",
            (name, tier, mg, 'cardio' if mg == 'cardio' else 'weight_reps'))
        ids[name] = cur.lastrowid
    conn.commit()
    conn.close()
    return ids


def main():
    ids = _mkdb()

    print("\n[_stage_for] tier + muscle group -> segment")
    for name, tier, mg, expected in EXS:
        got = gym_bank._stage_for({'tier': tier, 'muscle_group': mg})
        check("%s (%s, %s) -> %r" % (name, tier, mg, expected), got == expected)
    check("unknown meta -> None", gym_bank._stage_for({}) is None)

    print("\n[live-session] every set carries its stage + session finished flag")
    today = sqlite3.connect(_DB).execute("SELECT " + gym_bank.TODAY_SQL).fetchone()[0]
    yday = sqlite3.connect(_DB).execute("SELECT DATE(?, '-1 day')", (today,)).fetchone()[0]

    s_today = gym_bank.get_or_create_today_gym_session()
    gym_bank.log_gym_set(ids['BB Squat'], 100, 5, session_id=s_today)                 # t1
    gym_bank.log_gym_set(ids['DB Row'], 40, 8, session_id=s_today)                    # t2
    gym_bank.log_gym_set(ids['DB Skullcrusher'], 30, 8, session_id=s_today)          # t3:ar

    live = gym_bank.get_live_session_state()
    check("live has a top-level finished flag", 'finished' in live)
    check("an open session reports finished False", live['finished'] is False)
    stages = {s['exercise_name']: s['stage'] for s in live['sets']}
    check("every set dict carries a 'stage' key", all('stage' in s for s in live['sets']))
    check("BB Squat -> t1", stages.get('BB Squat') == 't1')
    check("DB Row -> t2", stages.get('DB Row') == 't2')
    check("DB Skullcrusher -> t3:ar", stages.get('DB Skullcrusher') == 't3:ar')

    print("\n[live-session] a back-dated day carries stage + finished too")
    s_y = gym_bank.get_or_create_today_gym_session(yday)
    gym_bank.log_gym_set(ids['BB Overhead Press'], 50, 5, session_id=s_y, recorded_at=yday + " 12:00:00")
    live_y = gym_bank.get_live_session_state(yday)
    check("past day: every set carries a stage", all('stage' in s for s in live_y['sets']))
    check("past day: BB Overhead Press (tier-1 shoulders) -> t1",
          live_y['sets'] and live_y['sets'][0]['stage'] == 't1')
    check("past day: finished flag present", 'finished' in live_y)

    print("\n[live-session] no session on a day -> finished False, no sets")
    older = sqlite3.connect(_DB).execute("SELECT DATE(?, '-9 day')", (today,)).fetchone()[0]
    empty = gym_bank.get_live_session_state(older)
    check("empty day: finished is False", empty['finished'] is False)
    check("empty day: no sets", empty['sets'] == [])

    print("\n" + "=" * 60)
    if FAILS:
        print("✗ %d check(s) failed:" % len(FAILS))
        for f in FAILS:
            print("   - " + f)
        return 1
    print("✓ All stage-tagging checks passed.")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    finally:
        try:
            os.remove(_DB)
        except OSError:
            pass
