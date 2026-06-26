import json as _json
import os as _os
import re as _re
import secrets as _secrets
from urllib.parse import quote as _urlquote
from datetime import date as date_type, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, abort
from datetime import datetime as _datetime
from database import (init_db, get_db, get_tier1_suggestion,
                       get_tier4_exercises, get_session_cardio, get_t1_last_weights,
                       get_t1_exercises_with_activity,
                       get_muscle_group_activity, get_exercise_decay,
                       get_progression, advance_progression,
                       set_progression_weight, get_all_progressions, get_schemes,
                       record_stage_completion, record_exercise_attempt,
                       get_sessions_with_headlines,
                       get_session_detail_with_progression,
                       get_macro_goals, set_macro_goals,
                       sync_food_log_from_library,
                       get_food_log, add_food_entry, delete_food_entry,
                       get_pending_foods, define_food_item, delete_food_item, get_food_history,
                       get_food_library, get_food_components, save_food_components,
                       get_profiles, _parse_qty_name, _parse_gram_prefix,
                       log_food_reconciliation, get_food_reconciliations,
                       log_body_weight, get_body_weight, get_body_weight_history,
                       get_pilates_session)
from swim_routes import bp as swim_bp
from pilates_routes import bp as pilates_bp

app = Flask(__name__)
app.secret_key = _os.environ.get('SECRET_KEY', 'gymtracker-local-secret-key')
app.jinja_env.filters['enumerate'] = enumerate
app.jinja_env.filters['fromjson']  = _json.loads
app.jinja_env.filters['urlencode'] = lambda s: _urlquote(str(s), safe='')
app.jinja_env.filters['strip_food_prefix'] = lambda s: _re.sub(
    r'^\d+(?:\.\d+)?\s*(?:g|ml|kg|l|oz|x)\s+', '', s, flags=_re.IGNORECASE).strip()

_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']

_FUZZY_QTY_RE = _re.compile(r'^(\d+(?:\.\d+)?)\s+(g|ml|kg|l|oz|lb)\b', _re.IGNORECASE)

def _normalize_food_name(name):
    """Collapse '<number> <unit>' → '<number><unit>'. Returns (normalized, changed)."""
    normalized = _FUZZY_QTY_RE.sub(r'\1\2', name.strip(), count=1)
    return normalized, normalized != name.strip()



_DAYS   = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

def _fmt_date(iso_str):
    """'2026-05-22' → 'Fri 22 May'"""
    try:
        from datetime import date as _d
        d = _d.fromisoformat(iso_str)
        return f"{_DAYS[d.weekday()]} {d.day} {_MONTHS[d.month-1]}"
    except Exception:
        return iso_str

app.jinja_env.filters['fmt_date'] = _fmt_date
app.register_blueprint(swim_bp)
app.register_blueprint(pilates_bp)


def _csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = _secrets.token_hex(32)
    return session['csrf_token']

app.jinja_env.globals['csrf_token'] = _csrf_token


def _validate_date(date_str, fallback=None):
    try:
        date_type.fromisoformat(str(date_str))
        return str(date_str)
    except (ValueError, TypeError):
        return fallback if fallback is not None else date_type.today().isoformat()


def _profile_id():
    return session.get('profile_id', 1)


@app.context_processor
def inject_profile():
    pid = session.get('profile_id')
    if pid:
        db = get_db()
        profile = db.execute('SELECT * FROM profiles WHERE id=?', (pid,)).fetchone()
        db.close()
        return {'current_profile': dict(profile) if profile else None}
    return {'current_profile': None}


_GAYATHRI_BLOCKED = {
    'session_new', 'analytics', 'progression',
    'progression_advance', 'progression_set_weight', 'exercises', 'exercise_delete',
}

_GAYATHRI_ROUTINES = {
    'A': {
        'name': 'Workout A',
        'location': 'Home',
        'exercises': [
            {'name': 'Incline Pushups',       'reps': 8,  'sets': 3, 'timed': False, 'weighted': False, 'muscle_group': 'Chest',    'reps_range': [6,  12], 'sets_range': [2, 4]},
            {'name': 'Incline Reverse Plank', 'reps': 30, 'sets': 2, 'timed': True,  'weighted': False, 'muscle_group': 'Core',     'reps_range': [20, 45], 'sets_range': [2, 3]},
            {'name': 'Neck Rolls',            'reps': 30, 'sets': 2, 'timed': True,  'weighted': False, 'muscle_group': 'Mobility', 'reps_range': [20, 45], 'sets_range': [1, 2]},
            {'name': 'Box Squats',            'reps': 10, 'sets': 3, 'timed': False, 'weighted': False, 'muscle_group': 'Legs',     'reps_range': [8,  15], 'sets_range': [2, 4]},
            {'name': 'Hip Flexor Stretch',    'reps': 30, 'sets': 2, 'timed': True,  'weighted': False, 'muscle_group': 'Mobility', 'reps_range': [20, 60], 'sets_range': [2, 3]},
        ]
    },
    'B': {
        'name': 'Workout B',
        'location': 'Gym',
        'warmup': '20 min walk',
        'exercises': [
            {'name': 'Machine Chest Press', 'reps': 8, 'sets': 3, 'timed': False, 'weighted': True,  'muscle_group': 'Chest', 'reps_range': [6, 10], 'sets_range': [2, 3]},
            {'name': 'Cable Row',           'reps': 8, 'sets': 2, 'timed': False, 'weighted': True,  'muscle_group': 'Back',  'reps_range': [6, 10], 'sets_range': [2, 3]},
            {'name': 'Lat Pulldown',        'reps': 8, 'sets': 2, 'timed': False, 'weighted': True,  'muscle_group': 'Back',  'reps_range': [6, 10], 'sets_range': [2, 3]},
            {'name': 'Leg Extensions',      'reps': 8, 'sets': 3, 'timed': False, 'weighted': True,  'muscle_group': 'Legs',  'reps_range': [8, 15], 'sets_range': [2, 4]},
        ]
    },
}

_GAYATHRI_CARDIO = [
    {'name': 'Walk',          'desc': '20–30 min, steady pace'},
    {'name': 'Treadmill Jog', 'desc': '15–20 min, light jog'},
    {'name': 'Cycling',       'desc': '20–30 min, moderate effort'},
    {'name': 'Elliptical',    'desc': '20–25 min'},
]

_GAYATHRI_EXTRA_EXERCISES = [
    {'name': 'Bicep Curls', 'muscle_group': 'Arms', 'location': 'home',
     'reps': 10, 'sets': 3, 'timed': False, 'weighted': False,
     'reps_range': [8, 15], 'sets_range': [2, 4]},
    {'name': 'Body Dips',   'muscle_group': 'Arms', 'location': 'home',
     'reps': 8,  'sets': 3, 'timed': False, 'weighted': False,
     'reps_range': [6, 12], 'sets_range': [2, 3]},
    {'name': 'Squats',      'muscle_group': 'Legs', 'location': 'home',
     'reps': 12, 'sets': 3, 'timed': False, 'weighted': False,
     'reps_range': [8, 20], 'sets_range': [2, 4]},
]

_CSRF_EXEMPT = {'static', 'profiles', 'profiles_select', None}

@app.before_request
def require_profile():
    if request.endpoint in (None, 'static', 'profiles', 'profiles_select'):
        return None

    # CSRF check for all mutating requests
    if request.method == 'POST':
        token = session.get('csrf_token')
        if token:
            # JSON API calls send token in header; form submissions in hidden field
            submitted = (request.headers.get('X-CSRF-Token')
                         or request.form.get('_csrf'))
            if not submitted or not _secrets.compare_digest(token, submitted):
                abort(403)

    if 'profile_id' not in session:
        return redirect(url_for('profiles'))
    ep = request.endpoint or ''
    if session['profile_id'] == 2 and (ep in _GAYATHRI_BLOCKED or ep.startswith('swim.') or ep.startswith('pilates.')):
        return redirect(url_for('food'))


def get_activity_calendar():
    """Return {date_iso: [type, ...]} for the past ~54 weeks."""
    db = get_db()
    rows = db.execute(
        "SELECT date, type FROM sessions WHERE date >= date('now','-371 days') AND profile_id = ? ORDER BY date",
        (_profile_id(),)
    ).fetchall()
    db.close()
    cal = {}
    for r in rows:
        d = r['date']
        if d not in cal:
            cal[d] = []
        if r['type'] not in cal[d]:
            cal[d].append(r['type'])
    return cal


@app.route('/profiles')
def profiles():
    return render_template('profiles.html', profiles=get_profiles())


