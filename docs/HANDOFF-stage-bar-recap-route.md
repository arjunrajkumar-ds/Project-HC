# Handoff: Stage bar, Finish recap and Route view (Arjun / scheme engine)

**Revision 2 · 2026-10-18.** You are implementing three UI features in a small personal Flask + SQLite gym tracker. The repo root is
`project-horsecock/`. Read this whole prompt before editing anything. If it disagrees with
`docs/PLAN-stage-bar-and-accumulation-recap.md`, **follow this prompt**.

The bar for this work is **a visual 1:1 recreation of the wireframe**. §3 says exactly what that means and how you'll prove it.

---

## 0. Read first

1. `docs/wireframes/stage-bar-and-accumulation.html` is the visual and motion spec. Open it from the repo; it loads the app's own
   fonts from `static/fonts/`, so it renders exactly as the app will. Use every button on it.
2. `docs/wireframes/ref/*.png` are **reference screenshots** of that file at 375px CSS width, @2x, with the app's fonts. You'll diff your
   build against them (§3.6).
3. `docs/PLAN-stage-bar-and-accumulation-recap.md` explains why each decision was made.
4. `docs/PLAN-scheme-accumulation-gym.md` defines what a "pass" is, the ladder, and why today is excluded.
5. `docs/PLAN-train-backdate-logging.md` covers the date header (`LOG_DATE`, `setDay()`, `withDate()`, `dateBody()`, `_valid_training_day`, `_day_sql`).
   **Every feature here must work when a previous day is selected** (§4.6, §5.5).
6. `docs/PLAN-train-rehaul.md` and `docs/REVIEW-train-hardening.md` describe the /train architecture and the bugs it was hardened against
   (§1.4 nav padding, §1.1 two-calendar bug). Don't reintroduce any of them.

## 1. Already done before you start (don't redo)

These were fixed and tested ahead of this handoff. They may still be uncommitted in the working tree, so **don't revert them**:

| Change | Where |
|---|---|
| `POST /api/gym/set/delete` takes an optional `date` (same contract as log-set: absent = today, past day = that day, future/garbage = 400). It used to check the set against **today's** session, so deleting on a back-dated day 404'd. | `gymtracker/app.py` `api_gym_set_delete()`; `templates/train.html` `removeSet()` now sends `dateBody({set_id})` |
| `POST /api/gym/session/finish` takes the same `date`. It used to always close **today's** session. | `gymtracker/app.py` `api_gym_session_finish()`; `finishSession()` now sends `dateBody({})` |
| A `gym_sessions` table is in the `_mkdb()` fixture, so the "today counts once finished" query won't crash the existing suite. | `tests/test_scheme_accumulation.py` |
| New regression suite (24 checks; it fails against the pre-fix code). | `tests/test_train_backdate_endpoints.py` |

Backups of those edits are `*.bak-dayfix-20261018-160730`. Don't touch them.

## 2. Scope and ground rules

"Arjun's experience" means the **`scheme` workout engine** (profile id 1). `/train` is in `_SCHEME_ROUTES` only, and the home
accumulation panel renders only through `templates/engines/_scheme_home.html`. Ladder-engine profiles must be unaffected, and you need
no profile checks beyond the existing routing.

| # | Feature | Where it shows |
|---|---|---|
| 01 | **Stage bar**: pinned chevrons Warm-up → T1 #1 → T1 #2 → T2 (optional) → T3 [SH \| AR \| CO], with a Next/Finish button | `/train` |
| 02 | **Finish recap**: the selected day's new passes animate into scheme pips, and rungs reaching T fill with colour | `/train` |
| 02b | **Today counts on home once finished** | home accumulation panel |
| 03 | **Route view**: a 4th view, Matrix / Bars / Rings / **Route** | home |

**Out of scope:** the Ledger view, warm-up data (`is_warmup`), schema changes, and recategorising exercises.

