# PLAN — rebuild /train around the session, not the exercise bank

**Status:** ✅ phase 1 implemented + hardened · phase 2 (warm-up + RPE migration) not started · **Date:** 2026-10-12
> Backups taken before the first edit: `templates/train.html.bak-trainrehaul-20261012-062855`,
> `gymtracker/app.py.bak-trainrehaul-20261012-062855`, `gymtracker/gym_bank.py.bak-trainrehaul-20261012-062855`.
> No database change in phase 1, so `tracker.db` was not backed up.

## 0. Why this is not just a restyle

The old page was the exercise taxonomy rendered as navigation: pick the day's muscles → expand a muscle
section → find the exercise → open a bottom sheet → log. Three taps per set, every set, when the overwhelmingly
common case is "same exercise, same weight, one more set".

Two further problems followed from that shape rather than from any bug:

* **Two components for one concept.** Chest/back/legs rendered as day-driver sections; arms/core/shoulders
  rendered as "constant" strips. They are the same thing — a muscle group with functions — differing only in
  *policy* (rotating vs every-session). The policy difference had grown into two renderers, two DOM roots and
  two `sessionStorage` keys.
* **"Done" after one set.** A function flipped to done, greyed and struck through the instant a single set
  landed under it. The mechanic was meant to encourage covering every function; what it actually rewarded was
  one token set per function, after which the UI removed it from consideration.

And the page never showed the number a lifter opens an app for: what they did last time.

A restyle could not fix any of that, because all three are consequences of the arrangement.

## 1. What replaced what

| # | Old element | Fate | New treatment |
|---|---|---|---|
| 1 | `#empty-state`, `.picker-grid`, `renderEmptyPicker()` | removed | Coverage strip is never empty — all six lifting groups always render |
| 2 | `#add-muscle-wrap`, `#mp-sheet`, `addMuscle`/`pickMuscle`/`removeMuscle` | removed | No day-driver concept; `train_picked_muscles` retired |
| 3 | `#live-root` `.mg-sec` sections | removed | `#cov-root` coverage strip with per-row drilldown |
| 4 | `#constant-root`, `toggleConst`, `train_const_open` | removed | Rows 1–3 of the same strip, marked by a 2px accent rule |
| 5 | `.checklist` / `.chk` done pills | replaced | Per-function volume meters (fill = sets today, 3 = full) |
| 6 | `#ex-sheet` exercise sheet | promoted | `#card-root` — always visible, the page's resting state |
| 7 | `#fajin-sheet` modal + `maybeOfferFajin` | replaced | Inline `.fajin-row` under the logger (REVIEW §2.2) |
| 8 | `#rest-bar` count-up stopwatch | replaced | Countdown against a tier target, persisted (REVIEW §2.5) |
| 9 | `#fajin-cta` header pill | kept | Still opens the full Fa Jin list |
| 10 | `.sheet` / `.sheet-panel` | kept | Reused by picker / summary / Fa Jin / plates, nav padding intact |
| 11 | `apiFetch`/`apiPost`/`esc`/`jsStr`/`resyncLive`, the `dur-*` work timer | kept verbatim | Load-bearing hardening, lifted across unchanged |

Cardio is untouched at the data layer — still `muscle_group='cardio'` with its `machine` / `muay_thai`
functions. It is excluded from the coverage strip and from the lifting picker's search, recents and browse,
and is reachable only through the Cardio button. That exclusion is enforced by `pickPool()`/`pickGroups()`.

## 2. Server work (no schema change)

### 2.1 `gymtracker/gym_bank.py` — three new reads

```python
get_exercise_recency()                      # {id: {last_day, days_since}} — the staleness readouts
get_e1rm_bests(max_reps=12)                 # {id: float} Epley, capped (21-rep 777s would dominate)
get_previous_session_sets(exercise_id, limit=12)   # {day, days_since, sets[]} — the PREV column
delete_gym_set(set_id, session_id=None)     # delete by id, not by recency
```

All date comparisons go through `_training_day()` / `TODAY_SQL`. `get_previous_session_sets` groups by
training day rather than `session_id`, because the 33 legacy rows from `import_history.py` carry
`session_id IS NULL`; it also expands `sets > 1` legacy rows so the PREV column lines up set-for-set.

### 2.2 `get_live_session_state()` — two fields it already had and dropped

Now emits `successful` (was SELECTed then discarded) and `n`, a 1-based ordinal per exercise, so deleting
set 2 renumbers server-side instead of leaving the client to infer from array position.

### 2.3 `gymtracker/app.py`

