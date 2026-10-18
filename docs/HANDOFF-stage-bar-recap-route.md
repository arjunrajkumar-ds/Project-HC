# Handoff: Stage bar, Finish recap and Route view (Arjun / scheme engine)

You are implementing three UI features in a small personal Flask + SQLite gym tracker. The repo root is
`project-horsecock/`. Read this whole prompt before editing anything. If this prompt disagrees with
`docs/PLAN-stage-bar-and-accumulation-recap.md`, **follow this prompt** (it is newer and was checked against the code).

## 0. Read first (in this order)

1. `docs/wireframes/stage-bar-and-accumulation.html` is the **visual and motion spec**. Open it in a browser and use every
   button on it. Its CSS (`.sb`, `.cv`, `.part`, `.sb-next`, the `rc-*`, `.pip`, `.rcell` classes, and keyframes `sheen`, `sweep`,
   `pop`, `arrive`, `unfold`, `wave`, `land`, `flood`) and its JS (`makeBar`, `playRecap`, `drawRoute`) are prototypes for you to
   port. Port them; don't redesign. Ignore section 03's "Ledger" view, which is out of scope.
2. `docs/PLAN-stage-bar-and-accumulation-recap.md` explains why each decision was made.
3. `docs/PLAN-scheme-accumulation-gym.md` defines what a "pass" is, the ladder, and why today is excluded.
4. `docs/PLAN-train-rehaul.md` and `docs/REVIEW-train-hardening.md` describe the /train page architecture and the
   bugs it was hardened against (§1.4 nav padding, §1.1 two-calendar bug). Don't reintroduce any of them.

## 1. Scope

"Arjun's experience" means the **`scheme` workout engine** (`profiles.workout_engine = 'scheme'`, profile id 1).
`/train` is in `_SCHEME_ROUTES` only, and the home accumulation panel renders only through
`templates/engines/_scheme_home.html`. Ladder-engine profiles (Gayathri, Raj) must be unaffected. You don't need any
profile checks beyond what the routing already does.

| # | Feature | Where it shows |
|---|---|---|
| 01 | **Stage bar**: a pinned chevron strip, Warm-up → T1 #1 → T1 #2 → T2 (optional) → T3 [SH \| AR \| CO], with a Next/Finish button | `/train` |
| 02 | **Finish recap**: on Finish, a sheet animates each laddered lift's new passes into its scheme pips, and rungs that reach T fill with colour | `/train` |
| 02b | **Today counts on home once finished** | home accumulation panel |
| 03 | **Route view**: a 4th view in the accumulation panel (Matrix / Bars / Rings / **Route**) | home |

**Out of scope:** the Ledger view, warm-up data (`is_warmup` / train-rehaul phase 2), any schema change, recategorising
exercises in the DB, and fixing finish/delete when back-dated except as described in §3.6.

## 2. Ground rules

* **The working tree is dirty on purpose.** Uncommitted back-date-logging work (`LOG_DATE`, `setDay()`, `withDate()`, `dateBody()`,
  `_valid_training_day`, `_day_sql`) is in `gymtracker/app.py`, `gymtracker/gym_bank.py`, `templates/train.html` and two partials.
  Build on top of it. **Don't revert, stash, reset or commit anything.** Don't run git commands that change the tree.
* **Backups before your first edit**, in the repo's convention, with one shared timestamp:
  `gymtracker/app.py.bak-stagebar-<YYYYMMDD-HHMMSS>`, likewise `gymtracker/gym_bank.py`, `templates/train.html`,
  `templates/_scheme_accumulation.html`.
* **Never write test data to `tracker.db`.** That's Arjun's real training history. For manual testing, copy it first (see §6.3).
* There's no schema change and no new dependencies. The stack is Flask + vanilla JS in Jinja templates, with no build step and no frameworks.
* Use **Pandora tokens only** (`var(--accent)`, `--panel`, `--panel-soft`, `--border`, `--dim`, `--dimmer`, `--text-strong`, `--bg`,
  `--font-mono`, `--radius-*`). The two existing hardcoded families are allowed: `MG_COLORS` in train.html and the hex palette
  constants at the top of `_scheme_accumulation.html`'s script.