Rules:
* **Back up before your first edit**, with one shared timestamp: `gymtracker/app.py`, `gymtracker/gym_bank.py`, `templates/train.html`,
  `templates/_scheme_accumulation.html` → `<file>.bak-stagebar-<YYYYMMDD-HHMMSS>`.
* **Never write to `tracker.db`.** It's Arjun's real history. Manual testing goes against a copy (§7.3).
* Don't commit, stash, reset or revert. There are no new dependencies (Flask + vanilla JS in Jinja, no build step).
* Use Pandora tokens. The only hardcoded colour families allowed are `MG_COLORS` (train.html) and the palette constants in
  `_scheme_accumulation.html`. Everything must work at **375px** on iPhone Safari, with tap targets of 40px or more, and every animation
  must be off under `prefers-reduced-motion`.
* Match the house style: comments that explain *why*, and docstrings that cite PLAN/REVIEW sections. Find code **by function name**;
  line numbers here are approximate.

---

## 3. Visual fidelity contract (applies to 01, 02 and 03)

### 3.1 Port the CSS verbatim, don't rewrite it

Copy these blocks from `docs/wireframes/stage-bar-and-accumulation.html` **character for character**, then apply only the
mechanical edits in 3.2:

| Wireframe lines (approx.) | Block | Goes into |
|---|---|---|
| 97–169 `/* 1 · STAGE BAR */` | `.sb`, `.sb-cap`, `.sb-row`, `.sb-chevs`, `.cv` and its states, `.parts`/`.part`, `.sb-next`, `.sb.complete`, keyframes `sheen sweep pop arrive unfold wave` | `train.html` `<style>`, new section `/* ── Stage bar ── */` |
| 170–215 `/* 2 · FINISH RECAP */` | `.rc-*`, `.grab`, `.rungs`, `.rung`, `.rcell`, `.pip`, `.rl`, `.mtag`, keyframes `land flood` | `train.html` `<style>`, new section `/* ── Finish recap ── */` |
| 216–241 `/* 3 · ALT VIEWS */` | **only** `.av-sum`, `.legend`, `.climber` + `@keyframes bob` | `_scheme_accumulation.html` `<style>`, renamed per 3.2 |
| 242–244 | the `prefers-reduced-motion` rule | both files, scoped to the new selectors |

Do **not** port `.phone`, `.sbar`, `.pg`, `.tr-*`, `.mock-*`, `.mt`, `.mfoot`, `.av-card`, `.av-top`, `.av-seg`, `.ex-pick`, `.states`, `.state*`,
`.notes`, `.ctl*`, `.cbtn` or the page chrome. Those are wireframe scaffolding.

### 3.2 Mechanical edits allowed while porting (and nothing else)

| Wireframe | App |
|---|---|
| `--mg-sh` / `--mg-ar` / `--mg-co` | inline `--c` from `MG_COLORS.shoulders` / `.arms` / `.core` (same hexes: `#d4a574`, `#6bb0a8`, `#a785c2`) |
| any selector in the stage-bar block that isn't already rooted at `.sb`/`.cv` (e.g. `.parts`, `.part`, `.part .pf`, `.part .pl`, `.part .pn`, `.part.full …`) | prefix it with `.sb ` (e.g. `.sb .part .pn`). `train.html` already has `.pick .pn`, `.plate .pl`, `.fnrow .fd.ok`, so **never** emit an unscoped `.pn`/`.pl`/`.ok`/`.part` rule. Keep the declarations identical |
| bare `.pip`, `.rcell`, `.rung`, `.rungs`, `.rl`, `.mtag`, `.grab` | prefix each with `#recap-panel ` |
| `.rc-sheet` + `.rc-sheet.on` (transform slide) | **don't port.** The recap uses the existing `.sheet` / `.sheet-panel` (see 3.4) |
| `.av-sum`, `.legend`, `.climber` | `.sa-rt-sum`, `.sa-rt-legend`, `.sa-rt-climber`. `_scheme_accumulation.html` already has `.sa-legend` for Bars, so don't collide |
| hex literals in the Route JS (`#2a313a`, `#3f4651`, `#5e5e5e`, `#6b7480`, `#8b93a0`, `#edf0f3`, `#111316`, `#22262c`, `#121212`) | keep the values; declare them as named constants next to the file's existing `ACCENT`, `BORDER`, `LINE`, `PIP_EMPTY`… |

