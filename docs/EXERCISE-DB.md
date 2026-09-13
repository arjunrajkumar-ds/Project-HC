# Exercise Databank

Arjun's GYM exercise bank (`profile_id 1`). Managed at **`/exercise-db`**; trained
from **`/train`**. Scope is the `gym_exercises`, `gym_progression` and
`gym_functions` tables only — the legacy `exercises` / `exercise_bank_config`
tables are untouched (see *Deferred* below).

## Schema

### `gym_exercises`
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT UNIQUE NOT NULL | |
| `tier` | INTEGER NOT NULL | CHECK 1–5 (T1 Main … T4 Fa Jin, T5 Cardio) |
| `muscle_group` | TEXT NOT NULL | e.g. `chest`, `legs`, `muay_thai` |
| `function` | TEXT | lowercase **key** (see `gym_functions`); may be NULL |
| `is_enabled` | INTEGER NOT NULL DEFAULT 1 | in-bank, parked-vs-active toggle |
| `engagement` | TEXT NOT NULL DEFAULT '{}' | JSON muscle map |
| `notes` | TEXT | free text; record dual-function overlap here |
| `reps_min`, `reps_max`, `sets` | INTEGER | optional targets |
| `tracking_type` | TEXT NOT NULL DEFAULT 'weight_reps' | **authority** — see below |
| `sort_order` | INTEGER NOT NULL DEFAULT 0 | hand-order within a function |
| `archived_at` | TEXT | NULL = active; soft-delete — see below |
| `created_at` | TEXT NOT NULL | |
| `is_weighted` | INTEGER NOT NULL DEFAULT 1 | **DEPRECATED** — see below |

### `gym_functions` (new)
First-class function lookup so the frontend isn't guessing labels/ordering.
| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `muscle_group` | TEXT NOT NULL | |
| `key` | TEXT NOT NULL | lowercase slug stored in `gym_exercises.function` |
| `label` | TEXT NOT NULL | display label |
| `sort_order` | INTEGER NOT NULL DEFAULT 0 | order within the muscle group |
| | | `UNIQUE(muscle_group, key)` |

There is **no foreign key** from `gym_exercises.function` → `gym_functions`. A
free-typed function is accepted and renders under its own raw label until it's
added to `gym_functions`. An exercise has exactly **one** primary function; a
genuine second function is noted in `notes` (not modelled many-to-many).

### `gym_progression`
Unchanged except one addition:
| column | type | notes |
|---|---|---|
| `duration_s` | INTEGER | seconds; NULL for non-timed work |

`log_gym_set()` takes an optional `duration_s` parameter.

## `tracking_type`
The authority for how a set is logged. Exactly three values:

| value | meaning | logged fields |
|---|---|---|
| `weight_reps` | load × reps × sets (default) | `weight_kg`, `reps`, `sets` |
| `reps` | bodyweight / unloaded reps | `reps`, `sets` |
| `time` | timed hold or duration per set | `duration_s`, `sets` |
| `weight_time` | load held for a duration per set (e.g. weighted plank, farmer's carry) | `weight_kg`, `duration_s`, `sets` |

Editable inline on `/exercise-db` (a dropdown), so any mis-assignment can be
corrected by hand.

## `is_enabled` vs `archived_at` — two different axes
- **`is_enabled = 0`** — *"in my bank, deliberately parked right now"* (e.g. BB
  Bench Press, Deadlift, Meadow Row, BB Hip Thrust). Still visible in the
  databank, greyed with an `off` tag, one tap to switch back on.
- **`archived_at` set** — *"not part of my programme any more."* Hidden from the
  bank and the `/session/log` picker, tucked behind the **Archived** disclosure.
  Its `gym_progression` history is preserved and still counts in analytics.
  Archiving is reversible (Unarchive sets it back to NULL).

## Delete vs Archive
- **Delete** is a hard delete, allowed **only** when the exercise has **zero**
  `gym_progression` rows. The UI arms it behind an inline "Really delete?"
  confirm (no browser modals). The API (`DELETE /api/gym/exercise/<id>`)
  **enforces** this regardless of the UI — it returns `409` with
  `"has N logged sets — archive it instead"` when history exists.
- **Archive** is the path for anything with history. History is kept.

## Adding a new function
On `/exercise-db`, the edit/add function select has a **`＋ new function…`**
option that reveals key + label inputs and POSTs to `/api/gym/functions`
(`{muscle_group, key, label, sort_order?}`). Or seed it in a migration by adding
a row to `gym_functions`. The `key` is what gets stored in
`gym_exercises.function`.

## `is_weighted` is deprecated but retained
`is_weighted` used to be the coarse loaded/unloaded flag. `tracking_type`
replaces it. We keep the column because dropping it in SQLite requires a full
table rebuild, which would churn the `gym_progression → gym_exercises` FK for no
functional gain. It is:
- **Not read** anywhere in application code any more.
- **Kept in sync on write** in `gym_bank.py`: `is_weighted = 1 if tracking_type
  == 'weight_reps' else 0`, purely so any missed legacy path still behaves.

## Deferred follow-ups
1. **Legacy consolidation.** The old `exercises` (63 rows) and
   `exercise_bank_config` (126 rows, `arjun`/`home` scopes) still power
   Gayathri's/home bank, cardio choices, `get_muscle_group_activity` and the
   fatigue engine. Consolidating those into the `gym_*` tables is out of scope
   for now.
2. **Structured hold durations.** `Plank (30s, 60s)` and `Front Support (30s,
   60s)` embed their target durations in the name (kept as-is here to avoid
   churning history and the Fa Jin map). A follow-up could move those targets
   into structured `duration_s` targets on the exercise.

## Migration
`migrate_exercise_db.py` — backs up to `tracker.db.bak-exercisedb-<ts>`,
idempotent (PRAGMA-guarded ALTERs, WHERE-guarded data changes), prints a PLAN
then a SUMMARY, and ends with a PASS/FAIL verification block asserting the
post-state. Safe to run twice.
