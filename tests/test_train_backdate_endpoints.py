"""
test_train_backdate_endpoints.py — set delete + session finish on a past day
============================================================================
The Train date header (docs/PLAN-train-backdate-logging.md) lets a set be
logged against a PAST training day. Two endpoints the page calls while a past
day is showing were never taught about the date:

  * POST /api/gym/set/delete checked the set against TODAY's live session, so
    deleting a set on a past day 404'd ("That set is not in today's session"),
    or, with no session today, "No live session today".
  * POST /api/gym/session/finish always closed TODAY's session (or 404'd when
    there was none) while the page kept showing the past day as open.

Both now take the same optional `date` as /api/gym/log-set: absent -> today,
a valid past training day -> that day, future/garbage -> 400. These checks
pin that, and pin that the today-path is unchanged.

The view functions are called inside app.test_request_context(), which does
NOT run before_request (require_profile / CSRF / engine gating). Those read
the profiles table in tracker.db; this suite must never touch it.

Run:
    TZ=Australia/Melbourne python -m tests.test_train_backdate_endpoints
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
database.DB_PATH = _DB                   # belt and braces: nothing here may reach tracker.db
from gymtracker.app import app, api_gym_set_delete, api_gym_session_finish   # noqa: E402

FAILS = []


def check(label, cond):
    print(("  ok  — " if cond else "  FAIL — ") + label)
    if not cond:
        FAILS.append(label)


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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL REFERENCES gym_exercises(id) ON DELETE CASCADE,
            weight_kg REAL, sets INTEGER, reps INTEGER,
            successful INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER REFERENCES gym_sessions(id) ON DELETE SET NULL,
            duration_s INTEGER, distance_m INTEGER, setting TEXT);
        INSERT INTO gym_functions (muscle_group, key, label) VALUES ('legs','squat','Squat');
        INSERT INTO gym_exercises (name, tier, muscle_group, function)
            VALUES ('BB Squat', 1, 'legs', 'squat');
    """)
    conn.commit()
    conn.close()


def _call(view, body):
    """Run a view with a JSON body; return (status, payload)."""
    with app.test_request_context(method='POST', json=body):
        rv = view()
    if isinstance(rv, tuple):
        return rv[1], rv[0]
    return 200, rv


def _finished(sid):
    conn = sqlite3.connect(_DB)
    v = conn.execute("SELECT finished FROM gym_sessions WHERE id = ?", (sid,)).fetchone()[0]
    conn.close()
    return v


