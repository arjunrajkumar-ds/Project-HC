# Train page — hardening pass, bugfixes and enhancement backlog

**Date:** 2026-09-30 · **Scope:** `/train` (`templates/train.html`, `gymtracker/app.py` live-workout
endpoints, `gymtracker/gym_bank.py` session helpers)

Everything in part 1 is **already applied and tested**. Part 2 is a proposal list — nothing there
has been built.

---

## 1. What was fixed

### 1.1 The session day was computed in two different calendars — P0

`gym_progression.recorded_at` and `gym_sessions.performed_at` default to SQLite's
`CURRENT_TIMESTAMP`, which is **UTC**. Every "is this today?" query compared that UTC date against
`DATE('now','localtime')`, a **Melbourne** date. East of Greenwich those disagree for the whole
window between local midnight and UTC midnight — i.e. **every session before about 10 AM**.

In that window:

| Helper | What actually happened |
| --- | --- |
| `get_live_session_state()` | found no session → the page showed "No sets yet today / Pick a muscle to begin" while sets were being written to the database |
| `get_or_create_today_gym_session()` | never matched the session it had just created → **a new `gym_sessions` row per set** |
| `get_today_tally()` | returned 0 |
| `undo_last_today()` | silently deleted nothing |
| `apply_session_rollover()` | read the just-opened session as "from a prior day" and closed it |

Nothing was lost — every set was still written — but the workout fragmented into one session per
set and none of it rehydrated on the page. It has not bitten yet only because the existing history
is all afternoon and evening training.

**Fix.** One canonical notion of a *training day*, in `gym_bank.py`:

```python
def _training_day(col):
    return ("CASE WHEN length({c}) <= 10 THEN DATE({c}) "
            "ELSE DATE({c}, 'localtime', '-{h} hours') END")

TODAY_SQL = "DATE('now', 'localtime', '-4 hours')"
```

Two things fall out of that shape:

- `'localtime'` puts both sides of every comparison in the same calendar.
- the `-ROLLOVER_HOUR` shift makes the 4 AM boundary **real** rather than cosmetic: a session
  finished at 1 AM still belongs to the day it started on, and 5 AM starts a new one. The separate
  hour guard inside `apply_session_rollover()` is gone — it is implied now.

The `length(col) <= 10` arm matters: `import_history.py` writes a bare `YYYY-MM-DD` with no time.
SQLite reads those as midnight UTC, so applying `'localtime'` to them would shift them a day. There
are 22 such rows in the live database.

Covered by `tests/test_train_session_day.py`, which pins the boundary at four known instants rather
than at the wall clock.

### 1.2 A dropped connection froze the page mid-save — P0

`fetch()` *rejects* on a network failure, and `apiPost`/`apiGet` did not catch it. A phone handing
over between Wi-Fi and cellular in the gym left the log button reading "Saving…" forever, with no
error and no retry — and the thrown rejection was never surfaced anywhere.

Both helpers now go through `apiFetch`, which resolves `{ok:false, offline:true}` instead of
throwing, and carries a 15-second `AbortController` timeout. Failures are now reported with the
server's own message, and only a network drop or a 5xx offers "tap to retry" — a 400 will never
succeed on a retry, so it says what is actually wrong instead.

### 1.3 Double-tapping ✓ logged the set twice — P0

Four rapid taps on the log button produced **four** `gym_progression` rows (verified, then verified
again after the fix: one row). Fixed with an in-flight flag plus `btn.disabled` while the request is
open. The same guard is on the Fa Jin log button.

### 1.4 The sheet's primary button sat underneath the bottom nav — P1

`.sheet-panel` padded for the safe-area inset but not for `--nav-h`. Measured at 414×896: the
"Log set" / "Log Fa Jin" / "Done / Next" buttons ended at y=878 with the fixed nav starting at
y=836. The button looked tappable; the tap landed on the nav and **navigated away mid-set**. Now
clears the nav (buttons end at y=818).

### 1.5 Sets could be logged but invisible — P1

`renderMuscle()` returned `''` when the muscle was absent from `BANK`. A muscle group is absent when
all its exercises are Fa Jin (tier 4 is filtered out of the picker), disabled, or archived — so
logging a Fa Jin against such a group left the status line counting "1 set · 1 muscle" above an
empty page. It now falls back to a stub section so the sets still render.

### 1.6 Timer state leaked between exercises — P1

Opening exercise B while a timer ran on exercise A kept A's interval alive, writing A's elapsed time
into B's display. `durMode` carried over too, so the new sheet rendered "Endure" as active while the
state still said "Target". `openExercise()` now resets both. `durStart` ignores a second tap,
`durLoop` bails if the sheet was rebuilt underneath it, and `durStop` no-ops without a run in
flight.

