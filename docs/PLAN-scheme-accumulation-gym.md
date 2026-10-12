# PLAN — point the scheme accumulation panel at the gym tables

**Status:** ✅ implemented (code) · no migration, no schema change · **Date:** 2026-10-12
> Backups: `gymtracker/app.py.bak-trainrehaul-20261012-062855` (pre-dates this change and the Train rehaul both).

## 0. Why this is not a table swap

The panel was dead and did not look dead. It renders on Arjun's home page, draws a full grid, and shows a
frozen snapshot of pre-July training. Its only fact table is `exercise_attempts`, whose newest row is
**2026-07-12**, and a repo-wide grep finds **zero call sites** for the four functions that write the legacy
progression tables (`record_exercise_attempt`, `advance_progression`, `record_stage_completion`,
`set_progression_weight`). The write path was removed during the Train rewrite and never re-pointed.
Meanwhile every set logged on `/train` goes to `gym_progression`, which the panel never touched.

Swapping `exercises` → `gym_exercises` would not have worked, for three reasons:

| Problem | Why it blocks a swap |
|---|---|
| **Separate ID spaces** | `exercises.id` runs 1–18937; `gym_exercises.id` runs 1–61. They overlap numerically and mean different things. Only 27 rows match by name. |
| **No ladder in the gym schema** | `gym_progression` has no `scheme_id`; `gym_exercises.reps_min`/`reps_max`/`sets` are NULL on all 59 rows; there is no gym equivalent of the `progression` table's "you are here". |
| **A different atom** | Legacy = one row per whole-exercise attempt with an explicit `result`. Gym = **one row per set**, no pass/fail. `successful` is a per-set flag, not a prescription verdict. |

So both the *pass* and the *ladder* had to be derived.

## 1. Decisions

| Question | Decision |
|---|---|
| What is a pass? | **The full prescription on one training day**: at least S sets of at least R reps at weight W is one pass of rung S×R at W. A day of 5, 5, 4 does **not** pass 3×5. |
| Where do the rungs come from? | **`database.TIER_BOUNDS`**, sets as the outer loop and reps inner — the same enumeration `_generate_schemes()` uses, so a rung means what it always meant. T1 3–5×3–5 (9 rungs), T2 3–4×6–8 (6), T3 3–4×8–12 (10). |
| Mastery threshold | **T = 5** (was 10, which with 111 gym sets would have shown nothing as mastered). |
| Scope | `tracking_type = 'weight_reps'`, tiers **1–3** only. Tier 4 is Fa Jin (explosive, not laddered); tier 5 is cardio (no weight axis). |
| Today | **Excluded.** The panel only ever counts finished training days, so it cannot shift under you mid-session. |
| Legacy data | **Not backfilled.** Only 27 of the 53 attempts name-match a gym exercise; porting the rest would attribute guessed history to the wrong lifts. |

## 2. Implementation

### 2.1 `gymtracker/gym_bank.py` — one new read

```python
get_weight_rep_day_groups(max_tier=3, before_today=True)
# -> [{exercise_id, name, tier, day, weight_kg, reps, n_sets}]
```

One row per (exercise, training day, weight, reps). `before_today` applies `{day} < {today}` — the same
idiom `get_previous_session_sets()` uses, through `_training_day()` / `TODAY_SQL`. A raw `DATE(recorded_at)`
here would reintroduce REVIEW §1.1's two-calendar bug. `SUM(COALESCE(sets, 1))` folds in the legacy
`import_history` rows that collapse several identical sets into one row.

### 2.2 `gymtracker/app.py` — `_build_scheme_viz()` rewritten

Legacy version deleted (90 lines over `exercise_attempts`/`exercises`/`schemes`/`progression`). The new one
groups the rows above by `(day, weight)`, and for each rung counts how many of that day's sets cleared the
rep demand; `cleared >= need_sets` is one pass. `_gym_tier_ladder(tier)` builds the rungs from `TIER_BOUNDS`.

**"You are here"** is derived, since there is no gym `progression` table: the heaviest weight of the most
recent finished day, and the highest rung passed at that weight. It can legitimately be `col: null` —
you are working at a weight you have not yet passed anything at.

**The seed shape is unchanged**, so `templates/_scheme_accumulation.html` and `engines/_scheme_home.html`
were not touched at all. An exercise with zero passes is omitted rather than rendered as an empty grid.

## 3. Verify

* `TZ=Australia/Melbourne python -m tests.test_scheme_accumulation` — 28 checks. The ones that matter:
  3 sets of 5 passes 3×5/3×4/3×3 but **not** 4×3; 5,5,4 against a 6-rep ladder yields no passes at all;
  logging today changes nothing while the same work on a finished day counts; timed/Fa Jin/cardio absent;
  a legacy `sets=4` row counts as four sets.
* `GET /` renders `#sa-card` with 17 exercises seeded from gym data, one rung mastered at T=5
  (DB Skullcrusher 3×6 @ 30 kg).

## 4. Known limits

1. **Nothing writes a "pass" explicitly** — it is recomputed from raw sets on every home-page load. Fine at
   111 rows; if `gym_progression` reaches tens of thousands this wants caching or a materialised table.
2. **No profile scoping.** `_build_scheme_viz()` takes no `pid` and `gym_progression` has no `profile_id`.
   Correct today because only Arjun uses the scheme engine; a trap if that changes.
3. **The legacy progression tables are now entirely unreferenced** by live code
   (`exercise_attempts`, `progression`, `stage_completions`, and the four writer functions). They are dead
   weight, not a dependency — worth a separate cleanup change-set rather than deleting them here.
