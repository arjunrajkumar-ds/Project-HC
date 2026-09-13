# Task: Reorganise the GYM Exercise Databank (schema + `/exercise-db` frontend)

You are working in the Flask app at the repo root (`app.py`, `database.py`, `gym_bank.py`,
`templates/`). SQLite DB is `tracker.db` at the repo root. **`tracker.db` is live and contains
real logged training history — read the Safety Rules before you touch it.**

---

## 0. Scope boundary (read this first)

**IN SCOPE:** the `gym_exercises` and `gym_progression` tables, `gym_bank.py`, the gym
routes in `app.py`, `templates/train.html`, and a **new** `templates/exercise_db.html`.
This is Arjun's bank only (profile_id 1).

**OUT OF SCOPE — do not modify, migrate, or refactor:**
- The legacy `exercises` table (63 rows) and `exercise_bank_config` (126 rows, `arjun`/`home`
  scopes). These still power Gayathri's/home bank, cardio choices, `get_muscle_group_activity`,
  and the fatigue engine. Leave them alone.
- `exercise_logs`, `exercise_attempts`, `exercise_muscles`, `exercise_tally`.
- Any Gayathri/home/profile-2/profile-3 code path.
- The `muay_thai` muscle group (ids 50–53) and its one-touch logging on `/train`.
- The Muay Thai / cardio / swim / pilates flows.

If a change appears to require touching out-of-scope code, **stop and report** rather than
proceeding.

---

## 1. Safety rules

1. **Back up first.** Before any DB write, create `tracker.db.bak-exercisedb-YYYYMMDD-HHMMSS`
   next to `tracker.db`, matching the existing `.bak-*` convention in the repo root.
2. **All DB changes go in one idempotent migration script**, `migrate_exercise_db.py`, following
   the style of the existing `migrate_gym_bank.py` / `migrate_gym_sessions.py`. It must be safe to
   run twice: guard every `ALTER TABLE` by inspecting `PRAGMA table_info`, and every data change
   with a `WHERE` clause that no-ops on a second run.
3. The script prints a **plan** of what it will change, then a **summary** of what it changed
   (row counts per operation). No silent writes.
4. **Never delete a row that has `gym_progression` history** except via the explicit merge steps
   in §3, which re-point that history first.
5. No new test suite is required. Instead, the script must end with a **verification block** that
   re-queries the DB and asserts the post-state described in §3 and §4 (correct function for every
   exercise, no orphaned `gym_progression.exercise_id`, expected row counts). Print PASS/FAIL per
   assertion.
6. After migrating, load `/train`, `/exercise-db` and `/session/log` and confirm they render
   without error (`python app.py`, or however the repo runs it — check `SETUP.md`).

---

## 2. Schema changes to `gym_exercises`

Current DDL:

```sql
CREATE TABLE gym_exercises (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    tier         INTEGER NOT NULL CHECK(tier IN (1,2,3,4,5)),
    muscle_group TEXT NOT NULL,
    function     TEXT,
    is_enabled   INTEGER NOT NULL DEFAULT 1,
    engagement   TEXT NOT NULL DEFAULT '{}',
    notes        TEXT,
    reps_min     INTEGER,
    reps_max     INTEGER,
    sets         INTEGER,
    created_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_weighted  INTEGER NOT NULL DEFAULT 1
);
```

### 2.1 Add `tracking_type` — replacing the coarse `is_weighted` flag

Add `tracking_type TEXT NOT NULL DEFAULT 'weight_reps'` with a CHECK constraint allowing exactly:

| value          | meaning                                   | logged fields              |
|----------------|-------------------------------------------|----------------------------|
| `weight_reps`  | load × reps × sets (the default case)     | weight_kg, reps, sets      |
| `reps`         | bodyweight / unloaded reps                | reps, sets                 |
| `time`         | timed hold or duration per set            | duration_s, sets           |

Backfill: `is_weighted = 0` → `'reps'`, otherwise `'weight_reps'`, then apply the explicit
per-exercise overrides in §4.