@app.route('/profiles/select', methods=['POST'])
def profiles_select():
    pid_str = request.form.get('profile_id', '').strip()
    if not pid_str.isdigit():
        return redirect(url_for('profiles'))
    pid = int(pid_str)
    db = get_db()
    row = db.execute('SELECT id FROM profiles WHERE id=?', (pid,)).fetchone()
    db.close()
    if not row:
        return redirect(url_for('profiles'))
    session['profile_id'] = pid
    return redirect(url_for('dashboard'))


@app.route('/')
def dashboard():
    if _profile_id() == 2:
        today     = date_type.today()
        today_iso = today.isoformat()
        monday    = (today - timedelta(days=today.weekday())).isoformat()
        db = get_db()
        food_row = db.execute(
            'SELECT COALESCE(SUM(calories),0) AS cal, COALESCE(SUM(protein_g),0) AS prot '
            'FROM food_log WHERE date=? AND profile_id=?',
            (today_iso, _profile_id())
        ).fetchone()
        total_cal  = round(food_row['cal'])
        total_prot = round(food_row['prot'])
        goals = get_macro_goals(_profile_id())
        today_weight   = get_body_weight(today_iso, _profile_id())
        weight_history = get_body_weight_history(_profile_id(), limit=30)
        week_rows = db.execute(
            "SELECT DISTINCT date FROM sessions WHERE date BETWEEN ? AND ? AND profile_id=? AND type='gym'",
            (monday, today_iso, _profile_id())
        ).fetchall()
        week_active  = {r['date'] for r in week_rows}
        monday_date  = today - timedelta(days=today.weekday())
        week_days = []
        for i in range(7):
            day = monday_date + timedelta(days=i)
            week_days.append({
                'iso':       day.isoformat(),
                'day_short': day.strftime('%a'),
                'day_num':   day.day,
                'is_today':  day == today,
                'is_past':   day < today,
                'active':    day.isoformat() in week_active,
            })
        last_sess = db.execute("""
            SELECT s.id, s.date, s.notes,
                   GROUP_CONCAT(DISTINCT e.name) AS exercise_names,
                   COUNT(DISTINCT sl.id) AS total_sets
            FROM sessions s
            LEFT JOIN session_lifts sl ON sl.session_id = s.id
            LEFT JOIN exercises e ON e.id = sl.exercise_id
            WHERE s.profile_id = ? AND s.type = 'gym'
            GROUP BY s.id ORDER BY s.date DESC, s.id DESC LIMIT 1
        """, (_profile_id(),)).fetchone()
        db.close()
        return render_template('home_gayathri.html',
                               today=today_iso,
                               total_cal=total_cal, total_prot=total_prot,
                               goals=goals,
                               today_weight=today_weight,
                               weight_history=[dict(r) for r in (weight_history or [])],
                               week_days=week_days,
                               last_session=dict(last_sess) if last_sess else None)

    today      = date_type.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    db = get_db()

    # 1. Last session with aggregate stats
    last_session = db.execute("""
        SELECT s.*,
               COALESCE(sw_agg.distance_m, 0) AS distance_m,
               COALESCE(agg.total_sets,   0)   AS total_sets,
               COALESCE(agg.total_volume, 0.0) AS total_volume
        FROM sessions s
        LEFT JOIN (
            SELECT session_id, SUM(distance_m) AS distance_m
            FROM swim_logs GROUP BY session_id
        ) sw_agg ON sw_agg.session_id = s.id
        LEFT JOIN (
            SELECT session_id,
                   COUNT(*)              AS total_sets,
                   SUM(reps * weight_kg) AS total_volume
            FROM session_lifts
            GROUP BY session_id
        ) agg ON agg.session_id = s.id
        ORDER BY s.date DESC, s.id DESC
        LIMIT 1
    """).fetchone()

    # 2. Weekly gym volume (current Mon–today)
    weekly_volume = db.execute("""
        SELECT COALESCE(SUM(sl.reps * sl.weight_kg), 0) AS vol
        FROM session_lifts sl
        JOIN sessions s ON s.id = sl.session_id
        WHERE s.type = 'gym' AND s.date >= ? AND s.profile_id = ?
    """, (week_start, _profile_id())).fetchone()['vol']

    # 3. Weekly swim distance
    weekly_swim = db.execute("""
        SELECT COALESCE(SUM(sw.distance_m), 0) AS dist
        FROM swim_logs sw
        JOIN sessions s ON s.id = sw.session_id
        WHERE s.date >= ? AND s.profile_id = ?
    """, (week_start, _profile_id())).fetchone()['dist']

    # 4. All T1 exercises with activity data for the Priorities panel
    t1_exercises = get_t1_exercises_with_activity()

    # 5. Top 5 heaviest sets ever
    prs = db.execute("""
        SELECT e.name, sl.weight_kg, sl.reps, s.date
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        JOIN sessions  s ON s.id = sl.session_id
        WHERE s.profile_id = ?
        ORDER BY sl.weight_kg DESC
        LIMIT 5
    """, (_profile_id(),)).fetchall()

    # 6. Today's body weight
    today_weight = get_body_weight(today.isoformat(), _profile_id())

    # 7. Week calendar (Mon–Sun of current week, per-day activity)
    monday = today - timedelta(days=today.weekday())
    week_session_rows = db.execute(
        "SELECT date, type FROM sessions WHERE date BETWEEN ? AND ? AND profile_id = ?",
        (monday.isoformat(), (monday + timedelta(days=6)).isoformat(), _profile_id())
    ).fetchall()
    db.close()

    week_activity = {}
    for r in week_session_rows:
        d = r['date']
        if d not in week_activity:
            week_activity[d] = []
        if r['type'] not in week_activity[d]:
            week_activity[d].append(r['type'])

    week_days = []
    for i in range(7):
        day = monday + timedelta(days=i)
        day_iso = day.isoformat()
        week_days.append({
            'date':       day_iso,
            'day_short':  day.strftime('%a'),
            'day_num':    day.day,
            'is_today':   day == today,
            'is_past':    day < today,
            'activities': week_activity.get(day_iso, []),
        })

    return render_template('dashboard.html',
                           last_session=last_session,
                           weekly_volume=weekly_volume,
                           weekly_swim=weekly_swim,
                           t1_exercises_json=_json.dumps(t1_exercises),
                           prs=prs,
                           today=today.isoformat(),
                           week_days=week_days,
                           heatmap_json=_json.dumps(get_activity_calendar()),
                           today_weight=today_weight)


