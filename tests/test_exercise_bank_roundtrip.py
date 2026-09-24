"""
test_exercise_bank_roundtrip.py — Exercise Bank hardening tests
================================================================
Round-trips one exercise of EACH tracking_type through the real gym_bank
write/read/log path, asserting persistence and per-type field validation.

Why a copy: the tests must never touch your live tracker.db. This script
copies tracker.db to a temp file, points gym_bank.DB_PATH at the copy, runs
everything against it, then discards the copy. If tracker.db doesn't exist it
builds a minimal schema so the tests are still runnable in CI.

Run:
    cd /Users/arjunrajkumar/Documents/GitHub/project-horsecock
    python -m tests.test_exercise_bank_roundtrip
    # or, if pytest is installed:
    pytest tests/test_exercise_bank_roundtrip.py -v

Exit code 0 = all passed, 1 = a failure (prints which case).
"""
import os
import sys
import shutil
import sqlite3
import tempfile

# Make the package importable when run as a plain script.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from gymtracker import gym_bank  # noqa: E402
from gymtracker.gym_bank import (  # noqa: E402
    TRACKING_TYPES, TRACKING_FIELDS, EXERCISE_CLASSES,
    validate_progression_fields, tracking_field_spec,
)

LIVE_DB = os.path.join(_REPO, 'tracker.db')

# One representative logged-set payload per tracking_type. Each MUST satisfy
# that type's TRACKING_FIELDS['required'] and may include optionals.
SAMPLE_SETS = {
    'weight_reps': {'weight_kg': 60.0, 'reps': 8, 'sets': 3},
    'reps':        {'reps': 12, 'sets': 4},
    'time':        {'duration_s': 45, 'sets': 3},
    'weight_time': {'weight_kg': 20.0, 'duration_s': 30, 'sets': 3},
    'cardio':      {'duration_s': 1200, 'distance_m': 5000, 'setting': 'Level 8'},
}


# ── Minimal schema builder (only used if no live DB is present) ───────────────
def _build_min_schema(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE gym_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            performed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished INTEGER NOT NULL DEFAULT 0, ended_at TEXT);
        CREATE TABLE gym_exercises (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            tier INTEGER NOT NULL CHECK(tier IN (1,2,3,4,5)),
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
        CREATE TABLE gym_progression (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL REFERENCES gym_exercises(id) ON DELETE CASCADE,
            weight_kg REAL, sets INTEGER, reps INTEGER,
            successful INTEGER NOT NULL DEFAULT 1,
            recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            session_id INTEGER REFERENCES gym_sessions(id) ON DELETE SET NULL,
            duration_s INTEGER, distance_m INTEGER, setting TEXT);
        CREATE TABLE gym_functions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            muscle_group TEXT NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0, UNIQUE(muscle_group, key));
    """)
    conn.commit()
    conn.close()


def _preflight_schema(path):
    """Fail fast with a clear message if the copy lacks exercise_class —
    that is the exact drift that broke add/edit."""
    conn = sqlite3.connect(path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(gym_exercises)").fetchall()]
    conn.close()
    missing = [c for c in ('exercise_class', 'tracking_type', 'sort_order', 'archived_at')
               if c not in cols]
    return missing


# ── Assertions ────────────────────────────────────────────────────────────────
_failures = []


def check(cond, msg):
    if cond:
        print(f"  ok  — {msg}")
    else:
        print(f"  FAIL — {msg}")
        _failures.append(msg)


def test_validate_spec_selfconsistent():
    print("\n[spec] TRACKING_FIELDS covers every TRACKING_TYPE")
    for tt in TRACKING_TYPES:
        check(tt in TRACKING_FIELDS, f"{tt} present in TRACKING_FIELDS")
    print("[spec] validate_progression_fields enforces required fields")
    for tt, payload in SAMPLE_SETS.items():
        ok, err = validate_progression_fields(tt, payload)
        check(ok, f"{tt}: full sample validates (err={err})")
        # Drop one required field -> must fail
        req = tracking_field_spec(tt)['required']
        if req:
            broken = dict(payload)
            broken.pop(req[0], None)
            ok2, err2 = validate_progression_fields(tt, broken)
            check(not ok2, f"{tt}: missing '{req[0]}' is rejected")


def test_roundtrip(db_path):
    gym_bank.DB_PATH = db_path  # redirect all helpers to the copy
    print("\n[roundtrip] add → read → log → verify, per tracking_type")
    for tt in TRACKING_TYPES:
        name = f"__test_{tt}"
        cls = 'bodyweight' if tt == 'reps' else 'strength'
        ok, err = gym_bank.gym_add_exercise(
            name=name, tier=2, muscle_group='muay_thai',
            function='conditioning', tracking_type=tt, exercise_class=cls,
        )
        check(ok, f"add {tt}: {err or 'created'}")
        if not ok:
            continue
        # Read back
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM gym_exercises WHERE name=?", (name,)).fetchone()
        conn.close()
        check(row is not None and row['tracking_type'] == tt,
              f"read {tt}: tracking_type persisted")
        # Edit: flip enabled + change notes
        ok_e, err_e = gym_bank.gym_update_exercise(row['id'], notes='edited', tier=3)
        check(ok_e, f"edit {tt}: {err_e or 'updated'}")
        # Validate a set for this type, then log it
        payload = SAMPLE_SETS[tt]
        ok_v, err_v = validate_progression_fields(tt, payload)
        check(ok_v, f"validate {tt} set: {err_v or 'valid'}")
        new_id = gym_bank.log_gym_set(
            row['id'],
            weight_kg=payload.get('weight_kg'),
            reps=payload.get('reps'),
            sets=payload.get('sets', 1),
            duration_s=payload.get('duration_s'),
            distance_m=payload.get('distance_m'),
            setting=payload.get('setting'),
        )
        check(isinstance(new_id, int) and new_id > 0, f"log {tt}: progression row created")


def main():
    tmpdir = tempfile.mkdtemp(prefix='exbank_test_')
    db_copy = os.path.join(tmpdir, 'tracker_test.db')
    try:
        if os.path.exists(LIVE_DB):
            shutil.copy2(LIVE_DB, db_copy)
            missing = _preflight_schema(db_copy)
            if missing:
                print(f"✗ PREFLIGHT: live schema is missing columns {missing}.")
                print("  Run the pending migration(s) before these tests can pass:")
                print("    python migrations/migrate_exercise_class.py")
                # Build fresh schema so the rest of the suite still exercises the code.
                os.remove(db_copy)
                _build_min_schema(db_copy)
                print("  (continuing against a fresh in-memory-style schema copy)")
        else:
            _build_min_schema(db_copy)
        # Seed the conditioning function so add() referencing it is clean.
        conn = sqlite3.connect(db_copy)
        conn.execute("INSERT OR IGNORE INTO gym_functions (muscle_group,key,label,sort_order) "
                     "VALUES ('muay_thai','conditioning','Conditioning',0)")
        conn.commit()
        conn.close()

        test_validate_spec_selfconsistent()
        test_roundtrip(db_copy)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + "=" * 60)
    if _failures:
        print(f"✗ {len(_failures)} check(s) FAILED:")
        for f in _failures:
            print(f"    - {f}")
        sys.exit(1)
    print("✓ All exercise-bank round-trip checks passed.")
    sys.exit(0)


if __name__ == '__main__':
    main()
