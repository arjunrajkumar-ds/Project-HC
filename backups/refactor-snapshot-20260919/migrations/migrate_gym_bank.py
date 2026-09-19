"""
Migration: GYM Exercise Bank
=============================
Replaces Arjun's exercise bank with the new gym_exercises/gym_progression schema.
Carries forward last T1/T2 progression data.
Leaves Gayathri/Raj's BODYWEIGHT bank (bank_scope='home') completely untouched.

Run: python migrate_gym_bank.py
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
BACKUP_SUFFIX = f".bak-gymbank-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def migrate():
    # --- Backup first ---
    backup_path = DB_PATH + BACKUP_SUFFIX
    shutil.copy2(DB_PATH, backup_path)
    print(f"✓ Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # --- Step 1: Snapshot T1/T2 progression ---
    snapshot = conn.execute("""
        SELECT e.name, p.weight_kg, s.sets, s.reps
        FROM progression p
        JOIN exercises e ON e.id = p.exercise_id
        JOIN schemes s ON s.id = p.scheme_id
        WHERE e.tier IN (1, 2)
          AND p.weight_kg IS NOT NULL
    """).fetchall()
    print(f"✓ Snapshotted {len(snapshot)} T1/T2 progressions to carry forward")
    for row in snapshot:
        print(f"    {row['name']}: {row['weight_kg']}kg × {row['sets']}×{row['reps']}")

    # --- Step 2: Create gym_exercises table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gym_exercises (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL UNIQUE,
            tier          INTEGER NOT NULL CHECK(tier IN (1, 2, 3, 4, 5)),
            muscle_group  TEXT NOT NULL,
            function      TEXT,
            is_enabled    INTEGER NOT NULL DEFAULT 1,
            engagement    TEXT NOT NULL DEFAULT '{}',
            notes         TEXT,
            reps_min      INTEGER,
            reps_max      INTEGER,
            sets          INTEGER,
            created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_ex_tier ON gym_exercises(tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_ex_muscle ON gym_exercises(muscle_group)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_ex_enabled ON gym_exercises(is_enabled, tier)")
    print("✓ Created gym_exercises table")

    # --- Step 3: Create gym_progression table ---
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gym_progression (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id   INTEGER NOT NULL REFERENCES gym_exercises(id) ON DELETE CASCADE,
            weight_kg     REAL,
            sets          INTEGER,
            reps          INTEGER,
            successful    INTEGER NOT NULL DEFAULT 1,
            recorded_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gym_prog_ex ON gym_progression(exercise_id, recorded_at DESC)")
    print("✓ Created gym_progression table")

    # --- Step 4: Seed the exercise bank ---
    exercises = [
        # CHEST - Press
        ("BB Incline Bench", 1, "chest", "press", 1),
        ("BB Bench Press", 1, "chest", "press", 0),
        ("DB Incline Bench", 2, "chest", "press", 1),
        ("DB Bench Press", 2, "chest", "press", 1),
        # CHEST - Fly
        ("Weighted Dips", 1, "chest", "fly", 1),
        # CHEST - Fa Jin
        ("Fa Jin : Pushups", 4, "chest", "press", 1),
        ("Fa Jin : Pec Deck", 4, "chest", "fly", 1),

        # SHOULDERS - Press
        ("BB Overhead Press", 1, "shoulders", "press", 1),
        ("DB Shoulder Press", 2, "shoulders", "press", 1),
        # SHOULDERS - Raise
        ("DB Lateral Raise (heavy)", 1, "shoulders", "raise", 1),
        ("Banded KB Raise", 2, "shoulders", "raise", 1),
        # SHOULDERS - Fa Jin
        ("Fa Jin : Face Pulls", 4, "shoulders", "raise", 1),

        # BACK - Upper
        ("Trap Bar Pendlay", 1, "back", "upper", 1),
        ("Meadow Row", 1, "back", "upper", 0),
        ("Weighted Pullups (wide-grip)", 1, "back", "upper", 1),
        ("Weighted Chinups", 1, "back", "upper", 1),
        ("DB Row", 2, "back", "upper", 1),
        ("Lat Pulldown", 3, "back", "upper", 1),
        # BACK - Posterior
        ("BB Row", 1, "back", "posterior", 1),
        ("Deadlift", 1, "back", "posterior", 0),
        ("Trap Bar Deadlift", 1, "back", "posterior", 1),

        # LEGS (top-level, no sub-function)
        ("BB Squat", 1, "legs", None, 1),
        ("DB Bulgarian SS", 1, "legs", None, 1),
        ("DB Lunges", 1, "legs", None, 1),
        ("Calf Raises", 2, "legs", None, 1),
        # LEGS - Quad
        ("Leg Extensions", 2, "legs", "quad", 1),
        # LEGS - Hamstring
        ("BB Romanian Deadlift", 1, "legs", "hamstring", 1),
        ("Hamstring Curl", 2, "legs", "hamstring", 1),
        # LEGS - Glutes
        ("BB Hip Thrust", 1, "legs", "glutes", 0),

        # CORE - Flexion
        ("Ab Wheel", 2, "core", "flexion", 1),
        ("Cable Crunch", 2, "core", "flexion", 1),
        ("Decline Weighted Situp", 2, "core", "flexion", 1),
        ("Weighted Leg Raise", 2, "core", "flexion", 1),
        # CORE - Static
        ("Plank (30s, 60s)", 2, "core", "static", 1),
        ("Front Support (30s, 60s)", 2, "core", "static", 1),

        # ARMS - Biceps
        ("DB Curl", 2, "arms", "biceps", 1),
        ("EZ-Bar Curl", 2, "arms", "biceps", 1),
        ("BB Curl", 2, "arms", "biceps", 1),
        ("EZ-Bar 777s (Biceps)", 2, "arms", "biceps", 1),
        # ARMS - Triceps
        ("DB Skullcrusher", 2, "arms", "triceps", 1),
        ("EZ-Bar Skullcrusher", 2, "arms", "triceps", 1),
        ("EZ-Bar 777s (Triceps)", 2, "arms", "triceps", 1),

        # STABILITY
        ("DB External Rotation", 3, "stability", None, 1),
        ("DB Pullover", 3, "stability", None, 1),
    ]

    conn.executemany(
        "INSERT OR IGNORE INTO gym_exercises (name, tier, muscle_group, function, is_enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        exercises
    )
    print(f"✓ Seeded {len(exercises)} exercises into gym_exercises")

    # --- Step 5: Carry forward T1/T2 progression ---
    carried = 0
    for row in snapshot:
        match = conn.execute(
            "SELECT id FROM gym_exercises WHERE LOWER(name) = LOWER(?)", (row['name'],)
        ).fetchone()
        if match:
            conn.execute(
                "INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, successful) "
                "VALUES (?, ?, ?, ?, 1)",
                (match['id'], row['weight_kg'], row['sets'], row['reps'])
            )
            carried += 1
    print(f"✓ Carried forward {carried} progression records")

    # --- Step 6: Clean up old Arjun bank config ---
    deleted = conn.execute("DELETE FROM exercise_bank_config WHERE bank_scope = 'arjun'").rowcount
    print(f"✓ Removed {deleted} old Arjun bank_config rows")

    # Verify home scope untouched
    home_count = conn.execute(
        "SELECT COUNT(*) as c FROM exercise_bank_config WHERE bank_scope = 'home'"
    ).fetchone()['c']
    print(f"✓ Gayathri/Raj BODYWEIGHT bank intact: {home_count} rows")

    conn.commit()
    conn.close()
    print("\n✅ Migration complete!")


if __name__ == '__main__':
    migrate()
