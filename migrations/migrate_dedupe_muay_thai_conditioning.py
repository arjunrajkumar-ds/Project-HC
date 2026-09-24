"""
migrate_dedupe_muay_thai_conditioning.py
=========================================
One-off cleanup for the duplicate muay_thai "conditioning" function rows.

Background
----------
gym_functions ended up with TWO muay_thai rows that both display as
"conditioning":

    id 18 | muay_thai | key='conditioning' | label='conditioning' | sort_order 0
    id 19 | muay_thai | key='cond'         | label='conditioning' | sort_order 1

No exercise references either (all muay_thai exercises have function = NULL),
but the duplicate key means a fresh "add conditioning function" attempt trips
the UNIQUE(muscle_group, key) constraint and the databank shows a confusing
double / mislabelled entry.

This script canonicalises on key='conditioning':
  1. Repoints any exercise still on function='cond' (muay_thai) -> 'conditioning'
     (defensive; expected count is 0).
  2. Deletes the stray 'cond' row.
  3. Normalises the kept row's label to a clean 'Conditioning' (title case)
     and sort_order 0.

Idempotent: safe to run more than once. Run it with the app NOT serving writes.

Usage:
    cd /Users/arjunrajkumar/Documents/GitHub/project-horsecock
    python migrations/migrate_dedupe_muay_thai_conditioning.py
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tracker.db')

MUSCLE = 'muay_thai'
KEEP_KEY = 'conditioning'
DROP_KEY = 'cond'
CANONICAL_LABEL = 'Conditioning'


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    before = conn.execute(
        "SELECT id, muscle_group, key, label, sort_order FROM gym_functions "
        "WHERE muscle_group = ? ORDER BY sort_order, key", (MUSCLE,)
    ).fetchall()
    print("Before:")
    for r in before:
        print(f"  id={r['id']} key={r['key']!r} label={r['label']!r} sort_order={r['sort_order']}")

    keep = conn.execute(
        "SELECT id FROM gym_functions WHERE muscle_group=? AND key=?",
        (MUSCLE, KEEP_KEY)
    ).fetchone()
    drop = conn.execute(
        "SELECT id FROM gym_functions WHERE muscle_group=? AND key=?",
        (MUSCLE, DROP_KEY)
    ).fetchone()

    if not keep and drop:
        # Only the 'cond' row exists — just rename its key rather than delete.
        print(f"\nNo '{KEEP_KEY}' row; renaming '{DROP_KEY}' -> '{KEEP_KEY}'.")
        conn.execute(
            "UPDATE gym_functions SET key=?, label=?, sort_order=0 "
            "WHERE muscle_group=? AND key=?",
            (KEEP_KEY, CANONICAL_LABEL, MUSCLE, DROP_KEY)
        )
        conn.commit()
    elif keep and drop:
        # 1. Repoint any exercises off the stray key (expected 0).
        moved = conn.execute(
            "UPDATE gym_exercises SET function=? WHERE muscle_group=? AND function=?",
            (KEEP_KEY, MUSCLE, DROP_KEY)
        ).rowcount
        print(f"\nRepointed {moved} exercise(s) from '{DROP_KEY}' to '{KEEP_KEY}'.")
        # 2. Delete the stray row.
        conn.execute(
            "DELETE FROM gym_functions WHERE muscle_group=? AND key=?",
            (MUSCLE, DROP_KEY)
        )
        # 3. Normalise the kept row.
        conn.execute(
            "UPDATE gym_functions SET label=?, sort_order=0 WHERE muscle_group=? AND key=?",
            (CANONICAL_LABEL, MUSCLE, KEEP_KEY)
        )
        conn.commit()
        print(f"Deleted stray '{DROP_KEY}' row; normalised '{KEEP_KEY}' label -> {CANONICAL_LABEL!r}.")
    elif keep and not drop:
        # Already clean — just normalise the label/sort_order.
        conn.execute(
            "UPDATE gym_functions SET label=?, sort_order=0 WHERE muscle_group=? AND key=?",
            (CANONICAL_LABEL, MUSCLE, KEEP_KEY)
        )
        conn.commit()
        print(f"\nAlready de-duplicated; normalised '{KEEP_KEY}' label -> {CANONICAL_LABEL!r}.")
    else:
        print(f"\nNeither '{KEEP_KEY}' nor '{DROP_KEY}' present for {MUSCLE} — nothing to do.")

    after = conn.execute(
        "SELECT id, muscle_group, key, label, sort_order FROM gym_functions "
        "WHERE muscle_group = ? ORDER BY sort_order, key", (MUSCLE,)
    ).fetchall()
    print("\nAfter:")
    for r in after:
        print(f"  id={r['id']} key={r['key']!r} label={r['label']!r} sort_order={r['sort_order']}")

    conn.close()
    print("\nDone.")


if __name__ == '__main__':
    main()