@app.route('/session/new', methods=['GET', 'POST'])
def session_new():
    if request.method == 'POST':
        date_val  = _validate_date(request.form.get('date', ''))
        notes     = request.form.get('notes', '').strip() or None
        day_type  = request.form.get('day_type', '').strip() or None

        db = get_db()

        # Continue-session mode: append to existing session instead of creating new
        continue_id_str = request.form.get('continue_session_id', '').strip()
        continuing = False
        existing_max_sets = {}
        if continue_id_str and continue_id_str.isdigit():
            csid = int(continue_id_str)
            existing = db.execute(
                'SELECT * FROM sessions WHERE id=? AND date=? AND type="gym" AND profile_id=?',
                (csid, date_val, _profile_id())
            ).fetchone()
            if existing:
                session_id = csid
                continuing = True
                for row in db.execute(
                    'SELECT exercise_id, MAX(set_number) as mx '
                    'FROM session_lifts WHERE session_id=? GROUP BY exercise_id',
                    (session_id,)
                ).fetchall():
                    existing_max_sets[row['exercise_id']] = row['mx']

        if not continuing:
            session_id = db.execute(
                'INSERT INTO sessions (date, type, day_type, notes, started_at, profile_id) VALUES (?,?,?,?,?,?)',
                (date_val, 'gym', day_type, notes, _datetime.utcnow().isoformat() + 'Z', _profile_id())
            ).lastrowid

        scheme_completions = []  # (ex_id, scheme_id)
        attempt_records    = []  # (ex_id, scheme_id, result, sets_done, weight_kg)
        any_lifts          = False

        for key in request.form:
            if key.startswith('exercise_id_'):
                idx           = key[len('exercise_id_'):]
                ex_id_str     = request.form[key]
                if not ex_id_str.strip():
                    continue
                ex_id         = int(ex_id_str)
                scheme_id_str = request.form.get(f'scheme_id_{idx}', '').strip()
                set_offset    = existing_max_sets.get(ex_id, 0)

                if scheme_id_str:
                    prog_weight_str = request.form.get(f'prog_weight_{idx}', '').strip()
                    if not prog_weight_str:
                        continue
                    prog_weight = float(prog_weight_str)
                    if prog_weight <= 0:
                        continue
                    scheme = db.execute('SELECT * FROM schemes WHERE id=?',
                                        (int(scheme_id_str),)).fetchone()
                    if not scheme:
                        continue
                    result_str = request.form.get(f'ex_result_{idx}', '').strip()
                    tally_str  = request.form.get(f'ex_tally_{idx}',  '0').strip()
                    sets_done  = int(tally_str) if tally_str.isdigit() else scheme['sets']
                    sets_done  = max(0, min(sets_done, scheme['sets']))
                    log_sets   = sets_done if result_str in ('completed', 'failed') else scheme['sets']
                    for set_num in range(1, log_sets + 1):
                        db.execute(
                            'INSERT INTO session_lifts '
                            '(session_id, exercise_id, set_number, reps, weight_kg) '
                            'VALUES (?,?,?,?,?)',
                            (session_id, ex_id, set_num + set_offset, scheme['reps'], prog_weight)
                        )
                    # Partial last set — reps user recorded on the set they failed to complete
                    if result_str == 'failed':
                        last_reps_str = request.form.get(f'ex_last_reps_{idx}', '0').strip()
                        last_reps = int(last_reps_str) if last_reps_str.isdigit() else 0
                        if 0 < last_reps < scheme['reps']:
                            db.execute(
                                'INSERT INTO session_lifts '
                                '(session_id, exercise_id, set_number, reps, weight_kg) '
                                'VALUES (?,?,?,?,?)',
                                (session_id, ex_id, log_sets + 1 + set_offset, last_reps, prog_weight)
                            )
                        any_lifts = True
                    if result_str == 'completed':
                        any_lifts = True
                        scheme_completions.append((ex_id, int(scheme_id_str)))
                    if result_str in ('completed', 'failed'):
                        attempt_records.append((ex_id, int(scheme_id_str), result_str, sets_done, prog_weight))
                else:
                    reps_list = request.form.getlist(f'reps_{idx}')
                    wt_list   = request.form.getlist(f'weight_{idx}')
                    for i, (r, w) in enumerate(zip(reps_list, wt_list), start=1):
                        try:
                            reps = int(r.strip())
                            kg   = float(w.strip())
                        except (ValueError, AttributeError):
                            continue
                        db.execute(
                            'INSERT INTO session_lifts '
                            '(session_id, exercise_id, set_number, reps, weight_kg) '
                            'VALUES (?,?,?,?,?)',
                            (session_id, ex_id, i + set_offset, reps, kg)
                        )
                        any_lifts = True

        # ── Tier 4 warmup / cardio entries ─────────────────────────────
        for key in list(request.form):
            if not key.startswith('cardio_exercise_id_'):
                continue
            suffix = key[len('cardio_exercise_id_'):]
            cex_str = request.form.get(key, '').strip()
            if not cex_str.isdigit():
                continue
            cex_id = int(cex_str)
            exrow = db.execute(
                'SELECT cardio_metrics FROM exercises WHERE id=? AND tier=4', (cex_id,)
            ).fetchone()
            if not exrow:
                continue
            try:
                spec = _json.loads(exrow['cardio_metrics'] or '{}')
            except Exception:
                spec = {}

            distance_m = duration_s = resistance = None
            done = 1 if request.form.get(f'cardio_done_{suffix}', '').strip() in ('1', 'true', 'on') else 0

            dist_raw = request.form.get(f'cardio_distance_{suffix}', '').strip()
            if dist_raw:
                try:
                    dv = float(dist_raw)
                    if dv > 0:
                        distance_m = dv * 1000 if spec.get('distance') == 'km' else dv
                except ValueError:
                    pass
            dur_raw = request.form.get(f'cardio_duration_{suffix}', '').strip()
            if dur_raw:
                try:
                    mv = float(dur_raw)
                    if mv > 0:
                        duration_s = int(round(mv * 60))
                except ValueError:
                    pass
            res_raw = request.form.get(f'cardio_resistance_{suffix}', '').strip()
            if res_raw:
                try:
                    resistance = float(res_raw)
                except ValueError:
                    pass

            if distance_m is None and duration_s is None and resistance is None and not done:
                continue
            db.execute(
                'INSERT INTO session_cardio '
                '(session_id, exercise_id, distance_m, duration_s, resistance, done, created_at) '
                'VALUES (?,?,?,?,?,?,?)',
                (session_id, cex_id, distance_m, duration_s, resistance, done,
                 _datetime.utcnow().isoformat() + 'Z')
            )
            any_lifts = True

        if not any_lifts:
            if continuing:
                # Session already has lifts — just redirect to it
                db.close()
                return redirect(url_for('session_detail', session_id=session_id))
            db.rollback()
            db.close()
            return redirect(url_for('session_new', date=date_val))

        # Record progression + attempts in the same connection before the single commit
        for ex_id, scheme_id in scheme_completions:
            record_stage_completion(ex_id, scheme_id, conn=db)
        for ex_id, scheme_id, result_str, sets_done, wkg in attempt_records:
            record_exercise_attempt(ex_id, scheme_id, result_str, sets_done, date_val, conn=db, weight_kg=wkg)
        db.execute(
            'UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL',
            (_datetime.utcnow().isoformat() + 'Z', session_id)
        )
        db.commit()
        db.close()
        return redirect(url_for('session_detail', session_id=session_id))

    # GET ---------------------------------------------------------------
    today_iso    = date_type.today().isoformat()
    session_date = request.args.get('date', today_iso)
    tier1_list   = get_tier1_suggestion()

    db = get_db()

    # Look for an existing gym session today to offer "continue" option
    today_gym_rows = db.execute(
        """SELECT s.id, s.day_type,
                  GROUP_CONCAT(DISTINCT e.name) AS exercise_names
           FROM sessions s
           LEFT JOIN session_lifts sl ON sl.session_id = s.id
           LEFT JOIN exercises e      ON e.id = sl.exercise_id
           WHERE s.date = ? AND s.type = 'gym' AND s.profile_id = ?
           GROUP BY s.id ORDER BY s.id DESC""",
        (today_iso, _profile_id())
    ).fetchall()
    today_sessions = [dict(r) for r in today_gym_rows]

    # Continue-session mode
    continue_session = None
    continue_arg = request.args.get('continue', '').strip()
    if continue_arg.isdigit():
        csid = int(continue_arg)
        cs = db.execute('SELECT * FROM sessions WHERE id=? AND date=? AND type="gym" AND profile_id=?',
                        (csid, today_iso, _profile_id())).fetchone()
        if cs:
            done_exs = db.execute(
                """SELECT e.id, e.name, e.tier, e.muscle_group,
                          COUNT(sl.id)      AS sets,
                          MAX(sl.reps)      AS reps,
                          MAX(sl.weight_kg) AS weight_kg
                   FROM session_lifts sl JOIN exercises e ON e.id = sl.exercise_id
                   WHERE sl.session_id = ?
                   GROUP BY sl.exercise_id
                   ORDER BY e.tier, e.name""",
                (csid,)
            ).fetchall()
            continue_session = {
                'id':        cs['id'],
                'day_type':  cs['day_type'],
                'exercises': [{'id': r['id'], 'name': r['name'], 'tier': r['tier'],
                               'muscle_group': r['muscle_group'],
                               'sets': r['sets'], 'reps': r['reps'],
                               'weight_kg': r['weight_kg']} for r in done_exs],
            }

    today_iso = date_type.today().isoformat()
    t2_rows = db.execute("""
        SELECT e.id, e.name, e.muscle_group, e.day_type,
               MAX(s.date) AS last_performed
        FROM exercises e
        LEFT JOIN session_lifts sl ON sl.exercise_id = e.id
        LEFT JOIN sessions      s  ON s.id = sl.session_id AND s.type = 'gym'
        WHERE e.tier = 2
        GROUP BY e.id
        ORDER BY e.muscle_group, e.name
    """).fetchall()
    tier2 = []
    for row in t2_rows:
        lp = row['last_performed']
        delta = (date_type.fromisoformat(today_iso) - date_type.fromisoformat(lp)).days if lp else None
        tier2.append({
            'id': row['id'], 'name': row['name'],
            'muscle_group': row['muscle_group'], 'day_type': row['day_type'],
            'days_since': delta,
        })

    today_iso = date_type.today().isoformat()
    t3_rows = db.execute("""
        SELECT
            e.id, e.name, e.muscle_group, e.reps_only,
            MAX(s.date) AS last_performed
        FROM exercises e
        LEFT JOIN session_lifts sl ON sl.exercise_id = e.id
        LEFT JOIN sessions      s  ON s.id = sl.session_id AND s.type = 'gym'
        WHERE e.tier = 3
        GROUP BY e.id
        ORDER BY e.muscle_group, last_performed ASC NULLS FIRST
    """).fetchall()
    db.close()

    tier3 = []
    for row in t3_rows:
        lp = row['last_performed']
        delta = (date_type.fromisoformat(today_iso) - date_type.fromisoformat(lp)).days if lp else None
        tier3.append({
            'id': row['id'], 'name': row['name'],
            'muscle_group': row['muscle_group'], 'reps_only': row['reps_only'],
            'days_since': delta,
        })

    # Unified progression dict: exercise_id → prog for all T1 + T2
    progs = {}
    for ex in tier1_list:
        p = get_progression(ex['id'], 1)
        if p:
            progs[str(ex['id'])] = p
    for ex in tier2:
        p = get_progression(ex['id'], 2)
        if p:
            progs[str(ex['id'])] = p
    for ex in tier3:
        p = get_progression(ex['id'], 3)
        if p:
            progs[str(ex['id'])] = p

    t1lw = get_t1_last_weights()
    tier1_json   = _json.dumps([
        {'id': ex['id'], 'name': ex['name'],
         'muscle_group': ex['muscle_group'], 'days_since': ex['days_since'],
         'is_barbell': bool(ex['is_barbell']), 'reps_only': bool(ex['reps_only']),
         'last_weight': t1lw.get(ex['id'], {}).get('last_weight'),
         'is_pr': t1lw.get(ex['id'], {}).get('is_pr', False)}
        for ex in tier1_list
    ])
    tier2_json   = _json.dumps([
        {'id': ex['id'], 'name': ex['name'],
         'muscle_group': ex['muscle_group'], 'day_type': ex['day_type'],
         'days_since': ex['days_since']}
        for ex in tier2
    ])
    tier4_list   = get_tier4_exercises()
    tier4_json   = _json.dumps(tier4_list)
    _schemes_dict = {str(t): get_schemes(t) for t in (1, 2, 3)}
    # Add exercise-specific scheme overrides keyed as 'ex_<id>'
    _all_ex = get_db()
    for _row in _all_ex.execute(
        'SELECT DISTINCT exercise_id FROM schemes WHERE exercise_id IS NOT NULL'
    ).fetchall():
        _eid = _row['exercise_id']
        _ex_row = _all_ex.execute('SELECT tier FROM exercises WHERE id=?', (_eid,)).fetchone()
        if _ex_row:
            _ex_schemes = get_schemes(_ex_row['tier'], _eid)
            if _ex_schemes:
                _schemes_dict[f'ex_{_eid}'] = _ex_schemes
    _all_ex.close()
    schemes_json = _json.dumps(_schemes_dict)
    tier3_json   = _json.dumps([
        {'id': ex['id'], 'name': ex['name'],
         'muscle_group': ex['muscle_group'], 'days_since': ex['days_since']}
        for ex in tier3
    ])

    return render_template('session_new.html',
                           session_date=session_date,
                           tier1_list=tier1_list,
                           tier2=tier2,
                           tier3=tier3,
                           progs_json=_json.dumps(progs),
                           tier1_json=tier1_json,
                           tier2_json=tier2_json,
                           tier3_json=tier3_json,
                           tier4_json=tier4_json,
                           schemes_json=schemes_json,
                           today_sessions=today_sessions,
                           continue_session=continue_session)


