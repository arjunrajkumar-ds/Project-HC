# PLAN — Home screen **visual refactor** (match the wireframe aesthetic)

**Scope:** the home screen only (`/` → `dashboard.html` for Arjun/Pandora; `home_gayathri.html` for profiles 2 & 3). This is a **full visual refactor of the home screen**, not an additive feature: every existing element is transposed into the wireframe's aesthetic, nothing is duplicated or lost, and existing pieces are *rewritten* in the new style rather than bolted on. Propagation to other pages is a **later** pass (but we build the new aesthetic as reusable tokens/partials so that pass is cheap).

**Reference:** `Home Redesign.dc.html` — options **1a (daily rings)** and **1b (weekly board)**. Chosen direction: **1a's arrangement** (rings hero → week strip) as the spine, adopting **1b's full-width week calendar** treatment for the weekly view. Both mockups are *complete home screens* in one consistent visual language; we adopt that language wholesale.

---

## 0. Reframe — what changed from the first cut

The first iteration treated this as "add a rings partial + a week-board partial above the untouched old layout." That was too narrow: it left the rest of the home screen (header, nutrition logger, last-session, weight chart) in the *old* visual style, so the page reads as two different designs stacked. **This plan restyles the entire home screen** to one aesthetic.

The rings + metrics backend work from the first cut is **kept and reused** (workout_target column, `_home_week_metrics`, adherence/streak). What changes now is scope: we also (a) build a shared aesthetic-token layer, (b) rewrite every remaining home element in that style, and (c) reconcile the two new partials into the fuller layout rather than treating them as bolt-ons.

---

## 1. The wireframe aesthetic — design language to adopt

Extracted from 1a/1b. This is the vocabulary every home element must be rewritten into:

- **Card system:** content sits in rounded cards — `background: elevation-1`, `1px solid border-subtle`, `border-radius: large (~16px)`, soft shadow (`elev-1`). Section groupings, not flat lists.
- **Typography:** a **display font** for big numbers/titles ("Today", ring %, stat values), `--font-mono` for secondary stats/readouts (e.g. `1,580 / 2,400`), `--font-sans` for body. Uppercase, letter-spaced, tertiary-colour **eyebrow labels** above each block ("THIS WEEK", "TODAY'S FOOD", "LAST SESSION").
- **Colour semantics:** calories = accent/amber, protein = blue, gym = success/green, swim = blue, pilates = violet, streak = violet/flame. Category **colour dots/chips** (8px rounded squares) prefix items.
- **Elevation hierarchy:** page bg (base) < card (elevation-1) < track/inset (elevation-3). Today's cell / primary actions use the accent fill.
- **Rounded everything:** pills for weight + toggles (`border-radius:999px`), `r-md` for tiles, `r-lg` for hero/last-session cards.
- **Icons:** the mockup uses Material Symbols (`monitor_weight`, `pool`, `fitness_center`, `local_fire_department`, `chevron_right`, `add`, bottom-nav glyphs). The app currently ships **no icon font** — see §3 decision.

### 1a full anatomy (top→bottom) — the target home layout

1. Header: eyebrow (`Wed · 16 Sep · Arjun`) + **Today** (display) | weight pill (`⚖ 82.4 kg`)
2. **Hero rings** card: calories · protein · workouts (ring + % + `x / y` readout)
3. **This week** strip: label + streak chip; 7 compact day cells (colour dot per day, today = accent fill, future = dashed)
4. **Weekly adherence**: 2 mini progress cards (Cal 92%, Protein 85%)
5. **Today's food**: eyebrow + meal rows (colour dot · meal label · name · kcal/protein) + dashed "Add …" row
6. **Last session**: icon tile + title + mono meta + chevron
7. Bottom nav (Today/Train/Food/Stats) — *mockup chrome; see §3, likely already provided by base.html*

1b contributes the **full-width week calendar** (tall day tiles with activity icon, today outlined, Gym/Swim/Pilates legend) and the **2×2 stat-tile grid** (Workouts / Streak / Cal adherence / Protein wk) as an alternative denser treatment we borrow for the weekly section.

---

## 2. Element-by-element mapping (current home → refactored) — nothing lost

