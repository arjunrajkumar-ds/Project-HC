# PLAN — rewire the Today "Last session" panel to the live gym schema

**Status:** 📋 proposed · no schema change · no migration · no table dropped
**Date:** 2026-10-14
**Scope:** read-side only. Fix the Today/home "Last session" card so it reflects the
session the user actually logged, instead of stale legacy data.

---

## 1. The symptom

On the Today screen, the **Last session** panel shows **"Stairmaster"** (a cardio
session dated **2026-07-18**), even though a gym session was logged on **2026-10-12**.

The panel *is* hooked to a live feed — it is not hard-coded. The problem is that it
reads the **wrong table**: a legacy one that stopped receiving gym data in mid-2026.

## 2. Root cause — two parallel session models, panel reads the dead one

The app has two generations of workout storage, and they were never unified:

| | Legacy model (what the panel reads) | Modern model (where logs actually go) |
|---|---|---|
| Session header | `sessions` (`date`, `type`, `profile_id`) | `gym_sessions` (`performed_at`, `finished`, `ended_at`) |
| Lifts / sets | `session_lifts`, `session_cardio` | `gym_progression` (`session_id`, `exercise_id`, `weight_kg`, `sets`, `reps`, `recorded_at`) |
| Exercise catalog | `exercises` | `gym_exercises` (`name`, `muscle_group`, `tracking_type`, …) |
| Newest gym row | id 82, **2026-08-02** | id 21, **2026-10-12 09:02** ← the user's session |

The Train rehaul (`docs/PLAN-train-rehaul.md`) moved live gym logging onto
`gym_sessions`/`gym_progression`/`gym_exercises`. The write path confirms it: the live
set logger and `/api/gym/session/finish` write there via `gym_bank.py`
(`get_live_session_state`, `end_gym_session`, etc.). But the **read** in
`_shared_home_context()` was never moved.

### 2.1 Exactly where it breaks

`gymtracker/app.py` → `_shared_home_context()` (≈ lines 666-690) runs the
"richer multi-type last-session" query against **`sessions` / `session_lifts` /
`session_cardio` / `exercises`**, `ORDER BY s.date DESC, s.id DESC LIMIT 1`.

For profile 1 that newest legacy row is a `cardio` session on 2026-07-18. The
`_last_session.html` partial titles a `cardio` card with its first cardio exercise
name → **"Stairmaster"**. Everything downstream is behaving correctly; it is being
fed a stale row.

### 2.2 This is wider than the home panel

The same legacy-read lives in two more places that *also* currently ignore the
2026-10-12 session (out of scope here, but note them so the fix isn't mistaken for
complete):

- `/sessions` list → `get_sessions_with_headlines()` (`database.py` ≈ 1750) reads `sessions`.
- `/session/<id>` detail → `get_session_detail_with_progression()` (`database.py` ≈ 1837) reads `sessions`/`session_lifts`.

The home panel links to `/session/{{ last_session.id }}`. Once the panel points at a
`gym_sessions` id, **that detail route must understand the same id space** or the
chevron 404s. See §4, Decision B.

## 3. Constraints discovered (must respect)

1. **No destructive migration.** `docs/PLAN-legacy-cleanup.md` set the house rule:
   keep every table, rewire reads to the current schema, never drop. Follow it.
2. **Training-day timezone handling is mandatory.** The modern tables store UTC
   timestamps and resolve "which day" via `_training_day(col)` / `TODAY_SQL` with a
   `ROLLOVER_HOUR` offset (`gym_bank.py` ≈ 51-59). This exists to fix the UTC/UTC+11
   "two-calendar" bug (`docs/REVIEW-train-hardening.md` §1.1). Any new gym query
   **must** sort/compare days through these helpers — never raw `DATE(performed_at)`.
3. **`gym_sessions`/`gym_progression` have no `profile_id`.** The modern gym engine is
   effectively single-profile (profile 1 / Arjun). The home panel is per-profile. So a
   rewired *gym* query is only valid for the profile that owns the modern engine;
   profiles 2/3 must keep falling back to their existing source. This asymmetry has to
   be explicit in the code, not accidental.
4. **Swim / pilates / cardio are not on the modern gym tables.** They have their own
   stores (`swim_sessions`, `pilates_sessions`, legacy `sessions.type IN ('swim','cardio')`).
   A "last session" is genuinely multi-source, so the fix must *merge* candidates and
   pick the most recent — not simply swap one table for another.