def _gayathri_by_muscle():
    seen = set()
    by_muscle = {}
    _MG_ORDER = ['Chest', 'Back', 'Arms', 'Legs', 'Core', 'Mobility']
    for mg in _MG_ORDER:
        by_muscle[mg] = []
    for routine in _GAYATHRI_ROUTINES.values():
        loc = routine.get('location', 'Home').lower()
        for ex in routine['exercises']:
            key = ex['name']
            if key not in seen:
                seen.add(key)
                mg = ex.get('muscle_group', 'Other')
                if mg not in by_muscle:
                    by_muscle[mg] = []
                by_muscle[mg].append({**ex, 'location': loc})
    for ex in _GAYATHRI_EXTRA_EXERCISES:
        key = ex['name']
        if key not in seen:
            seen.add(key)
            mg = ex['muscle_group']
            if mg not in by_muscle:
                by_muscle[mg] = []
            by_muscle[mg].append(ex)
    return by_muscle


@app.route('/workout/log/new')
def workout_log_new():
    if _profile_id() != 2:
        return redirect(url_for('dashboard'))
    today_iso = date_type.today().isoformat()
    db = get_db()
    today_gym_rows = db.execute("""
        SELECT s.id, GROUP_CONCAT(DISTINCT e.name) AS exercise_names
        FROM sessions s
        LEFT JOIN session_lifts sl ON sl.session_id = s.id
        LEFT JOIN exercises e ON e.id = sl.exercise_id
        WHERE s.date = ? AND s.type = 'gym' AND s.profile_id = ?
        GROUP BY s.id ORDER BY s.id DESC
    """, (today_iso, _profile_id())).fetchall()
    today_sessions = [dict(r) for r in today_gym_rows]

    continue_session = None
    continue_arg = request.args.get('continue', '').strip()
    if continue_arg.isdigit():
        csid = int(continue_arg)
        cs = db.execute(
            'SELECT * FROM sessions WHERE id=? AND date=? AND type="gym" AND profile_id=?',
            (csid, today_iso, _profile_id())
        ).fetchone()
        if cs:
            done_exs = db.execute("""
                SELECT e.name, COUNT(sl.id) AS sets, MAX(sl.reps) AS reps
                FROM session_lifts sl JOIN exercises e ON e.id = sl.exercise_id
                WHERE sl.session_id = ? GROUP BY sl.exercise_id ORDER BY MIN(sl.id)
            """, (csid,)).fetchall()
            continue_session = {
                'id': cs['id'],
                'exercises': [{'name': r['name'], 'sets': r['sets'], 'reps': r['reps']}
                              for r in done_exs],
            }
    db.close()
    return render_template('workout_log_new.html',
                           today=today_iso,
                           by_muscle=_gayathri_by_muscle(),
                           cardio=_GAYATHRI_CARDIO,
                           today_sessions=today_sessions,
                           continue_session=continue_session)


@app.route('/workout')
def workout():
    if _profile_id() != 2:
        return redirect(url_for('dashboard'))
    today = date_type.today().isoformat()
    done  = request.args.get('done') == '1'
    tab   = request.args.get('tab', 'routines')
    if tab not in ('routines', 'exercises'):
        tab = 'routines'

    return render_template('workout.html',
                           routines=_GAYATHRI_ROUTINES,
                           today=today, done=done,
                           tab=tab, by_muscle=_gayathri_by_muscle(),
                           cardio=_GAYATHRI_CARDIO)


@app.route('/workout/log', methods=['POST'])
def workout_log():
    if _profile_id() != 2:
        return redirect(url_for('dashboard'))
    routine_key = request.form.get('routine', '').strip()
    date_val    = request.form.get('date', date_type.today().isoformat()).strip()
    routine     = _GAYATHRI_ROUTINES.get(routine_key)
    if not routine:
        return redirect(url_for('workout'))

    db      = get_db()
    warmup  = routine.get('warmup')
    notes   = ('Warmup: ' + warmup) if warmup else None
    sess_id = db.execute(
        'INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
        (date_val, 'gym', notes, _datetime.utcnow().isoformat() + 'Z', _profile_id())
    ).lastrowid

    any_lifts = False
    for i, ex_spec in enumerate(routine['exercises']):
        ex = db.execute('SELECT id FROM exercises WHERE name=? COLLATE NOCASE',
                        (ex_spec['name'],)).fetchone()
        if not ex:
            continue
        weight = 0.0
        if ex_spec['weighted']:
            w_str = request.form.get(f'weight_{i}', '').strip()
            try:
                weight = float(w_str) if w_str else 0.0
            except ValueError:
                weight = 0.0
        for set_num in range(1, ex_spec['sets'] + 1):
            db.execute(
                'INSERT INTO session_lifts '
                '(session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                (sess_id, ex['id'], set_num, ex_spec['reps'], weight)
            )
            any_lifts = True

    if not any_lifts:
        db.rollback()
        db.close()
        return redirect(url_for('workout'))

    db.execute(
        'UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL',
        (_datetime.utcnow().isoformat() + 'Z', sess_id)
    )
    db.commit()
    db.close()
    return redirect(url_for('workout', done=1))