### 1.7 Stopping a Target timer early logged the full target — P1

`durStop` used `parseInt(target) || elapsed`, so racking at 20 s against a 60 s target logged 60 s —
crediting work that was not done. Now logs `min(target, elapsed)`. Past the target the display counts
the overrun up instead of freezing at `0:00`, so a 10 s hold and a 90 s hold no longer look alike.

### 1.8 "Log without timer" did not log — P1

For timed work the button only ever toasted *"Run the timer, then it logs on stop."* For cardio it
read `#dur-target`, which is hidden unless you first switch to Target mode — so it appeared broken.
Both now read a visible **mm:ss** box (`parseDuration` accepts `90`, `1:30` or `1:00:00`).

### 1.9 Typing was wiped 550 ms after every save — P2

The sheet re-renders half a second after a set lands. Anyone who started keying the next weight
immediately lost it. Field values and focus are now preserved across the re-render.

### 1.10 Free text was interpolated raw into HTML — P2

Exercise names, function labels and the cardio "setting" box are all user-typed in the databank, and
every one of them went into `innerHTML` unescaped. A name containing `<`, `"` or `'` broke the markup
or the inline `onclick` handlers. Added `esc()` / `jsStr()` and applied them throughout. Verified
with an exercise named `<img src=x onerror=alert(1)> "Evil" O'Brien`: renders as text, zero injected
nodes.

### 1.11 Server-side input validation — P2

`/api/gym/log-set` took any `exercise_id`. An unknown one reached the INSERT, tripped the foreign
key, and came back as an opaque 500 that the client read as transient and retried forever. It now
returns **404**. Weight, reps, duration and distance are bounded (negatives rejected, sane ceilings
to catch a mistyped extra digit) and `setting` is truncated to 60 characters. Database errors return
a described 500 rather than a Flask traceback page. Same exercise check on `/api/gym/quick-log`.

### 1.12 CSRF only covered POST — P2

`require_profile()` checked `request.method == 'POST'`, leaving `DELETE /api/gym/exercise/<id>`
(hard-delete of an exercise) unprotected. Now covers POST, PUT, PATCH and DELETE. The existing
callers in `exercise_db.html` already send the header, so nothing broke.

### 1.13 Undo could reach into a different session — P2

`undo_last_today()` deleted the newest set for an exercise *anywhere in the day*. Two sessions in one
day (a morning lift and an evening one) meant undo could delete from the wrong one. It now takes an
optional `session_id` and the Train endpoint scopes it to the live session. It also reports when
there was nothing to undo, instead of silently doing nothing.

### 1.14 `/api/gym/log-cardio` never committed its new exercise — P2

The endpoint inserted into `exercises` and then called `log_cardio_session()`, which opens its own
connection — so the new row was invisible to it, the open write transaction blocked its commit, and
`db.close()` rolled the insert back. Not reachable from the Train page today, but it is on the same
blueprint. Now commits and closes before handing off.

### 1.15 Smaller things

- `bump()` on `fj-r` inherited the float path — `+1` on `2.5` gave `3.5` reps. Rep and distance
  fields are integer-stepped now.
- A picked-but-empty muscle lived only in a JS variable, so a phone reload mid-warm-up dropped you
  back to the picker. Persisted for the day in `sessionStorage`.
- No way to remove a muscle group added by mistake. A `✕` now appears on sections with no sets, and
  disappears once one is logged.
- The Fa Jin popup opened over the logger **every** time you reopened the same exercise between
  sets, so dismissing it to get back to logging cost you the suggestion for the day. Now offers once
  per exercise per page session.
- The rest timer ran until you left the page. It now has a dismiss control and stops itself at
  30 minutes.
- The page never re-read live state. Lock the phone mid-workout, or log from a second device, and it
  kept rendering a stale `LIVE` — counts drifting from the database, and a 4 AM roll-over never
  noticed. Now re-syncs on `visibilitychange` and `pageshow`.
- `DB_PATH` is overridable via `GYMTRACKER_DB` so tests never open the live database.

### Verification

- `python3 -m tests.test_exercise_bank_roundtrip` — passes (unchanged).
- `TZ=Australia/Melbourne python3 -m tests.test_train_session_day` — new, 15 checks, passes.
- All 34 GET routes load without a 5xx.
- Headless browser pass at 414×896: page renders, no JS errors, console clean, tap targets clear the
  nav, hostile exercise name is inert, rapid taps log exactly one set (four without the guard).

---

