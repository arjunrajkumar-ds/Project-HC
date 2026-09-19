"""One-time casing cleanup for tracker.db.

Normalises existing food names to the same consistent Title Case now applied on write
(_display_name), so the library, log, recipes and recent-food chips all render uniformly.

  * food_items — names are reduced to their clean base (any leftover '1x'/gram prefix
    stripped) and Title Cased. Case-only duplicates are merged (the defined row wins).
  * food_log   — names are Title Cased in place, keeping their quantity prefix so
    portions still resolve (e.g. '200g shredded chicken' → '200g Shredded Chicken').

The database is backed up first. Safe to re-run (idempotent).
"""
import shutil
import sqlite3
from datetime import datetime

import database as d

DB = d.DB_PATH


def main():
    backup = f"{DB}.bak-casing-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copy2(DB, backup)
    print(f"Backup written: {backup}\n")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # ── food_items: clean base name + Title Case, merging case/prefix collisions ──
    item_changes = []
    for r in conn.execute('SELECT * FROM food_items').fetchall():
        base  = d._parse_entry_name(r['name'])['base_name'] or r['name']
        clean = d._display_name(base)
        if clean == r['name']:
            continue
        existing = conn.execute(
            'SELECT * FROM food_items WHERE id != ? AND name = ? COLLATE NOCASE',
            (r['id'], clean)
        ).fetchone()
        if existing:
            # Keep the better-defined row; drop this duplicate.
            if existing['calories'] == 0 and r['calories'] > 0:
                conn.execute(
                    'UPDATE food_items SET calories=?, protein_g=?, carbs_g=?, fat_g=?, unit_type=? WHERE id=?',
                    (r['calories'], r['protein_g'], r['carbs_g'], r['fat_g'], r['unit_type'], existing['id']))
            conn.execute('DELETE FROM food_items WHERE id=?', (r['id'],))
            item_changes.append((r['name'], f"merged into '{clean}'"))
        else:
            conn.execute('UPDATE food_items SET name=? WHERE id=?', (clean, r['id']))
            item_changes.append((r['name'], f"→ '{clean}'"))

    # Repopulate name_key for every row.
    for r in conn.execute('SELECT id, name FROM food_items').fetchall():
        conn.execute('UPDATE food_items SET name_key=? WHERE id=?', (d._food_key(r['name']), r['id']))

    # ── food_log: Title Case in place (keep quantity prefix) ──
    log_changed = 0
    for r in conn.execute('SELECT id, name FROM food_log').fetchall():
        clean = d._display_name(r['name'])
        if clean != r['name']:
            conn.execute('UPDATE food_log SET name=? WHERE id=?', (clean, r['id']))
            log_changed += 1

    # ── food_components: Title Case recipe + ingredient names ──
    for r in conn.execute('SELECT id, food_name, ingredient_name FROM food_components').fetchall():
        conn.execute('UPDATE food_components SET food_name=?, ingredient_name=? WHERE id=?',
                     (d._display_name(r['food_name']), d._display_name(r['ingredient_name']), r['id']))

    conn.commit()
    print("food_items:")
    for old, action in item_changes:
        print(f"  {old!r:42} {action}")
    print(f"\nfood_log rows recased: {log_changed}")
    print(f"food_items rows changed: {len(item_changes)}")
    print(f"integrity: {conn.execute('PRAGMA integrity_check').fetchone()[0]}")
    conn.close()


if __name__ == '__main__':
    main()