### 3.3 Measurements that must match (verify in devtools at 375px)

| Element | Value |
|---|---|
| Stage bar block | padding `7px 0 9px`, 1px `--border` bottom, `--bg` background. Caption row ≈13px + 6px gap. Chevron row **40px** tall. Total ≈66px |
| Caption | `Stage n / N` mono 8px, `.15em`, uppercase, `--dim` · exercise sans 11px `--text-strong`, ellipsis · counts mono 8.5px `--muted`, tabular |
| Chevron | point depth `--pt: 9px`, overlap `margin-left:-5px` (leaves a 4px diagonal gap), first chevron has a flat left edge. Label `.k` mono 7.5px `.12em`, name `.n` sans 9px |
| Chevron states | pending `--panel-soft`, labels `--dim`/`--dimmer` · active `rgba(212,165,116,.10)`, fill `rgba(212,165,116,.34)`, accent label, sheen · done fill `--accent`, text `#2a1d10` / `#3a2918`, ✓ after the key |
| Folded T2 | `flex:0 0 13px`, `--pt:7px`, 3px `--dimmer` / 3px gap horizontal stripes |
| T3 | `flex:1.75`, 3 parts with 2px gaps, label mono 8px 600 in the muscle colour, count 7.5px. Fill rises **from the bottom** |
| Next button | 56px wide, full row height, radius 6px, 1px `--accent` border, `--accent-soft` bg, `›` 15px over `NEXT` mono 8px `.14em`. Finish state: `--text-strong` bg, `#0d0e10` text, `✓ FINISH` |
| Recap cell | height 44px, radius 6px, 4px gap between cells, cells `flex:1` across the full width. Pips 6×5px radius 2, 2.5px gap, stacked bottom-up. Rung label mono 8px under it |
| Recap header | `+N` sans 40px/700 `-.04em` + `passes` 14px `--muted` · right: count sans 20px/700 accent over `rungs mastered` mono 10px |
| Route | hold radius 10, ring stroke 3, mastered halo r+4 at 14% opacity, floor spacing `min(108, 330/(floors-1))`, holds start x=84, odd holds raised 10px |

### 3.4 Allowed deviations (only these)

1. **Recap container.** It's an app `.sheet` + `.sheet-panel` (scrim, bottom-anchored, `max-height:86vh`, nav-clearing bottom padding from
   REVIEW §1.4) instead of the wireframe's absolutely positioned panel. Give the panel `id="recap-panel"`, a `border-top:1px solid
   var(--border-bright)`, and keep the `.grab` handle. **Done** is a normal `.log-btn` at the end of the panel content, not absolutely
   positioned. `.sheet` toggles `display:none` → `flex` and has no transition, so a `transition` won't fire. Make the slide-up a keyframe instead:
   `#recap-sheet.on #recap-panel { animation: rcUp .5s cubic-bezier(.2,.8,.2,1) both }` with
   `@keyframes rcUp { from { transform: translateY(102%) } to { transform: none } }`. Override the panel radius to `18px 18px 0 0` to
   match the wireframe.
2. **Real content.** Names, weights and counts come from the API, not the wireframe's sample data.
3. **Home view toggle.** It reads `Matrix · Bars · Rings · Route` (full words), not the wireframe's `Mtx … Log`. If it wraps at 375px,
   reduce `.sa-seg button` padding to `5px 9px`.
4. **Route exercise picker.** It's the panel's existing `<select>`, not the wireframe's chip buttons.
5. **Height.** On /train the bar sits above the date header and the back-date banner. The wireframe has neither.

Anything else that looks different is a bug.

### 3.5 Motion that must match

