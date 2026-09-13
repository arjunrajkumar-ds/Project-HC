# Plan — Add "Cardio" as a selectable group on Arjun's TRAIN screen

**Status:** ✅ implemented (code) · run `migrate_cardio_group.py` locally to apply · **Date:** 2026-09-07

---

## 0. Context — why this is not just "un-delete the old panel"

The Cardio group *does* exist in old/unused files, but it belongs to the **legacy **`exercises`** table**, which Arjun's Train screen no longer uses:

| Old/unused artifact | What it is |
| --- | --- |
| `_to_delete/session_log.html` | Full deprecated "Cardio" pick-group panel + `pickCardio()` / `logCardio()` / `/api/gym/log-cardio` |
| `templates/cardio.html` | Standalone `/cardio` logger driven by `get_cardio_choices()` |
| `database.py` (~L1079-1084) | Seeds `muscle_group='Cardio'` tier-4 rows: Swimming, Running, Rowing, Muay Thai, Cycling, Stairmaster (stores metrics as `cardio_metrics` JSON) |
| `app.py` `_GAYATHRI_GROUPS` | `'Cardio'` group — but that's **Gayathri's** home screen, not Arjun's Train |

**Arjun's **`/train` (`templates/train.html`) was rewritten to read the **new **`gym_exercises`** + **`gym_functions`** schema** as a `muscle → function → exercise` tree via `get_gym_bank_grouped()`. Group order/labels live in two mirrored spots that must stay in sync:

- `app.py`: `_GYM_GROUP_ORDER`, `_GYM_GROUP_LABELS` (L1188-1192), `_GYM_PRIMARY_MUSCLES`, `_GYM_CONSTANT_MUSCLES` (L1206-1207)
- `gym_bank.py`: `_muscle_order()`, `_muscle_labels()` (L209-218)

> Note: the original seed had **no "Treadmill"** row (closest were Running / Stairmaster). Treadmill will be a **new** activity.

---

## Field model (decided)

Cardio needs metrics the existing tracking types don't cover. The `gym_progression` table today has `weight_kg, sets, reps, duration_s` — it has `duration_s`** already**, but **no distance and no setting** column. So:

| Field | Applies to | Storage | UI input |
| --- | --- | --- | --- |
| **Duration** | all cardio | `gym_progression.duration_s` (exists) | timer / mm:ss entry |
| **Setting** | all cardio | **new** `gym_progression.setting TEXT` | small text/number box — machine level/resistance/incline (e.g. "Level 8", "Incline 5") |
| **Distance** | **Rowing only** | **new** `gym_progression.distance_m INTEGER` | number box (metres) |

Per-activity field visibility is driven by a small **metrics spec** stored in the exercise's existing `engagement` JSON column (reusing the legacy convention):

- Rowing → `{"duration": true, "distance": "m", "setting": true}`
- All others → `{"duration": true, "setting": true}`

> **"Setting" defined as:** the machine's level / resistance / incline, entered as free text (e.g. `Level 8`, `Incline 5%`, `Resistance 12`). If you'd rather it be a strict numeric stepper or a location dropdown, that's a one-line change in the logger + spec — flag it and I'll adjust.

This is the **new cardio **`tracking_type` — a fifth type added to `TRACKING_TYPES`.

---

## 1. Register the `cardio` group (code — edit BOTH mirrors)

`app.py`

```python
_GYM_GROUP_ORDER = ['chest', 'shoulders', 'back', 'legs', 'core', 'arms', 'stability', 'muay_thai', 'cardio']
_GYM_GROUP_LABELS = { ...,
    'muay_thai': 'Muay Thai',
    'cardio': 'Cardio',
}
# Make it pickable as the day's activity:
_GYM_PRIMARY_MUSCLES = ['chest', 'back', 'legs', 'cardio']

```

`gym_bank.py` (keep identical — divergence is "cosmetic only" per its own comment)

```python
def _muscle_order():
    return ['chest', 'shoulders', 'back', 'legs', 'core', 'arms', 'stability', 'muay_thai', 'cardio']

def _muscle_labels():
    return { ..., 'muay_thai': 'Muay Thai', 'cardio': 'Cardio' }

```

## 2. Schema change — add cardio columns + tracking type

