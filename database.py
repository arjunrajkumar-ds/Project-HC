import sqlite3
import os
from datetime import date

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS exercises (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            tier        INTEGER NOT NULL CHECK(tier IN (1, 2, 3)),
            muscle_group TEXT NOT NULL,
            day_type    TEXT NOT NULL CHECK(day_type IN ('push','pull','legs','core','any')),
            notes       TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            date     TEXT NOT NULL,
            type     TEXT NOT NULL CHECK(type IN ('gym','swim')),
            day_type TEXT CHECK(day_type IN ('push','pull','legs','core')),
            notes    TEXT
        );

        CREATE TABLE IF NOT EXISTS session_lifts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            exercise_id INTEGER NOT NULL REFERENCES exercises(id),
            set_number  INTEGER NOT NULL,
            reps        INTEGER NOT NULL,
            weight_kg   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS swim_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            distance_m      INTEGER NOT NULL,
            rep_distance_m  INTEGER,
            sets            INTEGER
        );
    """)

    exercises = [
        # Tier 1 — Heavy Compounds (day_type = 'any')
        ('Barbell Squat',              1, 'Legs',             'any'),
        ('Barbell Bench Press',        1, 'Chest',            'any'),
        ('Pendlay Row',                1, 'Upper Back',       'any'),
        ('Deadlift',                   1, 'Posterior Chain',  'any'),
        ('Barbell OHP',                1, 'Shoulders',        'any'),
        ('Trap Bar Deadlift',          1, 'Posterior Chain',  'any'),
        ('Weighted Hanging Leg Raise', 1, 'Core',             'any'),

        # Tier 2 — Supporting Compounds (locked to day_type)
        ('DB Shoulder Press',          2, 'Shoulders',  'legs'),
        ('Weighted Pullup (Wide Grip)',2, 'Upper Back',  'pull'),
        ('Weighted Chinups',           2, 'Upper Back',  'pull'),
        ('DB Pullover',                2, 'Upper Back',  'pull'),
        ('Bulgarian Split Squat',      2, 'Legs',        'legs'),
        ('Ab Wheel Roller',            2, 'Core',        'legs'),
        ('Weighted Dips',              2, 'Triceps',     'push'),
        ('DB Bench Press',             2, 'Chest',       'push'),

        # Tier 3 — Aesthetics (locked to day_type)
        ('Lateral Raises',             3, 'Shoulders',   'legs'),
        ('Cable/DB Curls',             3, 'Biceps',      'pull'),
        ('Tricep Pushdowns',           3, 'Triceps',     'push'),
    ]

    c.executemany("""
        INSERT OR IGNORE INTO exercises (name, tier, muscle_group, day_type)
        VALUES (?, ?, ?, ?)
    """, exercises)

    # Migrate: add swim rep columns if they don't exist yet
    existing = {row[1] for row in conn.execute('PRAGMA table_info(swim_logs)')}
    for col, typedef in [('rep_distance_m', 'INTEGER'), ('sets', 'INTEGER')]:
        if col not in existing:
            conn.execute(f'ALTER TABLE swim_logs ADD COLUMN {col} {typedef}')

    # Progression tracking table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progression (
            exercise_id  INTEGER PRIMARY KEY
                         REFERENCES exercises(id) ON DELETE CASCADE,
            weight_kg    REAL    NOT NULL,
            stage        INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


# ── Linear progression ladders ────────────────────────────────────────
# Each entry is (reps, sets).  Complete all stages → +2.5 kg, restart.
TIER1_LADDER = [
    (3, 2), (3, 3), (3, 4),
    (4, 2), (4, 3), (4, 4),
    (5, 2), (5, 3), (5, 4),
    (6, 2), (6, 3), (6, 4),
]
TIER2_LADDER = [
    (6, 2), (6, 3),
    (7, 2), (7, 3),
    (8, 2), (8, 3),
]
WEIGHT_INCREMENT = 2.5


def _ladder(tier):
    return TIER1_LADDER if tier == 1 else TIER2_LADDER


def _prog_dict(exercise_id, weight_kg, stage, tier):
    ladder = _ladder(tier)
    reps, sets = ladder[stage]
    return {
        'exercise_id':  exercise_id,
        'weight_kg':    weight_kg,
        'stage':        stage,
        'total_stages': len(ladder),
        'target_reps':  reps,
        'target_sets':  sets,
        'is_last':      stage == len(ladder) - 1,
        'ladder':       [{'reps': r, 'sets': s} for r, s in ladder],
        'pct':          round(stage / len(ladder) * 100),
    }


def get_progression(exercise_id, tier):
    """Return progression dict, auto-initialising from history if needed.
    Returns None if the exercise has never been logged and no weight is set."""
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM progression WHERE exercise_id = ?', (exercise_id,)
    ).fetchone()

    if row is None:
        # Try to seed from the most recent session for this exercise
        last = conn.execute("""
            SELECT sl.reps, sl.weight_kg, COUNT(*) AS set_count
            FROM session_lifts sl
            WHERE sl.exercise_id = ?
              AND sl.session_id = (
                  SELECT s.id FROM sessions s
                  JOIN session_lifts sl2 ON sl2.session_id = s.id
                  WHERE sl2.exercise_id = ? AND s.type = 'gym'
                  ORDER BY s.date DESC, s.id DESC
                  LIMIT 1
              )
            GROUP BY sl.reps, sl.weight_kg
            ORDER BY set_count DESC
            LIMIT 1
        """, (exercise_id, exercise_id)).fetchone()

        if last:
            ladder = _ladder(tier)
            stage = 0
            for i, (r, s) in enumerate(ladder):
                if r <= last['reps'] and s <= last['set_count']:
                    stage = i
            conn.execute(
                'INSERT OR IGNORE INTO progression (exercise_id, weight_kg, stage) '
                'VALUES (?,?,?)',
                (exercise_id, last['weight_kg'], stage)
            )
            conn.commit()
            row = conn.execute(
                'SELECT * FROM progression WHERE exercise_id = ?', (exercise_id,)
            ).fetchone()

    conn.close()
    if not row:
        return None
    return _prog_dict(exercise_id, row['weight_kg'], row['stage'], tier)


def advance_progression(exercise_id, tier):
    """Move forward one stage (wraps to next weight on completion)."""
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM progression WHERE exercise_id = ?', (exercise_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    ladder        = _ladder(tier)
    stage         = row['stage']
    weight        = row['weight_kg']
    weight_bumped = False

    if stage + 1 >= len(ladder):
        weight        = round(weight + WEIGHT_INCREMENT, 2)
        stage         = 0
        weight_bumped = True
    else:
        stage += 1

    conn.execute(
        'UPDATE progression SET weight_kg = ?, stage = ? WHERE exercise_id = ?',
        (weight, stage, exercise_id)
    )
    conn.commit()
    conn.close()

    d = _prog_dict(exercise_id, weight, stage, tier)
    d['weight_bumped'] = weight_bumped
    return d


def set_progression_weight(exercise_id, weight_kg, tier):
    """Set (or reset) the working weight and restart from stage 0."""
    conn = get_db()
    conn.execute("""
        INSERT INTO progression (exercise_id, weight_kg, stage) VALUES (?, ?, 0)
        ON CONFLICT(exercise_id) DO UPDATE SET weight_kg = excluded.weight_kg, stage = 0
    """, (exercise_id, weight_kg))
    conn.commit()
    conn.close()
    return _prog_dict(exercise_id, weight_kg, 0, tier)


def get_all_progressions():
    """Return progression dicts for every T1/T2 exercise that has one."""
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*, e.name, e.tier, e.muscle_group, e.day_type
        FROM progression p
        JOIN exercises e ON e.id = p.exercise_id
        WHERE e.tier IN (1, 2)
        ORDER BY e.tier, e.name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = _prog_dict(r['exercise_id'], r['weight_kg'], r['stage'], r['tier'])
        d.update({'name': r['name'], 'tier': r['tier'],
                  'muscle_group': r['muscle_group'], 'day_type': r['day_type']})
        result.append(d)
    return result


def get_tier1_suggestion():
    """Return all Tier 1 exercises ordered by days-since-last-performed descending.

    Never-performed exercises sort first (highest priority).
    Each row includes: id, name, muscle_group, last_performed (ISO date or None), days_since (int or None).
    The first row is the suggested next lift.
    """
    conn = get_db()
    today = date.today().isoformat()

    rows = conn.execute("""
        SELECT
            e.id,
            e.name,
            e.muscle_group,
            MAX(s.date) AS last_performed
        FROM exercises e
        LEFT JOIN session_lifts sl ON sl.exercise_id = e.id
        LEFT JOIN sessions      s  ON s.id = sl.session_id AND s.type = 'gym'
        WHERE e.tier = 1
        GROUP BY e.id
        ORDER BY last_performed ASC NULLS FIRST
    """).fetchall()
    conn.close()

    # Annotate with days_since (None if never performed)
    result = []
    for row in rows:
        lp = row['last_performed']
        if lp:
            delta = (date.fromisoformat(today) - date.fromisoformat(lp)).days
        else:
            delta = None
        result.append({
            'id':             row['id'],
            'name':           row['name'],
            'muscle_group':   row['muscle_group'],
            'last_performed': lp,
            'days_since':     delta,
        })

    return result
