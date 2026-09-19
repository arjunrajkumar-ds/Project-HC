"""
Migration: Exercise Class (bodyweight vs strength)
==================================================
Adds an `exercise_class` column to the gym_exercises table so the exercise bank
can group/filter exercises into two classes:

  - 'bodyweight'  — bodyweight / body-functionality movements (e.g. for elders)
  - 'strength'    — weighted / loaded training

Existing rows are backfilled with a sensible default ('strength') and flagged
for later manual re-tagging. Idempotent: safe to run multiple times — it checks
whether the column already exists before altering.

Backup: a timestamped copy of tracker.db is written to backups/ first, matching
the project's other migrate_*.py scripts.

Run: python migrate_exercise_class.py
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
BACKUP_DIR = os.path.join(os.path.dirname(__file__), 'backups')
BACKUP_SUFFIX = f".bak-exerciseclass-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

DEFAULT_CLASS = 'strength'
VALID_CLASSES = ('bodyweight', 'strength')


def _column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def migrate():
    if not os.path.exists(DB_PATH):
        print(f"✗ Database not found at {DB_PATH} — nothing to migrate.")
        return

    # --- Backup first (into backups/, consistent with the de-cluttered root) ---
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, 'tracker.db' + BACKUP_SUFFIX)
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # Guard: gym_exercises must exist (Arjun's bank). If not, nothing to do.
        has_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='gym_exercises'"
        ).fetchone()
        if not has_table:
            print("✗ gym_exercises table not found — run migrate_gym_bank.py first. Aborting.")
            return

        # --- Idempotent: add the column only if missing ---
        if _column_exists(conn, 'gym_exercises', 'exercise_class'):
            print("• exercise_class column already present — skipping ADD COLUMN.")
        else:
            # SQLite can't add a CHECK constraint referencing the new column via
            # ALTER in older versions reliably, so add a plain column with a
            # DEFAULT and enforce the allowed set in the app layer (gym_bank.py).
            conn.execute(
                f"ALTER TABLE gym_exercises ADD COLUMN exercise_class TEXT "
                f"NOT NULL DEFAULT '{DEFAULT_CLASS}'"
            )
            print(f"✓ Added gym_exercises.exercise_class (default '{DEFAULT_CLASS}')")

        # --- Backfill: any NULL/blank → default; normalise unknown values ---
        # (New rows already default to 'strength'; this catches any legacy blanks
        #  and coerces anything outside the valid set.)
        conn.execute(
            "UPDATE gym_exercises SET exercise_class = ? "
            "WHERE exercise_class IS NULL OR TRIM(exercise_class) = ''",
            (DEFAULT_CLASS,)
        )
        conn.execute(
            "UPDATE gym_exercises SET exercise_class = ? "
            "WHERE exercise_class NOT IN (?, ?)",
            (DEFAULT_CLASS, VALID_CLASSES[0], VALID_CLASSES[1])
        )

        # Helpful index for grouping/filtering by class.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_gym_ex_class ON gym_exercises(exercise_class)"
        )

        conn.commit()

        # --- Report ---
        counts = conn.execute(
            "SELECT exercise_class, COUNT(*) AS c FROM gym_exercises GROUP BY exercise_class"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS c FROM gym_exercises").fetchone()['c']
        print(f"✓ Backfill complete — {total} exercise(s) classified:")
        for row in counts:
            print(f"    {row['exercise_class']}: {row['c']}")
        print("  (existing rows defaulted to 'strength' — re-tag bodyweight ones in the bank UI)")
        print("✓ Migration complete.")
    finally:
        conn.close()


if __name__ == '__main__':
    migrate()