* `_GYM_COVERAGE_ORDER` — the one ordered list of lifting groups, standing goals first, cardio absent.
  A third list alongside `_GYM_PRIMARY_MUSCLES` / `_GYM_CONSTANT_MUSCLES`; reconciling all three plus
  `gym_bank`'s mirrored `_muscle_order()` (REVIEW §2.10) is deliberately left as its own change-set.
* `_GYM_REST_BY_TIER = {1:180, 2:120, 3:90, 4:90, 5:60}`.
* `_train_bank_entry()` widens the `bank` projection with `days_since`, `last_day`, `best_e1rm`, `rest_s`
  and `bar_kg`. `rest_s` and `bar_kg` ride `engagement` (the blob `target_s` already lives in), so neither
  needs a column.
* `/train` builds and passes `coverage` — the six rows with their functions and staleness, folded from the
  per-exercise recency map rather than queried again.
* `/api/gym/exercise/<id>/prefill` gains `previous_session`.
* **New** `/api/gym/set/delete` — removes one set by id, re-checking membership of today's live session.
* **New** `/api/gym/session/finish` — finally calls `end_gym_session()`, which has existed unused since the
  session tables were added (REVIEW §2.3).

## 3. Template

Single file, `pandora` tokens only. Structure: header → coverage strip → exercise card → fixed footer
(rest countdown + action bar) → four sheets. Every JS function stays a top-level declaration, because the
page still drives everything from inline `onclick` (24 of them).

Constraints carried forward: `.sheet-panel` keeps `calc(18px + var(--nav-h) + var(--sab))`; `.tr-wrap`
reserves for the footer *including* the rest bar, which is the same bug one layer up; `logInFlight` +
`disabled` on every log path; all free text through `esc()`/`jsStr()`; 16px inputs, ≥44px targets, no
horizontal scroll at 320px.

Two behaviours worth naming because they were bugs during the build:

* **`samePass` in `renderCard()`.** Typed values are preserved across re-renders only when the exercise
  *and* the prefill state are unchanged. `openExercise()` renders twice (once immediately, once when the
  prefill lands); carrying values across that boundary pinned every exercise to its fallback and silently
  logged 0 kg sets.
* **Picker scoping.** `pickPool()`/`pickGroups()` filter by scope so cardio never leaks into the lifting
  picker's recents, search or browse. Without it the Cardio button stops being the only door.

Storage: `train_picked_muscles` and `train_const_open` retired with their features. `train_fajin_dismissed`
is gone too — with the offer non-modal there is nothing to dismiss, which also removes `fjToday()`, whose
`toISOString()` UTC day disagreed with the server's Melbourne−4h training day every afternoon.
New: `train_rest` holds the countdown's end timestamp.

## 4. Phase 2 — not started

`migrations/migrate_train_set_meta.py`, following `migrate_muay_thai_under_cardio.py`: backup, `_has_column`
guard, idempotent, run with the app stopped.

```sql
ALTER TABLE gym_progression ADD COLUMN is_warmup INTEGER NOT NULL DEFAULT 0;
ALTER TABLE gym_progression ADD COLUMN rpe REAL;
```

Thread through in this order or writes vanish silently: `log_gym_set()` → `/api/gym/log-set` (with range
validation beside the `_MAX_*` ceilings) → the SELECT and emitted dict in `get_live_session_state()` →
`get_exercise_prefill()` (so a warm-up never becomes the prefill) → the coverage meters and volume total,
which must then count working sets only.

## 5. Verify

* `TZ=Australia/Melbourne python -m tests.test_train_recency` — 19 checks, new.
* `TZ=Australia/Melbourne python -m tests.test_train_session_day` — unchanged, must stay green.
* `python -m tests.test_exercise_bank_roundtrip` — unchanged.
* Headless pass at 414×896 / 360 / 320: 38 checks covering boot, logging, rest countdown, delete,
  coverage drilldown, picker search/scope, cardio isolation, summary, Fa Jin list, overflow, and the
  action bar clearing the fixed nav.
* Grep assertion: every `getElementById` id exists in the markup, every inline `onclick` resolves to a
  top-level function.

## 6. Rollback

Restore the three `.bak-trainrehaul-20261012-062855` sidecars. No database change to undo.

## 7. Files touched

| File | Change |
|---|---|
| `templates/train.html` | Rewritten (1140 → ~1200 lines) |
| `gymtracker/gym_bank.py` | +4 functions; `get_live_session_state` emits `successful` + `n` |
| `gymtracker/app.py` | Coverage order + rest table + `_train_bank_entry`; `/train` passes `coverage`; prefill gains `previous_session`; 2 new routes |
| `tests/test_train_recency.py` | New |
| `docs/PLAN-train-rehaul.md` | This file |