`is_weighted` is now **derived, not authoritative**. SQLite can't easily drop a column here
without a table rebuild, so:
- Keep the `is_weighted` column in place for now (a rebuild would churn FKs from
  `gym_progression`), but **stop reading it anywhere in application code**.
- Keep it in sync on write inside `gym_bank.py` (`is_weighted = 1 if tracking_type ==
  'weight_reps' else 0`) purely so any code path you may have missed still behaves.
- Add a code comment marking it DEPRECATED, and note it in the summary doc (§7).

### 2.2 Add soft-delete (archive)

Add `archived_at TEXT` (NULL = active). Semantics:
- `archived_at IS NULL` → exercise appears in the bank.
- `archived_at` set → hidden from the bank and from the `/session/log` picker, but its
  `gym_progression` history is preserved and still counts in analytics.
- Archiving is **reversible** (un-archive sets it back to NULL).

`is_enabled` and `archived_at` are **different axes** and both must be kept:
- `is_enabled = 0` — "in my bank, deliberately parked right now" (e.g. BB Bench Press,
  Deadlift, Meadow Row, BB Hip Thrust). Visible in the DB view, greyed, easy to switch on.
- `archived_at` — "not part of my programme any more". Hidden behind an "Archived" disclosure.

### 2.3 Add function metadata

Add `sort_order INTEGER NOT NULL DEFAULT 0` to `gym_exercises` so exercises can be hand-ordered
within a function group (ties broken by tier, then name).

Create a small lookup table so functions are first-class and the frontend isn't guessing at
labels or ordering:

```sql
CREATE TABLE gym_functions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    muscle_group TEXT NOT NULL,
    key          TEXT NOT NULL,      -- lowercase slug stored in gym_exercises.function
    label        TEXT NOT NULL,      -- display label
    sort_order   INTEGER NOT NULL DEFAULT 0,
    UNIQUE(muscle_group, key)
);
```

Seed it exactly as in §3. `gym_exercises.function` continues to store the `key` (keeps existing
code in `app.py` `_build_fajin_map`, `session_log.html` `data-function` working unchanged). Do
**not** add a foreign key — a free-typed function should still be accepted and simply render
under its own raw label until it's added to `gym_functions`.

### 2.4 Add `duration_s` to `gym_progression`

Add `duration_s INTEGER` (seconds, NULL for non-timed work). Do not alter existing columns.
`log_gym_set()` gains an optional `duration_s` parameter.

### 2.5 One exercise, one function

Confirmed decision: an exercise has exactly **one** primary function. Do not build a
many-to-many. Where an exercise genuinely serves two functions, record the overlap in `notes`.

---

## 3. Canonical databank — target state

This is the authority. After migration, `gym_exercises` for these muscle groups must match
exactly. Tier is shown as T1–T5; `(off)` means `is_enabled = 0` (**not** archived).

Seed `gym_functions` with these rows, in this order.

### CHEST (`chest`)
**Press** (`press`)
| Exercise | Tier | |
|---|---|---|
| BB Incline Bench | T1 | |
| BB Bench Press | T1 | (off) |
| DB Incline Bench | T2 | |
| DB Bench Press | T2 | |
| Fa Jin : Pushups | T4 | tracking_type `reps` |

**Fly** (`fly`)
| Weighted Dips | T1 | |
| Fa Jin : Pec Deck | T4 | |

### SHOULDERS (`shoulders`)
**Press** (`press`)
| BB Overhead Press | T1 | |
| DB Shoulder Press | T2 | merge target — see §3.1 |

**Raise** (`raise`)
| DB Lateral Raise (heavy) | T1 | |
| Banded KB Raise | T2 | |
| Fa Jin : Face Pulls | T4 | |

### BACK (`back`)
**Upper** (`upper`)
| Trap Bar Pendlay | T1 | merge target — see §3.1 |
| Meadow Row | T1 | (off) |
| Weighted Pullups (wide-grip) | T1 | |
| Weighted Chinups | T1 | |
| DB Row | T2 | |
| Lat Pulldown | T3 | |

**Posterior** (`posterior`)
| BB Row | T1 | |
| Deadlift | T1 | (off) |
| Trap Bar Deadlift | T1 | |

