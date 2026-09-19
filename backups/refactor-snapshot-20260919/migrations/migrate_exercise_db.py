"""
Migration: Exercise Databank reorganisation
============================================
Reorganises Arjun's GYM exercise bank (gym_exercises / gym_progression) and
introduces a first-class gym_functions lookup table. See docs/EXERCISE-DB.md.

Scope: gym_exercises, gym_progression, gym_functions — Arjun's bank only
(profile_id 1). Does NOT touch the legacy `exercises` / `exercise_bank_config`
tables, exercise_logs/attempts/muscles/tally, or any Gayathri/home path.

What it does (idempotent — safe to run twice):
  1. Adds gym_exercises.tracking_type  (weight_reps | reps | time)
  2. Adds gym_exercises.archived_at     (soft-delete; NULL = active)
  3. Adds gym_exercises.sort_order      (hand-ordering within a function)
  4. Adds gym_progression.duration_s    (seconds, NULL for non-timed work)
  5. Creates + seeds gym_functions      (muscle_group, key, label, sort_order)
  6. Renames the core `static` function key -> `isometrics`
  7. Sets the legs compound function on the 3 NULL-function compounds
  8. Moves "DB Hammer Curl" from uncategorised -> arms/biceps
  9. Adds the `calf` function under legs and assigns Calf Raises to it
 10. Backfills tracking_type from is_weighted, then applies the explicit
     overrides (isometrics -> time, Fa Jin : Pushups -> reps)
 11. Merges two duplicate rows into their canonical targets, re-pointing
     gym_progression history first (§3.1)
 12. Archives 4 out-of-programme exercises, history preserved (§3.2)

Every ALTER TABLE is guarded by PRAGMA table_info; every data change is
guarded by a WHERE clause that no-ops on a second run. The script prints a
PLAN before writing, a SUMMARY of rows changed per operation after, and a
VERIFICATION block with PASS/FAIL assertions on the post-state.

is_weighted is now DEPRECATED (derived from tracking_type). It is retained
in place (dropping it would require a table rebuild that churns the
gym_progression FK) and kept in sync on write in gym_bank.py. Nothing in
application code should read it any more.

NOTE ON RUNNING THIS: the tracker.db path may be a FUSE-mounted directory
that does not support SQLite's file locking, which can turn ALTER TABLE into
a wedged hot-journal (see migrate_gym_sessions.py for the same caveat). This
script takes a .bak-exercisedb-* backup first. If you hit "disk I/O error" on
a subsequent connection, run the migration against a local copy of the file
and copy the result back, rather than truncating/removing the journal
directly on the mount.

Run: python migrate_exercise_db.py
"""
import sqlite3
import os
import shutil
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')
BACKUP_SUFFIX = f".bak-exercisedb-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

ALLOWED_TRACKING = ('weight_reps', 'reps', 'time', 'weight_time')

# ── gym_functions seed — muscle_group, key, label, sort_order ────────────────
# Order matches §3 of the task spec. sort_order is 0-based within a muscle.
GYM_FUNCTIONS_SEED = [
    ('chest',     'press',       'Press',       0),
    ('chest',     'fly',         'Fly',         1),
    ('shoulders', 'press',       'Press',       0),
    ('shoulders', 'raise',       'Raise',       1),
    ('back',      'upper',       'Upper',       0),
    ('back',      'posterior',   'Posterior',   1),
    ('legs',      'compound',    'Compound',    0),
    ('legs',      'quad',        'Quad',        1),
    ('legs',      'hamstring',   'Hamstring',   2),
    ('legs',      'glutes',      'Glutes',      3),
    ('legs',      'calf',        'Calf',        4),
    ('core',      'flexion',     'Flexion',     0),
    ('core',      'isometrics',  'Isometrics',  1),
    ('arms',      'biceps',      'Biceps',      0),
    ('arms',      'triceps',     'Triceps',     1),
]


