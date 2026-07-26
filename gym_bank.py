"""
gym_bank.py — GYM Exercise Bank helpers
=========================================
Read/write functions for gym_exercises and gym_progression.
Replaces the old get_exercise_bank / bank_add_exercise / bank_update_exercise
for Arjun's profile.

Import into app.py alongside the existing database.py functions.
"""
import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')

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


# ── Read ─────────────────────────────────────────────────────────────────────

def get_gym_bank():
    """Full exercise bank grouped by tier, with latest progression."""
    conn = _gym_db()
    rows = conn.execute("""
        SELECT ge.*,
               gp.weight_kg, gp.sets AS last_sets, gp.reps AS last_reps,
               gp.successful, gp.recorded_at
        FROM gym_exercises ge
        LEFT JOIN gym_progression gp ON gp.id = (
            SELECT id FROM gym_progression
            WHERE exercise_id = ge.id
            ORDER BY recorded_at DESC LIMIT 1
        )
        ORDER BY ge.is_enabled DESC, ge.tier, ge.muscle_group, ge.function, ge.name
    """).fetchall()
    conn.close()

    bank = {1: [], 2: [], 3: [], 4: [], 5: []}
    for r in rows:
        entry = dict(r)
        entry['engagement'] = json.loads(entry['engagement'] or '{}')
        entry['suggestion'] = _suggest_next(entry)
        bank.setdefault(entry['tier'], []).append(entry)
    return bank


def get_gym_exercise(exercise_id):
    """Single exercise with latest progression."""
    conn = _gym_db()
    row = conn.execute("""
        SELECT ge.*,
               gp.weight_kg, gp.sets AS last_sets, gp.reps AS last_reps,
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
        entry['suggestion'] = _suggest_next(entry)
        return entry
    return None


def get_gym_exercises_by_muscle(muscle_group, enabled_only=True):
    """All exercises for a muscle group."""
    conn = _gym_db()
    sql = "SELECT * FROM gym_exercises WHERE muscle_group = ?"
    if enabled_only:
        sql += " AND is_enabled = 1"
    sql += " ORDER BY tier, function, name"
    rows = conn.execute(sql, (muscle_group,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_gym_exercises_by_tier(tier, enabled_only=True):
    """All exercises for a given tier."""
    conn = _gym_db()
    sql = "SELECT * FROM gym_exercises WHERE tier = ?"
    if enabled_only:
        sql += " AND is_enabled = 1"
    sql += " ORDER BY muscle_group, function, name"
    rows = conn.execute(sql, (tier,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Progression ──────────────────────────────────────────────────────────────

def log_gym_set(exercise_id, weight_kg, sets, reps, successful=True):
    """Record a completed set. Returns the suggestion for next attempt."""
    conn = _gym_db()
    conn.execute(
        "INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, successful) "
        "VALUES (?, ?, ?, ?, ?)",
        (exercise_id, weight_kg, sets, reps, 1 if successful else 0)
    )
    conn.commit()
    # Fetch updated exercise for suggestion
    row = conn.execute("""
        SELECT ge.*, gp.weight_kg, gp.sets AS last_sets, gp.reps AS last_reps,
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
        return _suggest_next(entry)
    return None


def get_gym_history(exercise_id, limit=10):
    """Last N progression entries for an exercise."""
    conn = _gym_db()
    rows = conn.execute(
        "SELECT * FROM gym_progression WHERE exercise_id = ? "
        "ORDER BY recorded_at DESC LIMIT ?",
        (exercise_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Write / Admin ────────────────────────────────────────────────────────────

def gym_add_exercise(name, tier, muscle_group, function=None, is_enabled=True,
                     engagement=None, notes=None, reps_min=None, reps_max=None, sets=None):
    """Add a new exercise to the GYM bank. Returns (ok, error)."""
    name = (name or '').strip()
    if not name:
        return False, 'Name is required.'
    if tier not in (1, 2, 3, 4, 5):
        return False, 'Tier must be 1–5.'
    conn = _gym_db()
    try:
        conn.execute(
            "INSERT INTO gym_exercises (name, tier, muscle_group, function, is_enabled, "
            "engagement, notes, reps_min, reps_max, sets) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, tier, muscle_group, function, 1 if is_enabled else 0,
             json.dumps(engagement or {}), notes, reps_min, reps_max, sets)
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
               'engagement', 'notes', 'reps_min', 'reps_max', 'sets'}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False, 'Nothing to update.'
    if 'engagement' in updates and isinstance(updates['engagement'], dict):
        updates['engagement'] = json.dumps(updates['engagement'])
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
    """Toggle exercise enabled/disabled."""
    conn = _gym_db()
    conn.execute("UPDATE gym_exercises SET is_enabled = ? WHERE id = ?",
                 (1 if enabled else 0, exercise_id))
    conn.commit()
    conn.close()


# ── Progression Suggestion Engine ────────────────────────────────────────────

def _suggest_next(entry):
    """Simple progression logic.

    Rules:
    - If no history → suggest starting weight (or 'Start logging')
    - If last attempt successful AND reps >= reps_max → suggest weight bump
    - If last attempt successful AND reps < reps_max → suggest rep bump
    - If last attempt failed → suggest same weight, same reps (retry)
    """
    weight = entry.get('weight_kg')
    last_reps = entry.get('last_reps')
    last_sets = entry.get('last_sets')
    successful = entry.get('successful')
    reps_max = entry.get('reps_max')
    reps_min = entry.get('reps_min')

    if weight is None or last_reps is None:
        return {'action': 'start', 'message': 'No history — start logging'}

    if not successful:
        return {
            'action': 'retry',
            'weight_kg': weight,
            'sets': last_sets,
            'reps': last_reps,
            'message': f'Retry: {weight}kg × {last_sets}×{last_reps}'
        }

    if reps_max and last_reps >= reps_max:
        # Bump weight: +2.5kg for barbell-style, +1kg for lighter
        bump = 2.5 if weight >= 20 else 1.0
        new_weight = weight + bump
        target_reps = reps_min or last_reps
        return {
            'action': 'weight_up',
            'weight_kg': new_weight,
            'sets': last_sets,
            'reps': target_reps,
            'message': f'↑ Weight: {new_weight}kg × {last_sets}×{target_reps}'
        }

    # Successful but below reps_max → bump reps
    new_reps = last_reps + 1
    return {
        'action': 'reps_up',
        'weight_kg': weight,
        'sets': last_sets,
        'reps': new_reps,
        'message': f'↑ Reps: {weight}kg × {last_sets}×{new_reps}'
    }


# ── Engagement Map (replaces hardcoded EXERCISE_MUSCLE_MAP) ──────────────────

def get_gym_muscle_engagement():
    """Return the full muscle engagement map from the DB.
    Format: {exercise_name: {muscle: weight, ...}, ...}
    """
    conn = _gym_db()
    rows = conn.execute(
        "SELECT name, engagement FROM gym_exercises WHERE is_enabled = 1"
    ).fetchall()
    conn.close()
    return {r['name']: json.loads(r['engagement'] or '{}') for r in rows}
