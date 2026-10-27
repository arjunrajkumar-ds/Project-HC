# Handoff: Food logging & day-fill (home page) — implementation

**2026-10-18.** Implement the food logging + day-fill feature into the scheme-engine home page, matching the approved
wireframe `docs/wireframes/food-logging-and-completion.html` and `docs/PLAN-food-logging-completion.md`. Design decision is
locked: **Day-fill / plate ring + logging-first, light completion.** Read this whole doc before editing.

## 0. Scope & ground rules
- Target: `templates/home.html` (shared home shell; the food section renders for every engine, but this feature is for
  Arjun's scheme experience — it must not break the ladder profiles). No new routes, no schema change, no new server fields.
- The hero is *logging*; completion is light (one calorie ring + protein bar + 4 meal-coverage dots). NO streaks/scores.
- Pandora tokens only. CRITICAL colour rule (see Gap G1): reuse the EXISTING `--meal-col` values already in home.html
  (`#f0b45f` breakfast / `#7fb069` lunch / `#6b93b8` dinner / `#a887b8` snack). Do NOT import the wireframe's meal tints —
  the wireframe used different hexes as a stand-in. The ring/dots must read from `--meal-col`.
- 375px, tap targets ≥40px, all motion off under `prefers-reduced-motion`.
- Back up `templates/home.html` → `home.html.bak-dayfill-<YYYYMMDD-HHMMSS>` before the first edit. Never write tracker.db.
  Do not commit/stash/revert. House style: comments explain WHY.

## 1. What exists today (build on it, do not duplicate)
- Home food context (app.py `_shared_home_context`, ~L825-891): `food_entries` (enriched), `recent_foods`,
  `food_totals {calories,protein_g,carbs_g,fat_g}`, `macro_goals`/`food_goals` (MAY be None or {} — see Gap G2).
- `#fl-grid` is a **4-column CSS grid** (`repeat(4,1fr)`), one `.fl-col` per meal, data-meal set, `--meal-col` per column.
- `flRender(opts)` rebuilds `#fl-grid` innerHTML; `flAddEntry(mealType)` → `POST /food/api/entry` (always `date: TODAY`),
  pushes `data.entry` into `FL_ENTRIES`, calls `flRender({animateEntry:…})`; undefined-macro foods route to
  `flPromptDefine` → `#define-food-dialog` → `/food/api/define`.
- Already-present animation primitives to REUSE: `macroBarUpdate()` (in `_macro_bar.html`), `flAnimateBarGrowth()` and
  `.bar-grow-overlay`, `flShowBarPopup()` and `.bar-popup` (home.html ~L161-181). The ring is the ONLY new primitive.
- `/food/api/entry` returns ONLY `{id,name,meal_type,calories,protein_g,carbs_g,fat_g,logged_at}` — NOT the enriched
  fields (`type_name`, `_multiplier`, …). `flRender` already tolerates their absence; keep that true for the ring.

## 2. Build
### 2.1 Day-fill panel (new partial `templates/_day_fill.html`, included at the top of the "Today's food" section)
- A calorie **ring** (SVG, `stroke-dashoffset` arc, accent; `--red` when over) with the % in the centre, a **protein bar**
  (reuse the macro-bar green language), and a row of **4 meal-coverage dots** (one per meal, lit in that meal's
  `--meal-col` when the meal has ≥1 entry). Ring geometry from the wireframe: r=42, stroke 8, cap round, `.6s` ease.
- Goal-reached (calorie total ≥ goal): one `ringflood` halo pulse + a `goal` tag; crossing only, not on every render.
- The panel reads the same client state `flRender` already computes (sum of `FL_ENTRIES`), so wire it as a function
  `dayFillUpdate(totalCal, calGoal, totalPro, proGoal, coveredMeals, opts)` called from inside `flRender`, right next to
  the existing `macroBarUpdate(...)` call. Keep `macroBarUpdate` working (do not remove the macro bar unless Arjun picks
  ring-only — default is ring + existing bars; see Open Qs).
### 2.2 Logging — entry lands
- When `flRender({animateEntry:e})` fires, the new row in its meal column gets a one-shot `entryland` spring, the ring %
  and protein bar slide to the new value (reuse `flAnimateBarGrowth` for the bar), and the `+kcal` `bar-popup` floats
  (already happens for the macro bar — ALSO float one off the ring, or reuse the existing badge; keep it to ONE badge per
  log, see Gap G5). Newly-covered meal dot pops (`dotpop`).
- The undefined-macro branch (`flPromptDefine`) stays exactly as is — it opens `#define-food-dialog`; on define success the
  ring/bars animate from the `/food/api/define` response (that path already refreshes the entry).

## 3. GAPS & PREDICTED BUGS (resolve each explicitly in the build)
- **G1 — Meal-tint collision (HIGH).** The wireframe's meal hexes ≠ home.html's `--meal-col`. If the ring/dots hardcode the
  wireframe tints, the home page shows two palettes. FIX: dots/ring must read `--meal-col` from the existing `.fl-col`
  rule; do not add a second meal palette. Verify breakfast reads `#f0b45f`, not `#d4a574`.
- **G2 — No-goal / zero-goal (HIGH).** `macro_goals` may be None or `{}` (new profile, goals unset). A ring that divides by
  goal will NaN/Infinity the dashoffset and throw. FIX: when `calGoal<=0`, render the ring empty with a `— no goal` center
  label, no arc, no flourish; same guard protein. Mirror the `goal>0` guards already in `_macro_bar.html`.
- **G3 — Over-goal is a colour, never a failure.** >100% clamps the arc at full, flips to `--red`, readout shows
  `+N kcal`. Do NOT cap the number at 100%. (Wireframe already models this.)
- **G4 — `flRender` rebuilds innerHTML every call.** The ring must NOT live inside `#fl-grid` (it would be wiped and lose
  its transition). Put the day-fill panel in its OWN element above `#fl-grid` and update it in place (set dashoffset /
  widths on existing nodes), exactly like `macroBarUpdate` does — never innerHTML the ring on each render, or the arc
  animation never plays and the goal flourish re-fires on every log. Gate the one-shot flourish on a crossing flag.
- **G5 — Double badge.** `flRender`'s `opts.animateEntry` already floats a `+kcal` over the macro-bar readout. If the ring
  also floats one unconditionally, every log shows two badges. FIX: float the ring badge, and if the macro bar is kept,
  suppress its badge OR keep only one. Decide once; comment why.
- **G6 — Delete / undo moves totals backward.** Deleting an entry (`flDelete`) must re-run `dayFillUpdate` with
  `opts.animate:false` (no land/flourish on a decrease) and must clear the goal-reached flag if the total drops below
  goal, so re-crossing re-animates. Over→under must flip `--red` back to accent.
- **G7 — Enriched fields absent on fresh entries.** A just-logged entry lacks `type_name`/`_multiplier`; the ring only
  needs `calories`/`protein_g`/`meal_type`, all present in the API response. Don't read enriched fields in the ring.
- **G8 — Reduced-motion.** The ring arc, grow-overlay, `entryland`, `dotpop`, `ringflood`, and badge must all be inert
  under `prefers-reduced-motion`; the ring must still show the correct FINAL dashoffset (snap, not animate).
- **G9 — Ladder engine.** The food section is shared. If the day-fill panel is added unconditionally it also renders for
  ladder profiles. CONFIRM that's acceptable (food is shared across engines today) OR gate the include on
  `current_engine.key == 'scheme'`. Default: leave it shared (food is already shared) unless it visually breaks the ladder
  home — screenshot both.
- **G10 — Carbs/fat.** Not shown on the ring (kept to the food-card inspector as today). Don't add them unless Arjun asks.
- **G11 — 4-col grid at 375px.** The meal columns are already narrow; the `entryland` animation must not cause horizontal
  overflow (no translateX that widens the grid). Keep the spring to translateY/scale/opacity only.

## 4. Visual QA (Playwright, Chromium, 375×812 @2x — same harness as the stage-bar work)
Serve the app on a DB COPY. Screenshot the home "Today's food" section in these states, write to
`docs/wireframes/impl-food/`, stitch each next to the wireframe section it mirrors (`<name>-compare.png`):
empty day (no goal) · empty day (goal set) · mid-day 2 meals ~54% · log-lands (fire a log, capture mid-animation) ·
goal reached (ringflood) · over goal (red). Iterate until only intended differences remain.

## 5. Tests
No new server endpoints, so no server tests are strictly required, BUT add a tiny guard test if any helper is added.
Run the existing suite to prove nothing regressed:
`for t in test_scheme_accumulation test_train_session_day test_train_recency test_exercise_bank_roundtrip; do TZ=Australia/Melbourne python3 -m tests.$t || break; done`
(plus the stage-bar suites if that work has landed by then). Confirm the home route still renders for BOTH a scheme and a
ladder profile (G9) via a quick request in the harness.

## 6. Report back
Files changed (one line each) + backup name; the new partial; QA compare images with any residual diffs explained;
how each of G1-G11 was handled; set `docs/PLAN-food-logging-completion.md` status to "✅ implemented (home)" with a
Deviations section; and surface the 3 Open Questions below with your recommendation.

## Open questions (carry Arjun's answer if given; else recommend + proceed, note it)
1. Ring **+** existing macro bars (default), ring beside bars, or ring-only?
2. Panel at the very top of Today (above week board), or inside the existing "Today's food" section (default: inside)?
3. Carbs/fat on the ring at all (default: no)?