| Moment | Spec (from wireframe CSS/JS) |
|---|---|
| Fill grows | `width .55s cubic-bezier(.3,.7,.2,1)` (T3 parts: `height .5s` same curve) |
| Active chevron | `sheen` 2.6s linear infinite |
| Stage completes | fill to 100% → `sweep` .55s starting at .3s → `pop` .6s starting at .35s `cubic-bezier(.3,1.6,.5,1)` → next chevron `arrive` .55s starting at .6s |
| T2 unfolds | `flex` .5s `cubic-bezier(.2,.8,.2,1)` + `unfold` .7s |
| T3 part reaches 3 | `pop` .6s on that part |
| Finish | `.sb.complete` `wave` .7s, staggered `--i × 90ms` |
| Recap | sheet up (`rcUp` .5s, §3.4) → first block at 650ms, +380ms to its first pip, pips 150ms apart (`land` .45s), mastery +120ms (`flood` .8s, tag, counter bump), +650ms after a mastery, +260ms between blocks |

The reference filmstrip `ref/01-stagebar-anim-{0000…1300}ms.png` shows Next pressed on a full T1 #1. Your build at the same offsets
should look the same.

### 3.6 Visual QA you must do

Run the app on the DB copy (§7.3), and use Playwright (Chromium, viewport **375×812, deviceScaleFactor 2**) to set up the same state as
each reference, then screenshot the same element:

| Reference | Your element | State to reproduce |
|---|---|---|
| `01-stagebar-state-a…f.png` | `#stage-root .sb` | a fresh day · b T1 #1 2/3 · c T1 #2 active · d T2 unfolded 2/3 · e T2 skipped, T3 SH 3/3 AR 1 · f finished |
| `01-stagebar-anim-*.png`, `01-stagebar-t2-*.png` | `#stage-root .sb` | as named |
| `01-train-phone-end.png` | top of /train, header through the first card | after §7.3 step 1. Compare **only** the bar and where it sits under the header; the coverage strip and card in the reference are wireframe mocks |
| `02-recap-end.png`, `02-recap-block-1…3.png`, `02-recap-midflight-2600ms.png` | `#recap-panel` / a block | after finishing a copied-DB day with OHP 4×5@50, Skullcrusher 3×7@30, DB Shoulder Press 3×6@22.5 |
| `03-route-sk.png`, `03-route-ohp.png`, `03-route-dsp.png` | the panel's Route stage | DB Skullcrusher / BB Overhead Press / DB Shoulder Press on the copied DB **after** the recap finish above |

Write the screenshots to `docs/wireframes/impl/`, with each one stitched next to its reference (`<name>-compare.png`, ref left, yours right).
Iterate until the only differences are the allowed deviations in 3.4. List any you're unsure about in your report.

---

## 4. Feature 01: Stage bar (`/train`)

### 4.1 Server: tag every live set with its stage (`gymtracker/gym_bank.py`)

Next to `_exercise_meta_map()`:

```python
STAGE_T2_GROUPS = ('chest', 'back', 'legs')
STAGE_T3_PARTS = {'shoulders': 't3:sh', 'arms': 't3:ar', 'core': 't3:co'}

def _stage_for(meta):
    """Stage-bar segment for a set: 't1' | 't2' | 't3:sh' | 't3:ar' | 't3:co' | None."""
```

