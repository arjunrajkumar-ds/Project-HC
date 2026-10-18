# PLAN — Train: log a workout for a previous day

**Status:** proposed (awaiting review)
**Date:** 2026-10-18
**Scope:** Add a prev/next **date header** to `/train` so a set can be logged against a past training day, not only today. Default behaviour (log for *today*) is unchanged.

---

## 1. The problem

Every write on `/train` is anchored to **today**. The anchor is the SQL constant
`TODAY_SQL = DATE('now','localtime','-4 hours')` plus the `_training_day(col)` expression
(the ~4 AM rollover). These two appear, hardcoded, in every function the Train stack calls:

| Function (`gymtracker/gym_bank.py`) | Uses today how |
|---|---|
| `get_or_create_today_gym_session()` | finds/creates the open session where `performed_at`'s training-day == `TODAY_SQL`; new row stamped `CURRENT_TIMESTAMP` |
| `get_live_session_state()` | reads the session whose training-day == `TODAY_SQL` (rehydrate) |
| `undo_last_today(exercise_id, session_id)` | deletes the last set where training-day == `TODAY_SQL` |
| `get_previous_session_sets(exercise_id)` | PREV column = newest day **strictly before** `TODAY_SQL` |
| `get_today_tally(exercise_id)` | counts sets where training-day == `TODAY_SQL` (not on the Train live path; leave as-is) |

The **home** screen (`/`) already has the exact UX we want and the pattern to copy:
`?date=` query param, `prev_date`/`next_date`, and `today = min(today, actual_today)`
(never navigate into the future). See `app.py` `dashboard()` and `_shared_home_context()`.

So the work is: **make the day boundary a parameter** that defaults to today, thread an
optional `date` through the four write/read functions and their API endpoints, and add the
date header + client date-state to `train.html`.

---

## 2. Design principles

1. **Backwards-compatible by default.** Every touched function keeps working with no
   `day` argument — it falls back to `TODAY_SQL`. The live "log right now" flow is byte-for-byte
   unchanged when no date is supplied.
2. **One source of truth for the day boundary.** Add a single helper that turns an optional
   ISO date into the right SQL day expression; never spell out `DATE('now',...)` in more than
   one place again.
3. **Never log to the future.** Validate server-side (reject `date > today`) *and* clamp the
   client, mirroring the home screen's `min(today, actual_today)`.
4. **Respect the 4 AM rollover.** A chosen calendar day is a *training day*. When creating a
   backdated session we stamp `performed_at` at **noon** of that date (`DATE 12:00:00`), so it
   is unambiguously inside that training day regardless of the −4h shift, and never collides
   with the UTC/local boundary.
5. **PREV column stays consistent.** When logging for day D, the PREV column compares against
   the newest outing **strictly before D** (confirmed with user — correct behaviour).

---

## 3. Backend changes — `gymtracker/gym_bank.py`

### 3.1 New day-boundary helper

```python
def _day_sql(date_iso=None):
    """Return (sql_expr, params) for the training day to operate on.
    None  -> the current training day (TODAY_SQL), no params.
    'YYYY-MM-DD' -> that exact day, as a bound parameter.
    The caller interpolates sql_expr where it currently writes TODAY_SQL and
    prepends params to its parameter tuple."""
    if not date_iso:
        return TODAY_SQL, ()
    return "DATE(?)", (date_iso,)
```

> `_training_day(col)` is unchanged — it still maps a stored timestamp to its training day.
> Only the right-hand side (what we compare against) becomes parameterised.

A tiny validator (module-level), reused by the API layer:

```python
def _valid_training_day(date_iso):
    """True if date_iso is a real ISO date and not in the future (training-day
    aware). Returns the normalised 'YYYY-MM-DD' or None."""
    try:
        d = date.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return None
    # 'today' here is the training day, read straight from SQLite so the −4h
    # rollover matches every other comparison.
    conn = _gym_db()
    t = conn.execute("SELECT %s AS d" % TODAY_SQL).fetchone()['d']
    conn.close()
    return date_iso if date_iso <= t else None
```

### 3.2 `get_or_create_today_gym_session(day=None)`

- Replace the two `TODAY_SQL` comparisons with `_day_sql(day)`.
- **Session stamp when creating a backdated row:** when `day` is given, insert
  `performed_at = '<day> 12:00:00'` instead of `CURRENT_TIMESTAMP`:

```python
if day:
    cur = conn.execute(
        "INSERT INTO gym_sessions (performed_at, finished) VALUES (?, 0)",
        (day + " 12:00:00",))
else:
    cur = conn.execute(
        "INSERT INTO gym_sessions (performed_at, finished) VALUES (CURRENT_TIMESTAMP, 0)")
```

- The "re-open an already-finished session for this day" branch is preserved — one training
  day is still one session, now for *any* day.

### 3.3 `get_live_session_state(day=None)`

- `apply_session_rollover()` still runs first (harmless for past days).
- Swap the `WHERE {day} = {today}` comparison to `_day_sql(day)`.
- Return shape unchanged. For a past day with no session, returns the same empty state.

### 3.4 `undo_last_today(exercise_id, session_id=None, day=None)`