def main():
    _mkdb()
    conn = sqlite3.connect(_DB)
    today = conn.execute("SELECT " + gym_bank.TODAY_SQL).fetchone()[0]
    yday = conn.execute("SELECT DATE(?, '-1 day')", (today,)).fetchone()[0]
    older = conn.execute("SELECT DATE(?, '-5 day')", (today,)).fetchone()[0]
    future = conn.execute("SELECT DATE(?, '+1 day')", (today,)).fetchone()[0]
    conn.close()

    # Today: one set. Yesterday (back-dated, noon-stamped like the real path): two.
    s_today = gym_bank.get_or_create_today_gym_session()
    t1 = gym_bank.log_gym_set(1, 100, 5, session_id=s_today)
    s_y = gym_bank.get_or_create_today_gym_session(yday)
    y1 = gym_bank.log_gym_set(1, 90, 5, session_id=s_y, recorded_at=yday + " 12:00:00")
    y2 = gym_bank.log_gym_set(1, 90, 5, session_id=s_y, recorded_at=yday + " 12:00:00")
    check("fixture: two distinct sessions", s_today != s_y)

    print("\n[delete] a past day's set, with its date")
    st, r = _call(api_gym_set_delete, {'set_id': y1, 'date': yday})
    check("200 ok", st == 200 and r.get('ok') is True)
    check("removed", r.get('removed') is True)
    sets = (r.get('workout_so_far') or {}).get('sets') or []
    check("workout_so_far is the PAST day's session", (r.get('workout_so_far') or {}).get('session_id') == s_y)
    check("…with the one remaining past-day set", [x['id'] for x in sets] == [y2])

    print("\n[delete] scoping still holds")
    st, r = _call(api_gym_set_delete, {'set_id': y2})
    check("a past-day set WITHOUT a date is refused (404) — today's guard unchanged", st == 404)
    st, r = _call(api_gym_set_delete, {'set_id': t1, 'date': yday})
    check("today's set sent with yesterday's date is refused (404)", st == 404)
    st, r = _call(api_gym_set_delete, {'set_id': y2, 'date': future})
    check("a future date is 400", st == 400)
    st, r = _call(api_gym_set_delete, {'set_id': y2, 'date': 'not-a-date'})
    check("a garbage date is 400", st == 400)
    st, r = _call(api_gym_set_delete, {'set_id': y2, 'date': older})
    check("a day with no session is 404", st == 404)

    print("\n[delete] today path unchanged")
    st, r = _call(api_gym_set_delete, {'set_id': t1})
    check("today's set, no date: 200 + removed", st == 200 and r.get('removed') is True)
    check("…and workout_so_far is today's session", (r.get('workout_so_far') or {}).get('session_id') == s_today)
    t2 = gym_bank.log_gym_set(1, 100, 5, session_id=s_today)   # restore a set for the finish checks

    print("\n[finish] a past day, with its date")
    st, r = _call(api_gym_session_finish, {'date': yday})
    check("200 ok", st == 200 and r.get('ok') is True)
    check("the PAST day's session is finished", _finished(s_y) == 1)
    check("today's session is left open", _finished(s_today) == 0)
    check("workout_so_far is the past day's", (r.get('workout_so_far') or {}).get('session_id') == s_y)

    print("\n[finish] bad / empty days")
    st, r = _call(api_gym_session_finish, {'date': future})
    check("future date is 400", st == 400)
    st, r = _call(api_gym_session_finish, {'date': older})
    check("a day with no session is 404", st == 404)
    check("…and today is still open", _finished(s_today) == 0)

    print("\n[finish] today path unchanged")
    st, r = _call(api_gym_session_finish, {})
    check("no date: 200 ok", st == 200 and r.get('ok') is True)
    check("today's session is finished", _finished(s_today) == 1)
    st, r = _call(api_gym_session_finish, None)
    check("no JSON body at all still works (200)", st == 200)

    print("\n[reopen] logging to a finished past day re-opens it, not a new session")
    s_y2 = gym_bank.get_or_create_today_gym_session(yday)
    check("same session id", s_y2 == s_y)
    check("finished flag cleared", _finished(s_y) == 0)

    print("\n[finish] a past day returns the accumulation delta for THAT day (Feature 02)")
    # A clean past day (older) with three passing sets of a tier-1 lift, so the
    # finish recap has something to animate. `older` had no session until now
    # (the earlier 404 check already ran).
    s_old = gym_bank.get_or_create_today_gym_session(older)
    for _ in range(3):
        gym_bank.log_gym_set(1, 100, 5, session_id=s_old, recorded_at=older + " 12:00:00")
    st, r = _call(api_gym_session_finish, {'date': older})
    check("200 ok", st == 200 and r.get('ok') is True)
    check("response carries an accumulation_delta list", isinstance(r.get('accumulation_delta'), list))
    check("…and `day` echoes the finished day", r.get('day') == older)
    delta = r.get('accumulation_delta') or []
    check("the delta reports the day's banked passes (3 clean sets → one lift)", len(delta) == 1)
    if delta:
        it = delta[0]
        cols = {c['label']: c for c in it['cols']}
        check("BB Squat @ 100 kg, 3×5 newly passed 0 → 1",
              it['exercise_id'] == 1 and cols['3×5']['before'] == 0 and cols['3×5']['after'] == 1)
        check("day_reps are the day's reps at that weight", it['day_reps'] == [5, 5, 5])

    print("\n" + "=" * 60)
    if FAILS:
        print("✗ %d check(s) failed:" % len(FAILS))
        for f in FAILS:
            print("   - " + f)
        sys.exit(1)
    print("✓ All back-dated delete / finish checks passed.")


if __name__ == '__main__':
    try:
        main()
    finally:
        try:
            os.remove(_DB)
        except OSError:
            pass