## 2. Enhancements worth considering

Ordered by what would change the most about using the page, not by effort.

### 2.1 Make logging survive a dead connection properly

Right now a failed save leaves the button in a retry state, which is honest but puts the burden on
you mid-set. A small outbox in `localStorage` — queue the set, show it as pending, flush on
reconnect — would mean the gym's dead spot never costs you a set or your attention. This is the
single biggest reliability win left.

### 2.2 Rethink the Fa Jin interstitial

It is the wrong shape for the moment it fires. You tap an exercise because you are about to lift;
a modal covering the logger is an interruption at the worst point, and both its exits are lossy
("Not now" burns the suggestion for the day). Better: keep the amber pill in the header as the entry
point and surface the contextual match as a one-line strip **inside** the exercise sheet, under the
reference cards. Same recommendation, no interruption, no accidental dismissal.

### 2.3 A session summary and a way to end a session

There is no "done" moment. `end_gym_session()` exists but nothing on the page calls it; sessions are
closed by a lazy roll-over stamp the next time you open Train, which is why `ended_at` values in the
database are days after the fact. A **Finish session** button that stamps it and shows total sets,
volume per muscle, session duration and which functions you left unticked would close the loop and
make `ended_at` mean something.

### 2.4 Show progression against last time, in the sheet

The sheet shows "Most recent" and "Best recent" as bare numbers. The useful question is *am I ahead
of last time?* A delta — `65kg × 5 · +2.5kg on last week` — plus a per-set comparison against the
same exercise's previous session would turn reference data into a decision. The data is already in
`get_exercise_prefill()`; it is a presentation change.

### 2.5 Rest timer that knows what it is timing

The rest bar is a count-up with no target. Per-exercise rest targets (or per-tier: 3 min for T1,
90 s for T3) with a cue at the target — reusing `fireTargetCue()` — would make it actionable. Worth
persisting the start timestamp so a phone lock does not lose it, which contradicts the current
"ephemeral by design" note but is what you would actually want.

### 2.6 Warm-up sets as a first-class thing

Every set is logged as a working set, so warm-ups inflate volume and pollute "Most recent" — the
prefill happily suggests your 40 kg warm-up as the next working set. A `successful=0`-style flag
already exists in the schema (`successful` is written but never set to 0 from this page); a **W**
toggle beside ✓ would separate them cleanly.

### 2.7 Plate maths

For barbell work, showing what to load per side for the target weight (given a bar weight and your
available plates) removes a small piece of arithmetic from every weight change. Low effort, and the
sort of thing that gets used every session.

### 2.8 Order the exercise list by what you actually do

Within a function, exercises are ordered by `sort_order, tier, name` — static. Surfacing the one you
have done least recently, or simply the one you did last time for this muscle, would cut most of the
scrolling. The README says the app already does this kind of "lead with what you have done least
recently" reasoning elsewhere; Train does not.

### 2.9 Reconcile the two muscle taxonomies

`_GYM_PRIMARY_MUSCLES` and `_GYM_CONSTANT_MUSCLES` between them do not cover `stability` or
`muay_thai`. The design note calls those "loggable-only, no strip", but `syncActiveMuscles()` only
filters out *constant* muscles — so a Muay Thai set logged through the quick-log path makes a full
Muay Thai day-driver section appear on Train. Decide whether that is wanted and make the taxonomy
explicit (a third `loggable_only` list), rather than leaving it as an accident of the filter.

### 2.10 Two mirrored sources of truth for muscle order and labels

`app.py`'s `_GYM_GROUP_ORDER` / `_GYM_GROUP_LABELS` and `gym_bank.py`'s `_muscle_order()` /
`_muscle_labels()` are duplicates kept in sync by hand, with a comment admitting divergence is
"cosmetic only". The cardio plan doc had to tell you to edit both. Move them into one module both
import.

### 2.11 Per-profile gym data

`gym_progression` and `gym_sessions` have no `profile_id`. Today that is safe because Train is
blocked for profiles 2 and 3, but it is a latent constraint on ever giving anyone else a gym log.

### 2.12 A couple of smaller ones

- **Scroll position** jumps on every save, because `render()` rebuilds `live-root` wholesale. A
  targeted update of the affected section would keep your place.
- **Best recent** ranks by weight first, so for a `reps` exercise it is really "most reps among the
  last 20" and for cardio it is "longest" — defensible, but the label says something else.
- **`gym_sessions.ended_at`** is currently meaningless (set by the roll-over, days late). §2.3 fixes
  this properly; failing that, stamp it with the last set's timestamp rather than `CURRENT_TIMESTAMP`.