@app.route('/workout/custom/log', methods=['POST'])
def workout_custom_log():
    if _profile_id() != 2:
        return redirect(url_for('dashboard'))
    date_val = _validate_date(request.form.get('date', ''))
    ex_names = request.form.getlist('ex_name')
    ex_reps  = request.form.getlist('ex_reps')
    ex_sets  = request.form.getlist('ex_sets')

    db = get_db()

    # Continue-session mode: append to existing session
    continuing = False
    existing_max_sets = {}
    continue_id_str = request.form.get('continue_session_id', '').strip()
    if continue_id_str and continue_id_str.isdigit():
        csid = int(continue_id_str)
        existing = db.execute(
            'SELECT id FROM sessions WHERE id=? AND type="gym" AND profile_id=?',
            (csid, _profile_id())
        ).fetchone()
        if existing:
            sess_id = csid
            continuing = True
            for row in db.execute(
                'SELECT exercise_id, MAX(set_number) AS mx FROM session_lifts WHERE session_id=? GROUP BY exercise_id',
                (sess_id,)
            ).fetchall():
                existing_max_sets[row['exercise_id']] = row['mx']

    if not continuing:
        if not ex_names:
            db.close()
            return redirect(url_for('workout_log_new'))
        now = _datetime.utcnow().isoformat() + 'Z'
        sess_id = db.execute(
            'INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
            (date_val, 'gym', 'custom', now, _profile_id())
        ).lastrowid

    any_lifts = False
    for name, reps_str, sets_str in zip(ex_names, ex_reps, ex_sets):
        ex = db.execute('SELECT id FROM exercises WHERE name=? COLLATE NOCASE', (name,)).fetchone()
        if not ex:
            continue
        try:
            reps = max(1, int(reps_str))
            sets = max(1, int(sets_str))
        except (ValueError, TypeError):
            continue
        set_offset = existing_max_sets.get(ex['id'], 0)
        for set_num in range(1, sets + 1):
            db.execute(
                'INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                (sess_id, ex['id'], set_num + set_offset, reps, 0.0)
            )
            any_lifts = True

    if not any_lifts:
        if continuing:
            db.close()
            return redirect(url_for('session_detail', session_id=sess_id))
        db.rollback()
        db.close()
        return redirect(url_for('workout_log_new'))

    db.execute(
        'UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL',
        (_datetime.utcnow().isoformat() + 'Z', sess_id)
    )
    db.commit()
    db.close()
    return redirect(url_for('workout', done=1))


# ── Gayathri live session ──────────────────────────────────────────────────

@app.route('/workout/session/start', methods=['POST'])
def workout_session_start():
    if _profile_id() != 2:
        return redirect(url_for('dashboard'))
    routine_key = request.form.get('routine', '').strip()
    date_val    = _validate_date(request.form.get('date', ''))
    if routine_key not in _GAYATHRI_ROUTINES and routine_key != 'custom':
        return redirect(url_for('workout'))
    db = get_db()
    sess_id = db.execute(
        'INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
        (date_val, 'gym', f'routine:{routine_key}', _datetime.utcnow().isoformat() + 'Z', _profile_id())
    ).lastrowid
    db.commit()
    db.close()
    return redirect(url_for('workout_session', session_id=sess_id))


@app.route('/workout/session/<int:session_id>')
def workout_session(session_id):
    if _profile_id() != 2:
        return redirect(url_for('dashboard'))
    db = get_db()
    sess = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['type'] != 'gym' or sess['profile_id'] != _profile_id():
        db.close()
        return redirect(url_for('workout'))

    notes       = sess['notes'] or ''
    routine_key = notes[len('routine:'):] if notes.startswith('routine:') else None
    routine     = _GAYATHRI_ROUTINES.get(routine_key) if routine_key and routine_key != 'custom' else None

    # Logged sets grouped by exercise_id
    lift_rows = db.execute("""
        SELECT sl.id AS lift_id, sl.exercise_id, sl.set_number, sl.reps, sl.weight_kg,
               e.name AS ex_name, e.muscle_group
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE sl.session_id = ?
        ORDER BY (SELECT MIN(sl2.id) FROM session_lifts sl2
                  WHERE sl2.session_id = sl.session_id
                    AND sl2.exercise_id = sl.exercise_id),
                 sl.set_number
    """, (session_id,)).fetchall()

    logged_by_exid = {}
    for r in lift_rows:
        eid = r['exercise_id']
        if eid not in logged_by_exid:
            logged_by_exid[eid] = []
        logged_by_exid[eid].append({
            'lift_id': r['lift_id'], 'set_number': r['set_number'],
            'reps': r['reps'], 'weight_kg': r['weight_kg'],
        })

    # Build exercise list: routine order first
    exercises      = []
    routine_ex_ids = set()
    _g_by_name     = {ex['name']: ex for rtn in _GAYATHRI_ROUTINES.values() for ex in rtn['exercises']}

    if routine:
        for ex in routine['exercises']:
            row = db.execute('SELECT id FROM exercises WHERE name=? COLLATE NOCASE', (ex['name'],)).fetchone()
            if row:
                eid = row['id']
                routine_ex_ids.add(eid)
                exercises.append({**ex, 'exercise_id': eid, 'sets_logged': logged_by_exid.get(eid, [])})

    # Extra exercises logged outside routine
    for eid, sets in logged_by_exid.items():
        if eid not in routine_ex_ids:
            ex_row = db.execute('SELECT name, muscle_group FROM exercises WHERE id=?', (eid,)).fetchone()
            if ex_row:
                template = _g_by_name.get(ex_row['name'], {})
                exercises.append({
                    'exercise_id': eid,
                    'name':        ex_row['name'],
                    'muscle_group': ex_row['muscle_group'],
                    'weighted':    template.get('weighted', True),
                    'timed':       template.get('timed', False),
                    'reps':        template.get('reps', 8),
                    'sets':        template.get('sets', 3),
                    'reps_range':  template.get('reps_range', [6, 15]),
                    'sets_range':  template.get('sets_range', [2, 4]),
                    'sets_logged': sets,
                })

    # Picker: all exercises not already in this session
    in_session = {e['exercise_id'] for e in exercises}
    picker_rows = db.execute(
        'SELECT id, name, muscle_group FROM exercises ORDER BY muscle_group, name'
    ).fetchall()
    db.close()

    picker_exs = []
    for r in picker_rows:
        if r['id'] in in_session:
            continue
        tmpl = _g_by_name.get(r['name'], {})
        picker_exs.append({
            'id': r['id'], 'name': r['name'], 'mg': r['muscle_group'],
            'weighted':   tmpl.get('weighted', True),
            'timed':      tmpl.get('timed', False),
            'reps':       tmpl.get('reps', 8),
            'sets':       tmpl.get('sets', 3),
            'reps_range': tmpl.get('reps_range', [6, 15]),
            'sets_range': tmpl.get('sets_range', [2, 4]),
        })

    return render_template('workout_session.html',
                           session=dict(sess),
                           routine=routine,
                           routine_key=routine_key,
                           exercises=exercises,
                           picker_exs_json=_json.dumps(picker_exs),
                           today=date_type.today().isoformat())


@app.route('/sessions')
def sessions():
    from datetime import date as _d, timedelta as _td
    rows = get_sessions_with_headlines(_profile_id())
    today = _d.today()
    # Attach formatted date, week_group, week_display to each row
    for r in rows:
        d = _d.fromisoformat(r['date'])
        r['date_fmt'] = _fmt_date(r['date'])
        # ISO week key e.g. "2026-W21"
        iso = d.isocalendar()
        r['week_group'] = f"{iso[0]}-W{iso[1]:02d}"
        # Week display: "Mon 18 – Sun 24 May" or "This week" / "Last week"
        monday = d - _td(days=d.weekday())
        sunday = monday + _td(days=6)
        days_ago = (today - monday).days
        if days_ago < 7:
            r['week_display'] = 'This week'
        elif days_ago < 14:
            r['week_display'] = 'Last week'
        else:
            if monday.month == sunday.month:
                r['week_display'] = (
                    f"{_DAYS[0]} {monday.day} – {_DAYS[6]} {sunday.day} "
                    f"{_MONTHS[monday.month-1]} {monday.year if monday.year != today.year else ''}"
                ).strip()
            else:
                r['week_display'] = (
                    f"{monday.day} {_MONTHS[monday.month-1]} – "
                    f"{sunday.day} {_MONTHS[sunday.month-1]}"
                )
    return render_template('sessions.html', sessions=rows)


