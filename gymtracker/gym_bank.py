"""
gym_bank.py — GYM Exercise Bank helpers
=========================================
Read/write functions for gym_exercises, gym_progression and gym_functions.
Replaces the old get_exercise_bank / bank_add_exercise / bank_update_exercise
for Arjun's profile.

Import into app.py alongside the existing database.py functions.

Schema note (see docs/EXERCISE-DB.md):
  - tracking_type ∈ {'weight_reps','reps','time'} is the authority for how a
    set is logged. is_weighted is DEPRECATED — derived from tracking_type and
    kept in sync on write purely for any legacy code path; do NOT read it.
  - archived_at (NULL = active) is soft-delete; it is a DIFFERENT axis from
    is_enabled (a deliberately-parked-but-in-bank flag).
  - function stores the lowercase key; gym_functions provides display labels
    and ordering. A free-typed function is still accepted and renders under
    its own raw label until added to gym_functions.
"""
import sqlite3
import json
import os
from datetime import datetime, date

# tracker.db lives at the repo root (one level above this package). Resolve
# relative to the package dir so it works regardless of the launch CWD.
# GYMTRACKER_DB overrides the path (used by the test-suite so tests never touch
# the live database). Falls back to the repo-root tracker.db.
DB_PATH = os.environ.get('GYMTRACKER_DB') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tracker.db')

# ── Training-day boundary ────────────────────────────────────────────────────
# SQLite's CURRENT_TIMESTAMP is UTC, so every recorded_at / performed_at is a
# UTC wall-clock string. Comparing DATE(recorded_at) against
# DATE('now','localtime') therefore mixes two calendars: in UTC+10 a set logged
# at 07:30 local is stored as the PREVIOUS UTC day, so "today" misses it. That
# silently broke morning sessions (no rehydrate, a fresh session row per set,
# undo/tally no-ops).
#
# A training day is the local calendar day shifted back by ROLLOVER_HOUR, so a
# 1 AM finish still belongs to the day the workout started on — the boundary
# apply_session_rollover() was already trying to express.
#
# Legacy rows imported by import_history.py store a bare 'YYYY-MM-DD' with no
# time. SQLite reads those as midnight UTC, so applying 'localtime' to them
# would shift them a day; they are already local dates, so pass them through.

ROLLOVER_HOUR = 4  # sessions from a prior day are stamped after ~4:00 AM local


def _training_day(col):
    """SQL expression: the training day a stored timestamp column belongs to."""
    return ("CASE WHEN length({c}) <= 10 THEN DATE({c}) "
            "ELSE DATE({c}, 'localtime', '-{h} hours') END").format(
                c=col, h=ROLLOVER_HOUR)


# The training day we are in right now.
TODAY_SQL = "DATE('now', 'localtime', '-{h} hours')".format(h=ROLLOVER_HOUR)


def _day_sql(date_iso=None):
    """Return (sql_expr, params) for the training day to operate on.

    None          -> the current training day (TODAY_SQL), no params.
    'YYYY-MM-DD'  -> that exact day, as a bound parameter.

    Callers interpolate sql_expr where they currently write TODAY_SQL and
    PREPEND params to their parameter tuple. This keeps the day boundary a
    single source of truth: logging/rehydrate/undo/prev-column all default to
    today and accept an explicit past day through the same door."""
    if not date_iso:
        return TODAY_SQL, ()
    return "DATE(?)", (date_iso,)


def _valid_training_day(date_iso):
    """Normalise an ISO date for use as a training day, or None if unusable.

    Rejects non-dates and any day in the FUTURE (training-day aware, read
    straight from SQLite so the -ROLLOVER_HOUR shift matches every other
    comparison). Returns the 'YYYY-MM-DD' string on success."""
    try:
        date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return None
    conn = _gym_db()
    t = conn.execute("SELECT {today} AS d".format(today=TODAY_SQL)).fetchone()['d']
    conn.close()
    return date_iso if date_iso <= t else None


def training_today():
    """The current training day as 'YYYY-MM-DD' (HANDOFF §5.1).

    Goes through TODAY_SQL so the ~4 AM roll-over boundary matches every other
    day comparison in this module. NEVER DATE('now') / DATE(recorded_at), which
    would reintroduce the two-calendar bug (REVIEW §1.1)."""
    conn = _gym_db()
    d = conn.execute("SELECT {today} AS d".format(today=TODAY_SQL)).fetchone()['d']
    conn.close()
    return d

# Allowed tracking_type values. weight_reps = load×reps×sets, reps = bodyweight
# reps, time = timed hold/duration per set, weight_time = load held for a
# duration per set (e.g. weighted plank, farmer's carry). cardio = duration +
# a free-text machine setting, and (when the exercise's engagement spec says so,
# e.g. Rowing) a distance in metres.
TRACKING_TYPES = ('weight_reps', 'reps', 'time', 'weight_time', 'cardio')

# ── Per-type field model ─────────────────────────────────────────────────────
# tracking_type is the SINGLE AUTHORITY for how a set of a given exercise is
# logged. TRACKING_FIELDS maps each type to the gym_progression columns it uses,
# split into 'required' (must be present + non-null on a logged set) and
# 'optional' (accepted, may be null). Every other progression column is ignored
# for that type. The Add/Edit form, the set-logging validation, and the
# round-trip tests all read from this one spec so they can never drift apart.
#
#   weight_reps  — loaded strength: weight × reps × sets   (e.g. Bench Press)
#   reps         — bodyweight reps: reps × sets            (e.g. Push-up, Muay
#                  Thai straight sets counted as reps)
#   time         — timed hold / duration per set           (e.g. Plank)
#   weight_time  — load held for a duration per set        (e.g. Farmer's Carry)
#   cardio       — duration + free-text machine setting, optional distance
#                  (e.g. Rowing, Bike)
#
# Column universe on gym_progression: weight_kg, reps, sets, duration_s,
# distance_m, setting.
TRACKING_FIELDS = {
    'weight_reps': {'required': ('weight_kg', 'reps', 'sets'),
                    'optional': ()},
    'reps':        {'required': ('reps', 'sets'),
                    'optional': ()},
    'time':        {'required': ('duration_s',),
                    'optional': ('sets',)},
    'weight_time': {'required': ('weight_kg', 'duration_s'),
                    'optional': ('sets',)},
    'cardio':      {'required': ('duration_s',),
                    'optional': ('distance_m', 'setting')},
}

# Human labels for the per-type fields — used by the form to render the right
# inputs and by error messages.
PROGRESSION_FIELD_LABELS = {
    'weight_kg': 'Weight (kg)',
    'reps': 'Reps',
    'sets': 'Sets',
    'duration_s': 'Duration (s)',
    'distance_m': 'Distance (m)',
    'setting': 'Setting',
}


def tracking_field_spec(tracking_type):
    """Return {'required': (...), 'optional': (...)} for a tracking_type.
    Unknown types fall back to weight_reps so the caller never KeyErrors."""
    return TRACKING_FIELDS.get(tracking_type, TRACKING_FIELDS['weight_reps'])