| Return | When |
|---|---|
| `'t1'` | `tier == 1`, any muscle group (BB OHP and DB Lateral Raise (heavy) are tier-1 shoulders, so they're T1) |
| `'t2'` | `tier in (2, 3)` and `muscle_group in STAGE_T2_GROUPS` |
| `'t3:sh'` / `'t3:ar'` / `'t3:co'` | `tier in (2, 3)` and `muscle_group` is shoulders / arms / core |
| `None` | tier 4 (Fa Jin), cardio, stability, `uncategorised`, unknown |

`'warm'` is never returned yet. Leave a comment saying it arrives with `is_warmup` (train-rehaul phase 2).

In `get_live_session_state(day=None)`:
* Add `'stage': _stage_for(m)` to each set dict, next to `'tier'`.
* Select `finished` in the `srow` query and return `'finished': bool(srow['finished'])`. Return `False` in the no-session branch.

Every endpoint returning `workout_so_far` (log-set, undo, set/delete, live-session, finish) carries both with no further change,
for today and for back-dated days alike.

### 4.2 Client: pure state derivation (`templates/train.html`)

Write `stageState(sets, manual, finished)`. It's pure: no DOM, no globals.

* Stage order: `['warm','t1a','t1b','t2','t3']`. `t1a` is the first distinct `exercise_id` with `stage==='t1'` in `sets` order. `t1b` is the
  second, and a third or later distinct T1 lift also counts into `t1b`.
* Per stage, record `sets`, `name` (most recent `exercise_name`), `lastExerciseId`. For t3, record `parts:{sh,ar,co}`.
* `t2.open = t2.sets > 0 || manual.t2Open`. A stage is visible unless it's t2 and closed.
* `loggedIdx` is the highest index with at least one set. `doneIdx` is the highest index in `manual.done`.
  `active = max(loggedIdx, next visible index after doneIdx, 0)`, clamped to 4.
* A stage is done if it's in `manual.done`, or visible with index < `active`. t3 is done only when `finished` is true.
* Fill targets are **fixed**: warm 2, t1a/t1b/t2 3, each t3 part 3. A full chevron **does not advance by itself**. It waits for
  **Next**, or for a set from a later stage. Next is always shown and always enabled until the day is finished.

**Manual state:** `sessionStorage['train_stage_' + curDayIso()] = {done:[keys], t2Open:bool}`. Wrap reads and writes in try/catch,
like `readRest()`/`writeRest()`. The key includes the day, so each back-dated day has its own state.

### 4.3 Client: DOM and rendering

* `<div id="stage-root"></div>` goes **between `.tr-head` and `#date-head`**. `.sb` is `position:sticky; top:0; z-index:50`.
* **Build the DOM once and update it in place**, so the transitions animate. `renderAll()` runs often (including 550ms after every save),
  so never rebuild the bar with innerHTML.
* `renderStage()` diffs against `STAGE_PREV` (done keys, active key, t2 open, t3 parts at 3, finished) and adds a one-shot class only on a
  real transition: `just-done`, `arrive`, `unfold`, `just`, `complete`. Remove each after its animation.
* **Silent paths:** first paint in `init()`, `resyncLive()`, and `setDay()` (switching day) set the state without any one-shot
  animation. Use a `STAGE_SILENT` flag around their `renderAll()`. Switching day also resets `STAGE_PREV` to the new day's state.
* Caption: `Stage n / N` (N is 4 or 5), the active stage's lift, and `x / 3 sets` or `k / 3 groups`. When finished: `Session done`.
* Wire it up by adding `renderStage()` to `renderAll()`.

### 4.4 Interactions

| Control | Behaviour |
|---|---|
| **Next** (active ≠ t3) | Push the active key into `manual.done`, save, render. The derivation skips a folded t2. |
| Tap folded **T2** | `manual.t2Open = true`, save, render. Plays `unfold`, and t2 becomes active if it's ahead. |
| Tap a **done** chevron / T3 part | `openExercise(stage.lastExerciseId)` if it has one. |
| **Finish** (active = t3, session exists, not finished) | Two-tap guard: first tap sets `fin-armed` (red `--red` bg, label "Tap again") and disarms after 3s. Second tap calls the existing `finishSession()`, which already sends `dateBody({})`, so **this works on a back-dated day too**. |
| Day already finished | The button shows `✓ DONE` at 45% opacity and is disabled. Logging another set reopens the session server-side (`get_or_create_today_gym_session`), `finished` goes false, and the button returns. |

### 4.5 Edge cases

* No session yet for the day: the bar shows the fresh state, Next still works (manual state only), and Finish is disabled ("No session yet").
* A cardio exercise is current: the coverage strip collapses, but the stage bar stays as it is.
* Deleting sets can move `active` backwards. That's fine (it's derived). `manual.done` keys stay.