@app.route('/session/<int:session_id>')
def session_detail(session_id):
    sess, lifts, duration_min = get_session_detail_with_progression(session_id)
    if sess is None:
        return 'Session not found', 404
    if sess.get('profile_id', 1) != _profile_id():
        abort(403)

    if sess['type'] == 'pilates':
        detail = get_pilates_session(session_id)
        if detail is None:
            return 'Session not found', 404
        return render_template('pilates/pilates_session.html', session=sess, detail=detail)

    cardio = get_session_cardio(session_id) if sess['type'] == 'gym' else []

    swim_dist = None
    if sess['type'] == 'swim':
        db = get_db()
        rows = db.execute(
            'SELECT distance_m, rep_distance_m, sets FROM swim_logs WHERE session_id = ? ORDER BY id',
            (session_id,)).fetchall()
        db.close()
        if rows:
            swim_dist = {
                'groups': [{'sets': r['sets'], 'rep_distance': r['rep_distance_m'],
                             'distance': r['distance_m']} for r in rows],
                'total':  sum(r['distance_m'] for r in rows),
            }

    return render_template('session_detail.html',
                           session=sess, lifts=lifts, cardio=cardio,
                           swim_dist=swim_dist, duration_min=duration_min)


@app.route('/session/<int:session_id>/finish', methods=['POST'])
def session_finish(session_id):
    """Called by JS when user navigates away from session logging."""
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        return '', 403
    db.execute(
        'UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL',
        (_datetime.utcnow().isoformat() + 'Z', session_id)
    )
    db.commit()
    db.close()
    return '', 204


@app.route('/session/<int:session_id>/lift/<int:lift_id>/edit', methods=['POST'])
def lift_edit(session_id, lift_id):
    """Inline set edit from session detail."""
    data = request.get_json(silent=True) or {}
    try:
        reps      = int(data['reps'])
        weight_kg = float(data['weight_kg'])
    except (KeyError, ValueError):
        return _json.dumps({'error': 'invalid'}), 400, {'Content-Type': 'application/json'}
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        return _json.dumps({'error': 'forbidden'}), 403, {'Content-Type': 'application/json'}
    db.execute(
        'UPDATE session_lifts SET reps = ?, weight_kg = ? WHERE id = ? AND session_id = ?',
        (reps, weight_kg, lift_id, session_id)
    )
    db.commit()
    db.close()
    return _json.dumps({'ok': True, 'reps': reps, 'weight_kg': weight_kg}), 200, \
           {'Content-Type': 'application/json'}


@app.route('/session/<int:session_id>/add_set', methods=['POST'])
def add_set(session_id):
    """Add a set to an existing exercise in a session."""
    data = request.get_json(silent=True) or {}
    try:
        exercise_id = int(data['exercise_id'])
        reps        = int(data['reps'])
        weight_kg   = float(data['weight_kg'])
    except (KeyError, ValueError):
        return _json.dumps({'error': 'invalid'}), 400, {'Content-Type': 'application/json'}
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        return _json.dumps({'error': 'forbidden'}), 403, {'Content-Type': 'application/json'}
    try:
        next_set = (db.execute(
            'SELECT COALESCE(MAX(set_number), 0) + 1 FROM session_lifts WHERE session_id = ? AND exercise_id = ?',
            (session_id, exercise_id)
        ).fetchone()[0])
        lift_id = db.execute(
            'INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
            (session_id, exercise_id, next_set, reps, weight_kg)
        ).lastrowid
        db.commit()
    except Exception as e:
        db.rollback()
        db.close()
        return _json.dumps({'error': str(e)}), 400, {'Content-Type': 'application/json'}
    db.close()
    return _json.dumps({'ok': True, 'lift_id': lift_id, 'set_number': next_set}), 201, \
           {'Content-Type': 'application/json'}


@app.route('/session/<int:session_id>/delete', methods=['POST'])
def session_delete(session_id):
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        abort(403)
    db.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
    db.commit()
    db.close()
    return redirect(url_for('sessions'))


@app.route('/exercises', methods=['GET', 'POST'])
def exercises():
    db = get_db()
    error = None

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        tier = request.form.get('tier', '')
        muscle_group = request.form.get('muscle_group', '').strip()
        notes = request.form.get('notes', '').strip() or None

        if not name or not tier or not muscle_group:
            error = 'Name, tier, and muscle group are required.'
        else:
            try:
                db.execute(
                    'INSERT INTO exercises (name, tier, muscle_group, day_type, notes) VALUES (?,?,?,?,?)',
                    (name, int(tier), muscle_group, 'any', notes)
                )
                db.commit()
                db.close()
                return redirect(url_for('exercises'))
            except Exception as e:
                error = f'Could not add exercise: {e}'

    rows = db.execute(
        'SELECT * FROM exercises ORDER BY tier, day_type, name'
    ).fetchall()
    db.close()

    by_tier = {1: [], 2: [], 3: []}
    for row in rows:
        by_tier[row['tier']].append(row)

    return render_template('exercises.html', by_tier=by_tier, error=error)


@app.route('/exercises/<int:exercise_id>/delete', methods=['POST'])
def exercise_delete(exercise_id):
    db = get_db()
    db.execute('DELETE FROM exercises WHERE id = ?', (exercise_id,))
    db.commit()
    db.close()
    return redirect(url_for('exercises'))


@app.route('/analytics')
def analytics():
    today = date_type.today()
    db    = get_db()

    pid = _profile_id()

    # 1. PRs — best weight per exercise (with reps on that set)
    prs = db.execute("""
        SELECT e.name, e.muscle_group, e.tier,
               sl.weight_kg AS best_weight, sl.reps, s.date
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        JOIN sessions  s ON s.id = sl.session_id
        WHERE s.profile_id = ?
          AND (sl.exercise_id, sl.weight_kg) IN (
            SELECT sl2.exercise_id, MAX(sl2.weight_kg)
            FROM session_lifts sl2
            JOIN sessions s2 ON s2.id = sl2.session_id
            WHERE s2.profile_id = ?
            GROUP BY sl2.exercise_id
        )
        GROUP BY sl.exercise_id
        ORDER BY sl.weight_kg DESC
    """, (pid, pid)).fetchall()

    # 2. Lift progression — max weight per session date per exercise
    prog_rows = db.execute("""
        SELECT e.name, s.date, MAX(sl.weight_kg) AS max_weight
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        JOIN sessions  s ON s.id = sl.session_id
        WHERE s.profile_id = ?
        GROUP BY e.id, s.date
        ORDER BY e.name, s.date
    """, (pid,)).fetchall()

    progression = {}
    for r in prog_rows:
        name = r['name']
        if name not in progression:
            progression[name] = {'dates': [], 'weights': []}
        progression[name]['dates'].append(r['date'])
        progression[name]['weights'].append(r['max_weight'])

    exercise_names = sorted(progression.keys())

    # 3 & 4. Weekly volume + swim — last 12 weeks
    week_labels = []
    volume_data = []
    swim_data   = []

    for i in range(11, -1, -1):
        ws = today - timedelta(days=today.weekday()) - timedelta(weeks=i)
        we = ws + timedelta(days=6)
        week_labels.append(ws.strftime('%b %-d'))

        vol = db.execute("""
            SELECT COALESCE(SUM(sl.reps * sl.weight_kg), 0) AS v
            FROM session_lifts sl
            JOIN sessions s ON s.id = sl.session_id
            WHERE s.type = 'gym' AND s.date BETWEEN ? AND ? AND s.profile_id = ?
        """, (ws.isoformat(), we.isoformat(), pid)).fetchone()['v']
        volume_data.append(round(float(vol), 1))

        dist = db.execute("""
            SELECT COALESCE(SUM(sw.distance_m), 0) AS d
            FROM swim_logs sw
            JOIN sessions s ON s.id = sw.session_id
            WHERE s.date BETWEEN ? AND ? AND s.profile_id = ?
        """, (ws.isoformat(), we.isoformat(), pid)).fetchone()['d']
        swim_data.append(int(dist))

    db.close()

    return render_template('analytics.html',
                           prs=prs,
                           progression_json=_json.dumps(progression),
                           exercise_names=exercise_names,
                           week_labels=_json.dumps(week_labels),
                           volume_data=_json.dumps(volume_data),
                           swim_data=_json.dumps(swim_data),
                           heatmap_json=_json.dumps(get_activity_calendar()))


@app.route('/progression/arc')
def progression_arc():
    return render_template('progression_arc.html')


