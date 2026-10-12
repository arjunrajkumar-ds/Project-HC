# Plan — Surface Muay Thai under Cardio on the Train screen

**Status:** ✅ implemented 2026-09-30 · **Scope:** data migration + small template/code tidy

> Applied. `migrations/migrate_muay_thai_under_cardio.py` has been run against the live
> `tracker.db` (backup: `tracker.db.bak-muaythaicardio-20260930-132736`, gitignored). §2.3 and §2.5
> are in `templates/train.html`; §2.4 is in `gymtracker/app.py`. One deviation from §2.4:
> `/api/gym/quick-log` and its undo were **kept**, with a docstring saying they have no in-app
> caller and why they might still earn their place — see "Deviations" at the foot of this doc.

---

## 0. Why it's invisible

The four drills exist and are enabled. Nothing is broken about them — they are simply **unreachable**.

```
id 50  3 Minute Bagwork               tier 5  reps         function NULL
id 51  10/20/30/40                    tier 5  reps         function NULL
id 52  Medicine Ball Bent-Over Slams  tier 3  weight_reps  function NULL
id 53  300/30/30/3                    tier 5  reps         function NULL
```

All four sit in `muscle_group = 'muay_thai'`. Since the Train rewrite, a group only renders if it
appears in one of two lists in `app.py`:

```python
_GYM_PRIMARY_MUSCLES  = ['chest', 'back', 'legs', 'cardio']   # picker tile
_GYM_CONSTANT_MUSCLES = ['arms', 'core', 'shoulders']         # standing-goal strip
```

`muay_thai` is in neither. The only other way a section appears is if it is *derived from sets
already logged against it* (`LIVE.muscles`) — so the group can only become visible after you log to
it, and you can only log to it once it's visible. A closed loop.

**How it used to work.** The pre-rewrite Train page had a one-touch tap-to-log row for these drills.
That flow was deleted with the rewrite, but its scaffolding was left behind:

- `_GYM_ONE_TOUCH_GROUPS = {'muay_thai'}` (`app.py:1173`) — **defined, referenced nowhere**
- the comment at `app.py:1181` claiming *"Muay Thai keeps its existing one-touch flow untouched"* —
  **stale**, the flow is gone
- `/api/gym/quick-log` and `/api/gym/quick-log/undo` — **no callers in any template**
- `.mg[data-m="muay_thai"]{--g:#d1495b}` in `train.html` — styling for a group that cannot render

So this is a regression from the rewrite, not a missing feature. Confirmed by the data: **all four
exercises have zero logged sets, ever.** Which is also what makes re-homing them free.

---

## 1. Decisions taken

| Question | Decision |
| --- | --- |
| Placement | **Nest under Cardio** as a new function, rather than its own tile |
| Tracking | **3 Minute Bagwork → `time`**; the two ladders stay `reps`; Slams stay `weight_reps` |
| Tier | **Retier the three tier-5 drills to tier 3**, matching the cardio machines |

Nesting is the cheapest correct move: Cardio is *already* primary, so the drills appear the moment
the data lands — no new code path, no new list entry, no new rendering branch. Cardio's function
checklist becomes `Machine / Steady-state ○  Muay Thai ○`, which reads as "cardio day, two ways to
do it" and gives the checklist something to actually say.

---

## 2. The change

### 2.1 Seed the function row

```sql
INSERT OR IGNORE INTO gym_functions (muscle_group, key, label, sort_order)
VALUES ('cardio', 'muay_thai', 'Muay Thai', 1);
```

`sort_order 1` puts it after `machine` (0), so machines stay the default read.

### 2.2 Re-home the four exercises

```sql
UPDATE gym_exercises
   SET muscle_group = 'cardio',
       function     = 'muay_thai',
       tier         = 3,
       sort_order   = :n
 WHERE id = :id;
```