5. **Partial contract is fixed.** `_last_session.html` expects a dict with:
   `id, type, date, exercise_names, total_sets, total_volume, distance_m,
   cardio_name, cardio_dur, cardio_dist`. The new builder must emit this exact shape
   so the template needs no change (ideal), or the template change must be minimal.

## 4. Key decisions to make before coding

**Decision A — how to source "last gym session"**
- **A1.** New helper in `gym_bank.py`, e.g. `get_last_gym_session()`, returning the
  partial-shaped dict from `gym_sessions`+`gym_progression`+`gym_exercises`, day-sorted
  via `_training_day`. Clean, colocated with the other modern gym reads. *(recommended)*
- A2. Inline the query in `_shared_home_context()`. Faster to write, but puts a second
  copy of training-day logic in `app.py` — the exact drift §3.2 warns about. Avoid.

**Decision B — the detail link target (`/session/<id>`)**
- **B1.** Point the panel at an existing modern detail view. Does one exist for a
  `gym_sessions` id, or does only `/session/<id>` (legacy) exist? *Needs a 5-min check.*
  If none exists, either (b1a) add a thin modern detail route, or (b1b) have the card
  link to `/train` (today's live view) when the last session is today. **This decision
  gates whether the card's chevron is safe.**
- B2. Leave the link legacy and accept it 404s for modern sessions. Not acceptable.

**Decision C — multi-source merge**
- C1. Gym-only rewire now; leave swim/cardio/pilates reading legacy. Smaller, but the
  panel could still show a stale swim over a newer gym, or vice-versa.
- **C2.** Build a small `get_last_session_multisource(pid)` that gathers the newest
  candidate from each live source (modern gym for p1; swim; pilates; legacy
  cardio/gym for p2/3), normalises to the partial's dict shape, and returns the one
  with the latest training-day. Correct for all profiles. *(recommended)*

## 5. Proposed implementation (A1 + C2, pending §4)

1. **`gym_bank.py`: add `get_last_gym_session()`**
   - Newest `gym_sessions` row by `_training_day('performed_at')` desc, id desc.
   - Aggregate its `gym_progression`: `total_sets = COUNT(*)`,
     `total_volume = SUM(reps*weight_kg)` (guard nulls), ordered exercise-name list for
     `exercise_names`, derive cardio fields when the session's exercises are
     `tracking_type='cardio'`.
   - Return the exact `_last_session.html` dict shape, `type='gym'` (or `'cardio'` if the
     session is purely cardio-tracked), plus an `id` usable by Decision B's detail route.
2. **`app.py`: replace the legacy block in `_shared_home_context()`**
   - For the profile that owns the modern gym engine (profile 1), call the new
     multisource picker; keep the legacy query as the fallback branch for other
     profiles. Single `last_session` out, same key into the context dict — no template
     churn.
3. **`get_last_session_multisource(pid)`** (location per Decision A): gather + normalise
   + pick latest. Pure reads.
4. **Detail link** per Decision B.

## 6. Verification

- **Data:** with the current DB, `get_last_gym_session()` returns `gym_sessions` id 21,
  2026-10-12, with the sets logged that day (not "Stairmaster").
- **Panel:** Today screen for profile 1 shows the 2026-10-12 gym session; profiles 2/3
  unchanged.
- **Timezone:** a set logged before ~ `ROLLOVER_HOUR` local still attributes to the
  correct training day (regression target from `REVIEW-train-hardening.md` §1.1).
- **Chevron:** tapping the card opens a detail view that resolves (no 404).
- **Tests:** extend `test_train_session_day` / add a `test_last_session_panel` asserting
  the picker returns the newest *modern* gym session and respects training-day rollover.
- **No write-path or schema change** — diffable as reads only.

## 7. Out of scope (flag, don't fix here)

- Rewiring `/sessions` list and `/session/<id>` detail to the modern schema (§2.2).
  These share the same root cause and are the natural follow-up, but they are a larger,
  separately-verifiable change — recommend a dedicated plan.
- Any migration/unification of legacy `sessions` into `gym_sessions`.
- Adding `profile_id` to the modern gym tables (would be needed before profiles 2/3 can
  use the modern gym engine).

## 8. Rollback

Single-file read change in `app.py` (+ one new pure-read helper). Back up
`gymtracker/app.py` and `gymtracker/gym_bank.py` before editing; revert by restoring
the backups. No data touched.