@app.route('/progression/api/arc')
def progression_arc_api():
    db = get_db()
    rows = db.execute("""
        SELECT p.exercise_id, p.weight_kg,
               e.name, e.tier, e.muscle_group, e.reps_only,
               s.id AS scheme_id, s.reps, s.sets, s.progression_order
        FROM progression p
        JOIN exercises e ON e.id = p.exercise_id
        JOIN schemes   s ON s.id = p.scheme_id
        ORDER BY e.tier, e.name
    """).fetchall()

    totals = {r['tier']: r['n'] for r in db.execute(
        'SELECT tier, COUNT(*) AS n FROM schemes GROUP BY tier'
    ).fetchall()}

    scheme_rows = db.execute(
        'SELECT id, tier, reps, sets, progression_order '
        'FROM schemes ORDER BY tier, progression_order'
    ).fetchall()
    db.close()

    schemes_by_tier = {}
    for sr in scheme_rows:
        t = str(sr['tier'])
        schemes_by_tier.setdefault(t, []).append({
            'id':               sr['id'],
            'reps':             sr['reps'],
            'sets':             sr['sets'],
            'progression_order': sr['progression_order'],
            'label':            f"{sr['sets']}×{sr['reps']}",
        })

    exercises = []
    for r in rows:
        tier  = r['tier']
        total = totals.get(tier, 0)
        cur   = r['progression_order']
        exercises.append({
            'id':           r['exercise_id'],
            'name':         r['name'],
            'tier':         tier,
            'muscle_group': r['muscle_group'],
            'reps_only':    bool(r['reps_only']),
            'weight_kg':    r['weight_kg'],
            'current_scheme': {
                'id':               r['scheme_id'],
                'reps':             r['reps'],
                'sets':             r['sets'],
                'progression_order': cur,
                'label':            f"{r['sets']}×{r['reps']}",
            },
            'total_stages':  total,
            'cycle_complete': cur >= total,
        })

    return (
        _json.dumps({'exercises': exercises, 'schemes': schemes_by_tier}),
        200,
        {'Content-Type': 'application/json'},
    )


@app.route('/progression')
def progression():
    items = get_all_progressions()
    return render_template('progression.html', items=items)


@app.route('/progression/advance', methods=['POST'])
def progression_advance():
    data   = request.get_json()
    result = advance_progression(int(data['exercise_id']), int(data['tier']))
    return _json.dumps(result), 200, {'Content-Type': 'application/json'}


@app.route('/progression/set-weight', methods=['POST'])
def progression_set_weight():
    data   = request.get_json()
    result = set_progression_weight(
        int(data['exercise_id']), float(data['weight_kg']), int(data['tier']),
        scheme_id=data.get('scheme_id')
    )
    return _json.dumps(result), 200, {'Content-Type': 'application/json'}


@app.route('/food/shared')
def food_shared():
    date_str = request.args.get('date', date_type.today().isoformat())
    d        = date_type.fromisoformat(date_str)
    today    = date_type.today()
    prev_date = (d - timedelta(days=1)).isoformat()
    next_date = (d + timedelta(days=1)).isoformat()
    if d == today:
        date_label = 'Today'
    elif d == today - timedelta(days=1):
        date_label = 'Yesterday'
    else:
        date_label = d.strftime('%a, %b %-d')

    profiles_data = []
    for pid, pname in ((1, 'Arjun'), (2, 'Gayathri')):
        sync_food_log_from_library(date_str, pid)
        goals   = get_macro_goals(pid)
        entries = [dict(e) for e in get_food_log(date_str, pid)]
        profiles_data.append({
            'id':      pid,
            'name':    pname,
            'goals':   goals,
            'entries': entries,
        })

    return render_template('food_shared.html',
                           date_str=date_str,
                           date_label=date_label,
                           prev_date=prev_date,
                           next_date=next_date,
                           is_today=(d == today),
                           profiles=profiles_data)


@app.route('/food')
def food():
    pid      = _profile_id()
    date_str = _validate_date(request.args.get('date', ''))
    d        = date_type.fromisoformat(date_str)
    today    = date_type.today()

    prev_date = (d - timedelta(days=1)).isoformat()
    next_date = (d + timedelta(days=1)).isoformat()

    if d == today:
        date_label = 'Today'
    elif d == today - timedelta(days=1):
        date_label = 'Yesterday'
    else:
        date_label = d.strftime('%a, %b %-d')

    sync_food_log_from_library(date_str, pid)
    goals   = get_macro_goals(pid)
    _rows   = get_food_log(date_str, pid)
    entries = [dict(e) for e in _rows]
    totals  = {
        'calories':  round(sum(e['calories']  for e in entries), 1),
        'protein_g': round(sum(e['protein_g'] for e in entries), 1),
    }

    library     = [dict(r) for r in get_food_library() if r['calories'] > 0]
    food_map    = {item['name'].lower(): item for item in library}
    components  = get_food_components()
    today_weight   = get_body_weight(date_str, pid)
    weight_history = get_body_weight_history(pid, days=30)
    return render_template('food.html',
                           date_str=date_str,
                           date_label=date_label,
                           prev_date=prev_date,
                           next_date=next_date,
                           is_today=(d == today),
                           goals=goals,
                           entries=entries,
                           totals=totals,
                           pending=get_pending_foods(pid),
                           history=get_food_history(pid),
                           food_library=library,
                           food_map=food_map,
                           food_components=components,
                           today_weight=today_weight,
                           weight_history=weight_history)


@app.route('/food/log', methods=['POST'])
def food_log_add():
    date_str = _validate_date(request.form.get('date', ''))
    raw_name = request.form.get('food_entry', '').strip()
    quantity = float(request.form.get('quantity', 0) or 0)
    if not raw_name:
        return redirect(url_for('food', date=date_str))

    pid = _profile_id()
    name, was_normalized = _normalize_food_name(raw_name)
    if was_normalized:
        log_food_reconciliation(raw_name, name, pid)

    multiplier, base_name = _parse_qty_name(name)
    has_qty_prefix = (multiplier != 1.0)

    # Try <num>g/<num>ml prefix (e.g. "100g chicken") if no <num>x multiplier found
    gram_qty = None
    if not has_qty_prefix:
        gram_qty, gram_base = _parse_gram_prefix(name)
        if gram_qty is not None:
            base_name     = gram_base
            has_qty_prefix = True

    conn = get_db()
    item = conn.execute(
        'SELECT * FROM food_items WHERE name=? COLLATE NOCASE', (base_name,)
    ).fetchone()
    conn.close()

    if item and item['calories'] > 0:
        if item['unit_type'] == 'unit':
            qty_factor = quantity if quantity > 0 else 1.0
        elif gram_qty is not None:
            qty_factor = gram_qty / 100.0
        else:
            qty_factor = (quantity / 100.0) if quantity > 0 else 1.0
        factor = multiplier * qty_factor
        cal  = round(item['calories']  * factor, 1)
        pro  = round(item['protein_g'] * factor, 1)
        carb = round(item['carbs_g']   * factor, 1)
        fat  = round(item['fat_g']     * factor, 1)
    else:
        cal = pro = carb = fat = 0

    if has_qty_prefix:
        # Insert directly — don't pollute food_items with qty-prefixed names
        conn = get_db()
        conn.execute(
            'INSERT INTO food_log (date,name,calories,protein_g,carbs_g,fat_g,profile_id) VALUES (?,?,?,?,?,?,?)',
            (date_str, name, cal, pro, carb, fat, pid)
        )
        conn.commit()
        conn.close()
    else:
        add_food_entry(date_str, name, cal, pro, carb, fat, pid)

    return redirect(url_for('food', date=date_str))


@app.route('/food/api/entry/<int:entry_id>/meal', methods=['POST'])
def food_api_entry_meal(entry_id):
    data      = request.get_json(silent=True) or {}
    meal_type = data.get('meal_type', 'snack')
    if meal_type not in ('breakfast', 'lunch', 'dinner', 'snack'):
        return _json.dumps({'error': 'invalid meal_type'}), 400, {'Content-Type': 'application/json'}
    conn = get_db()
    row = conn.execute('SELECT profile_id FROM food_log WHERE id=?', (entry_id,)).fetchone()
    if not row or row['profile_id'] != _profile_id():
        conn.close()
        return _json.dumps({'error': 'forbidden'}), 403, {'Content-Type': 'application/json'}
    conn.execute('UPDATE food_log SET meal_type=? WHERE id=?', (meal_type, entry_id))
    conn.commit()
    conn.close()
    return _json.dumps({'ok': True, 'meal_type': meal_type}), 200, {'Content-Type': 'application/json'}