- Swap `TODAY_SQL` → `_day_sql(day)`. Keep the `session_id` scoping exactly as-is (so undo on
  a backdated day only touches that day's session).

### 3.5 `get_previous_session_sets(exercise_id, limit=12, before=None)`

- `before` is the day we are logging for (defaults to today). The `{day} < {today}` filter
  becomes `{day} < {before-or-today}` via `_day_sql(before)`.
- `days_since` is computed relative to `before` (the chosen day), not the real today.

---

## 4. API changes — `gymtracker/app.py`

A shared parse step at the top of each affected endpoint:

```python
raw = (data.get('date') or '').strip() or request.args.get('date', '').strip()
day = _valid_training_day(raw) if raw else None
if raw and day is None:
    return {'ok': False, 'error': 'Invalid or future date'}, 400
```

| Endpoint | Change |
|---|---|
| `POST /api/gym/log-set` | parse `date`; `get_or_create_today_gym_session(day)`, `get_live_session_state(day)` |
| `POST /api/gym/log-set/undo` | parse `date`; `get_live_session_state(day)` + pass `day` to `undo_last_today` |
| `GET  /api/gym/live-session` | accept `?date=`; `get_live_session_state(day)` |
| `GET  /api/gym/exercise/<id>/prefill` | accept `?date=`; `get_previous_session_sets(id, before=day)` so PREV lines up with the chosen day |
| `POST /api/gym/log-cardio` | parse `date`; same session/day threading as log-set |

No change to `/train` (the page route) itself beyond handing the template `actual_today`
(ISO) so the client can clamp — see §5.

---

## 5. Frontend changes — `templates/train.html`

### 5.1 Date header (prev/next)

A compact header strip directly under `.tr-head`, mirroring the home date-nav:

```
‹   Mon 13 Oct   ›          [ Today ]
```

- `‹` = previous day (always enabled). `›` = next day (disabled when on today).
- Centre shows the selected day (`Today` when it is the real today, else `Ddd DD Mon`).
- A `[ Today ]` reset chip appears only when viewing a past day.
- Styling uses Pandora tokens only, consistent with `_week_strip.html`.

### 5.2 Client date state

- `let LOG_DATE = null;` — `null` means today (so all existing calls behave identically).
- `ACTUAL_TODAY` injected from the template as an ISO string for clamping.
- Changing the day:
  1. clamp to `<= ACTUAL_TODAY`;
  2. set `LOG_DATE` (null if equal to today);
  3. re-fetch `/api/gym/live-session?date=…` and re-render coverage + card;
  4. re-run prefill for the open exercise so the PREV column reflects the chosen day.

### 5.3 "Logging for a past day" banner

When `LOG_DATE` is non-null, show a persistent, high-contrast strip above the exercise card:

```
⚠  Logging for Mon 13 Oct — not today
```

so a backdated session is never entered by accident. Hidden entirely on today.

### 5.4 Thread `date` through every write/read

- `logSet`, `relogSet`, `logCardioManual`, `logTimedManual` → include `date: LOG_DATE` in the POST body (omitted/null on today).
- `removeSet` / undo → include `date: LOG_DATE`.
- `openExercise` prefill GET → append `?date=` when `LOG_DATE` set.
- `resyncLive` → append `?date=` when `LOG_DATE` set.
- Rest-timer, Fa Jin, finish-session: **unchanged** (a finished past day just marks that day's
  session finished; finish already targets the live session id returned for the chosen day).

---

## 6. Edge cases & decisions

- **Future dates:** blocked both client (disabled `›`, clamp) and server (`_valid_training_day`).
- **4 AM rollover:** backdated `performed_at` stamped at noon → unambiguous training day.
- **One day = one session:** re-open logic preserved for any day.
- **PREV column:** strictly-before-chosen-day (confirmed).
- **Undo safety:** stays session-scoped, so undo on a past day cannot reach today's session.
- **`get_today_tally`:** left on `TODAY_SQL` — not used by the Train live write path; out of scope.
- **Multi-profile:** gym_* tables carry no profile_id (single scheme-engine profile today) — unchanged.

---

## 7. Files touched

| File | Change |
|---|---|
| `gymtracker/gym_bank.py` | `_day_sql`, `_valid_training_day`; `day`/`before` params on 4 functions |
| `gymtracker/app.py` | parse+validate `date` on 5 endpoints; thread it through |
| `templates/train.html` | date header, client date state, past-day banner, `date` on all log/undo/rehydrate/prefill calls |

Each file backed up first using the existing `.bak-<tag>-<timestamp>` convention
(tag: `backdate`).

## 8. Test checklist (manual)

1. Log a set with no date → lands on today (unchanged).
2. Step back one day, log a set → new session stamped that day; appears on reload with `?date=`.
3. PREV column for the backdated exercise shows the outing before *that* day.
4. Undo on the past day removes only that day's set; today's session untouched.
5. `›` disabled on today; cannot POST a future date (400).
6. Home screen's week board / last-session reflects the backdated session (reads modern gym_* tables).
7. Finish session on a past day marks that day finished; re-logging re-opens it.