def validate_progression_fields(tracking_type, values):
    """Validate a dict of proposed per-set values against the type's spec.

    Returns (ok, error). Enforces that every 'required' field for the type is
    present and non-null; fields outside required+optional are ignored (not an
    error — the caller may pass a superset). Numeric fields must be numbers.
    """
    if tracking_type not in TRACKING_TYPES:
        return False, f'Invalid tracking type "{tracking_type}".'
    spec = tracking_field_spec(tracking_type)
    for field in spec['required']:
        v = values.get(field)
        if v is None or (isinstance(v, str) and v.strip() == ''):
            label = PROGRESSION_FIELD_LABELS.get(field, field)
            return False, f'{label} is required for {tracking_type} exercises.'
    # Type-check the numeric columns when supplied.
    for field in ('weight_kg', 'reps', 'sets', 'duration_s', 'distance_m'):
        v = values.get(field)
        if v is not None and v != '' and not isinstance(v, (int, float)):
            try:
                float(v)
            except (TypeError, ValueError):
                label = PROGRESSION_FIELD_LABELS.get(field, field)
                return False, f'{label} must be a number.'
    return True, None

# Tier labels for display
TIER_LABELS = {
    1: 'T1 — Main',
    2: 'T2 — Secondary',
    3: 'T3 — Accessory',
    4: 'T4 — Fa Jin',
    5: 'T5 — Cardio',
}


def _gym_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _db_error_message(exc):
    """Translate a raw sqlite3.Error into an actionable, user-facing message.

    The most common failure in this app is schema drift — the code writes a
    column the live tracker.db never got (a migration wasn't run). SQLite
    reports that as OperationalError: 'no such column: X'. Surface exactly
    which column and point at the fix, instead of a generic 'failed'.
    """
    msg = str(exc)
    low = msg.lower()
    if 'no such column' in low:
        col = msg.split(':', 1)[1].strip() if ':' in msg else msg
        return (f'Database schema is out of date — missing column "{col}". '
                f'Run the pending migration in migrations/ (e.g. '
                f'python migrations/migrate_exercise_class.py) and retry.')
    if 'no such table' in low:
        return (f'Database schema is out of date — {msg}. '
                f'Run the migrations in migrations/ and retry.')
    if 'not null constraint failed' in low:
        return f'A required field is missing: {msg.split(":",1)[-1].strip()}.'
    if 'datatype mismatch' in low:
        return f'A field has the wrong type: {msg}.'
    # Fall back to the raw reason — still far better than a generic "failed".
    return f'Database error: {msg}'


def _iw_from_tracking(tracking_type):
    """Derive the DEPRECATED is_weighted flag from tracking_type.
    is_weighted = 1 for the loaded types (weight_reps, weight_time); kept in
    sync on write so any missed legacy code path still behaves. Never read
    is_weighted in new code."""
    return 1 if tracking_type in ('weight_reps', 'weight_time') else 0


# ── Read ─────────────────────────────────────────────────────────────────────

def get_gym_bank(include_archived=False):
    """Full exercise bank grouped by tier, with latest progression.
    Archived exercises are excluded unless include_archived=True."""
    conn = _gym_db()
    where = "" if include_archived else "WHERE ge.archived_at IS NULL"
    rows = conn.execute(f"""
        SELECT ge.*,
               gp.weight_kg, gp.sets AS last_sets, gp.reps AS last_reps,
               gp.duration_s AS last_duration_s,
               gp.distance_m AS last_distance_m, gp.setting AS last_setting,
               gp.successful, gp.recorded_at
        FROM gym_exercises ge
        LEFT JOIN gym_progression gp ON gp.id = (
            SELECT id FROM gym_progression
            WHERE exercise_id = ge.id
            ORDER BY recorded_at DESC LIMIT 1
        )
        {where}
        ORDER BY ge.is_enabled DESC, ge.tier, ge.muscle_group, ge.function, ge.name
    """).fetchall()
    conn.close()

    bank = {1: [], 2: [], 3: [], 4: [], 5: []}
    for r in rows:
        entry = dict(r)
        entry['engagement'] = json.loads(entry['engagement'] or '{}')
        bank.setdefault(entry['tier'], []).append(entry)
    return bank


def get_gym_bank_grouped(include_archived=False):
    """Return the muscle → function → exercise tree the databank frontend needs.

    Joins gym_functions for labels + ordering, and attaches each exercise's
    latest gym_progression row ('latest') and its 'history_count'.

    Shape:
        [{'key': 'chest', 'label': 'Chest',
          'functions': [{'key': 'press', 'label': 'Press',
                         'exercises': [{...exercise fields..., 'latest': {...}|None,
                                        'history_count': int}]}],
          'active_count': int, 'total_count': int}]

    Ordering: muscle by _GYM_GROUP_ORDER (from app.py, passed in via the module
    constant mirror below); function by gym_functions.sort_order; exercise by
    sort_order, then tier, then name. Exercises whose function is NULL or absent
    from gym_functions fall into a trailing pseudo-function
    {'key': None, 'label': 'Unassigned'}.
    """
    conn = _gym_db()
    where = "" if include_archived else "WHERE ge.archived_at IS NULL"
    rows = conn.execute(f"""
        SELECT ge.*,
               gp.weight_kg, gp.sets AS last_sets, gp.reps AS last_reps,
               gp.duration_s AS last_duration_s,
               gp.distance_m AS last_distance_m, gp.setting AS last_setting,
               gp.successful, gp.recorded_at,
               (SELECT COUNT(*) FROM gym_progression WHERE exercise_id = ge.id) AS history_count
        FROM gym_exercises ge
        LEFT JOIN gym_progression gp ON gp.id = (
            SELECT id FROM gym_progression
            WHERE exercise_id = ge.id
            ORDER BY recorded_at DESC LIMIT 1
        )
        {where}
        ORDER BY ge.sort_order, ge.tier, ge.name
    """).fetchall()

    fn_rows = conn.execute(
        "SELECT muscle_group, key, label, sort_order FROM gym_functions "
        "ORDER BY muscle_group, sort_order, key"
    ).fetchall()
    conn.close()

    # Function metadata lookup: (muscle, key) -> {label, sort_order}
    fn_meta = {}
    fn_order_by_muscle = {}
    for fr in fn_rows:
        fn_meta[(fr['muscle_group'], fr['key'])] = {'label': fr['label'], 'sort_order': fr['sort_order']}
        fn_order_by_muscle.setdefault(fr['muscle_group'], []).append(fr['key'])

    # Muscle display order + labels are owned by app.py; mirror them here to
    # keep gym_bank importable standalone. app.py passes the same order to the
    # template, so any divergence is cosmetic only.
    from_order = _muscle_order()
    labels = _muscle_labels()

    # Bucket exercises by muscle -> function key
    by_muscle = {}
    for r in rows:
        entry = dict(r)
        entry['engagement'] = json.loads(entry['engagement'] or '{}')
        # Compact 'latest' readout for the frontend
        if entry.get('recorded_at'):
            entry['latest'] = {
                'weight_kg': entry.get('weight_kg'),
                'reps': entry.get('last_reps'),
                'sets': entry.get('last_sets'),
                'duration_s': entry.get('last_duration_s'),
                'distance_m': entry.get('last_distance_m'),
                'setting': entry.get('last_setting'),
                'recorded_at': entry.get('recorded_at'),
            }
        else:
            entry['latest'] = None
        by_muscle.setdefault(entry['muscle_group'], []).append(entry)

    # Muscle order: known order first, then any stragglers alphabetically
    ordered_muscles = [m for m in from_order if m in by_muscle]
    ordered_muscles += sorted(m for m in by_muscle if m not in from_order)

    tree = []
    for mg in ordered_muscles:
        exs = by_muscle[mg]
        # Function order for this muscle: seeded order, then any raw keys not in
        # gym_functions (rendered under their own label), then Unassigned last.
        seeded_keys = fn_order_by_muscle.get(mg, [])
        present_keys = []
        for e in exs:
            k = e.get('function')
            if k and k not in present_keys:
                present_keys.append(k)
        # Compose ordering: ALL seeded keys (in seed order) — including ones
        # with zero exercises so a freshly-created function is visible in the
        # databank before anything is filed under it — then any
        # present-but-unseeded keys (alphabetical), then None (Unassigned).
        ordered_keys = list(seeded_keys)
        ordered_keys += sorted(k for k in present_keys if k not in seeded_keys)

        functions = []
        for k in ordered_keys:
            members = [e for e in exs if e.get('function') == k]
            members.sort(key=lambda e: (e.get('sort_order', 0), e['tier'], e['name']))
            meta = fn_meta.get((mg, k))
            label = meta['label'] if meta else k  # raw key label if unseeded
            functions.append({'key': k, 'label': label, 'exercises': members})

        # Unassigned (function NULL or empty)
        unassigned = [e for e in exs if not e.get('function')]
        if unassigned:
            unassigned.sort(key=lambda e: (e.get('sort_order', 0), e['tier'], e['name']))
            functions.append({'key': None, 'label': 'Unassigned', 'exercises': unassigned})

        active_count = sum(1 for e in exs if e.get('is_enabled') and not e.get('archived_at'))
        tree.append({
            'key': mg,
            'label': labels.get(mg, mg.title()),
            'functions': functions,
            'active_count': active_count,
            'total_count': len(exs),
        })
    return tree