@app.route('/food/api/entry', methods=['POST'])
def food_api_entry():
    data      = request.get_json(silent=True) or {}
    pid       = _profile_id()
    date_str  = data.get('date', date_type.today().isoformat())
    name      = (data.get('name') or '').strip()
    meal_type = data.get('meal_type', 'snack')
    calories  = float(data.get('calories') or 0)
    protein_g = float(data.get('protein_g') or 0)
    if not name:
        return _json.dumps({'error': 'name required'}), 400, {'Content-Type': 'application/json'}

    carbs_g = fat_g = 0.0

    # If no explicit macros supplied, look up the food in food_items
    if calories == 0 and protein_g == 0:
        norm, _ = _normalize_food_name(name)
        multiplier, base_name = _parse_qty_name(norm)
        if multiplier == 1.0:
            gram_qty, gram_base = _parse_gram_prefix(norm)
            if gram_qty is not None:
                base_name = gram_base
                factor    = gram_qty / 100.0
            else:
                factor = 1.0
        else:
            factor = multiplier

        conn = get_db()
        item = conn.execute(
            'SELECT * FROM food_items WHERE name=? COLLATE NOCASE', (base_name,)
        ).fetchone()
        conn.close()

        if item and item['calories'] > 0:
            if item['unit_type'] == 'unit':
                factor = multiplier  # Nx units; gram prefix irrelevant for unit-type
            calories  = round(item['calories']  * factor, 1)
            protein_g = round(item['protein_g'] * factor, 1)
            carbs_g   = round(item['carbs_g']   * factor, 1)
            fat_g     = round(item['fat_g']     * factor, 1)

    from datetime import datetime as _dt
    logged_at = _dt.now().strftime('%H:%M')
    conn = get_db()
    entry_id = conn.execute(
        'INSERT INTO food_log (date,name,calories,protein_g,carbs_g,fat_g,profile_id,meal_type,logged_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (date_str, name, calories, protein_g, carbs_g, fat_g, pid, meal_type, logged_at)
    ).lastrowid
    conn.commit()
    conn.close()
    return _json.dumps({'entry': {
        'id': entry_id, 'name': name, 'meal_type': meal_type,
        'calories': calories, 'protein_g': protein_g, 'logged_at': logged_at,
    }}), 201, {'Content-Type': 'application/json'}


@app.route('/food/delete/<int:entry_id>', methods=['POST'])
def food_log_delete(entry_id):
    date_str = request.form.get('date', date_type.today().isoformat())
    conn = get_db()
    row = conn.execute('SELECT profile_id FROM food_log WHERE id=?', (entry_id,)).fetchone()
    conn.close()
    if not row or row['profile_id'] != _profile_id():
        abort(403)
    delete_food_entry(entry_id)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return _json.dumps({'ok': True}), 200, {'Content-Type': 'application/json'}
    return redirect(url_for('food', date=date_str))


@app.route('/food/goals', methods=['POST'])
def food_goals_set():
    pid     = _profile_id()
    current = get_macro_goals(pid)
    set_macro_goals(
        float(request.form.get('calories',  current['calories'])  or current['calories']),
        float(request.form.get('protein_g', current['protein_g']) or current['protein_g']),
        0, 0,
        pid,
    )
    return redirect(url_for('food'))


@app.route('/food/meal', methods=['POST'])
def food_meal_log():
    date_str      = request.form.get('date', date_type.today().isoformat())
    meal_name     = request.form.get('meal_name', '').strip()
    serves_to_log = max(1, int(request.form.get('serves_to_log', 1) or 1))
    kcal          = float(request.form.get('kcal_per_serve',    0) or 0)
    protein_g     = float(request.form.get('protein_per_serve', 0) or 0)
    carbs_g       = float(request.form.get('carbs_per_serve',   0) or 0)
    fat_g         = float(request.form.get('fat_per_serve',     0) or 0)
    if meal_name and kcal > 0:
        for _ in range(serves_to_log):
            add_food_entry(date_str, meal_name, kcal, protein_g, carbs_g, fat_g, _profile_id())
    return redirect(url_for('food', date=date_str))


@app.route('/food/components/<path:name>', methods=['POST'])
def food_components_save(name):
    date_str = request.form.get('date', date_type.today().isoformat())
    ings = request.form.getlist('ing')
    qtys = request.form.getlist('qty')
    components = []
    for ing, qty_s in zip(ings, qtys):
        ing = ing.strip()
        try:
            qty = float(qty_s or 0)
        except ValueError:
            qty = 0
        if ing and qty > 0:
            components.append((ing, qty))
    save_food_components(name, components)
    return redirect(url_for('food_database'))


@app.route('/food/database')
def food_database():
    pid     = _profile_id()
    library = [dict(r) for r in get_food_library()]
    lib_names_lower = {item['name'].lower() for item in library}
    # Foods logged with 0 cal but not yet in food_items — add as stub rows
    for pname in get_pending_foods(pid):
        if pname.lower() not in lib_names_lower:
            library.append({'name': pname, 'calories': 0, 'protein_g': 0,
                            'carbs_g': 0, 'fat_g': 0, 'unit_type': 'g'})
    # Pending (undefined, calories=0) float to top, then alphabetical
    library.sort(key=lambda x: (x['calories'] > 0, x['name'].lower()))
    return render_template('food_database.html', food_library=library)


@app.route('/food/edit/new', methods=['GET', 'POST'])
def food_edit_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return redirect(url_for('food_edit_new'))
        unit_type = 'unit' if request.form.get('unit_type') == 'unit' else 'g'
        define_food_item(
            name,
            float(request.form.get('calories',  0) or 0),
            float(request.form.get('protein_g', 0) or 0),
            float(request.form.get('carbs_g',   0) or 0),
            float(request.form.get('fat_g',     0) or 0),
            unit_type,
        )
        return redirect(url_for('food_edit', name=name))
    return render_template('food_edit.html', mode='new', name='',
                           existing=None, components=[], food_library=[],
                           macros_json='{}')


@app.route('/food/edit/<path:name>', methods=['GET', 'POST'])
def food_edit(name):
    pid  = _profile_id()
    conn = get_db()
    existing = conn.execute(
        'SELECT * FROM food_items WHERE name=? COLLATE NOCASE', (name,)
    ).fetchone()
    conn.close()
    existing_dict = dict(existing) if existing else None

    if request.method == 'POST':
        unit_type = 'unit' if request.form.get('unit_type') == 'unit' else 'g'
        define_food_item(
            name,
            float(request.form.get('calories',  0) or 0),
            float(request.form.get('protein_g', 0) or 0),
            float(request.form.get('carbs_g',   0) or 0),
            float(request.form.get('fat_g',     0) or 0),
            unit_type,
        )
        sync_food_log_from_library(date_type.today().isoformat(), pid)
        return redirect(url_for('food_edit', name=name))

    comps   = get_food_components().get(name.lower(), [])
    library = get_food_library()
    macros_json = _json.dumps({
        item['name'].lower(): {
            'calories':  item['calories'],
            'protein_g': item['protein_g'],
            'carbs_g':   item['carbs_g'],
            'fat_g':     item['fat_g'],
            'unit_type': item['unit_type'],
        }
        for item in library
    })
    if existing_dict is None:
        mode = 'pending'
    elif existing_dict['calories'] == 0:
        mode = 'pending'
    else:
        mode = 'existing'
    return render_template('food_edit.html',
                           mode=mode, name=name,
                           existing=existing_dict,
                           components=comps,
                           food_library=library,
                           macros_json=macros_json)


@app.route('/food/delete/<path:name>', methods=['POST'])
def food_delete(name):
    delete_food_item(name)
    return redirect(url_for('food_database'))


@app.route('/food/define/<path:name>')
def food_define_redirect(name):
    return redirect(url_for('food_edit', name=name), 301)


@app.route('/command-centre')
def command_centre():
    if _profile_id() != 1:
        return redirect(url_for('food'))
    return render_template('command_centre.html',
                           reconciliations=get_food_reconciliations())


@app.route('/command-centre/reconciliation/<int:rec_id>/dismiss', methods=['POST'])
def reconciliation_dismiss(rec_id):
    db = get_db()
    db.execute('DELETE FROM food_reconciliations WHERE id=?', (rec_id,))
    db.commit()
    db.close()
    return redirect(url_for('command_centre'))


@app.route('/body-weight', methods=['POST'])
def body_weight_log():
    pid = _profile_id()
    weight_str = request.form.get('weight_kg', '').strip()
    date_str   = _validate_date(request.form.get('date', ''))
    try:
        weight_kg = float(weight_str)
        if weight_kg > 0:
            log_body_weight(date_str, weight_kg, pid)
    except ValueError:
        pass
    if pid == 2:
        return redirect(url_for('food', date=date_str))
    return redirect(url_for('dashboard'))


@app.route('/info')
def info():
    return render_template('info.html')


# ── Priorities API ─────────────────────────────────────────────────────────────

@app.route('/api/muscle-activity')
def api_muscle_activity():
    data = get_muscle_group_activity(21)
    return _json.dumps(data), 200, {'Content-Type': 'application/json'}


@app.route('/api/exercise-activity/<int:exercise_id>')
def api_exercise_activity(exercise_id):
    data = get_exercise_decay(exercise_id, 21)
    return _json.dumps(data), 200, {'Content-Type': 'application/json'}


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=343, debug=True)