## 8. Open questions

1. ~~Cardio in the session summary~~ — resolved: its own line, minutes only, never in lifting volume.
2. ~~Mirrored muscle-order lists~~ — resolved: `_GYM_COVERAGE_ORDER` added; full reconciliation deferred.
3. ~~Offline outbox (REVIEW §2.1)~~ — resolved: out of scope, scheduled after this lands.
4. `bar_kg` is unset on every exercise, so the plate sheet currently says so rather than guessing from the
   name. Setting it per barbell exercise is a databank edit, not a code change.


---

## 9. Hardening round (same day, after an adversarial review)

Three P1s and a set of P2s found by reviewing the rewritten file against
`docs/REVIEW-train-hardening.md`'s guarantees. All fixed and pinned by
`harden_test.js` (32 checks) alongside the original interaction suite (39).

### P1 — broken interactions

| # | Symptom | Fix |
|---|---|---|
| 1 | A **running duration timer was orphaned by any card re-render**. `renderCard()` re-emits the controls idle, but `durT0`/`durTick` survive: the clock kept counting while Start showed and Stop was gone, `durStart()` refused to restart, and the set could only be logged by typing the duration. Reachable by a kg stepper tap on `weight_time`, by tapping a logged row, or by the phone locking (`resyncLive`). REVIEW §1.6's invariant through a new door. | `restoreDurUI()` called at the end of `renderCard()` for timed types. |
| 2 | **Delete was a 34×40 unconfirmed destructive target** flanked by three "load this set" targets, with no in-flight guard, and the `lastRemoved` payload captured for an undo was never read. It also toasted success when the server said `removed:false`. | 46×44 with its own divider and an active state; `removeInFlight` guard matching `logSet`'s; a real **Undo** in the toast that re-logs through the normal path; honest message when nothing was removed. |
| 3 | **Finish then log again hid the whole session.** `get_or_create_today_gym_session()` matches `finished = 0` only, so the next set opened a *second* session, and `get_live_session_state()`'s `ORDER BY finished ASC` made that the live one — every earlier set vanished from the page. Nothing lost in the DB; on a phone it reads as the app eating the workout. | The resolver now **re-opens** today's finished session rather than starting a second one: one training day is one session. Finish also became two-tap, since the frame cannot use `confirm()`. |

### P2 — correctness and mobile

- **Escaping has one owner.** `setLabel()` escaped its own `setting` while two of three call sites escaped the result again — PREV rendered `&quot;` as literal text while DONE, one cell right, was correct. `setLabel` now returns raw; every call site escapes.
- **Progression nudge uses the heaviest previous set**, not the chronologically first — opening with a back-off made it suggest *less* than was already done.
- **One PR rule.** `best_e1rm` is a load-time snapshot, so every set beating it stacked another badge. Now the single best set of today, and only if it beats the snapshot. The card tag and the row badge use the same threshold.
- **Coverage never drops a group.** A group whose exercises are all Fa Jin/disabled/archived produced no `bank` entry and so no coverage row — REVIEW §1.5 in reduced form. The server now emits a row regardless.
- **Fa Jin runs through `_train_bank_entry`**, so its card no longer reads "LAST NEVER" and a timed Fa Jin gets its own `target_s`.
- **Corrupt `train_rest` no longer wedges the bar.** A non-numeric `end` made every comparison NaN-false, leaving the bar stuck on GO with a 250 ms interval forever.
- **The rest countdown has its own cue** — it was flashing the *work* timer's digits.
- **The footer no longer changes height** when the rest bar appears; it used to grow over the Log button so the next tap landed on −15/+15/Skip.
- **Tap targets**: ∑ 32→44, plate-sheet entry 14→30, progression chip 24→34, "Manage databank" 11→44. No visible target under 30px at 390px.
- Bottom padding was reserved twice (`.page` already adds `--nav-h` + `--sab`) — ~310px of dead scroll removed.
- Status line counts lifting sets, volume and cardio minutes separately, matching the summary.
- The picker no longer autofocuses its search box (the keyboard covered the recents).
- Dead code removed: `TAXONOMY` (unreferenced), `.rpe-n` (phase-2 leftover).

### Verified at 414 / 390 / 360 / 320px
No horizontal overflow at any width; the log button clears the fixed footer; the footer clears the bottom nav.

> Separate, pre-existing, NOT from this change-set: `/` overflows horizontally — `.wb-stat` in
> `templates/_week_board.html` is a fixed 492px that never shrinks, so the overflow grows from 78px at
> 414px to 172px at 320px. `#sa-card` is contained at every width.
