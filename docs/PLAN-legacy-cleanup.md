# PLAN — remove verified-dead code from database.py

**Status:** ✅ implemented · no schema change, no migration, no table dropped · **Date:** 2026-10-12
> Backup: `gymtracker/database.py.bak-legacyclean-20261012-073145`

## 0. Why this needed scoping first, and what the earlier claim got wrong

The Train rehaul left a note saying the legacy progression tables were "entirely unreferenced by live code".
**That was wrong**, and worth recording. It came from grepping for the four *writer* functions
(`record_exercise_attempt`, `advance_progression`, `record_stage_completion`, `set_progression_weight`),
which are indeed uncalled. But `get_session_detail_with_progression()` — live, powering `/session/<id>` —
reads **all three** of `exercise_attempts`, `progression` and `schemes` to show the scheme label, stage and
PR flag against each lift, and `get_exercise_bank()` reads two of them.

The writers are dead. The tables are not. Those are different statements, and a grep for one does not
establish the other.

## 1. Method

Rather than grepping, a reachability analysis over all 306 top-level functions in `gymtracker/`:

* **Roots** = anything decorated as a Flask route / `before_request` / `context_processor` /
  `errorhandler` / template filter, every module-level statement, and every name mentioned in any
  template, test, migration or the `app.py` launcher.
* **Edges** = a function body textually referencing another function's name (deliberately loose — it
  counts mentions inside strings and comments, so it over-estimates reachability and only ever errs
  toward keeping code).
* Backups (`.bak-*`, `backups/`) excluded from the root set, or dead code would keep itself alive.

Result: **269 reachable, 37 unreachable.** Each of the 37 was then confirmed by direct grep across every
live `.py` / `.html` / `.js` — all had exactly one reference, their own `def` line (two had a second, from
another dead function). No `getattr` / `globals()[...]` dynamic dispatch exists anywhere in the package,
and none of the 37 appear in `app.py`'s `from .database import ...` line or in the swim / pilates
blueprints.

## 2. Removed — 33 functions, 940 lines

| Cluster | Functions |
|---|---|
| Legacy scheme ladder (writers) | `advance_progression`, `record_exercise_attempt`, `record_stage_completion`, `set_progression_weight` |
| Its readers and helpers | `get_progression`, `get_all_progressions`, `get_schemes`, `_build_prog`, `_first_scheme`, `_scheme_total`, `_regen_exercise_schemes` |
| Orphaned `exercise_attempts` analytics | `get_exercise_decay` (134 lines), `get_exercise_history_last_5` |
| Legacy tier-1 suggestion | `get_tier1_suggestion`, `get_t1_last_weights`, `get_t1_exercises_with_activity` |
| Priority lifts | `get_priority_lifts`, `set_priority_lift`, `clear_priority_lift`, `_lift_month_points` |
| Session drafts | `save_session_draft`, `get_session_draft`, `clear_session_draft` |
| Containers | `get_containers`, `add_container`, `delete_container` |
| Swim achievements | `get_swim_achievements`, `record_swim_achievement` |
| Misc | `adjust_fatigue`, `get_muscle_swimlane` (92 lines), `get_exercise_muscle_map`, `bank_set_exercise_enabled`, `bank_delete_exercise` |

`database.py`: **4847 → 3841 lines.** One orphaned section banner (`# ── Priority lifts (focus block) ──`)
removed with its contents.

## 3. Deliberately kept

* **Every table.** Dropping is destructive, needs a migration and a database backup, and buys nothing here.
  `stage_completions` (40 rows), `priority_lifts` (2), `session_drafts` (0), `containers` (0) and
  `exercise_logs` (4) now have no live SQL consumer, but they cost nothing sitting there and their rows stay
  readable. Their `CREATE TABLE IF NOT EXISTS` statements remain in `init_db()`.
* **`exercise_attempts`, `progression`, `schemes`** — live readers, see §0.
* **`TIER_BOUNDS` and `_generate_schemes()`** — still seed the `schemes` table in `init_db()`, and
  `TIER_BOUNDS` is now also the ladder behind the rewired accumulation panel.
* **Four unreachable `gym_bank` functions** (`start_gym_session`, `get_gym_exercises_by_muscle`,
  `get_gym_exercises_by_tier`, `get_gym_muscle_engagement`). These are current-schema API surface, not
  legacy residue; removing them was out of scope.

## 4. Verified after

* Second reachability pass: **no cascade** — the only remaining unreachable functions are the four gym ones
  above, kept on purpose.
* `app` imports cleanly, 94 routes registered.
* **105 GET routes swept across all three profiles — zero 5xx, zero exceptions.**
* `/session/<id>` renders for its owning profile, and `get_session_detail_with_progression()` still returns
  `scheme_label`, `stage` and `is_pr` — the fields sourced from the three kept tables. (403s on other
  profiles' sessions are the ownership check, not a regression.)
* All four test suites green: `test_train_recency`, `test_train_session_day`,
  `test_scheme_accumulation`, `test_exercise_bank_roundtrip`.
* No orphaned section banners left in `database.py`.

## 5. Rollback

`cp gymtracker/database.py.bak-legacyclean-20261012-073145 gymtracker/database.py`. Nothing else changed.
