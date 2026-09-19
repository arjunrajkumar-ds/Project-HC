"""One-time cleanup of messy food_items rows in tracker.db.

Removes quantity/gram-prefixed names that polluted the food library (e.g.
'Eggs x3', '120g Vermicelli Noodles') and replaces them with clean base foods:

  * '<N>x <food>'  → base food, unit_type='unit', macros divided by N
                     (the stored value was for the whole N-count).
  * '<N><unit> <food>' → base food, unit_type='g', stored value kept as the
                     per-100g figure (the food_items.calories column already
                     means per-100g; the gram label was just noise).

Plural/case duplicates are merged by canonical key, name_key is repopulated, and
0-calorie food_log rows that now resolve to a defined food are backfilled.

The database is backed up before any change. Safe to re-run (idempotent).
"""
import os
import shutil
import sqlite3
from datetime import datetime

import database as d

DB = d.DB_PATH


def _clean_value(row, parsed):
    """Return (calories, protein, carbs, fat, unit_type) for the base food."""
    if parsed['multiplier'] != 1.0:
        n = parsed['multiplier']
        return (round(row['calories']  / n, 1),
                round(row['protein_g'] / n, 1),
                round(row['carbs_g']   / n, 1),
                round(row['fat_g']     / n, 1),
                'unit')
    # gram/ml-prefixed: stored value is already per-100g
    return (row['calories'], row['protein_g'], row['carbs_g'], row['fat_g'], 'g')


def main():
    backup = f"{DB}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(DB, backup)
    print(f"Backup written: {backup}\n")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute('PRAGMA table_info(food_items)')}
    if 'name_key' not in cols:
        conn.execute('ALTER TABLE food_items ADD COLUMN name_key TEXT')

    rows = conn.execute('SELECT * FROM food_items').fetchall()
    changes = []
    for r in rows:
        parsed = d._parse_entry_name(r['name'])
        if parsed['multiplier'] == 1.0 and parsed['gram_qty'] is None:
            continue  # already a clean name
        base = parsed['base_name']
        key  = parsed['key']
        cal, pro, carb, fat, unit_type = _clean_value(r, parsed)

        # Is there already a clean row for this food (by canonical key or exact name)?
        existing = conn.execute(
            'SELECT * FROM food_items WHERE id != ? AND (name_key = ? OR name = ? COLLATE NOCASE) '
            'ORDER BY (calories > 0) DESC, calories DESC LIMIT 1',
            (r['id'], key, base)
        ).fetchone()

        if existing:
            # Promote our recovered macros onto the existing stub if it's undefined.
            if existing['calories'] == 0 and cal > 0:
                conn.execute(
                    'UPDATE food_items SET calories=?, protein_g=?, carbs_g=?, fat_g=?, '
                    'unit_type=?, name_key=? WHERE id=?',
                    (cal, pro, carb, fat, unit_type, key, existing['id']))
            conn.execute('DELETE FROM food_items WHERE id=?', (r['id'],))
            changes.append((r['name'], f"merged into '{existing['name']}'", cal, unit_type))
        else:
            conn.execute(
                'UPDATE food_items SET name=?, name_key=?, calories=?, protein_g=?, '
                'carbs_g=?, fat_g=?, unit_type=? WHERE id=?',
                (base, key, cal, pro, carb, fat, unit_type, r['id']))
            changes.append((r['name'], f"renamed to '{base}'", cal, unit_type))

    # Repopulate name_key for every row (self-heals the whole table).
    for r in conn.execute('SELECT id, name FROM food_items').fetchall():
        conn.execute('UPDATE food_items SET name_key=? WHERE id=?',
                     (d._food_key(r['name']), r['id']))
    conn.execute('CREATE INDEX IF NOT EXISTS idx_food_items_name_key ON food_items(name_key)')
    conn.commit()

    # Backfill any 0-cal log rows that now resolve to a defined food.
    backfilled = 0
    for row in conn.execute('SELECT id, name FROM food_log WHERE calories = 0').fetchall():
        resolved = d._resolve_food(conn, row['name'])
        if resolved['matched']:
            conn.execute(
                'UPDATE food_log SET calories=?, protein_g=?, carbs_g=?, fat_g=? WHERE id=?',
                (resolved['calories'], resolved['protein_g'],
                 resolved['carbs_g'], resolved['fat_g'], row['id']))
            backfilled += 1
    conn.commit()
    conn.close()

    print("Food library cleanup:")
    for old, action, cal, unit in changes:
        print(f"  {old!r:42} → {action}  (cal={cal}, unit_type={unit})")
    print(f"\nBackfilled {backfilled} previously-undefined food_log row(s).")
    print(f"Cleaned {len(changes)} library row(s). Done.")


if __name__ == '__main__':
    main()