### LEGS (`legs`)
**Compound** (`compound`) — *these are currently `function = NULL`; set them*
| BB Squat | T1 | |
| DB Bulgarian SS | T1 | |
| DB Lunges | T1 | |

**Quad** (`quad`)
| Leg Extensions | T2 | |

**Hamstring** (`hamstring`)
| BB Romanian Deadlift | T1 | |
| Hamstring Curl | T2 | |

**Glutes** (`glutes`)
| BB Hip Thrust | T1 | (off) |

**Calf** (`calf`) — *new function; resolves Calf Raises appearing twice in the source spec*
| Calf Raises | T2 | notes: "also serves the posterior chain on compound days" |

### CORE (`core`)
**Flexion** (`flexion`) — all T2
| Ab Wheel | T2 | |
| Cable Crunch | T2 | |
| Decline Weighted Situp | T2 | |
| Weighted Leg Raise | T2 | |

**Isometrics** (`isometrics`) — **rename the existing `static` key to `isometrics`**; all T2,
`tracking_type = 'time'`
| Plank (30s, 60s) | T2 | |
| Front Support (30s, 60s) | T2 | |

### ARMS (`arms`) — all T2 (tiers deliberately uniform here; leave as T2)
**Biceps** (`biceps`)
| DB Curl | T2 | |
| DB Hammer Curl | T2 | **move from `uncategorised` → `arms`/`biceps`** |
| BB Curl | T2 | |
| EZ-Bar Curl | T2 | |
| EZ-Bar 777s (Biceps) | T2 | |

**Triceps** (`triceps`)
| DB Skullcrusher | T2 | |
| EZ-Bar Skullcrusher | T2 | |
| EZ-Bar 777s (Triceps) | T2 | |

> Note on the 777s: the source spec lists "EZ-Bar 777s" under both Biceps and Triceps. `name` is
> UNIQUE and both variants have separate history, so **keep the two existing suffixed rows**
> (`EZ-Bar 777s (Biceps)`, `EZ-Bar 777s (Triceps)`). Do not rename or merge them.

### MUAY THAI (`muay_thai`) — untouched
ids 50–53. Do not change tier, function, names or one-touch behaviour. Only backfill
`tracking_type` from `is_weighted` (so `3 Minute Bagwork`, `10/20/30/40`, `300/30/30/3` →
`reps`; `Medicine Ball Bent-Over Slams` → `weight_reps`) and leave `archived_at` NULL.

### 3.1 Merges — re-point history, then delete the duplicate

Both duplicates carry `gym_progression` rows. For each: `UPDATE gym_progression SET exercise_id
= <target> WHERE exercise_id = <dupe>`, then `DELETE FROM gym_exercises WHERE id = <dupe>`.
Assert zero remaining `gym_progression` rows pointing at the deleted id.

| Duplicate (delete) | id | history rows | Merge into | id |
|---|---|---|---|---|
| Trap Bar Pendlay Row | 47 | 1 | Trap Bar Pendlay | 13 |
| DB Overhead Press | 45 | 2 | DB Shoulder Press | 9 |

Verify ids by name at runtime — do not hardcode the integers without a lookup.

### 3.2 Archive — not in the programme, history kept

Set `archived_at` (leave `is_enabled` as-is, do not delete):

| Exercise | current muscle_group | history rows |
|---|---|---|
| Cable Tricep Pushdown | uncategorised | 1 |
| Cable Bicep Curl | uncategorised | 1 |
| DB External Rotation | stability | 0 |
| DB Pullover | stability | 0 |

