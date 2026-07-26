"""
import_history.py — Reusable exercise history importer
======================================================
Parses a multi-day text format and inserts into gym_progression (lifts)
or session_cardio (cardio entries).

Format:
    DD/M/YY
    Exercise Name - SetsxReps @ Weight
    Cardio Name - DURmin @ Speed
    
    DD/M/YY
    ...

Examples:
    14/7/26
    DB Skullcrusher - 3x9 @ 30
    DB Curl - 4x6 @ 15
    Stairmaster - 10min @ 7
    
    16/7/26
    BB Squat - 4x5 @ 100
    Running - 25min

Rules:
- Dates are DD/M/YY (2-digit year assumed 2000s)
- Gym lifts: SetsxReps @ WeightKg → matched to gym_exercises by name
- Cardio: NUMmin @ Speed (or NUMmin alone) → matched to exercises table (tier 4)
- Blank lines separate days
- Exercise names are matched case-insensitively

Usage:
    python import_history.py < history.txt
    python import_history.py history.txt
    
Or call parse_history(text) / import_history(text) from code.
"""
import sqlite3
import re
import sys
import os
from datetime import date

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tracker.db')

# Patterns
DATE_RE = re.compile(r'^(\d{1,2})/(\d{1,2})/(\d{2})$')
LIFT_RE = re.compile(r'^(.+?)\s*-\s*(\d+)x(\d+)\s*@\s*([\d.]+)$')
CARDIO_RE = re.compile(r'^(.+?)\s*-\s*(\d+)\s*min(?:\s*@\s*([\d.]+))?$')


def parse_date(s):
    """Parse DD/M/YY → date object."""
    m = DATE_RE.match(s.strip())
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
    return date(year, month, day)


def parse_history(text):
    """Parse the text format into structured entries.
    
    Returns list of {'date': date, 'entries': [{'type': 'lift'|'cardio', ...}]}
    """
    days = []
    current_date = None
    current_entries = []

    for raw_line in text.split('\n'):
        line = raw_line.strip()
        if not line:
            continue

        # Try date
        d = parse_date(line)
        if d:
            if current_date and current_entries:
                days.append({'date': current_date, 'entries': current_entries})
            current_date = d
            current_entries = []
            continue

        if not current_date:
            continue  # skip lines before first date

        # Try lift: "Exercise - 3x9 @ 30"
        m = LIFT_RE.match(line)
        if m:
            current_entries.append({
                'type': 'lift',
                'name': m.group(1).strip(),
                'sets': int(m.group(2)),
                'reps': int(m.group(3)),
                'weight_kg': float(m.group(4)),
            })
            continue

        # Try cardio: "Stairmaster - 10min @ 7"
        m = CARDIO_RE.match(line)
        if m:
            current_entries.append({
                'type': 'cardio',
                'name': m.group(1).strip(),
                'duration_min': int(m.group(2)),
                'speed': float(m.group(3)) if m.group(3) else None,
            })
            continue

        print(f"  ⚠ Unparseable line on {current_date}: '{line}'")

    # Final day
    if current_date and current_entries:
        days.append({'date': current_date, 'entries': current_entries})

    return days