def _cols(conn, table):
    return [r['name'] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _id_by_name(conn, name):
    row = conn.execute("SELECT id FROM gym_exercises WHERE name = ?", (name,)).fetchone()
    return row['id'] if row else None


def migrate():
    # ── Backup first ─────────────────────────────────────────────────────────
    backup_path = DB_PATH + BACKUP_SUFFIX
    shutil.copy2(DB_PATH, backup_path)
    print(f"\u2713 Backup created: {backup_path}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    summary = {}

    # ══ PLAN ═════════════════════════════════════════════════════════════════
    print("\n" + "=" * 68)
    print("PLAN")
    print("=" * 68)
    ex_cols = _cols(conn, 'gym_exercises')
    gp_cols = _cols(conn, 'gym_progression')
    has_functions = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gym_functions'"
    ).fetchone())

    print(f"  add gym_exercises.tracking_type ...... "
          f"{'SKIP (exists)' if 'tracking_type' in ex_cols else 'ADD'}")
    print(f"  add gym_exercises.archived_at ........ "
          f"{'SKIP (exists)' if 'archived_at' in ex_cols else 'ADD'}")
    print(f"  add gym_exercises.sort_order ......... "
          f"{'SKIP (exists)' if 'sort_order' in ex_cols else 'ADD'}")
    print(f"  add gym_progression.duration_s ....... "
          f"{'SKIP (exists)' if 'duration_s' in gp_cols else 'ADD'}")
    print(f"  create gym_functions ................. "
          f"{'SKIP (exists)' if has_functions else 'CREATE'} + seed {len(GYM_FUNCTIONS_SEED)} rows")
    print("  rename core function key static -> isometrics")
    print("  set legs compound function on NULL-function compounds")
    print("  move 'DB Hammer Curl' -> arms/biceps")
    print("  assign 'Calf Raises' -> legs/calf")
    print("  backfill tracking_type; override isometrics->time, Fa Jin Pushups->reps")
    print("  merge 'Trap Bar Pendlay Row' -> 'Trap Bar Pendlay' (re-point history)")
    print("  merge 'DB Overhead Press' -> 'DB Shoulder Press' (re-point history)")
    print("  archive: Cable Tricep Pushdown, Cable Bicep Curl, DB External Rotation, DB Pullover")

    # ══ 1-3. gym_exercises columns ═══════════════════════════════════════════
    if 'tracking_type' not in ex_cols:
        # NOTE: SQLite can't add a CHECK-with-default-referencing column via a
        # bare ADD COLUMN and also enforce the CHECK on existing rows, so we add
        # the column with the literal default and enforce the allowed set in
        # application code (gym_bank.py) + the verification block below.
        conn.execute(
            "ALTER TABLE gym_exercises ADD COLUMN tracking_type TEXT NOT NULL "
            "DEFAULT 'weight_reps'"
        )
        print("\n\u2713 Added gym_exercises.tracking_type")
    else:
        print("\n\u00b7 gym_exercises.tracking_type already present \u2014 skipped")

    if 'archived_at' not in ex_cols:
        conn.execute("ALTER TABLE gym_exercises ADD COLUMN archived_at TEXT")
        print("\u2713 Added gym_exercises.archived_at")
    else:
        print("\u00b7 gym_exercises.archived_at already present \u2014 skipped")

    if 'sort_order' not in ex_cols:
        conn.execute("ALTER TABLE gym_exercises ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        print("\u2713 Added gym_exercises.sort_order")
    else:
        print("\u00b7 gym_exercises.sort_order already present \u2014 skipped")

    # ══ 4. gym_progression.duration_s ════════════════════════════════════════
    if 'duration_s' not in gp_cols:
        conn.execute("ALTER TABLE gym_progression ADD COLUMN duration_s INTEGER")
        print("\u2713 Added gym_progression.duration_s")
    else:
        print("\u00b7 gym_progression.duration_s already present \u2014 skipped")

    # ══ 5. gym_functions table + seed ════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gym_functions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            muscle_group  TEXT NOT NULL,
            key           TEXT NOT NULL,
            label         TEXT NOT NULL,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(muscle_group, key)
        )
    """)
    seeded = 0
    for mg, key, label, so in GYM_FUNCTIONS_SEED:
        cur = conn.execute(
            "INSERT OR IGNORE INTO gym_functions (muscle_group, key, label, sort_order) "
            "VALUES (?, ?, ?, ?)",
            (mg, key, label, so)
        )
        seeded += cur.rowcount
    summary['gym_functions seeded'] = seeded
    print(f"\u2713 gym_functions ready; seeded {seeded} new row(s) "
          f"({len(GYM_FUNCTIONS_SEED) - seeded} already present)")

    # ══ 6. rename core function static -> isometrics ═════════════════════════
    # (a) the gym_functions row: if an old 'static' seed row ever existed, drop
    #     it in favour of 'isometrics' (our seed already inserts isometrics).
    conn.execute("DELETE FROM gym_functions WHERE muscle_group='core' AND key='static'")
    # (b) the exercises that reference it
    renamed = conn.execute(
        "UPDATE gym_exercises SET function='isometrics' "
        "WHERE muscle_group='core' AND function='static'"
    ).rowcount
    summary["core function 'static'->'isometrics'"] = renamed
    print(f"\u2713 Renamed {renamed} core exercise(s) function static -> isometrics")

    # ══ 7. legs compound function ════════════════════════════════════════════
    # The 3 compounds currently have function = NULL. Calf Raises is also NULL
    # today but is handled separately in step 9 (-> calf), so exclude it here.
    compounds = ('BB Squat', 'DB Bulgarian SS', 'DB Lunges')
    placeholders = ','.join('?' for _ in compounds)
    set_compound = conn.execute(
        f"UPDATE gym_exercises SET function='compound' "
        f"WHERE muscle_group='legs' AND function IS NULL AND name IN ({placeholders})",
        compounds
    ).rowcount
    summary['legs compound function set'] = set_compound
    print(f"\u2713 Set legs/compound function on {set_compound} exercise(s)")

    # ══ 8. move DB Hammer Curl -> arms/biceps ════════════════════════════════
    moved_hammer = conn.execute(
        "UPDATE gym_exercises SET muscle_group='arms', function='biceps' "
        "WHERE name='DB Hammer Curl' AND (muscle_group!='arms' OR function IS NOT 'biceps')"
    ).rowcount
    summary['DB Hammer Curl -> arms/biceps'] = moved_hammer
    print(f"\u2713 Moved DB Hammer Curl -> arms/biceps ({moved_hammer} row)")

    # ══ 9. Calf Raises -> legs/calf ══════════════════════════════════════════
    calf_note = "also serves the posterior chain on compound days"
    set_calf = conn.execute(
        "UPDATE gym_exercises SET function='calf', "
        "notes = COALESCE(NULLIF(notes,''), ?) "
        "WHERE name='Calf Raises' AND muscle_group='legs' AND function IS NOT 'calf'",
        (calf_note,)
    ).rowcount
    summary['Calf Raises -> legs/calf'] = set_calf
    print(f"\u2713 Assigned Calf Raises -> legs/calf ({set_calf} row)")

    # ══ 10. tracking_type backfill + overrides ═══════════════════════════════
    # (a) Backfill from is_weighted for any row still at the column default that
    #     hasn't been explicitly set. We treat the physical is_weighted value
    #     (default 1 when absent) as the source: 0 -> reps, else weight_reps.
    #     Idempotent: only touches rows where tracking_type is still the default
    #     AND would change. We drive it off is_weighted so a re-run is a no-op.
    bf_reps = conn.execute(
        "UPDATE gym_exercises SET tracking_type='reps' "
        "WHERE is_weighted=0 AND tracking_type!='reps' "
        "AND function IS NOT 'isometrics'"
    ).rowcount
    # weight_reps is already the column default, so the only backfill move that
    # changes anything is is_weighted=0 -> reps. (Everything else stays.)
    summary['tracking_type backfill -> reps (is_weighted=0)'] = bf_reps

    # (b) Override: isometrics -> time
    ov_time = conn.execute(
        "UPDATE gym_exercises SET tracking_type='time' "
        "WHERE muscle_group='core' AND function='isometrics' AND tracking_type!='time'"
    ).rowcount
    summary["tracking_type override -> time (isometrics)"] = ov_time

    # (c) Override: Fa Jin : Pushups -> reps
    ov_pushups = conn.execute(
        "UPDATE gym_exercises SET tracking_type='reps' "
        "WHERE name='Fa Jin : Pushups' AND tracking_type!='reps'"
    ).rowcount
    summary["tracking_type override -> reps (Fa Jin : Pushups)"] = ov_pushups

    # (d) Keep is_weighted in sync with the new authority (derived, deprecated):
    #     is_weighted = 1 if tracking_type == 'weight_reps' else 0.
    sync_iw = conn.execute(
        "UPDATE gym_exercises SET is_weighted = CASE WHEN tracking_type='weight_reps' "
        "THEN 1 ELSE 0 END "
        "WHERE is_weighted != (CASE WHEN tracking_type='weight_reps' THEN 1 ELSE 0 END)"
    ).rowcount
    summary['is_weighted re-synced from tracking_type'] = sync_iw
    print(f"\u2713 tracking_type: {bf_reps} backfilled->reps, {ov_time} ->time, "
          f"{ov_pushups} Fa Jin Pushups->reps; {sync_iw} is_weighted re-synced")

    # ══ 11. Merges — re-point history, then delete the duplicate (§3.1) ═══════
    merges = [
        ('Trap Bar Pendlay Row', 'Trap Bar Pendlay'),
        ('DB Overhead Press',    'DB Shoulder Press'),
    ]
    merge_summary = []
    for dupe_name, target_name in merges:
        dupe_id = _id_by_name(conn, dupe_name)
        target_id = _id_by_name(conn, target_name)
        if dupe_id is None:
            merge_summary.append(f"{dupe_name}: already merged/absent \u2014 skipped")
            continue
        if target_id is None:
            raise RuntimeError(
                f"Merge target '{target_name}' not found while merging '{dupe_name}'. "
                f"Aborting without deleting anything."
            )
        repointed = conn.execute(
            "UPDATE gym_progression SET exercise_id=? WHERE exercise_id=?",
            (target_id, dupe_id)
        ).rowcount
        # Safety (Rule 4): never delete a row that still has history pointing at it.
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM gym_progression WHERE exercise_id=?", (dupe_id,)
        ).fetchone()['c']
        if remaining != 0:
            raise RuntimeError(
                f"Refusing to delete '{dupe_name}' (id {dupe_id}): {remaining} "
                f"gym_progression rows still point at it after re-point."
            )
        conn.execute("DELETE FROM gym_exercises WHERE id=?", (dupe_id,))
        merge_summary.append(
            f"{dupe_name} (id {dupe_id}) -> {target_name} (id {target_id}): "
            f"{repointed} history row(s) re-pointed, dupe deleted"
        )
    summary['merges'] = merge_summary
    for line in merge_summary:
        print(f"\u2713 Merge: {line}")

    # ══ 12. Archive out-of-programme exercises (§3.2) ════════════════════════
    to_archive = [
        'Cable Tricep Pushdown', 'Cable Bicep Curl',
        'DB External Rotation', 'DB Pullover',
    ]
    ph = ','.join('?' for _ in to_archive)
    archived = conn.execute(
        f"UPDATE gym_exercises SET archived_at=CURRENT_TIMESTAMP "
        f"WHERE name IN ({ph}) AND archived_at IS NULL",
        to_archive
    ).rowcount
    summary['archived'] = archived
    print(f"\u2713 Archived {archived} out-of-programme exercise(s) "
          f"({len(to_archive) - archived} already archived)")

    conn.commit()

    # ══ SUMMARY ══════════════════════════════════════════════════════════════
    print("\n" + "=" * 68)
    print("SUMMARY (rows changed per operation)")
    print("=" * 68)
    for k, v in summary.items():
        if isinstance(v, list):
            print(f"  {k}:")
            for line in v:
                print(f"      - {line}")
        else:
            print(f"  {k}: {v}")

    # ══ VERIFICATION ═════════════════════════════════════════════════════════
    print("\n" + "=" * 68)
    print("VERIFICATION")
    print("=" * 68)
    passed = failed = 0

    def check(label, ok, detail=''):
        nonlocal passed, failed
        tag = 'PASS' if ok else 'FAIL'
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"  [{tag}] {label}" + (f"  ({detail})" if detail else ''))

    # Schema present
    ex_cols_after = _cols(conn, 'gym_exercises')
    gp_cols_after = _cols(conn, 'gym_progression')
    for col in ('tracking_type', 'archived_at', 'sort_order'):
        check(f"gym_exercises.{col} exists", col in ex_cols_after)
    check("gym_progression.duration_s exists", 'duration_s' in gp_cols_after)
    check("gym_functions table exists", bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gym_functions'").fetchone()))

    # tracking_type domain: every row is one of the allowed values
    bad_tt = conn.execute(
        f"SELECT COUNT(*) AS c FROM gym_exercises "
        f"WHERE tracking_type NOT IN ({','.join('?' for _ in ALLOWED_TRACKING)})",
        ALLOWED_TRACKING
    ).fetchone()['c']
    check("all tracking_type values within allowed set", bad_tt == 0, f"{bad_tt} invalid")

    # Function correctness per §3 (active, in-scope rows)
    def fn_of(name):
        r = conn.execute("SELECT muscle_group, function, tier, is_enabled, tracking_type, archived_at "
                         "FROM gym_exercises WHERE name=?", (name,)).fetchone()
        return dict(r) if r else None

    # Legs compounds
    for nm in ('BB Squat', 'DB Bulgarian SS', 'DB Lunges'):
        r = fn_of(nm)
        check(f"{nm} -> legs/compound", r and r['muscle_group'] == 'legs' and r['function'] == 'compound')
    # Calf
    r = fn_of('Calf Raises')
    check("Calf Raises -> legs/calf", r and r['muscle_group'] == 'legs' and r['function'] == 'calf')
    # Hammer curl
    r = fn_of('DB Hammer Curl')
    check("DB Hammer Curl -> arms/biceps", r and r['muscle_group'] == 'arms' and r['function'] == 'biceps')
    # Core isometrics renamed + timed
    iso = conn.execute("SELECT name, function, tracking_type FROM gym_exercises "
                       "WHERE muscle_group='core' AND function='isometrics'").fetchall()
    check("core isometrics function present (>=2 rows)", len(iso) >= 2, f"{len(iso)} rows")
    check("all core isometrics are tracking_type='time'",
          all(r['tracking_type'] == 'time' for r in iso))
    no_static = conn.execute("SELECT COUNT(*) AS c FROM gym_exercises "
                             "WHERE function='static'").fetchone()['c']
    check("no exercise still uses function='static'", no_static == 0, f"{no_static} left")
    # Fa Jin Pushups
    r = fn_of('Fa Jin : Pushups')
    check("Fa Jin : Pushups tracking_type='reps'", r and r['tracking_type'] == 'reps')

    # Merges: dupes gone, no orphaned history
    for dupe_name in ('Trap Bar Pendlay Row', 'DB Overhead Press'):
        gone = _id_by_name(conn, dupe_name) is None
        check(f"duplicate '{dupe_name}' deleted", gone)
    orphans = conn.execute("""
        SELECT COUNT(*) AS c FROM gym_progression gp
        LEFT JOIN gym_exercises ge ON ge.id = gp.exercise_id
        WHERE ge.id IS NULL
    """).fetchone()['c']
    check("no orphaned gym_progression.exercise_id", orphans == 0, f"{orphans} orphans")

    # Archives: 4 archived, and uncategorised/stability have no ACTIVE members
    arch = conn.execute("SELECT COUNT(*) AS c FROM gym_exercises "
                        "WHERE archived_at IS NOT NULL").fetchone()['c']
    check("exactly 4 archived exercises", arch == 4, f"{arch} archived")
    active_junk = conn.execute("""
        SELECT COUNT(*) AS c FROM gym_exercises
        WHERE muscle_group IN ('uncategorised', 'stability') AND archived_at IS NULL
    """).fetchone()['c']
    check("no active members in uncategorised/stability", active_junk == 0, f"{active_junk} active")

    # is_weighted stays in sync with tracking_type (deprecated but consistent)
    desync = conn.execute("""
        SELECT COUNT(*) AS c FROM gym_exercises
        WHERE is_weighted != (CASE WHEN tracking_type='weight_reps' THEN 1 ELSE 0 END)
    """).fetchone()['c']
    check("is_weighted consistent with tracking_type", desync == 0, f"{desync} desynced")

    # gym_functions seeded with the isometrics (not static) key
    has_iso_fn = conn.execute("SELECT 1 FROM gym_functions "
                              "WHERE muscle_group='core' AND key='isometrics'").fetchone()
    check("gym_functions has core/isometrics", bool(has_iso_fn))
    has_calf_fn = conn.execute("SELECT 1 FROM gym_functions "
                               "WHERE muscle_group='legs' AND key='calf'").fetchone()
    check("gym_functions has legs/calf", bool(has_calf_fn))

    print("-" * 68)
    print(f"  {passed} passed, {failed} failed")
    conn.close()

    if failed:
        print("\n\u274c Migration completed with FAILED assertions \u2014 review above.")
        raise SystemExit(1)
    print("\n\u2705 Migration complete \u2014 all verifications passed.")


if __name__ == '__main__':
    migrate()