### 4.6 Previous-day behaviour (must work)

Selecting a past day in the date header (`setDay`) shows **that day's** bar. It's derived from that day's `LIVE.sets` and that day's `train_stage_<day>`
state, with no animation on switch. Logging, Next, T2, tapping a done chevron, and Finish all act on that day. Stepping back to today restores today's bar.

## 5. Feature 02: Finish recap, and today counts once finished

### 5.1 Server: `gymtracker/gym_bank.py`

* `training_today()` returns the current training day `'YYYY-MM-DD'` via `SELECT {TODAY_SQL}`. Use `TODAY_SQL`/`_training_day` only.
  **Never** use `DATE('now')` or `DATE(recorded_at)` (REVIEW §1.1).
* `is_today_session_finished()` returns True if the session `get_live_session_state()` would pick for today has `finished = 1`.
* `get_weight_rep_day_groups(max_tier=3, before_today=True)`: keep the signature. `before_today=False` gets callers now, so update the docstring.

### 5.2 Server: `gymtracker/app.py`

* Refactor `_build_scheme_viz()` **without changing its output**:
  * `_scheme_pass_counts(rows)` → `{exid: {'name','tier','days','last_day','pass_counts':{(ci, weight): n}}}` (the existing loop, moved).
  * `_build_scheme_viz(threshold=None, include_today=False)` calls `get_weight_rep_day_groups(max_tier=3, before_today=not include_today)`.
* `_home_rows(rows, today, today_finished)` keeps rows with `day < today`, plus `day == today` when `today_finished`. This is the home
  panel's rule as one function.
* `_scheme_delta(live, day=None, threshold=None)` works for **any** day D (D = `day or training_today()`):
  * `all_rows = get_weight_rep_day_groups(before_today=False)`.
  * `base = [r for r in _home_rows(all_rows, today, is_today_session_finished()) if r['day'] != D]`.
  * `before = _scheme_pass_counts(base)`, `after = _scheme_pass_counts(base + [r for r in all_rows if r['day'] == D])`.
  * Return one item per **(exercise, weight)** whose count rose:

  ```json
  {"exercise_id": 12, "name": "DB Skullcrusher", "tier": 2, "T": 5, "weight_kg": 30.0, "weight_label": "30 kg",
   "day_reps": [7, 7, 7],
   "cols": [{"label": "3×6", "before": 5, "after": 6}, ...every rung...],
   "gained": 2, "mastered": ["3×7"]}
  ```
  `mastered` lists rungs where `before < T <= after`. `gained = Σ(after − before)`, including overflow past T. `day_reps` holds the reps of D's
  weight_reps sets at that weight, from `live['sets']`. Order items by first appearance in `live['sets']`, then weight descending.
* `api_gym_session_finish()` (already date-aware; keep its `day` handling): after `end_gym_session`, take `live = get_live_session_state(day)`,
  then compute `delta = _scheme_delta(live, day)` in its **own try/except**. A failure logs and returns `[]`, and never fails the finish.
  Return `{'ok': True, 'workout_so_far': live, 'accumulation_delta': delta, 'day': day or training_today()}`.
* `build_scheme_home_context()`: `'scheme_viz': _build_scheme_viz(include_today=is_today_session_finished())`. Update the
  `from .gym_bank import …` line and the comment block above `SCHEME_VIZ_THRESHOLD` that says "The current training day is excluded…".

### 5.3 Client: `templates/train.html`

* Add `<div class="sheet" id="recap-sheet" onclick="if(event.target===this)closeRecap()"><div class="sheet-panel" id="recap-panel"></div></div>`
  next to the other sheets.
* `finishSession()`: keep the existing success path. Then, if `r.data.accumulation_delta` is non-empty, call `openRecap(delta, r.data.day)`.
  Otherwise keep `toast('Session finished.')`. Also trigger the bar's `complete` wave. Both entry points (summary sheet and stage bar)
  already go through this function.