`gym_bank.py`:

```python
TRACKING_TYPES = ('weight_reps', 'reps', 'time', 'weight_time', 'cardio')

```

Migration adds two nullable columns to `gym_progression` (idempotent — guard with a PRAGMA check):

```sql
ALTER TABLE gym_progression ADD COLUMN distance_m INTEGER;   -- Rowing (metres)
ALTER TABLE gym_progression ADD COLUMN setting    TEXT;      -- machine level / resistance

```

Extend `log_gym_set()` to accept and persist `distance_m` and `setting` (mirrors how `duration_s` is already threaded through). Add both to the SELECT lists in `get_gym_bank_grouped()`, `get_exercise_prefill()`, and `get_live_session_state()` so they round-trip on rehydrate.

## 3. Seed a function row for cardio (avoids "Unassigned")

```sql
INSERT OR IGNORE INTO gym_functions (muscle_group, key, label, sort_order)
VALUES ('cardio', 'machine', 'Machine / Steady-state', 0);

```

## 4. Seed the six activities into `gym_exercises`

Tier must be **1-3** — `train.html` L1241 excludes `tier == 4` (Fa Jin) and tier 5 isn't in the picker. Use **tier 3**. `tracking_type='cardio'`; per-activity fields in `engagement`.

```sql
INSERT OR IGNORE INTO gym_exercises
    (name, tier, muscle_group, function, is_enabled, tracking_type, engagement, sort_order)
VALUES
    ('Rowing',      3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"distance":"m","setting":true}', 0),
    ('Treadmill',   3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 1),
    ('Stairmaster', 3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 2),
    ('Cycling',     3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 3),
    ('Swimming',    3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 4),
    ('Running',     3, 'cardio', 'machine', 1, 'cardio', '{"duration":true,"setting":true}', 5);

```

> `name` is `UNIQUE` within `gym_exercises` — legacy `exercises` rows are a different table, so no conflict.

## 5. Train logger UI — new `cardio` branch (`train.html`)

`renderExerciseSheet()` currently branches on `tracking_type` (`weight_reps`/`reps`/`time`/`weight_time`). Add a `cardio` branch that reads the exercise's `engagement` spec and renders:

- a **duration** control (reuse the existing timer UI from the `time` branch),
- a **setting** text box (always, for cardio),
- a **distance (m)** number box **only when** `engagement.distance` is set (i.e. Rowing).

`logSet()` / `logFajin()` and `/api/gym/log-set` extend to pass `distance_m` and `setting` alongside the existing `duration_s`. Update `setLabel()` / `refLabel()` so cardio sets read e.g. `1200m · 5:00 · Level 8` (Rowing) or `20:00 · Level 6` (others).

## 6. Migration script (runs on Arjun's machine)

Create `migrate_cardio_group.py` following the `migrate_gym_bank.py` pattern:

1. Back up `tracker.db` (timestamped `.bak`).
2. `ALTER TABLE` add `distance_m` + `setting` (guarded/idempotent).
3. `INSERT OR IGNORE` the gym_functions row (step 3).
4. `INSERT OR IGNORE` the six gym_exercises rows (step 4).
5. Print a verification summary.

> Sandbox cannot open the live SQLite DB — run locally: `python migrate_cardio_group.py`

## 7. Verify

- `python -c "from gym_bank import get_gym_bank_grouped; ..."` → a `{'key':'cardio','label':'Cardio', ...}` node with 6 exercises appears.
- Load `/train` → **Cardio** is pickable (primary); each activity shows **duration + setting**; **Rowing** also shows **distance**.
- Log one set of Rowing and one of Treadmill; refresh → both rehydrate with their metrics.
- Confirm existing chest/back/legs flows are unchanged.

---

## Files touched

| File | Change |
| --- | --- |
| `app.py` | add `cardio` to group order/labels + primary list |
| `gym_bank.py` | mirror order/labels; add `'cardio'` tracking type; thread `distance_m` + `setting` through `log_gym_set` and the read/prefill/rehydrate SELECTs |
| `migrate_cardio_group.py` *(new)* | backup, add columns, seed function + 6 exercises |
| `templates/train.html` | new `cardio` logger branch (duration + setting, +distance for Rowing) + label formatting |