| id | name | tier | tracking_type | sort_order |
| --- | --- | --- | --- | --- |
| 50 | 3 Minute Bagwork | 5 → **3** | reps → **time** | 0 |
| 51 | 10/20/30/40 | 5 → **3** | reps | 1 |
| 53 | 300/30/30/3 | 5 → **3** | reps | 2 |
| 52 | Medicine Ball Bent-Over Slams | 3 | weight_reps | 3 |

Explicit `sort_order` matters: all four currently sit at `0`, and
`get_gym_bank_grouped()` falls back to `(sort_order, tier, name)` — which would list them
alphabetically as `10/20/30/40`, `3 Minute Bagwork`, `300/30/30/3`, `Medicine Ball…`. Bagwork first
is the sensible default.

`tracking_type` must be changed through `gym_update_exercise()` rather than raw SQL, or written
alongside `is_weighted` by hand — the helper keeps the deprecated `is_weighted` flag in sync
(`_iw_from_tracking`), and a raw UPDATE would leave it stale.

### 2.3 Give the bagwork timer a real target

`3 Minute Bagwork` as `tracking_type='time'` gets the duration timer — but `train.html` hardcodes
the target box at `value="60"`:

```html
<input type="number" id="dur-target" value="60">
```

For a 3-minute round you would have to retype `180` every time. Store the default on the exercise's
existing `engagement` blob (same trick cardio already uses for its field spec):

```json
{"target_s": 180}
```

and read it in `renderExerciseSheet()`'s timed branch:

```js
const targetDefault = (cspec.target_s) || 60;
```

`cspec` is already in scope in that function — it is currently only consulted in the cardio branch.
Two-line change, and every future timed exercise gets a sensible default for free.

### 2.4 Tidy the dead one-touch scaffolding

Now that these drills log through the normal exercise sheet, the leftovers are unambiguously dead:

- delete `_GYM_ONE_TOUCH_GROUPS` and correct the stale comment block at `app.py:1178-1183`
- decide on `/api/gym/quick-log` + `/api/gym/quick-log/undo`: no template calls either. They are also
  the last caller of `undo_last_today()` *without* a `session_id`, so removing them lets that
  parameter become required. Recommend deleting both; keep them only if you want a Shortcuts/
  home-screen tap-to-log integration later, in which case leave a comment saying so.
- leave `muay_thai` in `_GYM_GROUP_ORDER` / `_muscle_order()` and its `MG_COLORS` entry. They cost
  nothing, and any stray historical row still resolves to a label rather than `Other`.
- delete the now-orphaned `gym_functions` row `('muay_thai', 'conditioning')` (id 18) — guarded on
  there being no exercise still pointing at it.

### 2.5 One cosmetic bug this will expose

`pickerBtn()` in `train.html` renders `${m.functions.length} functions` unconditionally, so the
Cardio tile currently reads **"1 functions · 6 exercises"**. After this change it becomes
"2 functions · 10 exercises" and the bug hides itself — but it is one line to fix properly while you
are in there.

---

## 3. Migration script

`migrations/migrate_muay_thai_under_cardio.py`, following the `migrate_cardio_group.py` pattern:

1. Refuse to run if `tracker.db` is missing; back it up to `.bak-muaythaicardio-<timestamp>`.
2. `INSERT OR IGNORE` the `cardio / muay_thai` function row.
3. For each of the four ids, `UPDATE` group/function/tier/sort_order, and set `tracking_type` +
   `is_weighted` together for id 50.
4. Set `engagement = '{"target_s":180}'` on id 50.
5. Delete the orphaned `muay_thai / conditioning` function row, guarded by a count check.
6. Print a verification table of the cardio group, grouped by function.

**Idempotent** — every step is `INSERT OR IGNORE` or an `UPDATE … WHERE id =`, so a second run is a
no-op. Run with the Flask app stopped.

```
python migrations/migrate_muay_thai_under_cardio.py
```

---

## 4. Verify

- `python3 -m tests.test_exercise_bank_roundtrip` — still passes (it seeds its own `muay_thai`
  fixtures in a temp copy, so it is unaffected by the live re-home).
