# Plan — rebuild /train around the session, not the exercise bank

**Slug:** `trainrehaul` · **Target:** Arjun's `/train` (scheme engine, profile-blind) · **Phases:** 2

---

## Context

`/train` today is the exercise taxonomy rendered as navigation: pick muscles for the day → expand a muscle section → find the exercise → open a sheet → log. Three taps per set, every set. Alongside it sit two parallel reminder systems for the same concept — day-driver sections for chest/back/legs, always-on "constant" strips for arms/core/shoulders — and a `tier == 4` Fa Jin modal that interrupts at the moment of highest intent.

The wireframe built earlier this session inverts it: **one coverage instrument for all six groups, and the logging surface as the resting state of the page.** It also adds the things the page has never had — last session's sets per set, days-since recency, estimated 1RM and PRs, a rest countdown — and dissolves the Fa Jin modal into an inline offer.

Where the wireframe and the current implementation disagree, the wireframe wins (user's instruction). Two happy coincidences: `docs/REVIEW-train-hardening.md` §2.2 independently proposes exactly the inline Fa Jin strip, and §2.3 wants the Finish-session summary that `end_gym_session()` has been waiting for since it was written.

**Phase 1 touches no schema.** Everything in it is a pure query over `gym_progression` plus a template rewrite. Phase 2 adds the one migration (warm-up, RPE).

---

## 0. Before any edit — snapshot

`templates/train.html` has **316 uncommitted insertions**: the entire hardening pass (`_training_day` SQL, `apiFetch`, `esc`/`jsStr`, the double-tap guard, `resyncLive`, `fjOffered`). It exists in no commit. House convention is a sibling backup with a shared slug + timestamp:

```
templates/train.html.bak-trainrehaul-<YYYYMMDD>-<HHMMSS>
gymtracker/app.py.bak-trainrehaul-<same timestamp>
gymtracker/gym_bank.py.bak-trainrehaul-<same timestamp>
```

Also write `docs/PLAN-train-rehaul.md` in the house PLAN format (H1 → `**Status:**` line → `## 0.` why-not-trivial → numbered sections → Migration / Verify / Rollback / Files touched / Open questions). I will not run `git commit` — that's yours to do.

---

## 1. Component map — what replaces what

| Current | Fate | Replacement |
|---|---|---|
| `#empty-state` + `.picker-grid` + `renderEmptyPicker()` | **Delete** | Coverage strip is never empty — all six groups always render |
| `#add-muscle-wrap`, `addMuscle`, `pickMuscle`, `removeMuscle`, `#mp-sheet`, `#mp-grid` | **Delete** | No day-driver concept; `train_picked_muscles` key retired |
| `#live-root` muscle sections (`.mg-sec`) | **Delete** | `#cov` coverage strip with per-row drilldown |
| `#constant-root` strips, `toggleConst`, `train_const_open` | **Delete** | Rows 1–3 of the same strip, marked by a 2px accent rule |
| `.checklist` / `.chk` done-pills | **Replace** | Per-function volume meters (fill = sets today, 3 = full) |
| `#ex-sheet` exercise sheet | **Promote** | Becomes the always-visible `#card` current-exercise card |
| `#fajin-sheet` modal + `maybeOfferFajin` | **Replace** | Inline `.fajin` row inside the card (REVIEW §2.2) |
| `#rest-bar` count-up stopwatch | **Replace** | Count-down against a tier target, persisted |
| `#fajin-cta` header pill | **Keep** | Still opens the full Fa Jin list, ignoring suppression |
| `.sheet` / `.sheet-panel` | **Keep** | Reused by the new picker and summary sheets, padding rule intact |
| `apiFetch` / `apiPost` / `esc` / `jsStr` / `resyncLive` | **Keep verbatim** | Load-bearing hardening — lift across unchanged |

Cardio stays `muscle_group='cardio'` with its `machine` / `muay_thai` functions exactly as `migrate_muay_thai_under_cardio.py` left it. It is simply **excluded from the coverage strip** and reached by its own button — presentational only, no data change.

---

## 2. Phase 1 — server (no schema change)

### 2.1 New functions in `gymtracker/gym_bank.py`

All three must use the existing `_training_day(col)` / `TODAY_SQL` helpers (`gym_bank.py:51-59`) — never raw `DATE(recorded_at)` — and must tolerate the 33 legacy rows with `session_id IS NULL`, `sets > 1`, and bare `YYYY-MM-DD` timestamps.

```python
def get_exercise_recency():
    """{exercise_id: {'last_day': 'YYYY-MM-DD'|None, 'days_since': int|None}} for every exercise.
    One GROUP BY over gym_progression; delta computed in Python off TODAY_SQL."""

def get_e1rm_bests(max_reps=12):
    """{exercise_id: float} — best Epley e1RM, w*(1+r/30), weight>0 and reps<=max_reps.
    Capped because Epley overstates badly past ~12 reps (EZ-Bar 777s at 21 would dominate)."""

def get_previous_session_sets(exercise_id, exclude_session_id=None):
    """Ordered sets of the most recent PRIOR session for one exercise.
    Picks the newest session_id strictly before exclude_session_id; falls back to
    grouping by _training_day('recorded_at') for legacy rows with session_id IS NULL.
    Returns [{weight_kg, reps, duration_s, distance_m, setting}] in recorded_at ASC, id ASC."""
```

Reuse rather than reinvent: `get_exercise_prefill()` (`gym_bank.py:749`) stays as-is for the best/most-recent chips. Do **not** touch `database.py`'s existing `days_since`/Epley/PR code — it is bound to `exercises`/`session_lifts`, a schema `/train` never reads.

### 2.2 `get_live_session_state()` — emit two dropped fields

`gym_bank.py:882-907` SELECTs `successful` and then drops it, and emits no set ordinal. Add both to the per-set dict (`successful`, and `n` = 1-based index within that exercise for the session). The template currently derives the ordinal from array index; the new set table needs it to survive a delete + renumber.

### 2.3 `/train` route — widen the `bank` projection

`gymtracker/app.py:1546-1556` throws away data `get_gym_bank_grouped()` already fetched. Add to each exercise dict: `days_since` (from 2.1), `best_e1rm` (from 2.1), `rest_s` (from `engagement.rest_s`, else by tier: T1 180, T2 120, T3/T4 90), `bar_kg` (from `engagement.bar_kg`, else null). No new query for the last two — `engagement` is already parsed by `_cardio_spec()`.

Pass a new `recency` dict for per-function and per-muscle days-since (fold the per-exercise map over the bank tree in Python — no extra SQL).

### 2.4 `/api/gym/exercise/<id>/prefill` — add previous-session sets

`app.py:1840`. Add `'previous_session': get_previous_session_sets(id, exclude_session_id=<today's>)` to the response. Fetching per-exercise on open, rather than for all 50 up front, keeps the page load unchanged.

---

## 3. Phase 1 — template (`templates/train.html`)

Single file, `{% extends 'base.html' %}`, `{% block body_class %}pandora{% endblock %}`, CSS in `{% block extra_css %}`. **Pandora tokens only** — `--panel`, `--panel-soft`, `--border`, `--text-strong`, `--dim`, `--accent` (`#d4a574`), `--green`/`--red`/`--blue`/`--purple`, `--radius-*`, `--elev-*`. The wireframe's bone-white primary action maps to `--text-strong` on `--accent`-free fill; its warm near-black ground is already what `.pandora` is. Existing `MG_COLORS` muscle accents stay.

Structure, top to bottom:

1. **`.lw-header`** — title, `#lw-status` (now `elapsed · N sets · volume kg`), `#fajin-cta` pill, new `#summary-cta` (`∑`).
2. **`#cov`** — six rows (shoulders, arms, core, chest, back, legs), each: accent rule for the three every-session groups, mono name, per-function meters, right column showing `N today` or `Nd` (stale tone ≥ 7d). Tap expands functions → exercises with days-since; tapping an exercise makes it current.
3. **`#card`** — the promoted exercise sheet: crumb, name, tag row (equip, last-logged, e1RM/PR, progression nudge), set table (`#` / `PREV` / `DONE` / ✕) with a ghost next-set row, steppers by `tracking_type`, inline `.fajin` row, primary log button.
4. **`#rest-bar`** — countdown, ±15s, skip, sticky above the action bar.
5. **`.abar`** — `⌕ Change exercise` and `Cardio`.
6. **Sheets** — `#pick-sheet` (search / continue / recent / browse-by-group) and `#sum-sheet` (session summary → `end_gym_session()`), both reusing `.sheet`/`.sheet-panel`.

**Hard constraints, carried from `docs/REVIEW-train-hardening.md` and `docs/PROMPT-exercise-databank.md`:**

- `.sheet-panel` keeps `padding-bottom: calc(18px + var(--nav-h) + var(--sab))` — §1.4, the tap that navigated away mid-set.
- Every JS function stays a **top-level declaration** reachable from inline `onclick`. No module, no IIFE — there are 22 inline handlers.
- `logInFlight` + `btn.disabled` double-tap guard on every log path, Fa Jin included (§1.3).
- All interpolated free text through `esc()` / `jsStr()` (§1.10) — exercise names, function labels, cardio `setting`.
- `apiFetch`'s resolve-don't-throw + 15s abort, and the retryable-vs-4xx discrimination (§1.2).
- Timer state reset on exercise switch (§1.6); `min(target, elapsed)` on early stop (§1.7).
- 360px min width, no horizontal scroll, tap targets ≥ 44px, inputs at `font-size: 16px` (iOS zoom), `-webkit-tap-highlight-color: transparent`.
- Do not touch `base.html` chrome; do not add a fifth nav tab.

**Fix REVIEW §2.12 while here:** logging a set must update only the set table, the affected coverage meter, and the status line — not rebuild the page. The old wholesale `#live-root` innerHTML rebuild (and the scroll jump it caused) has no equivalent in the new structure; keep it that way.

**Storage.** `train_picked_muscles` and `train_const_open` are retired with their features. `train_fajin_dismissed` survives but its day key moves off `fjToday()`'s UTC `toISOString()` to a training-day helper matching the server's Melbourne−4h, or the client and server disagree about what "today" is every afternoon. New: `train_rest_end` (epoch ms) for the persisted countdown.

---

## 4. Phase 2 — warm-up and RPE (one migration)

`migrations/migrate_train_set_meta.py`, following `migrate_muay_thai_under_cardio.py` exactly: `shutil.copy2` backup to `tracker.db.bak-trainrehaul-<ts>` first, `_has_column()` PRAGMA guard, idempotent, prints plan → changelog → verification, run with the app stopped.

```sql
ALTER TABLE gym_progression ADD COLUMN is_warmup INTEGER NOT NULL DEFAULT 0;
ALTER TABLE gym_progression ADD COLUMN rpe REAL;
```

Thread both through, in this order or writes silently vanish: `log_gym_set()` (`gym_bank.py:467`) → `/api/gym/log-set` (`app.py:1750`, with range validation beside the existing `_MAX_*` ceilings) → the SELECT and emitted dict in `get_live_session_state()` (`gym_bank.py:882-907`) → `get_exercise_prefill()` so a warm-up never becomes the prefill → the coverage meters and volume total, which count **working sets only**.

Plate maths rides `engagement.bar_kg` (per your choice) — no migration, and `gym_update_exercise`'s whitelist (`gym_bank.py:631-633`) already permits `engagement`.

---

## 5. Verify

Run from the repo root with the app stopped for the migration, running for the rest.

- `TZ=Australia/Melbourne python -m tests.test_train_session_day` — 15 checks, must stay green; the new recency and previous-session queries are the exact class of bug it pins.
- `python -m tests.test_exercise_bank_roundtrip` — round-trips one exercise per `tracking_type`.
- **New** `tests/test_train_recency.py` in the house harness style (module `FAILS` list, `check(label, cond)`, `sys.exit(0|1)`): seed a temp db via `GYMTRACKER_DB`, assert `get_previous_session_sets` returns the prior session and not today's, that it falls back correctly for a `session_id IS NULL` legacy row, that `get_exercise_recency` is off-by-none across the 4 AM boundary, and that `get_e1rm_bests` ignores reps > 12.
- `python3 app.py`, then headless at **414×896** and **360×640**: page renders, console clean, no horizontal scroll, every tap target clears the bottom nav.
- Grep assertion before/after, per `docs/PLAN-home-redesign-rings.md` §6: every `getElementById`/`querySelector` id in the new JS exists in the new markup, and every one of the 22 inline `onclick` names resolves to a top-level function.
- Manual loop on the phone: log 3 sets → meter fills, rest counts down, refresh mid-rest and the timer survives → edit a set → delete and undo → switch exercise from the coverage drilldown → log cardio → finish session.
- Hostile-name check (§1.10): temporarily rename an exercise to `<img src=x onerror=alert(1)> "Evil" O'Brien` and confirm it renders inert everywhere it appears.

## 6. Rollback

Phase 1 is template + two Python files: restore the three `.bak-trainrehaul-*` sidecars. Phase 2 additionally: restore `tracker.db.bak-trainrehaul-*`, or leave the columns in place (they default to `0`/`NULL` and nothing else reads them).

## 7. Files touched

| File | Change |
|---|---|
| `templates/train.html` | Rewrite — the bulk of the work |
| `gymtracker/gym_bank.py` | +3 query functions; `get_live_session_state` emits `successful` + `n`; Phase 2 `log_gym_set` signature |
| `gymtracker/app.py` | `/train` bank projection widened + `recency`; prefill endpoint gains `previous_session`; Phase 2 validation in `/api/gym/log-set` |
| `migrations/migrate_train_set_meta.py` | New, Phase 2 only |
| `tests/test_train_recency.py` | New |
| `docs/PLAN-train-rehaul.md` | New, house PLAN format |
| `*.bak-trainrehaul-<ts>` | Snapshots before first edit |

## 8. Open questions

1. **Cardio in the session summary** — count its duration separately from lifting volume, or omit it? (Recommn: show as its own line, since the wireframe keeps cardio strictly out of the lifting instrument.)
2. **`_GYM_PRIMARY_MUSCLES` / `_GYM_CONSTANT_MUSCLES`** are mirrored in `app.py:1519` and `gym_bank.py:352` (REVIEW §2.10). The coverage strip needs one ordered list of the six lifting groups. (Recommn: add `_GYM_COVERAGE_ORDER` in `app.py` only for now; the full reconciliation is its own change-set.)
3. **Offline outbox** (REVIEW §2.1, "the single biggest reliability win left") stays out of scope here. (Recommn: separate change-set after this lands.)
