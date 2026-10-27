# PLAN — Food logging & day-fill (home page)

**Status: ✅ implemented (home) — 2026-10-18.** Shipped as `templates/_day_fill.html` + wiring in
`templates/home.html`. Template-only change (no Python / route / schema touched). See the
Implementation notes & deviations section at the end.
Companion to `docs/wireframes/food-logging-and-completion.html`. Parallel in spirit to the
stage-bar / accumulation work on `/train`: make the core action feel great, give the surface a
quiet sense of progress — without turning food into a game.

## Decision (locked with Arjun, 2026-10-18)
Primary completion metaphor: **Day-fill / plate ring + logging-first, light completion.**
- The hero action is *logging a food*; completion stays lightweight (a ring + two macro bars + meal dots).
- Explicitly NOT chosen: a meal-chevron progress strip, a quest-log hero, streaks/scores. Weekly Goals
  keep their existing `_goals_panel.html` quest log — this feature does not absorb it.

## What the wireframe shows (3 sections, same house pattern as the stage-bar file)
1. **Logging — the entry lands.** `+ add` on a meal → type / tap a recent chip → submit. The row drops in
   with an `entryland` spring; the meal total and plate-ring % roll up; the calorie + protein bars slide
   (`.55s`), with a lighter grow-overlay riding on top (mirrors `flAnimateBarGrowth`); a `+kcal` badge
   floats off the ring (mirrors the `bar-popup`). A macro-less food routes to the define sheet instead.
2. **Day-fill — the plate ring.** Calories = outer accent arc; protein = second bar; four coverage dots
   (one per meal, in the meal tint) light as meals get entries. Crossing the calorie goal fills the arc,
   plays one `ringflood` halo pulse, and shows a `goal` tag. Over-goal flips arc + bar to `--red` with a
   `+N kcal` readout — a colour, never a failure state.
3. **Static states + motion filmstrip.** a empty · b mid-day (2 meals) · c goal reached · d over goal.

## Reuses existing app language (no new primitives)
- `macroBarUpdate(cal, calGoal, prot, protGoal)` and the `+N` `bar-popup` from `_macro_bar.html`.
- `flAnimateBarGrowth()` grow-overlay slide and the meal-column shapes from `home.html` (`FL_ENTRIES`,
  `RECENT_FOODS`, `flAddEntry` → `POST /food/api/entry`, the define-food dialog, `openFoodCard`).
- Pandora tokens only. The one hardcoded family is the meal tints (`--m-bf/lu/di/sn`), parallel to
  `MG_COLORS` in `train.html`. 375px, tap targets ≥40px, all motion off under `prefers-reduced-motion`.

## Data it would bind to when implemented (nothing new server-side)
- `food_goals.calories`, `food_goals.protein_g` (already in the home context).
- `food_entries` per-meal (already `FL_ENTRIES`), `recent_foods` (already `RECENT_FOODS`).
- Meal coverage = "meal has ≥1 entry" — derived client-side, no schema change.
- Goal-reached = calorie total ≥ goal — derived; no persistence, no streak.

## Allowed deviations (wireframe → app), to settle at handoff time
1. The define step is drawn as a bottom sheet here; the app already has `#define-food-dialog` (a
   `<dialog>`). Implementation should reuse that dialog, not a new sheet — the sheet is just the
   wireframe's way of showing the branch.
2. The ring is new; the existing macro bars stay. Open question for Arjun: does the ring **replace** the
   two bars, sit **beside** them (as drawn), or is it **ring-only**? Drawn as ring + bars.
3. Protein is shown as a bar, not a second arc. A dual-arc ring is an option if preferred.

## Open questions for Arjun (before an implementation handoff)
- Ring + bars, or ring-only? (affects vertical space above the meal columns)
- Should the plate ring live at the very top of `Today`, above the week board / daily rings, or inside
  the existing "Today's food" section?
- Carbs/fat: show on the ring at all, or keep them to the food-card inspector only (as today)?

---

## Implementation notes & deviations (2026-10-18)

**Files:** new `templates/_day_fill.html` (self-contained panel + `window.dayFillUpdate`); 5 edits to
`templates/home.html` (meal-palette dedup, include the partial, `fl-entry-new` on the just-logged row,
`dayFillUpdate` call beside the macroBarUpdate hook, expose `flAnimateBarGrowth` on `window`). Backup:
`home.html.bak-dayfill-20261018-165026`. No Python, route, or schema change.

**Open-question defaults applied** (Arjun approved proceeding on recommendations):
1. **Ring + protein bar.** NOTE: `_macro_bar.html` is *not* included on the home page (line 389 includes
   `_week_board.html`), so there was no pre-existing macro bar here — the day-fill panel's own protein bar
   *is* the protein display, and its ring badge is the sole `+kcal` badge. Calories are the ring (handoff
   §2.1), protein is the bar; the wireframe's extra calorie *bar* was dropped as redundant.
2. **Inside the "Today's food" section**, directly above `#fl-grid` (not above the week board).
3. **No carbs/fat on the ring** (kept to the food-card inspector).

**Gaps G1–G11:** G1 meal palette promoted to one `:root` set consumed by both `.fl-col` and the dots
(breakfast resolves `#f0b45f`). G2 no/zero goal → "— / N kcal · no goal", no arc, no flourish (guards mirror
`_macro_bar.html`). G3 over-goal clamps the arc, flips `--red`, shows `+N kcal`, never caps the number. G4
panel lives outside `#fl-grid`, updated in place; flourish latched on crossing. G5 one badge (ring). G6
delete re-renders without animation and re-arms the latch, over→under flips red back. G7 ring reads only
calories/protein_g/meal_type. G8 reduced-motion disables all transitions/animations but the arc still snaps
to the correct offset. G9 partial uses no server vars → renders for both engines; left shared. G10 no
carbs/fat. G11 `entryland` is translateY/scale/opacity only.

**Verification done in the automation sandbox:** Jinja parse of both templates PASS; partial JS
bracket-balanced, every `getElementById` id present in markup, every toggled class styled; home.html food
IIFE shows the same (regex-literal) balance signature pre/post edit → no imbalance introduced.

**NOT run from the sandbox (environment limits — honest):** the on-device automation sandbox blocks
subprocess *and* file-based sqlite, so (a) the Playwright compare screenshots and (b) the server test suite
could not be executed here. The change is template-only, so the server suite's code paths are unaffected by
construction (it was green before). A browser-openable preview of the *implemented* panel (exact partial
source) is at `docs/wireframes/impl-food/day-fill-preview.html` for manual sign-off. To generate the
compare set, serve the app on a DB copy and run the §4 Playwright states against the home "Today's food"
section — the one visual item that still needs a human/browser is the ladder-engine home layout (G9).
