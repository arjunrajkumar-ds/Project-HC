"""
test_scheme_accumulation.py — the scheme-accumulation grid over gym_progression
===============================================================================
The home-screen accumulation panel used to read the LEGACY tables
(exercise_attempts / exercises / schemes / progression), whose newest row is
2026-07-12. Nothing writes to them any more — every set logged on /train goes to
gym_progression — so the panel showed a frozen pre-July snapshot. It is now
derived from the gym schema, which means two things have to be invented and
therefore pinned:

  1. A PASS. The gym schema has no `result` column, so a pass is "the full
     prescription on one training day": at least S sets of at least R reps at
     weight W passes rung SxR at W. A day of 5, 5, 4 does NOT pass 3x5. That
     distinction is the entire point of a double-progression ladder, and it is
     the single easiest thing to get quietly wrong.

  2. THE LADDER. database.TIER_BOUNDS, sets as the outer loop, reps inner —
     the same enumeration _generate_schemes() uses, so a rung means the same
     thing as it did in the legacy ladder.

Also pinned: the current training day is excluded (so the panel cannot shift
under you mid-session), and only tiers 1-3 weight_reps work qualifies.

Run:
    TZ=Australia/Melbourne python -m tests.test_scheme_accumulation
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
from gymtracker.app import _build_scheme_viz, _gym_tier_ladder, _scheme_delta   # noqa: E402

FAILS = []


def check(label, cond):
    print(("  ok  — " if cond else "  FAIL — ") + label)
    if not cond:
        FAILS.append(label)


def _mkdb():
    conn = sqlite3.connect(_DB)
    conn.executescript("""
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
        -- gym_sessions mirrors migrate_gym_sessions.py. Nothing here reads it
        -- yet; it is in the fixture so the stage-bar / finish-recap work
        -- (docs/HANDOFF-stage-bar-recap-route.md §4, "today counts once
        -- finished") can query it without this suite crashing on a missing table.
        CREATE TABLE gym_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            performed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished INTEGER NOT NULL DEFAULT 0,
            ended_at TEXT);
        CREATE TABLE gym_progression (
            id INTEGER PRIMARY KEY AUTOINCREMENT, exercise_id INTEGER NOT NULL,
            weight_kg REAL, sets INTEGER, reps INTEGER,
            successful INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER, duration_s INTEGER, distance_m INTEGER, setting TEXT);
    """)
    ex = [(1, 'Squat', 1, 'weight_reps'), (2, 'Row', 2, 'weight_reps'),
          (3, 'Plank', 1, 'time'), (4, 'Clap Push-up', 4, 'reps'),
          (5, 'Rowing', 5, 'cardio')]
    for i, name, tier, tt in ex:
        conn.execute("INSERT INTO gym_exercises (id, name, tier, muscle_group, tracking_type) "
                     "VALUES (?, ?, ?, 'legs', ?)", (i, name, tier, tt))
    conn.commit()
    return conn


def _utc(local_dt):
    return datetime.utcfromtimestamp(local_dt.timestamp()).strftime('%Y-%m-%d %H:%M:%S')


def main():
    conn = _mkdb()
    today = (datetime.now() - timedelta(hours=gym_bank.ROLLOVER_HOUR)).date()

    def at(days_ago, hour=10):
        d = today - timedelta(days=days_ago)
        return datetime.combine(d, datetime.min.time()) + timedelta(hours=hour)

    def log(ex, when, w, reps, n=1, sets_col=1):
        for _ in range(n):
            conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at) "
                         "VALUES (?, ?, ?, ?, ?)", (ex, w, sets_col, reps, _utc(when)))
        conn.commit()

    print("\n[ladder] rungs come from TIER_BOUNDS, sets outer / reps inner")
    t1 = _gym_tier_ladder(1)
    check("tier 1 is 3-5 sets x 3-5 reps = 9 rungs", len(t1) == 9)
    check("first rung is 3x3, last is 5x5", t1[0] == (3, 3) and t1[-1] == (5, 5))
    check("sets is the OUTER loop (3x3, 3x4, 3x5, 4x3 ...)",
          t1[:4] == [(3, 3), (3, 4), (3, 5), (4, 3)])
    check("tier 2 is 3-4 x 6-8 = 6 rungs", len(_gym_tier_ladder(2)) == 6)
    check("tier 4 (Fa Jin) has no ladder", _gym_tier_ladder(4) == [])
    check("tier 5 (cardio) has no ladder", _gym_tier_ladder(5) == [])

    print("\n[pass] the full prescription on one day, nothing less")
    log(1, at(5), 60.0, 5, n=3)                       # 3 sets of 5 @ 60
    v = _build_scheme_viz()
    d = v['data'].get('1')
    check("exercise with a clean 3x5 appears", d is not None)
    cols = [c['label'] for c in d['cols']]
    wi = {w['label']: i for i, w in enumerate(d['weights'])}
    k = lambda lbl, w: '%d,%d' % (cols.index(lbl), wi[w])
    check("3x5 @ 60 passed once", d['passes'].get(k('3×5', '60.0 kg')) == 1)
    check("3x4 also passed (5 reps clears a 4-rep demand)", d['passes'].get(k('3×4', '60.0 kg')) == 1)
    check("3x3 also passed", d['passes'].get(k('3×3', '60.0 kg')) == 1)
    check("4x3 NOT passed — only three sets were done", k('4×3', '60.0 kg') not in d['passes'])
    check("5x5 NOT passed", k('5×5', '60.0 kg') not in d['passes'])

    print("\n[pass] 5, 5, 4 is not a pass of 3x5 — the whole point of the ladder")
    check("tier 2 ladder starts at 3x6", _gym_tier_ladder(2)[0] == (3, 6))
    log(2, at(4), 50.0, 5, n=2)
    log(2, at(4), 50.0, 4, n=1)
    # Tier 2's easiest rung demands 6 reps, so 5,5,4 clears nothing at all and
    # the exercise is omitted rather than rendered as an empty grid.
    check("5,5,4 against a 6-rep ladder yields no passes at all",
          '2' not in _build_scheme_viz()['data'])

    log(2, at(3), 50.0, 6, n=3)
    d2 = _build_scheme_viz()['data'].get('2')
    check("3 clean sets of 6 brings the exercise into the panel", d2 is not None)
    cols2 = [c['label'] for c in d2['cols']]
    wi2 = {w['label']: i for i, w in enumerate(d2['weights'])}
    k2 = lambda lbl, w: '%d,%d' % (cols2.index(lbl), wi2[w])
    check("3 sets of 6 passes 3x6", d2['passes'].get(k2('3×6', '50.0 kg')) == 1)
    check("but not 4x6 — only three sets", k2('4×6', '50.0 kg') not in d2['passes'])
    check("and the 5,5,4 day still contributed nothing",
          sum(d2['passes'].values()) == 1)

    print("\n[today] the current training day is excluded")
    before = sum(_build_scheme_viz()['data']['1']['passes'].values())
    log(1, at(0), 60.0, 5, n=5)                        # a big session TODAY
    after = sum(_build_scheme_viz()['data']['1']['passes'].values())
    check("logging today changes nothing in the panel", before == after)
    log(1, at(1), 60.0, 5, n=5)                        # same session, yesterday
    check("the same work on a finished day does count",
          sum(_build_scheme_viz()['data']['1']['passes'].values()) > after)

    print("\n[scope] only tiers 1-3 weight_reps work")
    log(3, at(2), 0.0, 0, n=3)       # Plank, time
    log(4, at(2), 0.0, 8, n=4)       # Fa Jin, reps, tier 4
    log(5, at(2), 0.0, 0, n=1)       # Rowing, cardio, tier 5
    v = _build_scheme_viz()
    check("timed work is absent", '3' not in v['data'])
    check("tier 4 Fa Jin is absent", '4' not in v['data'])
    check("tier 5 cardio is absent", '5' not in v['data'])

    print("\n[legacy] an imported row with sets > 1 counts as that many sets")
    conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at) "
                 "VALUES (2, 55.0, 4, 8, ?)", ((today - timedelta(days=9)).isoformat(),))
    conn.commit()
    d2 = _build_scheme_viz()['data']['2']
    wi2 = {w['label']: i for i, w in enumerate(d2['weights'])}
    cols2 = [c['label'] for c in d2['cols']]
    k2 = lambda lbl, w: '%d,%d' % (cols2.index(lbl), wi2[w])
    check("one row of sets=4 reps=8 passes 4x8", d2['passes'].get(k2('4×8', '55.0 kg')) == 1)
    check("and 3x6 at that weight too", d2['passes'].get(k2('3×6', '55.0 kg')) == 1)

    print("\n[shape] the seed matches the template's contract")
    v = _build_scheme_viz()
    d = v['data']['1']
    check("threshold defaults to 5", d['T'] == 5)
    check("exercises list is ordered by total passes desc",
          v['exercises'] == sorted(v['exercises'],
                                   key=lambda e: -sum(v['data'][e]['passes'].values())))
    check("every pass key indexes a real column and weight",
          all(0 <= int(kk.split(',')[0]) < len(d['cols'])
              and 0 <= int(kk.split(',')[1]) < len(d['weights']) for kk in d['passes']))
    check("current carries a weight and a rung", d['current']['weight_kg'] == 60.0)
    check("weights are ascending",
          [w['kg'] for w in d['weights']] == sorted(w['kg'] for w in d['weights']))

    print("\n[today] today counts on the home panel only once finished (Feature 02b)")
    # A fresh pass TODAY for Squat at a brand-new weight, inside an OPEN session.
    sid = conn.execute("INSERT INTO gym_sessions (performed_at, finished) VALUES (?, 0)",
                       (_utc(at(0)),)).lastrowid
    conn.commit()
    for _ in range(3):
        conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at, session_id) "
                     "VALUES (1, 72.5, 1, 5, ?, ?)", (_utc(at(0)), sid))
    conn.commit()

    def squat_72():
        # The home panel's own rule: include today iff the session is finished.
        d = _build_scheme_viz(include_today=gym_bank.is_today_session_finished())['data'].get('1')
        if not d:
            return None
        cols = [c['label'] for c in d['cols']]
        wi = {w['label']: i for i, w in enumerate(d['weights'])}
        if '72.5 kg' not in wi:
            return None
        return d['passes'].get('%d,%d' % (cols.index('3×5'), wi['72.5 kg']))

    check("open session today → is_today_session_finished() is False",
          gym_bank.is_today_session_finished() is False)
    check("today's 3×5@72.5 is EXCLUDED while the session is open", squat_72() is None)
    conn.execute("UPDATE gym_sessions SET finished=1 WHERE id=?", (sid,))
    conn.commit()
    check("finishing the session → is_today_session_finished() is True",
          gym_bank.is_today_session_finished() is True)
    check("now today's 3×5@72.5 is INCLUDED on the home panel", squat_72() == 1)
    conn.execute("UPDATE gym_sessions SET finished=0 WHERE id=?", (sid,))
    conn.commit()
    check("logging again reopens → today drops back out",
          gym_bank.is_today_session_finished() is False and squat_72() is None)

    print("\n[delta] the finish recap diffs today's passes against every other day (Feature 02)")
    # Add two more of today's passes so the delta spans weights + exercises:
    #   Squat 60 (already has one historical pass → overflow), Row 50 (3×6).
    for _ in range(3):
        conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at, session_id) "
                     "VALUES (1, 60.0, 1, 5, ?, ?)", (_utc(at(0)), sid))
    for _ in range(3):
        conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at, session_id) "
                     "VALUES (2, 50.0, 1, 6, ?, ?)", (_utc(at(0)), sid))
    conn.commit()
    # live is only read for item ORDER (first appearance) and day_reps; the
    # before/after pass counts come from the DB. Squat appears before Row.
    live = {'sets': (
        [{'exercise_id': 1, 'weight_kg': 72.5, 'reps': 5, 'tracking_type': 'weight_reps'}] * 3 +
        [{'exercise_id': 1, 'weight_kg': 60.0, 'reps': 5, 'tracking_type': 'weight_reps'}] * 3 +
        [{'exercise_id': 2, 'weight_kg': 50.0, 'reps': 6, 'tracking_type': 'weight_reps'}] * 3)}
    dl = _scheme_delta(live)
    pairs = [(it['exercise_id'], it['weight_kg']) for it in dl]
    check("only (exercise, weight) pairs that ROSE are returned",
          pairs == [(1, 72.5), (1, 60.0), (2, 50.0)])
    sq72 = dl[0]
    check("Squat 72.5 label is '72.5 kg'", sq72['weight_label'] == '72.5 kg')
    check("day_reps are today's reps at that weight", sq72['day_reps'] == [5, 5, 5])
    c72 = {c['label']: c for c in sq72['cols']}
    check("3×5@72.5 rose 0 → 1", c72['3×5']['before'] == 0 and c72['3×5']['after'] == 1)
    check("4×3@72.5 stayed 0 → 0 (3 sets never clears a 4-set rung)",
          c72['4×3']['before'] == 0 and c72['4×3']['after'] == 0)
    check("gained counts every rung the day cleared (3×3/3×4/3×5)", sq72['gained'] == 3)
    check("nothing mastered at the default T=5", sq72['mastered'] == [])
    sq60 = dl[1]
    c60 = {c['label']: c for c in sq60['cols']}
    check("Squat 60 already had a pass → 1 → 2", c60['3×5']['before'] == 1 and c60['3×5']['after'] == 2)
    check("Row is last and reports its reps", dl[2]['day_reps'] == [6, 6, 6])
    # Threshold forces crossings so mastered / overflow are exercised.
    dlT = _scheme_delta(live, threshold=1)
    m72 = [it for it in dlT if it['exercise_id'] == 1 and it['weight_kg'] == 72.5][0]
    check("at T=1 a 0→1 rung MASTERS (before < T <= after)",
          set(m72['mastered']) >= {'3×3', '3×4', '3×5'})
    o60 = [it for it in dlT if it['exercise_id'] == 1 and it['weight_kg'] == 60.0][0]
    check("gained still counts passes PAST T as overflow (60: 1→2 at T=1)", o60['gained'] == 3)

    print("\n[delta] a past day diffs against every OTHER counted day; today never leaks")
    yiso = (today - timedelta(days=1)).isoformat()
    for _ in range(3):
        conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at) "
                     "VALUES (1, 80.0, 1, 5, ?)", (_utc(at(1)),))
    conn.commit()
    live_y = {'sets': [{'exercise_id': 1, 'weight_kg': 80.0, 'reps': 5, 'tracking_type': 'weight_reps'}] * 3}
    ydl = _scheme_delta(live_y, day=yiso)
    yids = [(it['exercise_id'], it['weight_kg']) for it in ydl]
    check("the past-day delta surfaces that day's 80 kg pass", (1, 80.0) in yids)
    check("an UNFINISHED today never leaks into a past-day delta",
          (1, 72.5) not in yids and (1, 60.0) not in yids)
    y80 = [it for it in ydl if it['weight_kg'] == 80.0][0]
    cy = {c['label']: c for c in y80['cols']}
    check("before excludes the day itself (0), after includes it (1)",
          cy['3×5']['before'] == 0 and cy['3×5']['after'] == 1)

    conn.close()
    os.unlink(_DB)

    print("\n" + "=" * 60)
    if FAILS:
        print("✗ %d check(s) failed:" % len(FAILS))
        for f in FAILS:
            print("   - " + f)
        return 1
    print("✓ All scheme-accumulation checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