* `openRecap`/`playRecap`/`closeRecap`: port the wireframe's `buildRecap()` and `playRecap()`, keeping their DOM structure and class names so the ported CSS
  applies unchanged:
  * Header `SESSION BANKED · <day>` (`fmtDayLabel`-style, e.g. `SUN 18 OCT`), `+N passes`, `rungs mastered`.
  * Note line: **today** → "Today now counts on the home panel." · **past day** → "Already counted on the home panel."
  * One block per item: name, weight chip, `day_reps` as `4×5` if all equal or `5,5,4` otherwise, and `+gained`. Then every rung as a cell
    (`.quiet` if unchanged, `.none` if 0→0, `.mastered` + `+n` badge if `before ≥ T`). Pips below `before` are `on`; pips from `before` up to
    `min(after, T)` are `new` and land in sequence.
  * Keep timers in an array. Done, scrim tap, or reopen cancels them and snaps to the final state. Under reduced motion, render the final state at once.
  * T comes from `item.T`. Never hardcode 5.

### 5.4 Home: no template change

The panel picks up today automatically through `include_today`.

### 5.5 Previous-day behaviour (must work)

Finishing a back-dated day closes **that** session (already fixed) and plays the recap for **that** day's passes, measured against
every other counted day. If that day's session was already finished, logging to it reopens it, and finishing again replays only what's new
relative to the other days. Home is unaffected by finishing a past day because past days always count.

## 6. Feature 03: Route view (`templates/_scheme_accumulation.html`)

* Add `<button data-view="route">Route</button>` to `#sa-view`, and `state.view === 'route' ? renderRoute(d)` in `render()`. The focus chips stay hidden.
* `renderRoute(d)` returns an inline-SVG string (the file builds strings, so keep that style). Port `drawRoute()` from the wireframe
  **geometry-for-geometry**, mapped to the seed:
  * Floors are `d.weights` (ascending, heaviest drawn on top) plus a ghost floor at `top.kg + 2.5` labelled `NEXT`. Holds are `d.cols`.
    Count = `d.passes[ci + ',' + wi] || 0`. T = `d.T`.
  * Holds: mastered ones filled with a halo and ✓ or `+overflow`; in-progress ones get a ring arc `n/T` (`stroke-dasharray`, rotated -90°);
    untouched ones a dashed ring; ghost-floor ones dashed. Rope: same-floor segments are lit when the hold on their left is mastered. A dashed
    left-side pitch joins floor n's first hold to floor n+1's, labelled `+<kg step>`. ▲ (`sa-rt-climber`, `bob`) sits over `d.current`;
    skip it if `col` is null. Rung labels go under the bottom floor and over the ghost floor. A summit flag sits on the last ghost hold.
  * Above the SVG (`.sa-rt-sum`): mastered holds, touched/total, and `current rung @ weight`. Below it (`.sa-rt-legend`): mastered, in
    progress, untouched, ▲ you are here.
  * `viewBox="0 0 320 H"`, `width="100%"`, H from the floor count, as in the wireframe.
* Exercise names are interpolated into HTML here. Add an `esc()` helper and use it for every name (the file has none today).

## 7. Tests and verification

### 7.1 Extend `tests/test_scheme_accumulation.py`

The fixture already has `gym_sessions`. Keep every existing check unchanged. Add:
1. Today's sets with an open session today are excluded by `_build_scheme_viz(include_today=is_today_session_finished())`. With `finished = 1`
   they're included. After logging again (reopen), they're excluded again.
2. `_scheme_delta(live)` for today: only changed (exercise, weight) pairs are returned, `before`/`after` per rung are right, `mastered` is exactly the
   rungs that crossed T, `gained` counts overflow past T, and items follow `live['sets']` order.
3. `_scheme_delta(live, day=<yesterday>)`: before excludes yesterday, after includes it, and an unfinished today never leaks into either.

### 7.2 New `tests/test_train_stage.py`