| # | Current element (`dashboard.html`) | Fate | New treatment |
| --- | --- | --- | --- |
| A | Header: eyebrow + `‹ Today ›` date-nav + mini-weight form + info btn | **Rewrite** | Same controls, restyled: display "Today", pill-styled weight input, date-nav as round pill buttons, info as ghost pill. Keep the dev "switch user" link. All existing form POSTs/JS (`editWeight()`) preserved. |
| B | `_week_strip.html` (flat chips) | **Replaced** | Superseded by the new week section (`_daily_rings` + `_week_board`). Partial retired from home (leave file until propagation pass). |
| C | `_macro_bar.html` (2 stacked bars) | **Replaced** | Superseded by hero rings. Live-update hook preserved via same `macroBarUpdate` name. |
| D | Rings (`_daily_rings.html`, new) | **Keep + restyle** | Already built; restyle to match card/typography system (display font for %, mono readouts, elevation card). |
| E | Week board (`_week_board.html`, new) | **Keep + restyle** | Already built; align to 1b's full-width calendar + adherence/streak tiles in the new aesthetic. Reconcile so week info isn't split awkwardly between rings-card and board. |
| F | Today's nutrition — `fl-grid` 4-col logger + all JS (add/delete/define/recent chips/bar-growth/food-card dialog) | **Rewrite (visual only)** | This is live interactive machinery — **preserve every handler and DOM id**. Restyle the *containers* (meal columns → cards, entries → colour-dot rows matching 1a's food rows, add buttons → dashed rows/tiles, dialogs → rounded cards). No JS logic changes; only markup/CSS the JS targets, and only where ids are kept stable. |
| G | `define-food-dialog`, `food-card-dialog` | **Rewrite (visual)** | Restyle to rounded card dialogs in the aesthetic; keep ids/handlers. |
| H | `_last_session.html` | **Rewrite** | Icon tile + title + mono meta + chevron, per 1a. Links preserved. |
| I | `_weight_chart.html` | **Rewrite** | Wrap in the card system + eyebrow label; keep the chart JS/data. (Not in the mockup explicitly — we keep it, restyled, so nothing is lost.) |
| J | `editWeight()` + weight POST | **Keep** | Untouched logic; pill styling only. |
| K | `home_gayathri.html` equivalents (its own food section "food · today", missions, etc.) | **Rewrite to match** | Apply the same aesthetic + partials. Gayathri-specific sections (bodyweight tallies/missions) restyled into the same card language, not dropped. Simple active/rest week board (per earlier decision). |
| L | Bottom nav / base chrome | **Verify** | Lives in `base.html` (already has `.nav-item`). Mockup's bottom nav likely maps to existing chrome — restyle in base only if in scope; otherwise leave. |

**Rule:** any DOM id or `window.*` function referenced by JS is immutable. Restyling touches classes, wrappers, and CSS — never the contract the JS depends on.

---

## 3. Aesthetic token layer (the foundation — do first)

The mockup's tokens don't exist in the app. Rather than hardcode values per element, add a **home-aesthetic token layer** so the style is consistent and the later propagation pass inherits it. Add to `base.html`'s `body.pandora` (and light theme) scope:

| New need | Token to add | Value (pandora) | Maps mockup token |
| --- | --- | --- | --- |
| Display font | `--font-display` | reuse `--font-sans` (Inter 600) or add a display face | `--font-display` |
| Large radius | `--radius-lg` | `14px` | `--r-lg` |
| Elevation shadows | `--elev-1`, `--elev-2`, `--elev-3` | subtle dark-theme shadows | `--elev-*` |
| Elevation surfaces | reuse `--panel` / `--panel-alt` / `--panel-soft` | existing | `--bg-elevation-1/2/3` |
| Category colours | reuse `--accent`(cal), `--blue`(protein/swim), `--green`(gym), `--purple`(pilates), `--waiting`(streak/flame) | existing | `--a-*/--b-*/--v-*/status-success` |
| fg tiers | reuse `--text-strong` / `--text` / `--dim` / `--dimmer` | existing | `--fg-primary/secondary/tertiary` |

Confirm before build:

- **Icons (open Q1):** the app ships no icon font. Options: (i) add Material Symbols via the existing `base.html` head (one `<link>`), matching the mockup 1:1; (ii) use a small inline-SVG set; (iii) keep the app's current text-label convention. Recommn: **(i)** — cheapest way to match the wireframe exactly and reusable across the app. Needs your ok to add a web font (or self-host the subset).
- **Display font (open Q2):** reuse Inter (already loaded) at heavy weight, or add a distinct display face? Recommn: reuse Inter 600/700 — zero new assets, still matches the mockup's feel.

---

## 4. Build order