After this, the `uncategorised` and `stability` groups should have no *active* members. Keep
`stability` in `_GYM_GROUP_ORDER` (it's a legitimate future group); keep the `uncategorised`
catch-all rendering logic in `/train` so any future stray still surfaces.

---

## 4. `tracking_type` assignment

Apply in this order:
1. Backfill everything from `is_weighted` (`0` → `reps`, `1` → `weight_reps`).
2. Override: `core`/`isometrics` (Plank, Front Support) → `time`.
3. Override: `Fa Jin : Pushups` → `reps`.
4. Everything else keeps its backfilled value.

Do not guess beyond this. In the frontend, `tracking_type` is an inline-editable dropdown so the
remaining calls can be corrected by hand.

`Plank (30s, 60s)` / `Front Support (30s, 60s)` embed their target durations in the name. **Leave
the names as-is** for this task (renaming churns history and the Fa Jin map). Note in the summary
doc that a follow-up could move those targets into structured `duration_s` targets.

---

## 5. Backend work — `gym_bank.py` and `app.py`

### 5.1 `gym_bank.py`

- `get_gym_bank()` — add an `include_archived=False` parameter. Default query filters
  `archived_at IS NULL`. Keep returning the existing tier-keyed dict shape so `/train` and
  `/session/log` keep working.
- **New** `get_gym_bank_grouped(include_archived=False)` — returns the muscle → function → exercise
  tree the new frontend needs, joined to `gym_functions` for labels and ordering, with each
  exercise's latest `gym_progression` row and its `history_count`. Shape:
  ```python
  [{'key': 'chest', 'label': 'Chest',
    'functions': [{'key': 'press', 'label': 'Press',
                   'exercises': [{...exercise fields..., 'latest': {...}|None,
                                  'history_count': int}]}],
    'active_count': int, 'total_count': int}]
  ```
  Ordering: muscle by `_GYM_GROUP_ORDER`; function by `gym_functions.sort_order`; exercise by
  `sort_order`, then `tier`, then `name`. Exercises whose `function` is NULL or absent from
  `gym_functions` go in a trailing pseudo-function `{'key': None, 'label': 'Unassigned'}`.
- `get_gym_exercises_by_muscle` / `get_gym_exercises_by_tier` — exclude archived by default.
- `gym_add_exercise()` — accept `tracking_type` (default `weight_reps`) and `sort_order`. Validate
  `tracking_type` against the allowed set. Keep the existing `(ok, error)` return contract.
- `gym_update_exercise()` — add `tracking_type`, `sort_order`, `archived_at` to `allowed`.
- **New** `gym_archive_exercise(exercise_id)` / `gym_unarchive_exercise(exercise_id)`.
- **New** `gym_delete_exercise(exercise_id)` — hard delete, but **only** when the exercise has
  zero `gym_progression` rows. Return `(ok, error)`; on refusal return a clear error
  (`"has N logged sets — archive it instead"`) so the API can surface it.
- **New** `get_gym_functions()` / `gym_add_function(muscle_group, key, label, sort_order)`.
- `log_gym_set()` — optional `duration_s`.
- `get_gym_muscle_engagement()` — also exclude archived.
- Where you write `tracking_type`, keep `is_weighted` in sync (see §2.1).

### 5.2 `app.py` routes

Revive `/exercise-bank` as a redirect to the new `/exercise-db` (it is currently a redirect to
`/train`). Add:

| Route | Method | Purpose |
|---|---|---|
| `/exercise-db` | GET | New databank management page → `exercise_db.html` |
| `/api/gym/exercise/<id>/archive` | POST | `{archived: true\|false}` |
| `/api/gym/exercise/<id>` | DELETE | Hard delete; 409 + error message if it has history |
| `/api/gym/exercise/reorder` | POST | `{order: [ids...]}` within one function — writes `sort_order` |
| `/api/gym/functions` | GET, POST | List / add function rows |

Extend the existing `/api/gym/exercise/add` and `/api/gym/exercise/<id>` handlers to pass
`tracking_type` and `sort_order` through.

Guard `/exercise-db` the same way the other Arjun-only routes are guarded (`if _profile_id() in
(2, 3): return redirect(...)` — copy the pattern from `session_log`).

### 5.3 Fix a live bug you will otherwise inherit

`require_profile()` in `app.py` (~line 254) enforces CSRF on **every** POST, requiring an
`X-CSRF-Token` header or `_csrf` form field. The `fetch()` calls in `templates/train.html`
(`saveEdit`, `toggleEnabled`, `addExercise`, `quickTap`, `logWeightedSet`, `undoTap`) send only
`Content-Type` — so **every edit/add/toggle from the Train page currently 403s.**

`base.html` already exposes the token as `window._csrf`. Fix this:
- Add `'X-CSRF-Token': window._csrf` to every `fetch()` in `train.html`.
- Use a shared helper in the new `exercise_db.html` (e.g. `apiPost(url, body)` /
  `apiDelete(url)`) that always attaches the header — do not hand-roll headers per call site.
- Verify by performing an actual edit in the browser and confirming a 200.

---

## 6. Frontend — new `/exercise-db` page

Create `templates/exercise_db.html`. **Read `templates/train.html` and `templates/base.html`
first and match the existing house style** — the `pandora` body class, the CSS custom properties
(`--panel`, `--panel-soft`, `--border`, `--dim`, `--dimmer`, `--accent`, `--text-strong`,
`--radius-md`, `--radius-sm`, `--radius-pill`, `--font-mono`, `--font-sans`), the per-muscle
stripe colours already defined in `train.html`, the mono uppercase micro-labels, the accordion
mechanics. **Do not introduce a new design language, a CSS framework, or a CDN dependency.**
Reuse the muscle colour map verbatim and add one for `calf` if needed.

### Purpose split
- **`/exercise-db`** — browse, add, edit, reorder, archive, delete. The management view.
- **`/train`** — stays the lean training view. Restructure its grouping to
  Muscle → Function → Tier (per §6.2) and keep its Muay Thai one-touch logging. **Remove the
  add-exercise panel and the inline edit panels from `train.html`** and replace them with a single
  small "Manage databank →" link to `/exercise-db`, so there's exactly one place to edit.

### 6.1 Layout — `/exercise-db`

Three levels, top to bottom:

```
EXERCISE DATABANK                              44 active · 4 archived

[All] [T1] [T2] [T3] [Fa Jin] [T5]     [search…]     [＋ Add exercise]

▸ CHEST                                                          7
  ├ PRESS
  │   BB Incline Bench          T1   100kg × 5      ✎
  │   BB Bench Press            T1   off            ✎
  │   DB Incline Bench          T2   32.5kg × 8     ✎
  │   ...
  └ FLY
      Weighted Dips             T1   +20kg × 6      ✎
      Fa Jin : Pec Deck         T4   —              ✎
▸ SHOULDERS                                                      5
...
▸ Archived (4)
```

Requirements:
- Muscle group = collapsible accordion with its colour stripe, label, and active count. Remember
  open/closed state in `sessionStorage` so a save doesn't collapse everything.
- Function = sub-header inside the group (reuse/extend the `.fn-label` style), showing the
  **display label** from `gym_functions`, not the raw key.
- Exercise row shows: name, tier badge (reuse `.ex-tier-badge` and its `t1`/`t2`/`t4` colours; add
  `t3`/`t5`), a compact latest-progression readout appropriate to `tracking_type`
  (`weight_reps` → `100kg × 5`; `reps` → `12 reps`; `time` → `60s`), an `off` tag when
  `is_enabled = 0` (row at reduced opacity), and an edit affordance.
- **Tier filter pills** and a **live text search** (filter by name, function label, muscle) that
  compose — applying both narrows to the intersection. Groups with zero visible rows hide
  themselves and counts update, as `train.html` already does.
- **Archived section** at the bottom, collapsed by default, showing archived exercises with an
  "Unarchive" action and their history count.

### 6.2 `/train` regrouping

`train()` in `app.py` currently sorts within a muscle group by `(function or 'zzz', name)` and
emits function labels inline via a Jinja `namespace`. Replace that with the
`get_gym_bank_grouped()` tree so functions are explicit, correctly labelled and correctly
ordered, and exercises sort by `sort_order`, `tier`, `name` within each function. Keep everything
else about `/train` — one-touch Muay Thai, tier pills, the info bottom-sheet — working identically.

### 6.3 Editing

Inline edit panel per row (extend the `.ex-edit-panel` pattern already in `train.html`), with:
- **Name** (text)
- **Tier** (T1–T5 select)
- **Muscle group** (select from `_GYM_GROUP_ORDER`)
- **Function** (select of that muscle's functions from `gym_functions`, plus an
  `＋ new function…` option that reveals key/label inputs and POSTs to `/api/gym/functions`).
  Changing the muscle group must re-populate the function select.
- **Tracking type** (select: Weight × reps / Reps only / Time)
- **Reps min / max / sets** (the existing optional columns — surface them, they're currently
  invisible in the UI)
- **Notes** (text)
- Actions: **Save** · **Enable/Disable** · **Archive** · **Delete**

**Delete behaviour (important):**
- If `history_count == 0`: Delete is enabled, behind a confirmation. Do **not** use
  `confirm()`/`alert()` — build a small inline confirm (a second click on a "Really delete?"
  state, or an inline confirm strip). Browser modal dialogs are to be avoided in this app.
- If `history_count > 0`: Delete is **disabled/greyed**, with the reason shown inline
  (`"3 logged sets — archive instead"`), and Archive is offered in its place.
- The API must enforce this too — never rely on the UI alone.

Replace the existing `alert('Save failed')` patterns with inline error text on the row.

### 6.4 Add exercise

An add panel (mirroring the existing `.add-section` style) with: Name, Tier, Muscle group,
Function (dependent select + new-function option), Tracking type, and optional Notes. Validate
that name is non-empty client-side; surface the server's duplicate-name error inline rather than
via `alert()`.

### 6.5 Reordering

Within a function, allow reordering via small ▲/▼ buttons on each row (not drag-and-drop —
this is a phone-first app and drag is fiddly). Each move POSTs the new id order for that function
to `/api/gym/exercise/reorder`. Optimistically reorder in the DOM, roll back on failure.

### 6.6 Mobile-first, non-negotiable

This app is used on a phone. Everything must work at 360px wide with no horizontal scroll: tap
targets ≥ 40px, `-webkit-tap-highlight-color: transparent` as used elsewhere, no hover-only
affordances, long names ellipsised. The existing `@media (min-width: 640px)` two-column
`.groups-grid` treatment can be carried over for desktop.

### 6.7 Navigation

Add `/exercise-db` to the Arjun nav in `base.html`. The bottom nav is a **fixed 4-tab** layout
(Today / Train / Food / Stats) — **do not add a fifth tab**. Instead include `/exercise-db` in the
`Train` tab's active-path condition (alongside `/train`, `/session/log`, etc.) and reach it from
the "Manage databank →" link on `/train`.

---

## 7. Deliverables

1. `migrate_exercise_db.py` — backup, idempotent migration, plan/summary output, verification
   assertions with PASS/FAIL.
2. Updated `gym_bank.py` — new/changed functions per §5.1, docstrings in the file's existing style.
3. Updated `app.py` — new routes per §5.2, `/train` regrouping per §6.2, CSRF fix per §5.3.
4. New `templates/exercise_db.html`.
5. Updated `templates/train.html` — regrouped, edit/add panels removed, CSRF headers added,
   "Manage databank →" link.
6. Updated `base.html` nav active-path condition.
7. `docs/EXERCISE-DB.md` — short (one page). Must cover: the final schema of `gym_exercises`,
   `gym_functions` and `gym_progression`; the `tracking_type` values; the `is_enabled` vs
   `archived_at` distinction; the delete-vs-archive rule; how to add a new function; that
   `is_weighted` is deprecated-but-retained and why; and the two deferred follow-ups (legacy
   `exercises`/`exercise_bank_config` consolidation, structured target durations for the isometric
   holds).

## 8. Reporting back

When done, report:
- The migration's summary output (rows changed per operation) and the PASS/FAIL verification block.
- Any exercise whose `tracking_type` you had to guess at, so it can be corrected by hand.
- Any place where you found in-scope code depending on `is_weighted` that you rewired.
- Anything in §3 you could **not** reconcile with the DB — do not silently paper over a mismatch.
- A confirmation that `/train`, `/exercise-db` and `/session/log` all render, and that an
  edit/add/toggle from each page returns 200 rather than 403.

Do not commit. Leave the working tree for review.