Use the same harness style (temp DB via `GYMTRACKER_DB`, `check()`). Cover `_stage_for` for: BB Overhead Press (1, shoulders) → `t1`, BB Squat → `t1`,
DB Row (2, back) → `t2`, Lat Pulldown (3, back) → `t2`, DB Shoulder Press (2, shoulders) → `t3:sh`, DB Skullcrusher (2, arms) → `t3:ar`,
Ab Wheel (2, core) → `t3:co`, Fa Jin : Pec Deck (4, chest) → None, Rowing (3, cardio) → None, Cable Bicep Curl (2, uncategorised) → None.
Also check that `get_live_session_state()` and `get_live_session_state(<past day>)` carry `stage` on every set, plus `finished`.

Also extend `tests/test_train_backdate_endpoints.py`: finishing a past day returns `accumulation_delta` for that day, and `day` echoes it.

Run all six suites:
```bash
for t in test_train_backdate_endpoints test_scheme_accumulation test_train_stage test_train_session_day test_train_recency test_exercise_bank_roundtrip; do
  TZ=Australia/Melbourne python3 -m tests.$t || break; done
```

### 7.3 Manual check against a DB copy

```bash
cp tracker.db /tmp/horsecock-stagebar.db
GYMTRACKER_DB=/tmp/horsecock-stagebar.db python3 -c "
import gymtracker.database as d; d.DB_PATH='/tmp/horsecock-stagebar.db'
from gymtracker.app import app, init_db; init_db(); app.run(host='0.0.0.0', port=5055, debug=True)"
```
Confirm afterwards that `tracker.db`'s mtime didn't change. At 375px:
1. Today: Next (warm-up) → BB Squat ×3 → Next → BB OHP ×3 → DB Lateral Raise ×3, DB Skullcrusher ×3, Ab Wheel ×1. The animations match §3.5, T2 stays folded,
   and the caption counts are right.
2. Log a DB Row set: T2 unfolds. Tap a done chevron: the card switches to that lift.
3. Refresh, and lock/unlock: the same state, **no animation**.
4. **Previous day:** step the date header back to a day with no sets. You get a fresh bar with no animation on switch. Log OHP 4×5@50 there,
   delete one set (works now), log it again, Next through, then Finish (two taps). The recap shows that day's label and
   "Already counted…". Step back to today: today's bar is intact and today's session is still open.
5. Today: Finish via the stage bar, and on another run via the summary sheet. The recap plays. A cardio-only finish just shows the toast.
6. Home: today is included after finishing. Log one more set on /train and today drops out. Finish again and it's back.
7. Home → Route for DB Skullcrusher, BB Overhead Press and DB Shoulder Press. Matrix/Bars/Rings are unchanged.
8. Reduced motion: no animation and the final states are correct. Ladder profile: nothing changed. Console: no errors.
9. Do the visual QA in §3.6.

## 8. Acceptance criteria

- [ ] All six suites pass. Existing checks are unmodified.
- [ ] `_build_scheme_viz()` with no arguments returns byte-identical JSON before and after the refactor on the DB copy (show the diff command and its empty output).
- [ ] Every `docs/wireframes/impl/*-compare.png` matches its reference apart from the deviations in §3.4.
- [ ] The bar stays pinned while scrolling on iPhone Safari. If `position:sticky` fails (`body` has `overflow-x:hidden`), report it and propose a fix. Don't silently switch to `fixed`.
- [ ] One-shot animations fire only on real transitions: never on refresh, resync, day switch, or the 550ms post-save re-render.
- [ ] A previous day works end to end (§4.6, §5.5).
- [ ] A finish never fails because of the recap.
- [ ] No writes to `tracker.db`. Backups exist. Nothing committed.

## 9. Report back with

1. Files changed (one line each) and backup names.
2. Test output, with counts per suite.
3. The `docs/wireframes/impl/*-compare.png` set, plus a list of every remaining visual difference and why it's allowed.
4. Any decision this prompt didn't cover.
5. In `docs/PLAN-stage-bar-and-accumulation-recap.md`, set the status to "✅ 01, 02, 03-Route implemented" and add a "Deviations" section if needed.