1. **Aesthetic tokens** (§3) in `base.html` — `--font-display`, `--radius-lg`, `--elev-1/2/3`; resolve icon + display-font decisions. *Nothing visual changes yet.*
2. **Restyle the two new partials** (`_daily_rings`, `_week_board`) to the finalized token layer + reconcile the weekly section so it reads as one block (rings hero → week calendar → adherence/streak), matching 1a top + 1b calendar.
3. **Header** (element A) — rewrite markup/CSS; verify date-nav + weight form + `editWeight()` still work.
4. **Nutrition logger** (F) + **dialogs** (G) — restyle containers/entries/buttons; **run the JS-contract check**: every id/`window.*` the script uses still exists. This is the highest-risk step.
5. **Last session** (H) + **weight chart** (I) — rewrite into the card system.
6. **Gayathri home** (K) — apply tokens + partials + restyle its bespoke sections.
7. **Full render QA** both templates (Jinja→static) + visual QA both profiles at mobile width; diff against 1a/1b.

---

## 5. Backend (already done in first cut — reused, minor additions)

- `macro_goals.workout_target` column + migration ✅ (done, DB backed up).
- `_home_week_metrics()` → `workouts`, `adherence`, `streak` in both `/` branches ✅ (done).
- **Possible addition:** the 1a food rows show per-meal grouping (Breakfast/Lunch/Snack) with kcal+protein — the current logger already has meal grouping + entries, so no new query; the display card just reads existing `food_entries`. Confirm `food_entries` carries `meal_type` + protein (it does, per `enrich_log_entries`).
- No further schema changes anticipated.

---

## 6. Risks / guardrails

- **Interactivity is the #1 risk.** The nutrition logger is ~350 lines of JS bound to specific ids (`fl-grid`, `fl-input-<meal>`, `fl-add-<meal>`, `mb-*` for the old bar, food-card dialog). Restyle must keep those ids or update **both** sides atomically. Before/after, grep the JS for every `getElementById`/`querySelector` id and assert each still exists in the new markup.
- **Live macro update:** keep `window.macroBarUpdate` name (rings already define it) so food add/delete refreshes the rings — already handled.
- **No divide-by-zero / no-goal / over-goal** ring + adherence guards — already in the partials; re-verify after restyle.
- **Two themes:** tokens must be added to both `body.pandora` (dark) and the light/default scope so non-pandora profiles (Gayathri uses `body_class` without pandora when id==2) don't break.
- **Adding a web font** (if icons/display via CDN) affects load + offline. Prefer self-hosting a subset or reusing loaded fonts.
- **Scope discipline:** bottom nav / global chrome live in `base.html` and are shared with every page — do **not** restyle base chrome as part of this home-only pass unless it's purely additive tokens. Flag anything that would leak into other pages.

---

## 7. Files touched

| File | Change |
| --- | --- |
| `templates/base.html` | **new tokens** (`--font-display`, `--radius-lg`, `--elev-1/2/3`); optional icon-font `<link>` |
| `templates/_daily_rings.html` | restyle to token layer; reconcile with week section |
| `templates/_week_board.html` | restyle to 1b calendar + stat tiles in aesthetic |
| `templates/dashboard.html` | rewrite header, nutrition logger markup, dialogs, wrappers into the aesthetic (JS logic untouched) |
| `templates/_last_session.html` | rewrite into icon-tile card |
| `templates/_weight_chart.html` | wrap/restyle into card system |
| `templates/home_gayathri.html` | apply tokens + partials; restyle bespoke sections |
| `gymtracker/app.py`, `gymtracker/database.py` | already done (metrics + workout_target); no new changes expected |

---

## 8. Open questions (blockers for the aesthetic layer — need your call)

1. **Icons** — add **Material Symbols** (matches mockup exactly, reusable app-wide) / use inline SVGs / keep text labels? (Recommn: Material Symbols.)
2. **Display font** — reuse **Inter** heavy (no new asset) / add a distinct display typeface? (Recommn: reuse Inter.)
3. **Nutrition logger fidelity** — the mockup shows food as a simple *read* list (1a) or compact tiles (1b), but the real app has a full inline *add/define/delete* logger. Keep the **full interactive logger** (just restyled), or genuinely simplify the home food section to a read-only summary with a link to the full `/food` page? (Recommn: keep full logger, restyled — nothing lost.)
4. **Bottom nav** — is the mockup's bottom nav already satisfied by `base.html` chrome (leave it), or do you want it restyled too (touches all pages → separate pass)?