* Every new animation must be off under `@media (prefers-reduced-motion: reduce)`. Everything has to work at **375px** wide on iPhone
  Safari, with tap targets of 40px or more.
* Match the house style: long explanatory comments that say *why*, and docstrings that cite the PLAN/REVIEW section.
* Line numbers below are approximate as of 2026-10-18. Find code by function name.

## 3. Feature 01: Stage bar (`/train`)

### 3.1 Server: tag every live set with its stage (`gymtracker/gym_bank.py`)

Add a pure function next to `_exercise_meta_map()` (about L1067):

```python
STAGE_T2_GROUPS = ('chest', 'back', 'legs')
STAGE_T3_PARTS = {'shoulders': 't3:sh', 'arms': 't3:ar', 'core': 't3:co'}

def _stage_for(meta):
    """Which stage-bar segment a set belongs to: 't1' | 't2' | 't3:sh' | 't3:ar' | 't3:co' | None."""
```

The rules are fixed. The data was checked, and `tier` alone is wrong because shoulders/arms/core lifts are mostly tier 2 in the bank:

| Return | When |
|---|---|
| `'t1'` | `tier == 1`, any muscle group (BB OHP and DB Lateral Raise (heavy) are tier-1 shoulders, so they're T1) |
| `'t2'` | `tier in (2, 3)` and `muscle_group in STAGE_T2_GROUPS` |
| `'t3:sh'` / `'t3:ar'` / `'t3:co'` | `tier in (2, 3)` and `muscle_group` is shoulders / arms / core |
| `None` | everything else: tier 4 (Fa Jin), cardio, stability, `uncategorised`, unknown |

`'warm'` is never returned yet. Leave a comment saying the warm-up stage arrives with `is_warmup` (train-rehaul phase 2).

In `get_live_session_state()` (about L1145), add `'stage': _stage_for(m)` to each dict appended to `sets`, next to `'tier'`.
Every endpoint that returns `workout_so_far` (log-set, undo, set/delete, live-session, finish) then carries it with no further change.

### 3.2 Client: state derivation (`templates/train.html`)

Write a **pure** function, `stageState(sets, manual)`, that takes no DOM and reads no globals other than its arguments.

* Stage order is `['warm','t1a','t1b','t2','t3']`.
* `t1a` is the first distinct `exercise_id` with `stage==='t1'` today (in `LIVE.sets` order). `t1b` is the second, and a third or later
  distinct T1 lift also counts into `t1b`.
* Each stage records `sets` (count), `name` (the most recent exercise_name), `lastExerciseId`, and for t3, `parts:{sh,ar,co}` with counts.
* `t2.open = t2.sets > 0 || manual.t2Open`. A stage is *visible* unless it's t2 and not open.
* `loggedIdx` is the highest stage index with at least one set (t3 if any part has one). `doneIdx` is the highest index in `manual.done`.
  `active = max(loggedIdx, next visible index after doneIdx, 0)`, clamped to 4.
* `done(stage)` is true if the key is in `manual.done`, **or** if it's visible and its index is below `active`. t3 is done only via Finish
  (`LIVE` reports `finished`, see 3.5) or `manual.done`.
* Fill targets (fixed, not rung-derived): warm 2, t1a/t1b/t2 3, each t3 part 3. These are the minimum sets in `TIER_BOUNDS` and match the coverage
  meters' "3 = full".

**Manual state** lives in `sessionStorage` under `'train_stage_' + curDayIso()` as `{done:[keys], t2Open:bool}`. Wrap every read and write
in try/catch, the same as `readRest()`/`writeRest()`. That makes back-dated days independent automatically.

### 3.3 Client: DOM, CSS, rendering

* Markup: put `<div id="stage-root"></div>` **between `.tr-head` and `#date-head`**.
* CSS: port `.sb` / `.sb-cap` / `.sb-row` / `.sb-chevs` / `.cv` (+ `.fill`, `.tx`, `.k`, `.n`, `.ok`, states `.active`, `.done`,
  `.just-done`, `.arrive`, `.fold`, `.unfold`, `.t3`, `.parts`, `.part`, `.pf`, `.pl`, `.pn`, `.full`, `.just`) / `.sb-next` / `.sb.complete`
  and their keyframes from the wireframe into train.html's `<style>` block, under a new
  `/* ── Stage bar ── */` section. Make `.sb` `position:sticky; top:0; z-index:50; background:var(--bg)`. T3 part colours are
  `--c: MG_COLORS.shoulders | arms | core`, set inline.
* **Build the DOM once, then update it in place**, so the `width`/`height`/`flex` transitions animate. Don't rebuild with
  innerHTML on every render: `renderAll()` runs often, including 550ms after every save (`logSet`).
* `renderStage(opts)`: compute `stageState(LIVE.sets, manual)`, **diff it against the previous render** (`STAGE_PREV`: done keys, active
  key, t2 open, t3 parts that reached 3) and only add the one-shot classes on a transition: `just-done` on a newly done stage, `arrive` on a
  newly active one, `unfold` when t2 opens, `just` on a part that just filled. Remove each class after its animation (setTimeout of
  about 1.2s, as in the wireframe). For `{animate:false}` (first paint, and after `setDay()` / `resyncLive()`), just set the state.
* Caption: `Stage n / N` (N is 4 or 5, depending on whether t2 is visible), the active exercise name, and on the right
  `x / 3 sets` (or `k / 3 groups` on T3). When `LIVE.finished`, show `Session done`.
* Hook it up by adding `renderStage()` to `renderAll()` (about L956). Call it with `{animate:false}` from `init()` before
  `openExercise`, and from `resyncLive()` and `setDay()`. Simplest approach: a module flag `STAGE_SILENT` that those paths set around their
  `renderAll()` call.

### 3.4 Interactions

| Control | Behaviour |
|---|---|
| **Next** (active ≠ t3) | Push the active key into `manual.done`, save, `renderStage()`. The derivation moves to the next visible stage and skips a folded t2. |
| Tap folded **T2** notch | `manual.t2Open = true`, save, render. This plays `unfold`, and t2 becomes active if it's ahead. |
| Tap a **done** chevron or T3 part | `openExercise(stage.lastExerciseId)` if it has one. Otherwise do nothing. |
| **Finish** (active = t3, today, session exists) | Two-tap guard, like the summary sheet: first tap sets class `fin-armed` (red, label "Tap again") and disarms after 3s; second tap calls `finishSession()`. |
| Back-dated day (`isBackdated()`) | The T3 button shows `Done` and is disabled. Finish only applies to today, because `/api/gym/session/finish` acts on today's session. |

### 3.5 `finished` on the live state

`get_live_session_state()` doesn't currently return whether the session is finished. Add `'finished': bool(srow['finished'])`
(select `finished` in the `srow` query) to the returned dict, and `False` in the no-session branch. The bar uses it for the
done state, and the server uses it in §4.1.

## 4. Feature 02: Finish recap, and today counts once finished

### 4.1 Server (`gymtracker/gym_bank.py`)

* Add `is_today_session_finished()`. It returns True if the session `get_live_session_state()` would pick for **today** (same query:
  `{training_day(performed_at)} = TODAY_SQL ORDER BY finished ASC, id DESC LIMIT 1`) has `finished = 1`. This mirrors the
  reopen-on-log behaviour in `get_or_create_today_gym_session()` (about L698–711): log after finishing → `finished = 0` → today drops out again.
* Add `training_today()`, which returns the current training day as `'YYYY-MM-DD'` via `SELECT {TODAY_SQL}`. Use `TODAY_SQL`/`_training_day`.
  **Never** use `DATE('now')` or `DATE(recorded_at)` (REVIEW §1.1).
* Keep `get_weight_rep_day_groups(max_tier=3, before_today=True)` as it is, and update its docstring: `before_today=False` now has a caller.

### 4.2 Server (`gymtracker/app.py`)

* Refactor `_build_scheme_viz()` (about L580) **without changing its output**:
  * `_scheme_pass_counts(rows)` → `{exid: {'name','tier','days','last_day','pass_counts':{(ci, weight): n}}}`. This is the existing day×rung
    loop, moved out unchanged.
  * `_build_scheme_viz(threshold=None, include_today=False)` calls
    `get_weight_rep_day_groups(max_tier=3, before_today=not include_today)` and then builds the seed exactly as it does now.
* `_scheme_delta(live, threshold=None)` returns a list, one item per **(exercise, weight)** whose pass count went up today:

  ```json
  {"exercise_id": 12, "name": "DB Skullcrusher", "tier": 2, "T": 5, "weight_kg": 30.0, "weight_label": "30 kg",
   "today_reps": [7, 7, 7],
   "cols": [{"label": "3×6", "before": 5, "after": 6}, {"label": "3×7", "before": 4, "after": 5}, ...all rungs...],
   "gained": 2, "mastered": ["3×7"]}
  ```
  `before` comes from `_scheme_pass_counts(get_weight_rep_day_groups(before_today=True))` and `after` from
  `before_today=False`. `mastered` lists rungs where `before < T <= after`, and `gained = Σ(after − before)`, which includes overflow past T.
  `today_reps` holds the reps of today's weight_reps sets at that weight, taken from `live['sets']`. Order the items by each exercise's
  first appearance in `live['sets']`, then by weight descending.
* `api_gym_session_finish()` (about L2030): after `end_gym_session(sid)`, read `live = get_live_session_state()`, then compute
  `delta = _scheme_delta(live)` inside its **own try/except**. A failure there logs and returns `[]`, and must never fail the finish.
  Return `{'ok': True, 'workout_so_far': live, 'accumulation_delta': delta}`.
* `build_scheme_home_context()` (about L785): `'scheme_viz': _build_scheme_viz(include_today=is_today_session_finished())`.
  Import the new gym_bank functions in the long `from .gym_bank import ...` line (about L14).
* Update the block comment above `SCHEME_VIZ_THRESHOLD` (about L563–565, "The current training day is excluded…") to describe the new rule.

### 4.3 Client (`templates/train.html`)

* Add `<div class="sheet" id="recap-sheet">…<div class="sheet-panel" id="recap-panel"></div></div>` next to the other sheets.
  Reuse `.sheet`/`.sheet-panel` so the REVIEW §1.4 nav padding still applies. Port the `rc-*`, `.rungs`, `.rung`, `.rcell`,
  `.pip`, `.mtag` CSS and the `land`/`flood` keyframes.
* `finishSession()` (about L1392): on success, keep `closeSummary(); stopRest(); LIVE = …; renderAll();`. Then, if
  `r.data.accumulation_delta` is non-empty, call `openRecap(delta)`. Otherwise keep `toast('Session finished.')`. Also play the stage bar's
  `.sb.complete` wave. Both Finish entry points (summary sheet and stage bar) go through this function.
* `openRecap(delta)` and `playRecap()` follow the wireframe's `buildRecap`/`playRecap`:
  * Header: `Session banked · <day>`, a big `+N passes` counter, and `rungs mastered` on the right.
    Note line: "Today now counts on the home panel."
  * One block per delta item: name, weight chip, `today_reps` formatted as `4×5` if all equal or `5,5,4` otherwise, and `+gained`.
    Below that, one cell per rung (all rungs of the ladder) with T pips stacked bottom-up. Rungs that didn't change get `.quiet` (38% opacity).
    Pips below `before` are `on`, and pips from `before` to `min(after, T)` are `new` and land in sequence. `before ≥ T` shows the cell
    `mastered` with a `+n` overflow badge.
  * Timing: the sheet slides up, blocks come in about 900ms apart, pips land 150ms apart, and the counter goes up per pass. Passes past T
    tick the counter and update the overflow badge. When a rung crosses T, add `mastered flood` to the cell, show a `MASTERED 3×7` tag, and bump the count.
  * Keep all timers in an array. **Done**, a scrim tap, or a reopen cancels the timers and snaps to the final state. Under reduced motion,
    render the final state immediately.
  * T is `delta[i].T` (5). Never hardcode it.

## 5. Feature 03: Route view (`templates/_scheme_accumulation.html`)

* Add `<button data-view="route">Route</button>` to `#sa-view`. Check the View row doesn't wrap at 375px. If it does, reduce
  `.sa-seg button` padding to `5px 9px`, and only then shorten labels.
* `render()` dispatches `state.view === 'route' ? renderRoute(d)`. The focus-weight chips stay hidden for route: `renderChips()` already only
  shows them for `1f`/`1g`.
* `renderRoute(d)` returns an inline SVG string (this file builds HTML strings, so keep that style). Port `drawRoute` from the
  wireframe, adapted to the seed shape:
  * Floors are `d.weights` (ascending; heaviest drawn on top) plus a ghost floor at `top.kg + 2.5` labelled `NEXT`.
  * Holds are `d.cols` in order on each floor, alternate holds raised 10px. Count = `d.passes[ci + ',' + wi] || 0`. Mastered holds
    (`n ≥ T`) are filled `ACCENT` with a soft halo and show ✓ or `+overflow`. In-progress holds get a ring arc of `n/T`
    (`stroke-dasharray`). Untouched holds get a dashed ring. Ghost-floor holds are dashed.
  * Rope: segments within a floor are lit (accent, 2px) when the hold to their left is mastered. Floors connect with a dashed vertical
    pitch on the **left**, from the first hold of floor *n* to the first of *n+1*, labelled with the kg step. That's the "heavier weight
    restarts at the easiest rung" rule.
  * ▲ "you are here" goes above `d.current` (`weight_kg` + `col`). Skip it when `col` is null.
  * Above the SVG: mastered holds, touched/total, and `current rung @ weight`. Below it: a small legend.
  * Use `viewBox` with `width="100%"`. Compute the height from the floor count. Use the file's existing palette constants
    (`ACCENT`, `BORDER`, `LINE`, `PIP_EMPTY`, `T1`…`T5`, `BG`) and add any new ones as constants there.
* Labels in this template are interpolated into HTML. Exercise names come from the DB, so keep whatever escaping the file
  already does and add `esc()` if it has none.

## 6. Tests and verification

### 6.1 Extend `tests/test_scheme_accumulation.py`

`_mkdb()` has **no `gym_sessions` table**, so add one (`id, performed_at, finished, ended_at`). Keep every existing check passing,
because `_build_scheme_viz()` with no arguments must behave exactly as before. Add checks for:
1. Today's sets with an **open** session today are excluded from `_build_scheme_viz(include_today=is_today_session_finished())`.
2. The same sets with `finished = 1` are included.
3. After finishing, logging again via `get_or_create_today_gym_session()` reopens the session, so today is excluded again.
4. `_scheme_delta()` returns only (exercise, weight) pairs that changed, `before`/`after` per rung are correct, `mastered` is exactly
   the rungs that crossed T, `gained` counts overflow past T, and ordering follows first appearance in `live['sets']`.

### 6.2 New `tests/test_train_stage.py`

Use the same harness style: temp DB via `GYMTRACKER_DB`, a `check()` helper, run with `TZ=Australia/Melbourne python -m tests.test_train_stage`. Cover:
* `_stage_for` across the table in §3.1. Use real names as fixtures: BB Overhead Press (1, shoulders) → `t1`, BB Squat → `t1`, DB Row (2, back) → `t2`,
  Lat Pulldown (3, back) → `t2`, DB Shoulder Press (2, shoulders) → `t3:sh`, DB Skullcrusher (2, arms) → `t3:ar`,
  Ab Wheel (2, core) → `t3:co`, Fa Jin : Pec Deck (4, chest) → None, Rowing (3, cardio) → None,
  Cable Bicep Curl (2, uncategorised) → None.
* `get_live_session_state()` puts `stage` on every set and includes `finished`.

Run **all** suites when you finish:
`for t in test_scheme_accumulation test_train_stage test_train_session_day test_train_recency test_exercise_bank_roundtrip; do TZ=Australia/Melbourne python3 -m tests.$t || break; done`

### 6.3 Manual check against a copy of the real DB

```bash
cp tracker.db /tmp/horsecock-stagebar.db
GYMTRACKER_DB=/tmp/horsecock-stagebar.db python3 -c "
import gymtracker.database as d; d.DB_PATH='/tmp/horsecock-stagebar.db'
from gymtracker.app import app, init_db; init_db(); app.run(host='0.0.0.0', port=5055, debug=True)"
```
Confirm that `tracker.db`'s mtime didn't change afterwards. Then at 375px (Playwright, or a real iPhone on the LAN):
1. Run the wireframe's auto-play sequence by hand: 2 warm-up taps of Next → BB Squat ×3 → Next → BB OHP ×3 → Lateral Raise / Skullcrusher /
   Ab Wheel sets. Warm-up and T1 #1 complete with sweep + pop, T2 stays folded, T3 parts fill in their colours, and the caption
   counts are right.
2. Log a DB Row set: T2 unfolds and earlier stages complete. Tap a done chevron: the card switches to that lift.
3. Refresh mid-session: the bar redraws in the same state **with no animation**. Lock and unlock the phone (`resyncLive`): same result.
4. Step the date header back a day: that day's bar is shown and Finish is disabled. Step forward to today: today's state is back.
5. Finish via the stage bar (two taps) and via the summary sheet: the recap plays with real numbers, a rung crossing 5 floods, and
   Done closes it. Finish with only cardio logged: just the toast, no sheet.
6. Home: the panel now includes today. On /train log one more set, then go back to home: today has dropped out. Finish again: it's back.
7. Home → View → Route for DB Skullcrusher, BB Overhead Press and DB Shoulder Press: floors, lit rope, ▲ and the ghost floor look like the
   wireframe. Matrix/Bars/Rings are unchanged.
8. Turn on reduced motion: no animations, and the final states are correct.
9. Switch the profile to a ladder-engine profile: its home and nav are unchanged, and there's no stage bar anywhere.
10. Check the browser console has no errors on /train and home.

## 7. Acceptance criteria

- [ ] All five test suites pass. Existing checks are unmodified (apart from adding `gym_sessions` to the fixture schema).
- [ ] `_build_scheme_viz()` with no arguments returns the same seed as before your change for the copied DB. Prove it by diffing JSON
      before and after the refactor.
- [ ] The stage bar stays pinned while scrolling /train on iPhone Safari. If `position:sticky` fails because of an ancestor's overflow
      (`body` has `overflow-x:hidden`), say so and propose a fix rather than silently switching to `fixed`.
- [ ] One-shot animations fire only on real transitions, never on refresh, resync, or the 550ms post-save re-render.
- [ ] A finish never fails because of the recap. A thrown `_scheme_delta` still returns `ok: True` with an empty delta.
- [ ] No writes to `tracker.db`. Backups exist. Nothing committed.

## 8. When you're done, report back with

1. The files changed, with a one-line summary each, and the backup filenames.
2. Test output (counts per suite).
3. Screenshots at 375px: the stage bar in 3 states, the recap at its end state, and Route for DB Skullcrusher.
4. Anything you had to decide that this prompt didn't cover, and anything you noticed but didn't fix. Two to look at: `/api/gym/set/delete`
   calls `get_live_session_state()` without the back-dated `day`, and `finishSession()` sends no date.
5. Update the status line in `docs/PLAN-stage-bar-and-accumulation-recap.md` to "✅ 01, 02, 03-Route implemented", and add a short
   "Deviations" section if anything differs from this prompt.
