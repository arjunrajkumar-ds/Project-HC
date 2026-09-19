"""
Migration: Muay Thai category on the Train page
==================================================
Adds a standalone "Muay Thai" muscle_group to Arjun's gym_exercises bank so
it renders as its own accordion group on /train, and seeds the four
one-touch-loggable drills:

    - 3 minute bagwork               (unweighted)
    - 10/20/30/40                    (unweighted)
    - Medicine Ball Bent-Over Slams  (weighted, sets/reps recorded)
    - 300/30/30/3                    (unweighted)

Also adds an is_weighted column to gym_exercises (defaults to 1 / True for
every existing row, since the whole prior bank is barbell/dumbbell work) so
the Train page knows which exercises get a plain tap-to-log-a-rep button
vs. a quick weight+reps entry.

Run: python migrate_muay_thai.py
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
BACKUP_SUFFIX = f".bak-muaythai-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def migrate():
    backup_path = DB_PATH + BACKUP_SUFFIX
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # --- Step 1: add is_weighted column (default true — existing bank is all weighted lifts) ---
    cols = [r['name'] for r in conn.execute("PRAGMA table_info(gym_exercises)").fetchall()]
    if 'is_weighted' not in cols:
        conn.execute("ALTER TABLE gym_exercises ADD COLUMN is_weighted INTEGER NOT NULL DEFAULT 1")
        print("✓ Added is_weighted column to gym_exercises (existing rows default to 1)")
    else:
        print("· is_weighted column already present on gym_exercises — skipped")

    # --- Step 2: seed Muay Thai exercises ---
    exercises = [
        # (name, tier, muscle_group, function, is_enabled, is_weighted)
        ("3 Minute Bagwork",              5, "muay_thai", None, 1, 0),
        ("10/20/30/40",                   5, "muay_thai", None, 1, 0),
        ("Medicine Ball Bent-Over Slams", 3, "muay_thai", None, 1, 1),
        ("300/30/30/3",                   5, "muay_thai", None, 1, 0),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO gym_exercises "
        "(name, tier, muscle_group, function, is_enabled, is_weighted) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        exercises
    )
    print(f"✓ Seeded {len(exercises)} Muay Thai exercises into gym_exercises")

    conn.commit()
    conn.close()
    print("\n✅ Migration complete!")


if __name__ == '__main__':
    migrate()
