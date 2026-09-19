"""
migrate_cardio_group.py — Add a "Cardio" group to Arjun's Train screen
=======================================================================
Registers cardio as a selectable (Primary) group in the new gym_exercises /
gym_functions schema, adds the columns cardio logging needs, and seeds the
six activities.

Fields (per the plan):
  - every cardio activity logs  duration + setting
  - Rowing additionally logs    distance   (duration + distance + setting)

Idempotent — safe to run more than once:
  - ALTER TABLE guarded by a PRAGMA column check
  - INSERT OR IGNORE for the function row and the exercises
  - Leaves Gayathri/Raj's bodyweight bank and all existing data untouched

Run:  python migrate_cardio_group.py
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
BACKUP_SUFFIX = f".bak-cardiogroup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

# name, tier, muscle_group, function, is_enabled, tracking_type, engagement(JSON), sort_order
CARDIO_EXERCISES = [
    ('Rowing',      3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"distance":"m","setting":true}', 0),
    ('Treadmill',   3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 1),
    ('Stairmaster', 3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 2),
    ('Cycling',     3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 3),
    ('Swimming',    3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 4),
    ('Running',     3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 5),
]


def _has_column(conn, table, column):
    cols = [r['name'] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def migrate():
    if not os.path.exists(DB_PATH):
        raise SystemExit(f"✗ Database not found at {DB_PATH}")

    # --- Backup first ---
    backup_path = DB_PATH + BACKUP_SUFFIX
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Backed up tracker.db → {os.path.basename(backup_path)}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # --- Step 1: add cardio columns to gym_progression (guarded) ---
    added = []
    if not _has_column(conn, 'gym_progression', 'distance_m'):
        conn.execute("ALTER TABLE gym_progression ADD COLUMN distance_m INTEGER")
        added.append('distance_m')
    if not _has_column(conn, 'gym_progression', 'setting'):
        conn.execute("ALTER TABLE gym_progression ADD COLUMN setting TEXT")
        added.append('setting')
    if added:
        print(f"✓ Added gym_progression columns: {', '.join(added)}")
    else:
        print("• gym_progression already has distance_m + setting — skipped")

    # --- Step 2: seed the cardio function row ---
    conn.execute(
        "INSERT OR IGNORE INTO gym_functions (muscle_group, key, label, sort_order) "
        "VALUES ('cardio', 'machine', 'Machine / Steady-state', 0)"
    )
    print("✓ Seeded gym_functions row (cardio / machine)")

    # --- Step 3: seed the six cardio activities ---
    before = conn.execute("SELECT COUNT(*) AS n FROM gym_exercises WHERE muscle_group='cardio'").fetchone()['n']
    conn.executemany(
        "INSERT OR IGNORE INTO gym_exercises "
        "(name, tier, muscle_group, function, is_enabled, tracking_type, engagement, sort_order) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        CARDIO_EXERCISES
    )
    after = conn.execute("SELECT COUNT(*) AS n FROM gym_exercises WHERE muscle_group='cardio'").fetchone()['n']
    print(f"✓ Seeded cardio activities (cardio rows: {before} → {after})")

    conn.commit()

    # --- Verify ---
    rows = conn.execute(
        "SELECT name, tier, tracking_type, engagement, sort_order "
        "FROM gym_exercises WHERE muscle_group='cardio' ORDER BY sort_order"
    ).fetchall()
    print("\nCardio group now contains:")
    for r in rows:
        print(f"  [{r['sort_order']}] {r['name']:<12} tier={r['tier']} "
              f"tt={r['tracking_type']} engagement={r['engagement']}")

    conn.close()
    print("\n✓ Migration complete. Restart the Flask app and open /train — "
          "'Cardio' is now an addable primary group.")


if __name__ == '__main__':
    migrate()
