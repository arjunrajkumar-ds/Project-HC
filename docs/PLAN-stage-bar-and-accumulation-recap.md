# PLAN — stage bar on /train, finish recap, two more accumulation views

**Status:** 📐 drafted · wireframes only, no code changed · **Date:** 2026-10-18
> Wireframes (interactive, real `tracker.db` numbers): `docs/wireframes/stage-bar-and-accumulation.html`.
> Take the usual backups before the first edit: `templates/train.html`, `templates/_scheme_accumulation.html`,
> `gymtracker/app.py`, `gymtracker/gym_bank.py` → `*.bak-stagebar-<ts>`. No schema change, so `tracker.db` is not backed up.

## 0. What and why

Three things, in build order:

1. **Stage bar.** A pinned chevron strip on /train that shows the shape of the workout
   (Warm-up → T1 #1 → T1 #2 → T2 → T3 [SH | AR | CO]), moves forward as sets are logged, and has a **Next** button.
2. **Finish recap.** Finishing a session opens a sheet where each laddered lift's new passes drop into their scheme pips,
   and any rung that reaches T fills with colour.
3. **Two alternate accumulation views** for the home panel's view toggle: **Route** (the ladder as a climbing route)
   and **Ledger** (passes over time with a frontier line).

## 1. Decisions

| Question | Decision |
|---|---|
| How does a stage advance? | **Logging + manual.** The first set in a stage makes it active. A set from a *later* stage marks every earlier non-optional stage done. **Next** marks the current stage done and moves on. |
| T1 #1 vs #2 | Two **different** tier-1 lifts: the first distinct T1 `exercise_id` of the day is #1 and the second is #2. A third T1 lift counts toward #2. |
| T2 visibility | Folded to a 13px dotted notch until a T2 set lands (or the notch is tapped). Next skips a folded T2, and the stage count drops from 5 to 4. |
| When does the recap play? | On **Finish session**. The finish response carries the deltas, so nothing extra is fetched. |
| Does today count on home? | **Yes, once finished.** `before_today` becomes "before today, *or* today if today's session is `finished = 1`". Logging again reopens the session (`gym_bank.py` L698–711 already does this), so today drops out again until the next Finish. |
| Where does stage state live? | Stages are **derived from `LIVE.sets`** on every render. The only stored part is what the user chose (manual Next, T2 opened) in `sessionStorage['train_stage_<day>']`, the same pattern as `REST_KEY`. |

## 2. Part 1 — Stage bar

### 2.1 Which stage a set belongs to (the data doesn't match the obvious rule)

`gym_exercises.tier` cannot drive this alone. Shoulders, arms and core are mostly **tier 2** in the bank (DB Shoulder Press,
every curl and skullcrusher, Ab Wheel, Cable Crunch), while BB OHP and DB Lateral Raise (heavy) are **tier 1**. So the rule is:

| Stage | Rule (`muscle_group`, `tier`) |
|---|---|
| `t1` | `tier == 1`, any muscle group, including OHP and heavy laterals |
| `t2` | `tier == 2` and `muscle_group in (chest, back, legs)` |
| `t3:sh` / `t3:ar` / `t3:co` | `tier in (2, 3)` and `muscle_group` is `shoulders` / `arms` / `core` |
| `warm` | `is_warmup = 1` (train-rehaul phase 2; **does not exist yet**, see §7) |
| `None` | tier 4 (Fa Jin), cardio, stability, `uncategorised` (they never move the bar) |

Do this **on the server** so it can be tested in Python: add `_stage_for(meta)` in `gym_bank.py` and a `stage`
key on every set in `get_live_session_state()` (next to `tier`). The client never re-derives it.

> Data fix worth doing alongside: `Cable Tricep Pushdown` and `Cable Bicep Curl` are `uncategorised`. Move them to `arms`,
> otherwise they won't fill the AR segment.

### 2.2 Deriving state (client, pure)

```js
function stageState(sets, manual){        // manual = {done:[keys], t2Open:bool} from sessionStorage
  const t1Ids = [...new Set(sets.filter(s=>s.stage==='t1').map(s=>s.exercise_id))];
  const keyOf = s => s.stage==='t1' ? (s.exercise_id===t1Ids[0] ? 't1a' : 't1b')
                   : s.stage && s.stage.startsWith('t3') ? 't3' : s.stage;
  // per-stage: sets, exercise names, t3 part counts
  // active = max(highest stage index that has a set, index after the last manual 'done')
  // every non-optional stage before `active` is done; t2 is open iff it has sets || manual.t2Open
}
```

Fill targets: T1 uses the **current rung's `need_sets`** from the scheme ladder (`PREFILL` already knows the lift)
and falls back to 3. T2 is 3 and warm-up is 2. Each T3 part fills at 3 sets, the same as the coverage meters' "3 = full".

### 2.3 DOM and placement

* `<div id="stage-root"></div>` goes between `.tr-head` and `#date-head`. `.sb` is `position:sticky; top:0; z-index:50`,
  with `background: var(--bg)` so the cards scroll underneath. It's about 66px tall and needs no extra padding on `.tr-wrap`.
* Pandora tokens only. T3 part colours come from `MG_COLORS.shoulders/arms/core`, the one hardcoded family the page already uses.
* Chevrons are `clip-path` polygons (`--pt: 9px`) overlapping by -5px, which leaves a 4px diagonal gap. T2 folds by
  animating `flex` from `0 0 13px` to `1 1 0`.
* Animations (all in the wireframe CSS, ready to lift): `sheen` on the active chevron, then `sweep` + `pop` on the one that just
  completed, then `arrive` on the next. T3 parts get `pop` when they fill. Finish plays a staggered `wave` across the bar.
  Everything is off under `prefers-reduced-motion`.
* Wire it up by adding `renderStage()` to `renderAll()` (L956). That covers log, delete, `resyncLive()` and `setDay()` for back-dated
  days, because the storage key includes the selected day.

### 2.4 Interactions

| Control | Behaviour |
|---|---|
| **Next** | Adds the active key to `manual.done`, then moves to the next visible stage (skips a folded T2). |
| **Next on T3 → Finish** | Uses the **existing double-tap guard**: first tap calls `armFinish()` and shows "Tap again" in red, second tap calls `finishSession()`. |
| Tap a done chevron | `openExercise()` on that stage's most recent `exercise_id`, the same as tapping a row in the coverage strip. |
| Tap folded T2 | Sets `manual.t2Open`, unfolds T2, and makes it active if it's ahead of the current stage. |

## 3. Part 2 — Finish recap

### 3.1 Server

* `gym_bank.get_weight_rep_day_groups(max_tier=3, before_today=True)` gets a new mode, `include_finished_today=True`:

  ```sql
  ({day} < {today}) OR ({day} = {today} AND EXISTS (
      SELECT 1 FROM gym_sessions s WHERE s.finished = 1 AND {s_day} = {today}))
  ```
  Use `_training_day()` / `TODAY_SQL` on both sides (REVIEW §1.1's two-calendar bug).
* Split `_build_scheme_viz()` into `_scheme_pass_counts(rows)` (the existing day×rung loop) plus the seed builder,
  so the delta reuses the same pass rule instead of copying it.
* New `_scheme_delta()` computes pass counts twice (`before_today=True` and with today) and returns only the cells that changed,
  grouped per (exercise, today's top weight):

  ```json
  [{"exercise_id": 12, "name": "DB Skullcrusher", "tier": 2, "T": 5, "weight_kg": 30.0, "today": "3×7",
    "cols": [{"label": "3×6", "before": 5, "after": 6}, {"label": "3×7", "before": 4, "after": 5}, ...],
    "gained": 2, "mastered": ["3×7"]}]
  ```
  Order lifts by when their first set was logged today.
* `api_gym_session_finish()` adds `'accumulation_delta': _scheme_delta()` to its response. Finishing still only stamps
  `finished = 1`, so nothing about the save path changes.

### 3.2 Client

* `#recap-sheet` reuses `.sheet` / `.sheet-panel`, keeping the nav padding from REVIEW §1.4. `finishSession()` opens it in place of the
  `toast('Session finished.')` toast when `accumulation_delta.length`. Otherwise the toast stays.
* Timing (from the wireframe): sheet slides up in 0.5s, lifts come in 0.9s apart, each new pip lands 150ms after the last
  (`land` keyframe: scale 0→1.6→1 with a fading ring), the counter goes up per pip, and mastery triggers a `flood` glow, a
  `MASTERED 3×7` tag and a bump on the right-hand counter. Unchanged rungs drop to 38% opacity.
* Passes beyond T have no pip to drop into, so they tick the counter and update the `+n` overflow badge.

## 4. Part 3 — Alternate views (pick one, or both)

Both go into `_scheme_accumulation.html` as extra `data-view` buttons (`route`, `ledger`) next to Matrix/Bars/Rings.
Rename the existing labels to `Mtx · Bar · Rng` so five buttons fit at 375px.

| View | Reads | New data |
|---|---|---|
| **Route.** Floors = weights (heaviest on top), holds = rungs in `TIER_BOUNDS` order, a ring of T segments per hold. Rope lights up once a hold is mastered. Each new weight joins on the left (a heavier weight restarts at the easiest rung). A dashed "next floor" at +2.5 kg sits above. ▲ = you are here. | `d.weights`, `d.cols`, `d.passes`, `d.current` | none |
| **Ledger.** x = training days, y = rungs. A dot per pass (accent for the day's heaviest weight, grey for lighter), a white step-line frontier for the hardest rung each day, weight-jump labels, gap markers over 21 days, and a tally at the current weight on the right. | the above | `d.pass_days: [[day, weight_index, col], …]`, collected in the existing pass loop of `_build_scheme_viz()` |

My recommendation is **Ledger**, because it's the only one of the five views that shows *when* things happened (stalls, going back to
a lighter weight, breakthroughs). Route is the more fun one and could replace Rings, which shows a subset of the same information.

## 5. Tests

* `tests/test_scheme_accumulation.py`: (a) an unfinished today is still excluded; (b) the same sets on a finished today count;
  (c) logging after finishing reopens the session, so today is excluded again; (d) `_scheme_delta()` returns only changed cells,
  `mastered` lists exactly the rungs that crossed T, and overflow past T is counted in `gained`.
* New `tests/test_train_stage.py`: `_stage_for()` across the table in §2.1 (OHP → `t1`, DB Shoulder Press → `t3:sh`,
  DB Row → `t2`, Fa Jin / cardio / uncategorised → `None`), and `get_live_session_state()` puts `stage` on every set.
* Manual: run through the wireframe's "Auto-play a session" sequence on a phone at 375px, then do it again with a back-dated day selected.

## 6. Files touched

| File | Change |
|---|---|
| `gymtracker/gym_bank.py` | `_stage_for()`; `stage` on live sets; `include_finished_today` on `get_weight_rep_day_groups()` |
| `gymtracker/app.py` | split `_build_scheme_viz()`; `_scheme_delta()`; `pass_days` in the seed; finish response |
| `templates/train.html` | `#stage-root` + CSS + `renderStage()`/`stageState()`; `#recap-sheet` + `playRecap()` |
| `templates/_scheme_accumulation.html` | `route` / `ledger` renderers, view buttons |
| `tests/…` | as §5 |

## 7. Open questions and known limits

1. **Warm-up has no signal yet.** Until train-rehaul phase 2 adds `is_warmup`, the warm-up stage completes only by **Next**
   or when the first T1 set lands, and its fill stays empty. Building phase 2 first would make the stage real.
2. **T1 #1/#2 is about order, not which lift is more important.** If you start with a T1 accessory and then squat, the squat becomes #2. If that's wrong,
   the fix is a per-day "pin as #1" from the chevron's long-press, which is out of scope here.
3. **Should T3 complete itself?** It's currently manual (Next → Finish). The alternative is auto-completing when all three parts are full.
4. **Today on home after finishing** means the home panel can change during the day if you finish, log one more set, and finish again.
   That's intended, since the panel follows Finish, but it's a change from "stable until the day ends".
