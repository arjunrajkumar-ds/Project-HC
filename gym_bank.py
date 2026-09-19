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
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')

# Allowed tracking_type values. weight_reps = load×reps×sets, reps = bodyweight
# reps, time = timed hold/duration per set, weight_time = load held for a
# duration per set (e.g. weighted plank, farmer's carry). cardio = duration +
# a free-text machine setting, and (when the exercise's engagement spec says so,
# e.g. Rowing) a distance in metres.
TRACKING_TYPES = ('weight_reps', 'reps', 'time', 'weight_time', 'cardio')

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
        # Compose ordering: seeded keys (in seed order) that are present, then
        # any present-but-unseeded keys (alphabetical), then None (Unassigned).
        ordered_keys = [k for k in seeded_keys if k in present_keys]
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
                session_id=None, duration_s=None, distance_m=None, setting=None):
    """Record one completed set. No auto-suggestion.
    duration_s (seconds) is optional and only meaningful for timed work.
    distance_m (metres) + setting (free-text machine level/resistance) are
    optional and only meaningful for cardio work."""
    conn = _gym_db()
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
    """Sets/reps already logged today for one exercise (local-time date)."""
    conn = _gym_db()
    row = conn.execute(
        "SELECT COUNT(*) AS sets, COALESCE(SUM(reps), 0) AS reps "
        "FROM gym_progression "
        "WHERE exercise_id = ? AND DATE(recorded_at) = DATE('now', 'localtime')",
        (exercise_id,)
    ).fetchone()
    conn.close()
    return {'sets': row['sets'], 'reps': row['reps']}


def undo_last_today(exercise_id):
    """Delete the most recently logged set for this exercise, if it was
    logged today. Used to walk back an accidental one-touch tap. Returns
    True if a row was deleted."""
    conn = _gym_db()
    row = conn.execute(
        "SELECT id FROM gym_progression "
        "WHERE exercise_id = ? AND DATE(recorded_at) = DATE('now', 'localtime') "
        "ORDER BY id DESC LIMIT 1",
        (exercise_id,)
    ).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("DELETE FROM gym_progression WHERE id = ?", (row['id'],))
    conn.commit()
    conn.close()
    return True


# ── Sessions ─────────────────────────────────────────────────────────────────

def start_gym_session():
    """Begin a new workout session. Returns the new session id."""
    conn = _gym_db()
    cur = conn.execute("INSERT INTO gym_sessions (performed_at, finished) VALUES (CURRENT_TIMESTAMP, 0)")
    conn.commit()
    session_id = cur.lastrowid
    conn.close()
    return session_id


def get_or_create_today_gym_session():
    """Find today's still-open gym session, or start a new one. Lets
    one-touch logging (e.g. Muay Thai on the Train page) drop straight into
    the existing session/progression backend without a separate
    Start/End Session step."""
    conn = _gym_db()
    row = conn.execute(
        "SELECT id FROM gym_sessions "
        "WHERE finished = 0 AND DATE(performed_at) = DATE('now', 'localtime') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row:
        session_id = row['id']
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

ROLLOVER_HOUR = 4  # sessions from a prior day are stamped after ~4:00 AM local


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
    guard = conn.execute(
        "SELECT DATE(?) < DATE('now','localtime') AS is_prior, "
        "CAST(strftime('%H', 'now','localtime') AS INTEGER) >= ? AS past_hour",
        (row['performed_at'], ROLLOVER_HOUR)
    ).fetchone()
    stamped = None
    if guard['is_prior'] and guard['past_hour']:
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


def get_live_session_state():
    """Return today's live-session state for rehydrate.

    Applies the lazy roll-over stamp first, then reads TODAY's open session and
    every set logged against it. Muscle groups are derived; the function
    checklist per muscle is derived from which functions have logged sets.
    """
    apply_session_rollover()

    conn = _gym_db()
    srow = conn.execute(
        "SELECT id FROM gym_sessions "
        "WHERE DATE(performed_at) = DATE('now','localtime') "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not srow:
        conn.close()
        return {'session_id': None, 'has_sets': False, 'sets': [],
                'muscles': [], 'current_exercise_id': None}
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
            'tracking_type': m.get('tracking_type', 'weight_reps'),
            'weight_kg': r['weight_kg'], 'reps': r['reps'],
            'duration_s': r['duration_s'],
            'distance_m': r['distance_m'], 'setting': r['setting'],
            'recorded_at': r['recorded_at'],
        })

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
    }