# Muscle order/labels mirror app.py's _GYM_GROUP_ORDER / _GYM_GROUP_LABELS so
# gym_bank stays importable without a circular import. Kept in one place here.
def _muscle_order():
    return ['chest', 'shoulders', 'back', 'legs', 'core', 'arms', 'stability', 'muay_thai', 'cardio']


def _muscle_labels():
    return {
        'chest': 'Chest', 'shoulders': 'Shoulders', 'back': 'Back',
        'legs': 'Legs', 'core': 'Core', 'arms': 'Arms', 'stability': 'Stability',
        'muay_thai': 'Muay Thai', 'cardio': 'Cardio',
    }


def get_gym_exercise(exercise_id):
    """Single exercise with latest progression."""
    conn = _gym_db()
    row = conn.execute("""
        SELECT ge.*,
               gp.weight_kg, gp.sets AS last_sets, gp.reps AS last_reps,
               gp.duration_s AS last_duration_s,
               gp.distance_m AS last_distance_m, gp.setting AS last_setting,
               gp.successful, gp.recorded_at
        FROM gym_exercises ge
        LEFT JOIN gym_progression gp ON gp.id = (
            SELECT id FROM gym_progression
            WHERE exercise_id = ge.id
            ORDER BY recorded_at DESC LIMIT 1
        )
        WHERE ge.id = ?
    """, (exercise_id,)).fetchone()
    conn.close()
    if row:
        entry = dict(row)
        entry['engagement'] = json.loads(entry['engagement'] or '{}')
        return entry
    return None


