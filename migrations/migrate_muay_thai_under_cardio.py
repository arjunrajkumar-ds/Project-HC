"""
migrate_muay_thai_under_cardio.py — reveal the Muay Thai drills on /train
=========================================================================
The four Muay Thai drills exist and are enabled, but they sit in their own
muscle_group ('muay_thai'), and since the Train rewrite a group only renders
if it is listed in _GYM_PRIMARY_MUSCLES or _GYM_CONSTANT_MUSCLES. It is in
neither, so the group is unreachable: it can only appear once a set is logged
against it, and a set can only be logged once it appears.

This re-homes them into the existing (already primary) 'cardio' group under a
new 'Muay Thai' function, so they surface with no code change to the renderer.

Per docs/PLAN-muay-thai-under-cardio.md:
  - seed gym_functions ('cardio', 'muay_thai', 'Muay Thai', sort_order 1)
  - move the four drills to muscle_group='cardio', function='muay_thai'
  - retier the three tier-5 drills to tier 3 (matching the cardio machines)
  - '3 Minute Bagwork' becomes tracking_type='time' with a 180s default target
    stored on its engagement blob
  - give all four an explicit sort_order (they were all 0, which fell back to
    alphabetical and put the ladders before the bagwork)
  - drop the orphaned ('muay_thai','conditioning') function row

All four drills have ZERO logged sets, so nothing is relabelled and nothing can
be orphaned by reverting.

Idempotent — safe to run more than once:
  - INSERT OR IGNORE for the function row
  - every exercise change is an UPDATE ... WHERE id =, matched on the exercise's
    name so a renamed/missing row is reported rather than silently skipped
  - the orphan delete is guarded on nothing referencing it

Run with the Flask app stopped:
    python migrations/migrate_muay_thai_under_cardio.py
"""
import sqlite3
import os
import shutil
import sys
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tracker.db')
BACKUP_SUFFIX = f".bak-muaythaicardio-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

NEW_FUNCTION = ('cardio', 'muay_thai', 'Muay Thai', 1)

# Matched on name (stable, UNIQUE) rather than id, so the script is safe to run
# against a database where the ids differ.
#   name, tier, tracking_type, engagement, sort_order
DRILLS = [
    ('3 Minute Bagwork',              3, 'time',        '{"target_s": 180}', 0),
    ('10/20/30/40',                   3, 'reps',        '{}',                1),
    ('300/30/30/3',                   3, 'reps',        '{}',                2),
    ('Medicine Ball Bent-Over Slams', 3, 'weight_reps', '{}',                3),
]

# gym_bank._iw_from_tracking — mirrored here so the migration stays standalone.
# is_weighted is deprecated but still written, so it must not go stale when
# tracking_type moves.
WEIGHTED_TYPES = ('weight_reps', 'weight_time')


def migrate():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"✗ Database not found at {DB_PATH}")

    backup_path = DB_PATH + BACKUP_SUFFIX
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Backed up tracker.db → {os.path.basename(backup_path)}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # --- Step 0: refuse to run if these drills have history we'd be relabelling ---
    logged = conn.execute(
        "SELECT COUNT(*) AS n FROM gym_progression p JOIN gym_exercises e ON e.id = p.exercise_id "
        "WHERE e.name IN (%s)" % ','.join('?' * len(DRILLS)),
        [d[0] for d in DRILLS]
    ).fetchone()['n']
    if logged:
        print(f"\n  ! {logged} set(s) are already logged against these drills.")
        print("    Re-homing them will retroactively re-label that history to 'cardio',")
        print("    because a set's muscle_group is derived from its exercise, not stored.")
        print("    That is probably what you want — but it is not what the plan assumed.")
        if '--force' not in sys.argv:
            conn.close()
            raise SystemExit("    Re-run with --force to proceed.")
        print("    --force given, continuing.\n")

    # --- Step 1: seed the cardio/muay_thai function row ---
    conn.execute(
        "INSERT OR IGNORE INTO gym_functions (muscle_group, key, label, sort_order) "
        "VALUES (?, ?, ?, ?)", NEW_FUNCTION)
    print(f"✓ Function row ready: cardio / {NEW_FUNCTION[1]} ('{NEW_FUNCTION[2]}')")

    # --- Step 2: re-home the four drills ---
    missing = []
    for name, tier, tt, engagement, sort_order in DRILLS:
        row = conn.execute("SELECT id FROM gym_exercises WHERE name = ?", (name,)).fetchone()
        if not row:
            missing.append(name)
            continue
        conn.execute(
            "UPDATE gym_exercises SET muscle_group = 'cardio', function = 'muay_thai', "
            "tier = ?, tracking_type = ?, is_weighted = ?, engagement = ?, sort_order = ? "
            "WHERE id = ?",
            (tier, tt, 1 if tt in WEIGHTED_TYPES else 0, engagement, sort_order, row['id']))
        print(f"  → {name:<32} tier={tier} tt={tt} sort={sort_order}")
    if missing:
        print(f"\n  ! Not found (skipped): {', '.join(missing)}")

    # --- Step 3: drop the orphaned muay_thai/conditioning function row ---
    still_referencing = conn.execute(
        "SELECT COUNT(*) AS n FROM gym_exercises "
        "WHERE muscle_group = 'muay_thai'").fetchone()['n']
    if still_referencing == 0:
        cur = conn.execute("DELETE FROM gym_functions WHERE muscle_group = 'muay_thai'")
        if cur.rowcount:
            print(f"✓ Removed {cur.rowcount} orphaned muay_thai function row(s)")
    else:
        print(f"  · {still_referencing} exercise(s) still in muscle_group='muay_thai' — "
              f"leaving its function rows alone")

    conn.commit()

    # --- Verify ---
    print("\nCardio group now contains:")
    rows = conn.execute("""
        SELECT e.name, e.tier, e.tracking_type, e.engagement, e.sort_order,
               COALESCE(f.label, 'Unassigned') AS fn
          FROM gym_exercises e
          LEFT JOIN gym_functions f
                 ON f.muscle_group = e.muscle_group AND f.key = e.function
         WHERE e.muscle_group = 'cardio'
         ORDER BY f.sort_order, e.sort_order
    """).fetchall()
    current_fn = None
    for r in rows:
        if r['fn'] != current_fn:
            current_fn = r['fn']
            print(f"  {current_fn}")
        print(f"    [{r['sort_order']}] {r['name']:<32} tier={r['tier']} "
              f"tt={r['tracking_type']} engagement={r['engagement']}")

    left = conn.execute(
        "SELECT COUNT(*) AS n FROM gym_exercises WHERE muscle_group = 'muay_thai'").fetchone()['n']
    print(f"\n  exercises still in muscle_group='muay_thai': {left} (expected 0)")

    conn.close()
    print("\n✓ Migration complete. Restart the Flask app and open /train — "
          "pick Cardio and the Muay Thai drills are under their own heading.")


if __name__ == '__main__':
    migrate()