- `TZ=Australia/Melbourne python3 -m tests.test_train_session_day` — still passes.
- `python -c "from gymtracker.gym_bank import get_gym_bank_grouped; ..."` → the `cardio` node has
  **two** functions, `machine` (6) and `muay_thai` (4), and **no** `muay_thai` top-level node.
- Load `/train`:
  - the **Cardio** tile reads *2 functions · 10 exercises*
  - picking Cardio shows a `MUAY THAI` heading under `MACHINE / STEADY-STATE`
  - the checklist pills read `Machine / Steady-state ○  Muay Thai ○`
  - `3 Minute Bagwork` opens with the duration timer and a **180 s** target pre-filled
  - `Medicine Ball Bent-Over Slams` opens with weight + reps
  - log one set of each, refresh — both rehydrate, and the `Muay Thai` pill ticks
- `/exercise-db` shows all four under Cardio → Muay Thai, and the old Muay Thai accordion group is
  gone.

---

## 5. Rollback

The timestamped `.bak-muaythaicardio-*` copy beside `tracker.db` is a full restore. Failing that, the
change is four `UPDATE`s and one `INSERT`, all reversible by hand — and since the four exercises have
no logged history, there is nothing that can be orphaned by reverting.

---

## 6. Worth noting for later

`muscle_group` on a *set* is not stored — it is derived from the exercise at read time
(`_exercise_meta_map()`). That is what makes this migration free today, but it also means that if you
re-home an exercise **after** logging against it, all its history silently re-labels itself to the new
group. Fine here (zero history), and arguably the behaviour you want, but it is worth knowing before
the next re-home.

Related: §2.9 of `REVIEW-train-hardening.md` flagged that `syncActiveMuscles()` only filters *constant*
muscles, so a set logged against any unlisted group (`stability`, `muay_thai`) would render a full
day-driver section. After this change `muay_thai` is empty, which removes one of the two ways to trip
that — `stability` (2 exercises, both archived) is the other. The taxonomy is still worth making
explicit.


---

## 7. Deviations from the plan as written

**`/api/gym/quick-log` kept, not deleted.** §2.4 offered deleting it. It is the one logging endpoint
that needs no CSRF-bearing page load, which makes it the natural target for an iOS Shortcut or a
home-screen tap-to-log for bag rounds — a plausible enough future that deleting it now would be
throwing away the exact shape you'd rebuild. Its docstring now states plainly that nothing in the app
calls it, so it can't be mistaken for live code. `undo_last_today()` therefore keeps its optional
`session_id` rather than making it required.

`_GYM_ONE_TOUCH_GROUPS` **was** removed, and the stale comment block replaced with one that records
why the drills went missing in the first place.

## 8. What was verified after applying

- `python3 -m tests.test_exercise_bank_roundtrip` and
  `TZ=Australia/Melbourne python3 -m tests.test_train_session_day` — both pass.
- All 34 GET routes load without a 5xx.
- `get_gym_bank_grouped()`: no `muay_thai` top-level group; `cardio` has two functions
  (`machine` 6, `muay_thai` 4) and `active_count` 10.
- Migration re-run twice: idempotent, no duplicate function row, no drift.
- The history guard was exercised by seeding a fake set against a drill — the script refuses and
  changes nothing without `--force`.
- Headless browser pass at 414×896, against a copy of the migrated database:
  - Cardio tile reads *2 functions · 10 exercises* (the "1 functions" bug is fixed properly)
  - `MACHINE / STEADY-STATE` and `MUAY THAI` headings, checklist `Machine / Steady-state ○  Muay Thai ○`
  - `3 Minute Bagwork` → subtitle `CARDIO · MUAY THAI · T3`, target box defaults to **180**
  - logged one set of each type: timer (stopped early against a 3 s target → stored 1 s, confirming
    the `min(target, elapsed)` fix), manual `1:30` → stored 90 s, ladder → 20 reps with no weight box,
    slams → 8 kg × 12
  - refreshed: all four sets rehydrate, `Muay Thai` pill ticks, status reads *4 sets · 1 muscle*
  - no JS errors, console clean