def get_gym_exercises_by_muscle(muscle_group, enabled_only=True, include_archived=False):
    """All exercises for a muscle group. Archived excluded by default."""
    conn = _gym_db()
    sql = "SELECT * FROM gym_exercises WHERE muscle_group = ?"
    if enabled_only:
        sql += " AND is_enabled = 1"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY sort_order, tier, function, name"
    rows = conn.execute(sql, (muscle_group,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gym_exercises_by_tier(tier, enabled_only=True, include_archived=False):
    """All exercises for a given tier. Archived excluded by default."""
    conn = _gym_db()
    sql = "SELECT * FROM gym_exercises WHERE tier = ?"
    if enabled_only:
        sql += " AND is_enabled = 1"
    if not include_archived:
        sql += " AND archived_at IS NULL"
    sql += " ORDER BY muscle_group, sort_order, function, name"
    rows = conn.execute(sql, (tier,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Functions lookup ─────────────────────────────────────────────────────────

def get_gym_functions(muscle_group=None):
    """Return gym_functions rows, optionally filtered to one muscle group,
    ordered by muscle then sort_order."""
    conn = _gym_db()
    if muscle_group:
        rows = conn.execute(
            "SELECT * FROM gym_functions WHERE muscle_group = ? ORDER BY sort_order, key",
            (muscle_group,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM gym_functions ORDER BY muscle_group, sort_order, key"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def gym_add_function(muscle_group, key, label, sort_order=None):
    """Add a function row. Returns (ok, error). If sort_order is None it is
    placed at the end of that muscle group's list."""
    muscle_group = (muscle_group or '').strip()
    key = (key or '').strip().lower()
    label = (label or '').strip()
    if not muscle_group or not key or not label:
        return False, 'Muscle group, key and label are all required.'
    conn = _gym_db()
    try:
        if sort_order is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 AS nxt FROM gym_functions "
                "WHERE muscle_group = ?", (muscle_group,)
            ).fetchone()
            sort_order = row['nxt']
        conn.execute(
            "INSERT INTO gym_functions (muscle_group, key, label, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (muscle_group, key, label, sort_order)
        )
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, f'Function "{key}" already exists for {muscle_group}.'
    finally:
        conn.close()


# ── Progression ──────────────────────────────────────────────────────────────

def log_gym_set(exercise_id, weight_kg, reps, sets=1, successful=True,
                session_id=None, duration_s=None, distance_m=None, setting=None,
                recorded_at=None):
    """Record one completed set. No auto-suggestion.
    duration_s (seconds) is optional and only meaningful for timed work.
    distance_m (metres) + setting (free-text machine level/resistance) are
    optional and only meaningful for cardio work.

    recorded_at (TEXT 'YYYY-MM-DD HH:MM:SS') overrides the default
    CURRENT_TIMESTAMP. This MUST be set for back-dated logging: every
    recorded_at-grouped view (undo_last_today, get_previous_session_sets, the
    week board, scheme accumulation) buckets by _training_day('recorded_at'),
    so a set left on the UTC-now default would land on today even though its
    session is back-dated. Stamp it at noon of the target day so the
    -ROLLOVER_HOUR shift cannot misfile it. None keeps the live-path default."""
    conn = _gym_db()
    if recorded_at:
        cur = conn.execute(
            "INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, successful, "
            "session_id, duration_s, distance_m, setting, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exercise_id, weight_kg, sets, reps, 1 if successful else 0, session_id,
             duration_s, distance_m, setting, recorded_at)
        )
    else:
        cur = conn.execute(
            "INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, successful, "
            "session_id, duration_s, distance_m, setting) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exercise_id, weight_kg, sets, reps, 1 if successful else 0, session_id,
             duration_s, distance_m, setting)
        )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def get_gym_history(exercise_id, limit=10):
    """Last N logged sets for an exercise, most recent first."""
    conn = _gym_db()
    rows = conn.execute(
        "SELECT * FROM gym_progression WHERE exercise_id = ? "
        "ORDER BY recorded_at DESC LIMIT ?",
        (exercise_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_today_tally(exercise_id):
    """Sets/reps already logged today for one exercise (current training day)."""
    conn = _gym_db()
    row = conn.execute(
        "SELECT COUNT(*) AS sets, COALESCE(SUM(reps), 0) AS reps "
        "FROM gym_progression "
        "WHERE exercise_id = ? AND {day} = {today}".format(
            day=_training_day('recorded_at'), today=TODAY_SQL),
        (exercise_id,)
    ).fetchone()
    conn.close()
    return {'sets': row['sets'], 'reps': row['reps']}


def undo_last_today(exercise_id, session_id=None, day=None):
    """Delete the most recently logged set for this exercise within a training
    day. Used to walk back an accidental tap. Returns True if a row was deleted.

    When session_id is given the undo is scoped to that session, so undo can
    never reach back into an earlier session that happens to share the day.
    `day` ('YYYY-MM-DD') scopes the undo to a PAST training day; None (default)
    is today. Combined with session_id scoping, an undo on a back-dated day can
    never reach today's session.
    """
    day_rhs, day_params = _day_sql(day)
    conn = _gym_db()
    params = [exercise_id]
    scope = ""
    if session_id is not None:
        scope = "AND session_id = ? "
        params.append(session_id)
    params = params + list(day_params)
    row = conn.execute(
        "SELECT id FROM gym_progression "
        "WHERE exercise_id = ? {scope}AND {day} = {today} "
        "ORDER BY id DESC LIMIT 1".format(
            scope=scope, day=_training_day('recorded_at'), today=day_rhs),
        params
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("DELETE FROM gym_progression WHERE id = ?", (row['id'],))
    conn.commit()
    conn.close()
    return True


def get_weight_rep_day_groups(max_tier=3, before_today=True):
    """Raw material for the scheme-accumulation grid.

    Returns [{exercise_id, name, tier, day, weight_kg, reps, n_sets}] — one row
    per (exercise, training day, weight, reps), with n_sets counting how many
    sets landed there. Only loaded rep work qualifies: the grid's axes are
    weight and sets x reps, which mean nothing for a plank or a row machine
    session, and tier 4 (Fa Jin) is explosive work that is not laddered.

    before_today (default True) excludes the current training day, so the home
    panel is stable mid-session and only reflects days that are actually
    finished. Feature 02 (HANDOFF §5.2) now also calls it with before_today=False
    to get EVERY day including today — the finish recap / _scheme_delta diff the
    with-today and without-today pass counts, and the home panel includes today
    once the session is finished. Either way the day boundary is the same
    `{day} < {today}` idiom get_previous_session_sets() uses, through
    _training_day() / TODAY_SQL rather than a raw DATE().

    COALESCE(sets, 1) folds in the legacy import_history rows, which collapse
    several identical sets into one row with sets > 1.
    """
    day_expr = _training_day('p.recorded_at')
    where = [
        "e.tracking_type = 'weight_reps'",
        "e.tier BETWEEN 1 AND ?",
        "e.is_enabled = 1",
        "e.archived_at IS NULL",
        "p.weight_kg > 0",
        "p.reps > 0",
    ]
    if before_today:
        where.append("{day} < {today}".format(day=day_expr, today=TODAY_SQL))

    conn = _gym_db()
    rows = conn.execute(
        "SELECT p.exercise_id AS exercise_id, e.name AS name, e.tier AS tier, "
        "{day} AS day, p.weight_kg AS weight_kg, p.reps AS reps, "
        "SUM(COALESCE(p.sets, 1)) AS n_sets "
        "FROM gym_progression p JOIN gym_exercises e ON e.id = p.exercise_id "
        "WHERE {where} "
        "GROUP BY p.exercise_id, day, p.weight_kg, p.reps "
        "ORDER BY p.exercise_id, day, p.weight_kg, p.reps".format(
            day=day_expr, where=" AND ".join(where)),
        (int(max_tier),)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_gym_set(set_id, session_id=None):
    """Delete one specific logged set by id.

    undo_last_today() removes the NEWEST set for an exercise, which is the
    right shape for walking back an accidental tap. The Train page's set table
    needs the other shape: a mistyped set is rarely still the most recent one
    by the time it is noticed.

    Scoped to a session when given, so a stale tab can never reach back into an
    earlier day's history — the same guard, and for the same reason, as
    undo_last_today(). Returns True when a row was removed.
    """
    conn = _gym_db()
    params = [set_id]
    scope = ""
    if session_id is not None:
        scope = " AND session_id = ?"
        params.append(session_id)
    cur = conn.execute("DELETE FROM gym_progression WHERE id = ?" + scope, params)
    conn.commit()
    removed = cur.rowcount > 0
    conn.close()
    return removed


# ── Sessions ─────────────────────────────────────────────────────────────────

def start_gym_session():
    """Begin a new workout session. Returns the new session id."""
    conn = _gym_db()
    cur = conn.execute("INSERT INTO gym_sessions (performed_at, finished) VALUES (CURRENT_TIMESTAMP, 0)")
    conn.commit()
    session_id = cur.lastrowid
    conn.close()
    return session_id


def get_or_create_today_gym_session(day=None):
    """Find the still-open gym session for a training day, or start a new one.
    Lets one-touch logging (e.g. Muay Thai on the Train page) drop straight into
    the existing session/progression backend without a separate
    Start/End Session step.

    `day` ('YYYY-MM-DD') targets a PAST training day for back-dated logging;
    None (the default) is today, so the live "log right now" path is unchanged.
    A back-dated session is stamped performed_at at noon of that day so the
    -ROLLOVER_HOUR shift can never misfile it into an adjacent training day."""
    day_rhs, day_params = _day_sql(day)
    conn = _gym_db()
    row = conn.execute(
        "SELECT id FROM gym_sessions "
        "WHERE finished = 0 AND {day} = {today} "
        "ORDER BY id DESC LIMIT 1".format(
            day=_training_day('performed_at'), today=day_rhs),
        day_params
    ).fetchone()
    if not row:
        # Already finished that day, then logged again: re-open that session
        # rather than starting a second one. get_live_session_state() orders by
        # `finished ASC`, so a new row would become "the" live session and every
        # set logged before Finish would vanish from the Train page — nothing
        # lost in the database, but on a phone mid-gym it reads as the app
        # eating the workout. One training day is one session.
        row = conn.execute(
            "SELECT id FROM gym_sessions WHERE {day} = {today} "
            "ORDER BY id DESC LIMIT 1".format(
                day=_training_day('performed_at'), today=day_rhs),
            day_params
        ).fetchone()
        if row:
            conn.execute("UPDATE gym_sessions SET finished = 0, ended_at = NULL WHERE id = ?",
                         (row['id'],))
            conn.commit()
    if row:
        session_id = row['id']
    elif day:
        # Noon-stamp the back-dated session (see docstring).
        cur = conn.execute(
            "INSERT INTO gym_sessions (performed_at, finished) VALUES (?, 0)",
            (day + " 12:00:00",))
        conn.commit()
        session_id = cur.lastrowid
    else:
        cur = conn.execute("INSERT INTO gym_sessions (performed_at, finished) VALUES (CURRENT_TIMESTAMP, 0)")
        conn.commit()
        session_id = cur.lastrowid
    conn.close()
    return session_id


def end_gym_session(session_id):
    """Mark a workout session finished. Returns False if it doesn't exist."""
    conn = _gym_db()
    cur = conn.execute(
        "UPDATE gym_sessions SET finished = 1, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
        (session_id,)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


# ── Write / Admin ────────────────────────────────────────────────────────────

EXERCISE_CLASSES = ('bodyweight', 'strength')


def gym_add_exercise(name, tier, muscle_group, function=None, is_enabled=True,
                     engagement=None, notes=None, reps_min=None, reps_max=None, sets=None,
                     tracking_type='weight_reps', sort_order=0, exercise_class='strength'):
    """Add a new exercise to the GYM bank. Returns (ok, error).
    tracking_type must be one of TRACKING_TYPES; is_weighted is derived from it.
    exercise_class is 'bodyweight' or 'strength' (defaults to 'strength')."""
    name = (name or '').strip()
    if not name:
        return False, 'Name is required.'
    if tier not in (1, 2, 3, 4, 5):
        return False, 'Tier must be 1–5.'
    if tracking_type not in TRACKING_TYPES:
        return False, f'Invalid tracking type "{tracking_type}".'
    if exercise_class not in EXERCISE_CLASSES:
        return False, f'Invalid exercise class "{exercise_class}".'
    conn = _gym_db()
    try:
        conn.execute(
            "INSERT INTO gym_exercises (name, tier, muscle_group, function, is_enabled, "
            "engagement, notes, reps_min, reps_max, sets, tracking_type, sort_order, "
            "is_weighted, exercise_class) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, tier, muscle_group, function, 1 if is_enabled else 0,
             json.dumps(engagement or {}), notes, reps_min, reps_max, sets,
             tracking_type, sort_order, _iw_from_tracking(tracking_type), exercise_class)
        )
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, f'An exercise named "{name}" already exists.'
    except sqlite3.Error as e:
        return False, _db_error_message(e)
    finally:
        conn.close()


def gym_update_exercise(exercise_id, **kwargs):
    """Update fields on an existing exercise. Pass only the fields to change."""
    allowed = {'name', 'tier', 'muscle_group', 'function', 'is_enabled',
               'engagement', 'notes', 'reps_min', 'reps_max', 'sets',
               'tracking_type', 'sort_order', 'archived_at', 'exercise_class'}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False, 'Nothing to update.'
    if 'tracking_type' in updates and updates['tracking_type'] not in TRACKING_TYPES:
        return False, f'Invalid tracking type "{updates["tracking_type"]}".'
    if 'exercise_class' in updates and updates['exercise_class'] not in EXERCISE_CLASSES:
        return False, f'Invalid exercise class "{updates["exercise_class"]}".'
    if 'engagement' in updates and isinstance(updates['engagement'], dict):
        updates['engagement'] = json.dumps(updates['engagement'])
    # Keep the DEPRECATED is_weighted flag in sync whenever tracking_type moves.
    if 'tracking_type' in updates:
        updates['is_weighted'] = _iw_from_tracking(updates['tracking_type'])
    set_clause = ', '.join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [exercise_id]
    conn = _gym_db()
    try:
        conn.execute(f"UPDATE gym_exercises SET {set_clause} WHERE id = ?", values)
        conn.commit()
        return True, None
    except sqlite3.IntegrityError as e:
        return False, str(e)
    except sqlite3.Error as e:
        return False, _db_error_message(e)
    finally:
        conn.close()


def gym_set_enabled(exercise_id, enabled):
    """Toggle exercise enabled/disabled. Independent of archived_at."""
    conn = _gym_db()
    conn.execute("UPDATE gym_exercises SET is_enabled = ? WHERE id = ?",
                 (1 if enabled else 0, exercise_id))
    conn.commit()
    conn.close()


def gym_archive_exercise(exercise_id):
    """Soft-delete: hide from the bank + pickers, keep history + analytics.
    Reversible via gym_unarchive_exercise. No-op if already archived."""
    conn = _gym_db()
    conn.execute(
        "UPDATE gym_exercises SET archived_at = CURRENT_TIMESTAMP "
        "WHERE id = ? AND archived_at IS NULL",
        (exercise_id,)
    )
    conn.commit()
    conn.close()


def gym_unarchive_exercise(exercise_id):
    """Reverse an archive: set archived_at back to NULL."""
    conn = _gym_db()
    conn.execute("UPDATE gym_exercises SET archived_at = NULL WHERE id = ?", (exercise_id,))
    conn.commit()
    conn.close()


def gym_delete_exercise(exercise_id):
    """Hard delete — ONLY when the exercise has zero gym_progression rows.
    Returns (ok, error). On refusal returns a clear error so the API can
    surface it; history-bearing exercises must be archived instead."""
    conn = _gym_db()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM gym_progression WHERE exercise_id = ?",
            (exercise_id,)
        ).fetchone()['c']
        if n > 0:
            return False, f'has {n} logged set{"s" if n != 1 else ""} — archive it instead'
        cur = conn.execute("DELETE FROM gym_exercises WHERE id = ?", (exercise_id,))
        conn.commit()
        if cur.rowcount == 0:
            return False, 'Exercise not found.'
        return True, None
    finally:
        conn.close()


def gym_reorder_function(function_key, ordered_ids):
    """Persist a new sort_order for a list of exercise ids within one function.
    Writes sort_order = index for each id. function_key is accepted for API
    symmetry / future validation but ordering is applied by id list only."""
    conn = _gym_db()
    try:
        for idx, ex_id in enumerate(ordered_ids):
            conn.execute("UPDATE gym_exercises SET sort_order = ? WHERE id = ?", (idx, ex_id))
        conn.commit()
        return True, None
    finally:
        conn.close()


# ── Engagement Map (replaces hardcoded EXERCISE_MUSCLE_MAP) ───────────────────

def get_gym_muscle_engagement():
    """Return the full muscle engagement map from the DB.
    Format: {exercise_name: {muscle: weight, ...}, ...}
    Archived exercises are excluded.
    """
    conn = _gym_db()
    rows = conn.execute(
        "SELECT name, engagement FROM gym_exercises "
        "WHERE is_enabled = 1 AND archived_at IS NULL"
    ).fetchall()
    conn.close()
    return {r['name']: json.loads(r['engagement'] or '{}') for r in rows}


# ── Live Workout Engine ──────────────────────────────────────────────────────
# The Train page is a "working out right now" surface. Every set is a
# gym_progression row written the instant it's logged (log_gym_set). A workout
# session is a gym_sessions row; its muscle groups are DERIVED from the distinct
# muscle_groups of the exercises logged against it — there is no stored
# "muscle of the day". These helpers supply pre-fill, rehydrate and roll-over.

def get_exercise_prefill(exercise_id):
    """Pre-fill source for opening an exercise in the live view.

    Returns {'most_recent': {...}|None, 'best_recent': {...}|None} where:
      - most_recent -> the last logged set (drives the pre-filled working set)
      - best_recent -> the heaviest recent set (weight_kg, then duration_s,
        then reps as tie-breakers) shown as reference only.
    'best_recent' is looked at over the last ~20 logged sets so it reflects
    genuinely recent work, not an all-time PR.
    """
    conn = _gym_db()
    rows = conn.execute(
        "SELECT id, weight_kg, sets, reps, duration_s, distance_m, setting, "
        "successful, recorded_at "
        "FROM gym_progression WHERE exercise_id = ? "
        "ORDER BY recorded_at DESC, id DESC LIMIT 20",
        (exercise_id,)
    ).fetchall()
    conn.close()
    if not rows:
        return {'most_recent': None, 'best_recent': None}
    most_recent = dict(rows[0])
    best = max(
        rows,
        key=lambda r: (
            r['weight_kg'] or 0,
            r['duration_s'] or 0,
            r['reps'] or 0,
        )
    )
    return {'most_recent': most_recent, 'best_recent': dict(best)}


# ── Recency, estimated 1RM and previous-session history ──────────────────────
# These three power the Train page's coverage strip and its exercise card.
# All are pure reads over gym_progression — none of them needs a schema change.
#
# Every "which day is this" comparison goes through _training_day() / TODAY_SQL.
# A raw DATE(recorded_at) here would reintroduce the exact two-calendar bug
# that §1.1 of docs/REVIEW-train-hardening.md was written about: in UTC+11 a
# morning set is stored under the previous UTC day, so "days since" would read
# one day stale for every session logged before ~10 AM.

def get_exercise_recency():
    """{exercise_id: {'last_day': 'YYYY-MM-DD'|None, 'days_since': int|None}}.

    Drives the staleness readouts on the coverage strip ("8d", "21d"). One
    GROUP BY over the whole table — the day delta is done in Python because
    julianday() over the shifted CASE expression is far harder to read than it
    is worth. Exercises that have never been logged are simply absent from the
    map; callers treat a missing key as "never".
    """
    conn = _gym_db()
    rows = conn.execute(
        "SELECT exercise_id, MAX({day}) AS last_day "
        "FROM gym_progression GROUP BY exercise_id".format(
            day=_training_day('recorded_at'))
    ).fetchall()
    trow = conn.execute("SELECT {today} AS d".format(today=TODAY_SQL)).fetchone()
    conn.close()

    try:
        today = date.fromisoformat(trow['d'])
    except (TypeError, ValueError):
        today = date.today()

    out = {}
    for r in rows:
        last_day, days = r['last_day'], None
        if last_day:
            try:
                days = (today - date.fromisoformat(last_day)).days
            except (TypeError, ValueError):
                days = None
        out[r['exercise_id']] = {'last_day': last_day, 'days_since': days}
    return out


def get_e1rm_bests(max_reps=12):
    """{exercise_id: float} — best Epley estimated 1RM, w * (1 + reps/30).

    Capped at max_reps because Epley diverges badly on high-rep work: an
    EZ-Bar 777s set at 21 reps would otherwise outrank every genuine heavy
    single and make the PR badge meaningless. Only weighted, rep-counted sets
    qualify, so timed and bodyweight work is absent rather than zero.
    """
    conn = _gym_db()
    rows = conn.execute(
        "SELECT exercise_id, MAX(weight_kg * (1 + reps / 30.0)) AS best "
        "FROM gym_progression "
        "WHERE weight_kg > 0 AND reps > 0 AND reps <= ? "
        "GROUP BY exercise_id",
        (max_reps,)
    ).fetchall()
    conn.close()
    return {r['exercise_id']: r['best'] for r in rows if r['best']}


def get_previous_session_sets(exercise_id, limit=12, before=None):
    """The ordered sets of the most recent PRIOR outing for one exercise.

    Returns {'day': 'YYYY-MM-DD'|None, 'days_since': int|None, 'sets': [...]}.

    "Prior" is the newest training day strictly before the reference day on
    which this exercise was logged. The reference day is today by default, or
    `before` ('YYYY-MM-DD') when logging for a PAST day — so the PREV column
    lines up with the outing before the day being logged, not before today.
    Grouping by training day rather than session_id is deliberate: 33 legacy
    rows imported by import_history.py carry session_id IS NULL, and those are
    exactly the oldest history worth showing.

    Feeds the PREV column of the set table, so set 3 is compared against set 3
    instead of against an undifferentiated blob from last time.
    """
    ref_rhs, ref_params = _day_sql(before)
    conn = _gym_db()
    day_expr = _training_day('recorded_at')
    drow = conn.execute(
        "SELECT MAX({day}) AS d FROM gym_progression "
        "WHERE exercise_id = ? AND {day} < {today}".format(
            day=day_expr, today=ref_rhs),
        (exercise_id, *ref_params)
    ).fetchone()
    prev_day = drow['d'] if drow else None
    if not prev_day:
        conn.close()
        return {'day': None, 'days_since': None, 'sets': []}

    rows = conn.execute(
        "SELECT weight_kg, sets, reps, duration_s, distance_m, setting, "
        "successful, recorded_at FROM gym_progression "
        "WHERE exercise_id = ? AND {day} = ? "
        "ORDER BY recorded_at ASC, id ASC".format(day=day_expr),
        (exercise_id, prev_day)
    ).fetchall()
    trow = conn.execute("SELECT {today} AS d".format(today=ref_rhs), ref_params).fetchone()
    conn.close()

    out = []
    for r in rows:
        # Legacy imported rows collapse several identical sets into one row with
        # sets > 1. Expand them so the PREV column still lines up set-for-set;
        # live rows are always sets = 1 and pass through untouched.
        n = r['sets'] if (r['sets'] or 0) > 1 else 1
        for _ in range(n):
            if len(out) >= limit:
                break
            out.append({
                'weight_kg': r['weight_kg'], 'reps': r['reps'],
                'duration_s': r['duration_s'], 'distance_m': r['distance_m'],
                'setting': r['setting'], 'successful': bool(r['successful']),
            })
        if len(out) >= limit:
            break

    days_since = None
    try:
        days_since = (date.fromisoformat(trow['d']) - date.fromisoformat(prev_day)).days
    except (TypeError, ValueError, KeyError):
        pass
    return {'day': prev_day, 'days_since': days_since, 'sets': out}


def _exercise_meta_map():
    """id -> {name, muscle_group, function, tier, tracking_type} for all
    exercises (including archived, so historical sets still resolve)."""
    conn = _gym_db()
    rows = conn.execute(
        "SELECT id, name, muscle_group, function, tier, tracking_type "
        "FROM gym_exercises"
    ).fetchall()
    conn.close()
    return {r['id']: dict(r) for r in rows}


# ── Stage-bar segment classification (Feature 01, HANDOFF §4.1) ─────────────
# The /train stage bar groups a session's live sets into pinned stages:
# Warm-up → T1 → T2 (optional) → T3[shoulders|arms|core]. The server tags each
# set with its segment so the client derivation (stageState) stays a pure
# function of the set list. 'warm' is NOT returned yet — warm-up sets carry no
# is_warmup flag until train-rehaul phase 2 (PLAN-train-rehaul); it arrives then.
STAGE_T2_GROUPS = ('chest', 'back', 'legs')
STAGE_T3_PARTS = {'shoulders': 't3:sh', 'arms': 't3:ar', 'core': 't3:co'}


def _stage_for(meta):
    """Stage-bar segment for a set: 't1' | 't2' | 't3:sh' | 't3:ar' | 't3:co' | None.

    Keyed off tier + muscle_group (HANDOFF §4.1). Tier 1 is always T1 (BB OHP and
    DB Lateral Raise (heavy) are tier-1 shoulders, so they count as T1, not T3).
    Tier 2/3 split by muscle: chest/back/legs are the optional T2 compound slot;
    shoulders/arms/core are the three T3 accessory parts. Everything else — tier 4
    (Fa Jin), cardio, stability, uncategorised, unknown — has no stage.
    """
    tier = meta.get('tier')
    mg = meta.get('muscle_group')
    if tier == 1:
        return 't1'
    if tier in (2, 3):
        if mg in STAGE_T2_GROUPS:
            return 't2'
        if mg in STAGE_T3_PARTS:
            return STAGE_T3_PARTS[mg]
    return None


def apply_session_rollover():
    """Lazy ~4 AM roll-over stamp (called on Train-page load).

    No scheduler exists, so this runs on app load. If the most recent OPEN
    session's date is before today AND the local time is past ROLLOVER_HOUR,
    close it (finished=1 + ended_at). All its sets are already persisted — this
    is a cosmetic 'this session is over' stamp only. The next set logged on the
    new day lands in a fresh session via get_or_create_today_gym_session().

    Returns the id of the session that was stamped, or None.
    """
    conn = _gym_db()
    row = conn.execute(
        "SELECT id, performed_at FROM gym_sessions "
        "WHERE finished = 0 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        return None
    # The ROLLOVER_HOUR shift is baked into the training-day expression, so a
    # session started yesterday evening is still "today" until 4 AM — no
    # separate hour guard needed (the old one also compared a UTC date against
    # a local one, which closed sessions that had only just been opened).
    guard = conn.execute(
        "SELECT {day} < {today} AS is_prior".format(
            day=_training_day('?'), today=TODAY_SQL),
        (row['performed_at'],) * _training_day('?').count('?')
    ).fetchone()
    stamped = None
    if guard['is_prior']:
        conn.execute(
            "UPDATE gym_sessions SET finished = 1, ended_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (row['id'],)
        )
        conn.commit()
        stamped = row['id']
    conn.close()
    return stamped


def _checklist_for_muscle(mg, logged_functions):
    """Build the function-completion checklist for one muscle group.
    Every seeded function starts unticked; a function ticks once any set for an
    exercise of that function has been logged this session. Returns a list of
    {'key', 'label', 'done'} in seeded order (then any raw keys logged that
    aren't seeded, then Unassigned if a NULL-function set was logged)."""
    fns = get_gym_functions(mg)  # seeded functions for this muscle, ordered
    checklist = []
    seen = set()
    for f in fns:
        checklist.append({
            'key': f['key'], 'label': f['label'],
            'done': f['key'] in logged_functions,
        })
        seen.add(f['key'])
    for k in sorted(x for x in logged_functions if x is not None):
        if k in seen:
            continue
        checklist.append({'key': k, 'label': k.title(), 'done': True})
        seen.add(k)
    if None in logged_functions:
        checklist.append({'key': None, 'label': 'Unassigned', 'done': True})
    return checklist


def get_live_session_state(day=None):
    """Return a day's live-session state for rehydrate.

    Applies the lazy roll-over stamp first, then reads the open session for the
    training day and every set logged against it. Muscle groups are derived; the
    function checklist per muscle is derived from which functions have logged
    sets.

    `day` ('YYYY-MM-DD') rehydrates a PAST training day for back-dated logging;
    None (the default) is today, so the live path is unchanged.
    """
    apply_session_rollover()

    day_rhs, day_params = _day_sql(day)
    conn = _gym_db()
    srow = conn.execute(
        "SELECT id, finished FROM gym_sessions "
        "WHERE {day} = {today} "
        "ORDER BY finished ASC, id DESC LIMIT 1".format(
            day=_training_day('performed_at'), today=day_rhs),
        day_params
    ).fetchone()
    if not srow:
        conn.close()
        return {'session_id': None, 'has_sets': False, 'sets': [],
                'muscles': [], 'current_exercise_id': None, 'finished': False}
    session_id = srow['id']
    prog = conn.execute(
        "SELECT id, exercise_id, weight_kg, sets, reps, duration_s, "
        "distance_m, setting, successful, recorded_at FROM gym_progression "
        "WHERE session_id = ? ORDER BY recorded_at ASC, id ASC",
        (session_id,)
    ).fetchall()
    conn.close()

    meta = _exercise_meta_map()
    labels = _muscle_labels()
    order = _muscle_order()

    sets = []
    for r in prog:
        m = meta.get(r['exercise_id'], {})
        sets.append({
            'id': r['id'], 'exercise_id': r['exercise_id'],
            'exercise_name': m.get('name', "#%s" % r['exercise_id']),
            'muscle_group': m.get('muscle_group'),
            'function': m.get('function'),
            'tier': m.get('tier'),
            'stage': _stage_for(m),  # Feature 01 stage-bar segment (HANDOFF §4.1)
            'tracking_type': m.get('tracking_type', 'weight_reps'),
            'weight_kg': r['weight_kg'], 'reps': r['reps'],
            'duration_s': r['duration_s'],
            'distance_m': r['distance_m'], 'setting': r['setting'],
            'successful': bool(r['successful']),
            'recorded_at': r['recorded_at'],
        })

    # 1-based ordinal within the exercise. The set table labels rows from this
    # rather than from array position, so deleting set 2 renumbers 3 -> 2
    # server-side instead of leaving the client to guess.
    _seen = {}
    for s_ in sets:
        _seen[s_['exercise_id']] = _seen.get(s_['exercise_id'], 0) + 1
        s_['n'] = _seen[s_['exercise_id']]

    by_muscle = {}
    fns_logged = {}
    for s in sets:
        mg = s['muscle_group']
        by_muscle.setdefault(mg, []).append(s)
        fns_logged.setdefault(mg, set()).add(s['function'])

    ordered_muscles = [m for m in order if m in by_muscle]
    ordered_muscles += sorted(m for m in by_muscle if m not in order)

    muscles = []
    for mg in ordered_muscles:
        muscles.append({
            'key': mg,
            'label': labels.get(mg, (mg or 'Other').title()),
            'checklist': _checklist_for_muscle(mg, fns_logged.get(mg, set())),
            'sets': by_muscle[mg],
        })

    current_exercise_id = sets[-1]['exercise_id'] if sets else None
    return {
        'session_id': session_id,
        'has_sets': bool(sets),
        'sets': sets,
        'muscles': muscles,
        'current_exercise_id': current_exercise_id,
        'finished': bool(srow['finished']),  # Feature 01/02 explicit done-state
    }


def is_today_session_finished():
    """True if today's live session is explicitly finished (HANDOFF §5.1).

    Reads the same session get_live_session_state() would pick for today, so the
    home panel's "today counts once finished" rule (Feature 02b) and the live
    stage bar agree. Day boundary via TODAY_SQL only (REVIEW §1.1)."""
    return bool(get_live_session_state().get('finished'))


# ── Last-session card (Today screen) ─────────────────────────────────────────
# The Today "Last session" card historically read the LEGACY sessions/
# session_lifts/exercises tables via _shared_home_context(). Live gym logging
# moved to gym_sessions/gym_progression/gym_exercises in the Train rehaul, so the
# legacy read showed stale data (the newest legacy gym row for the scheme-engine
# profile is months old). get_last_gym_session() reads the MODERN tables, and
# get_last_session_multisource() picks the most recent across every live source.
#
# Both return the dict shape templates/_last_session.html expects:
#   {id, type, date, exercise_names, total_sets, total_volume,
#    distance_m, cardio_name, cardio_dur, cardio_dist, detail_href}
# `date` is an ISO 'YYYY-MM-DD' string (the fmt_date filter's input), derived
# through _training_day so the UTC/local two-calendar boundary is respected.
# `detail_href` lets the card target the modern view: gym sessions have no
# per-session legacy detail page, so they link to /train (B1b).


def get_last_gym_session():
    """Most recent MODERN gym session (gym_sessions) as a last-session card dict.

    Returns None when there is no gym session. Day ordering goes through
    _training_day('performed_at') so a pre-ROLLOVER_HOUR morning session is not
    mis-dated to the previous UTC day. total_volume sums reps*weight_kg over the
    session's gym_progression rows; a purely cardio-tracked session reports
    type='cardio' with its first exercise name + duration/distance instead.
    """
    conn = _gym_db()
    day_expr = _training_day('performed_at')
    srow = conn.execute(
        "SELECT id, performed_at, {day} AS training_day "
        "FROM gym_sessions ORDER BY {day} DESC, id DESC LIMIT 1".format(day=day_expr)
    ).fetchone()
    if not srow:
        conn.close()
        return None
    session_id = srow['id']
    prog = conn.execute(
        "SELECT exercise_id, weight_kg, sets, reps, duration_s, distance_m "
        "FROM gym_progression WHERE session_id = ? ORDER BY recorded_at ASC, id ASC",
        (session_id,)
    ).fetchall()
    conn.close()

    meta = _exercise_meta_map()

    # Ordered, de-duplicated exercise names (first-seen order).
    names = []
    seen = set()
    all_cardio = bool(prog)
    for r in prog:
        m = meta.get(r['exercise_id'], {})
        nm = m.get('name') or ('#%s' % r['exercise_id'])
        if nm not in seen:
            seen.add(nm)
            names.append(nm)
        if m.get('tracking_type') != 'cardio':
            all_cardio = False

    total_sets = sum((r['sets'] or 1) for r in prog)
    total_volume = sum(((r['reps'] or 0) * (r['weight_kg'] or 0)) for r in prog)
    cardio_dur = sum((r['duration_s'] or 0) for r in prog)
    cardio_dist = sum((r['distance_m'] or 0) for r in prog)

    card = {
        'id': session_id,
        'date': srow['training_day'],
        'exercise_names': ', '.join(names),
        'total_sets': total_sets,
        'total_volume': total_volume,
        'distance_m': 0,
        'cardio_name': names[0] if names else None,
        'cardio_dur': cardio_dur,
        'cardio_dist': cardio_dist,
        # Modern gym sessions have no legacy per-session detail page; the card
        # points at the live Train view (B1b).
        'detail_href': '/train',
    }
    # A session whose every exercise is cardio-tracked reads better as a cardio
    # card (duration/distance) than as a 0-volume gym card.
    card['type'] = 'cardio' if all_cardio else 'gym'
    return card


def get_last_session_multisource(pid, legacy_fallback=None):
    """Pick the most recent session across every live source for this profile.

    The scheme engine's gym data lives in the modern gym_* tables (which carry no
    profile_id and so serve the single scheme-engine profile); swim/cardio/pilates
    still live in their own stores. This gathers the newest candidate from each,
    normalises to the last-session card dict, and returns the one with the latest
    training day. `legacy_fallback` is the already-built legacy last_session row
    (or None) — used for swim/cardio that have not been migrated, and as the
    answer when there is no modern gym session at all.

    Comparison is by the card's ISO `date` string (lexicographic == chronological
    for YYYY-MM-DD), with the modern gym session winning ties on the same day
    since finishing a gym session is the most recent explicit act.
    """
    candidates = []

    gym = get_last_gym_session()
    if gym and gym.get('date'):
        candidates.append((gym['date'], 0, gym))

    if legacy_fallback is not None:
        lf = dict(legacy_fallback)
        lf.setdefault('detail_href', '/session/%s' % lf.get('id'))
        d = lf.get('date')
        if d:
            # Legacy rows sort BEHIND a same-day modern gym session (order key 1).
            candidates.append((d, 1, lf))

    if not candidates:
        return None
    # Latest date first; within a day the lower order-key (modern gym) wins.
    candidates.sort(key=lambda c: (c[0], -c[1]), reverse=True)
    return candidates[0][2]


# ── Week board activity (Today screen "This week") ───────────────────────────
# The week board classifies each day by what was trained, to pick a per-day
# icon. For the scheme engine, gym days come from the MODERN gym_sessions/
# gym_progression (the legacy sessions table is stale, so the board showed rest
# tiles for real training days). Muay Thai is not a session type — it is a
# function ('muay_thai') under the cardio muscle group (see
# docs/PLAN-muay-thai-under-cardio.md), so a day is tagged 'muay_thai' when any
# set logged that day is a muay_thai drill, and 'gym' when any non-muay-thai set
# was logged. A day can carry both. Day bucketing goes through
# _training_day('recorded_at') so morning sets land on the right day in UTC+n.


def get_week_activity_days(monday_iso, sunday_iso):
    """{ 'YYYY-MM-DD': [types] } for the modern gym week.

    types is a subset of ('gym', 'muay_thai') per day, 'gym' first when both are
    present (strength is the day's primary read; muay_thai shows as a secondary
    marker). Days with no modern gym activity are simply absent from the map —
    the caller merges these with any legacy swim/pilates activity it already has.
    """
    conn = _gym_db()
    day_expr = _training_day('gp.recorded_at')
    rows = conn.execute(
        "SELECT {day} AS day, "
        "       SUM(CASE WHEN ge.function = 'muay_thai' THEN 1 ELSE 0 END) AS mt, "
        "       SUM(CASE WHEN ge.function IS NULL OR ge.function != 'muay_thai' "
        "                THEN 1 ELSE 0 END) AS other "
        "FROM gym_progression gp "
        "JOIN gym_exercises ge ON ge.id = gp.exercise_id "
        "WHERE {day} BETWEEN ? AND ? "
        "GROUP BY {day}".format(day=day_expr),
        (monday_iso, sunday_iso)
    ).fetchall()
    conn.close()

    out = {}
    for r in rows:
        acts = []
        if (r['other'] or 0) > 0:
            acts.append('gym')
        if (r['mt'] or 0) > 0:
            acts.append('muay_thai')
        if acts:
            out[r['day']] = acts
    return out