def run_import(text, profile_id=1, dry_run=False):
    """Parse and import history into the database.
    
    Returns a summary dict with counts.
    """
    days = parse_history(text)
    if not days:
        print("No data parsed.")
        return {'days': 0, 'lifts': 0, 'cardio': 0, 'errors': []}

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Also cache old exercises table (needed for session_lifts / analytics chart)
    old_ex_cache = {}  # name_lower → id
    for r in conn.execute("SELECT id, name FROM exercises"):
        old_ex_cache[r['name'].lower()] = r['id']

    # Cache exercise lookups
    gym_ex_cache = {}  # name_lower → id
    for r in conn.execute("SELECT id, name FROM gym_exercises"):
        gym_ex_cache[r['name'].lower()] = r['id']

    cardio_ex_cache = {}  # name_lower → id
    for r in conn.execute("SELECT id, name FROM exercises WHERE tier = 4 AND muscle_group = 'Cardio'"):
        cardio_ex_cache[r['name'].lower()] = r['id']

    lifts_imported = 0
    cardio_imported = 0
    errors = []
    # Track sessions created per date (for session_lifts)
    session_cache = {}  # date_iso → session_id

    for day in days:
        date_iso = day['date'].isoformat()
        print(f"\n📅 {date_iso} ({len(day['entries'])} entries)")

        for entry in day['entries']:
            if entry['type'] == 'lift':
                name_lower = entry['name'].lower()
                ex_id = gym_ex_cache.get(name_lower)
                if not ex_id:
                    errors.append(f"  ✗ No gym_exercise match: '{entry['name']}' on {date_iso}")
                    print(errors[-1])
                    continue

                if not dry_run:
                    # 1. Write to gym_progression (new progression engine)
                    conn.execute(
                        "INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, successful, recorded_at) "
                        "VALUES (?, ?, ?, ?, 1, ?)",
                        (ex_id, entry['weight_kg'], entry['sets'], entry['reps'], date_iso)
                    )

                    # 2. Write to session_lifts (for analytics volume chart)
                    old_ex_id = old_ex_cache.get(name_lower)
                    if not old_ex_id:
                        # Create in old exercises table too
                        old_ex_id = conn.execute(
                            "INSERT INTO exercises (name, tier, muscle_group, day_type) VALUES (?, ?, 'Uncategorised', 'any')",
                            (entry['name'], 1)
                        ).lastrowid
                        old_ex_cache[name_lower] = old_ex_id

                    # Get or create session for this date
                    if date_iso not in session_cache:
                        existing = conn.execute(
                            "SELECT id FROM sessions WHERE date=? AND type='gym' AND profile_id=?",
                            (date_iso, profile_id)
                        ).fetchone()
                        if existing:
                            session_cache[date_iso] = existing['id']
                        else:
                            session_cache[date_iso] = conn.execute(
                                "INSERT INTO sessions (date, type, profile_id, started_at) VALUES (?, 'gym', ?, ?)",
                                (date_iso, profile_id, date_iso + 'T00:00:00Z')
                            ).lastrowid

                    sess_id = session_cache[date_iso]
                    # Get next set_number for this exercise in this session
                    max_set = conn.execute(
                        "SELECT COALESCE(MAX(set_number), 0) AS mx FROM session_lifts WHERE session_id=? AND exercise_id=?",
                        (sess_id, old_ex_id)
                    ).fetchone()['mx']
                    # Insert one row per set
                    for s in range(1, entry['sets'] + 1):
                        conn.execute(
                            "INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)",
                            (sess_id, old_ex_id, max_set + s, entry['reps'], entry['weight_kg'])
                        )

                print(f"  ✓ {entry['name']}: {entry['sets']}×{entry['reps']} @ {entry['weight_kg']}kg")
                lifts_imported += 1

            elif entry['type'] == 'cardio':
                name_lower = entry['name'].lower()
                ex_id = cardio_ex_cache.get(name_lower)
                if not ex_id:
                    # Auto-create the exercise
                    if not dry_run:
                        ex_id = conn.execute(
                            "INSERT INTO exercises (name, tier, muscle_group, day_type, cardio_metrics) "
                            "VALUES (?, 4, 'Cardio', 'any', ?)",
                            (entry['name'], '{"time":true}')
                        ).lastrowid
                        cardio_ex_cache[name_lower] = ex_id
                    print(f"  + Created cardio exercise: '{entry['name']}'")

                if not dry_run and ex_id:
                    # Create a cardio session
                    sess_id = conn.execute(
                        "INSERT INTO sessions (date, type, profile_id, started_at) "
                        "VALUES (?, 'cardio', ?, ?)",
                        (date_iso, profile_id, date_iso + 'T00:00:00Z')
                    ).lastrowid
                    conn.execute(
                        "INSERT INTO session_cardio (session_id, exercise_id, duration_s, speed, done, created_at) "
                        "VALUES (?, ?, ?, ?, 1, ?)",
                        (sess_id, ex_id, entry['duration_min'] * 60, entry['speed'], date_iso)
                    )
                print(f"  ✓ {entry['name']}: {entry['duration_min']}min"
                      + (f" @ speed {entry['speed']}" if entry['speed'] else ""))
                cardio_imported += 1

    if not dry_run:
        conn.commit()
    conn.close()

    summary = {'days': len(days), 'lifts': lifts_imported, 'cardio': cardio_imported, 'errors': errors}
    print(f"\n{'🧪 DRY RUN' if dry_run else '✅'} Done: {summary['days']} days, "
          f"{summary['lifts']} lifts, {summary['cardio']} cardio, {len(errors)} errors")
    return summary


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--dry-run':
        dry = True
        src = sys.argv[2] if len(sys.argv) > 2 else None
    elif len(sys.argv) > 1 and sys.argv[1] != '-':
        dry = False
        src = sys.argv[1]
    else:
        dry = False
        src = None

    if src:
        with open(src) as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    run_import(text, dry_run=dry)


# Alias for backward compat
import_history = run_import
