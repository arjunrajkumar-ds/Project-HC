"""
Migration: GYM Session Grouping
================================
Adds real "workout session" grouping on top of the gym_bank schema:
  - session_id FK column added to gym_progression, referencing gym_sessions(id)
  - ended_at TEXT column added to gym_sessions (an orphaned table from an
    earlier, unused schema iteration — it already had id/performed_at/finished,
    so we reuse it here rather than creating a duplicate table)

A session's lifecycle: performed_at is set on creation (Start button),
finished flips 0 -> 1 and ended_at is stamped on End.

Existing gym_progression rows are left with session_id = NULL (ungrouped
historical sets); grouping only applies going forward via explicit
Start/End session actions in the UI.

NOTE ON RUNNING THIS: the tracker.db path may be a FUSE-mounted directory
that does not support SQLite's file locking, which can turn ALTER TABLE
into a wedged hot-journal (see the .bak-gymsessions-* backup taken by this
script for pre-migration recovery). If you hit "disk I/O error" on a
subsequent connection, run the migration against a local copy of the file
and copy the result back, rather than truncating/removing the journal
directly on the mount.

Run: python migrate_gym_sessions.py
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
BACKUP_SUFFIX = f".bak-gymsessions-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def migrate():
    backup_path = DB_PATH + BACKUP_SUFFIX
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # --- Step 1: gym_sessions table (reuse if it already exists) ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gym_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            performed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished     INTEGER  NOT NULL DEFAULT 0
        )
    """)

    session_cols = [r['name'] for r in conn.execute("PRAGMA table_info(gym_sessions)").fetchall()]
    if 'ended_at' not in session_cols:
        conn.execute("ALTER TABLE gym_sessions ADD COLUMN ended_at TEXT")
        print("✓ Added ended_at column to gym_sessions")
    else:
        print("· ended_at column already present on gym_sessions — skipped")

    # --- Step 2: Add session_id column to gym_progression ---
    prog_cols = [r['name'] for r in conn.execute("PRAGMA table_info(gym_progression)").fetchall()]
    if 'session_id' not in prog_cols:
        conn.execute(
            "ALTER TABLE gym_progression ADD COLUMN session_id INTEGER "
            "REFERENCES gym_sessions(id) ON DELETE SET NULL"
        )
        print("✓ Added session_id column to gym_progression")
    else:
        print("· session_id column already present on gym_progression — skipped")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gym_prog_session ON gym_progression(session_id)"
    )

    conn.commit()
    conn.close()
    print("\n✅ Migration complete!")


if __name__ == '__main__':
    migrate()
