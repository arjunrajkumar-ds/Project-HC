
import json as _json
import os as _os
import re as _re
import secrets as _secrets
from urllib.parse import quote as _urlquote
from datetime import date as date_type, timedelta
from flask import Flask, render_template, request, redirect, url_for, session, abort
from datetime import datetime as _datetime
from database import init_db, get_db, get_tier1_suggestion, get_tier4_exercises, get_session_cardio, get_t1_last_weights, get_cardio_choices, log_cardio_session, save_session_draft, get_session_draft, clear_session_draft, get_t1_exercises_with_activity, get_muscle_group_activity, get_muscle_swimlane, get_exercise_bank, bank_add_exercise, bank_update_exercise, bank_delete_exercise, get_exercise_decay, get_fatigue_state, adjust_fatigue, get_exercise_muscle_map, FATIGUE_TIER_BASE, get_priority_lifts, set_priority_lift, clear_priority_lift, get_progression, advance_progression, set_progression_weight, get_all_progressions, get_schemes, record_stage_completion, record_exercise_attempt, get_sessions_with_headlines, get_session_detail_with_progression, get_macro_goals, set_macro_goals, sync_food_log_from_library, get_food_log, add_food_entry, log_food_entry, delete_food_entry, get_pending_foods, define_food_item, delete_food_item, get_food_history, get_recent_foods, get_food_library, get_food_components, get_food_component_mode, save_food_components, save_food_components_pct, get_profiles, _parse_qty_name, _parse_gram_prefix, _food_key, log_food_reconciliation, get_food_reconciliations, log_body_weight, get_body_weight, get_body_weight_history, get_pilates_session, get_mission_progress, clear_mission_stage
from swim_routes import bp as swim_bp
from pilates_routes import bp as pilates_bp
app = Flask(__name__)
app.secret_key = _os.environ.get('SECRET_KEY', 'gymtracker-local-secret-key')
app.jinja_env.filters['enumerate'] = enumerate
app.jinja_env.filters['fromjson'] = _json.loads

app.jinja_env.filters['urlencode'] = lambda s: _urlquote(str(s), safe='')

app.jinja_env.filters['strip_food_prefix'] = lambda s: _re.sub('^\\d+(?:\\.\\d+)?\\s*(?:g|ml|kg|l|oz|x)\\s+', '', s, flags=_re.IGNORECASE).strip()
app.jinja_env.filters['food_key'] = _food_key
_MONTHS = [
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec']
_FUZZY_QTY_RE = _re.compile('^(\\d+(?:\\.\\d+)?)\\s+(g|ml|kg|l|oz|lb)\\b', _re.IGNORECASE)

def _normalize_food_name(name):
    """Collapse '<number> <unit>' → '<number><unit>'. Returns (normalized, changed)."""
    normalized = _FUZZY_QTY_RE.sub('\\1\\2', name.strip(), count=1)
    return (normalized, normalized != name.strip())

_DAYS = [
    'Mon',
    'Tue',
    'Wed',
    'Thu',
    'Fri',
    'Sat',
    'Sun']

def _fmt_date(iso_str):
    """'2026-05-22' -> 'Fri 22 May'"""
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


def _safe_float(value, default=0.0):
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0):
    try:
        if value is None or value == '':
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


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
    'analytics',
    'exercises',
    'progression',
    'session_new',
    'exercise_delete',
    'progression_advance',
    'progression_set_weight'}
# Gayathri trains at home with no equipment. Every exercise is bodyweight and
# chosen for a 60-year-old building foundational strength. Routines are retired
# in favour of the all-day tap-to-log flow, so _GAYATHRI_ROUTINES is empty (kept
# for the legacy routine routes, which now simply no-op).
_GAYATHRI_ROUTINES = {}

_GAYATHRI_CARDIO = []  # legacy static list — cardio is now a proper group panel

# name, muscle_group, timed(bool). All home bodyweight, for foundational strength.
_GAYATHRI_EXTRA_EXERCISES = [
    {'name': 'Wall Push-ups',         'muscle_group': 'Push',     'location': 'home', 'timed': False},
    {'name': 'Incline Push-ups',      'muscle_group': 'Push',     'location': 'home', 'timed': False},
    {'name': 'Chair Dips',            'muscle_group': 'Push',     'location': 'home', 'timed': False},
    {'name': 'Prone Superman',        'muscle_group': 'Pull',     'location': 'home', 'timed': False},
    {'name': 'Floor Angels',          'muscle_group': 'Pull',     'location': 'home', 'timed': False},
    {'name': 'Sit-to-Stand',          'muscle_group': 'Legs',     'location': 'home', 'timed': False},
    {'name': 'Chair Squats',          'muscle_group': 'Legs',     'location': 'home', 'timed': False},
    {'name': 'Squats',                'muscle_group': 'Legs',     'location': 'home', 'timed': False},
    {'name': 'Step Down',             'muscle_group': 'Legs',     'location': 'home', 'timed': False},
    {'name': 'Incline Reverse Plank', 'muscle_group': 'Core',     'location': 'home', 'timed': True},
    {'name': 'Dead Bug',              'muscle_group': 'Core',     'location': 'home', 'timed': False},
    {'name': 'Heel Raises',           'muscle_group': 'Balance',  'location': 'home', 'timed': False},
    {'name': 'Single-Leg Stand',      'muscle_group': 'Balance',  'location': 'home', 'timed': True},
    {'name': 'Neck Rolls',            'muscle_group': 'Mobility', 'location': 'home', 'timed': True},
    {'name': 'Hip Flexor Stretch',    'muscle_group': 'Mobility', 'location': 'home', 'timed': True},
    {'name': '1KM Walk',              'muscle_group': 'Cardio',   'location': 'home', 'timed': False, 'step': 1,
     'desc': '1 lap around the neighbourhood. 10 minutes'},
]

# Missions: milestone movements Gayathri works up to, one stage at a time.
_GAYATHRI_MISSIONS = [
    {'key': 'first_pushup', 'title': 'First Push-up', 'icon': '💪',
     'blurb': 'Build up to a single full push-up.',
     'stages': [{'label': 'Wall Push-up', 'target': '5 reps'},
                {'label': 'Incline Push-up', 'target': '5 reps'},
                {'label': 'Knee Push-up', 'target': '3 reps'},
                {'label': 'Full Push-up', 'target': '1 rep', 'goal': True}]},
    {'key': 'first_squat', 'title': 'First Squat', 'icon': '🦵',
     'blurb': 'Work toward a full bodyweight squat.',
     'stages': [{'label': 'Sit-to-Stand', 'target': '5 reps'},
                {'label': 'Chair Squat', 'target': '5 reps'},
                {'label': 'Half Squat', 'target': '5 reps'},
                {'label': 'Bodyweight Squat', 'target': '1 rep', 'goal': True}]},
    {'key': 'first_starjump', 'title': 'First Star Jump', 'icon': '⭐',
     'blurb': 'Build the power and balance for a star jump.',
     'stages': [{'label': 'Step-Touch', 'target': '10 reps'},
                {'label': 'Heel Raises', 'target': '10 reps'},
                {'label': 'Half Jump', 'target': '3 reps'},
                {'label': 'Star Jump', 'target': '1 rep', 'goal': True}]},
]

def _gayathri_missions_map():
    return {m['key']: m for m in _GAYATHRI_MISSIONS}


def _gayathri_missions_with_progress(profile_id):
    """Return the mission list annotated with cleared-stage count + done flag."""
    prog = get_mission_progress(profile_id)
    out = []
    for m in _GAYATHRI_MISSIONS:
        cleared = prog.get(m['key'], 0)
        total = len(m['stages'])
        out.append({**m, 'cleared': cleared, 'total': total, 'done': cleared >= total})
    return out


@app.route('/missions')
def missions():
    if _profile_id() not in (2, 3):
        return redirect(url_for('dashboard'))
    return render_template('missions.html',
                           missions=_gayathri_missions_with_progress(_profile_id()))


@app.route('/missions/clear', methods=['POST'])
def missions_clear():
    if _profile_id() not in (2, 3):
        return {'error': 'not_allowed'}, 403
    data = request.get_json(silent=True) or {}
    key = str(data.get('key', '')).strip()
    stage = _safe_int(data.get('stage'), -1)
    mission = _gayathri_missions_map().get(key)
    if not mission or stage < 0 or stage >= len(mission['stages']):
        return {'error': 'bad_request'}, 400
    cleared = clear_mission_stage(_profile_id(), key, stage)
    total = len(mission['stages'])
    return {'key': key, 'cleared': cleared, 'total': total, 'done': cleared >= total}
_CSRF_EXEMPT = {
    None,
    'static',
    'profiles',
    'profiles_select'}

def require_profile():
    if request.endpoint in _CSRF_EXEMPT:
        return None
    if request.method == 'POST':
        token = session.get('csrf_token')
        if token:
            submitted = request.headers.get('X-CSRF-Token') or request.form.get('_csrf')
            if not submitted or not _secrets.compare_digest(token, submitted):
                abort(403)
    if 'profile_id' not in session:
        return redirect(url_for('profiles'))
    ep = request.endpoint or ''
    if session['profile_id'] in (2, 3):
        if ep in _GAYATHRI_BLOCKED or ep.startswith('swim.') or ep.startswith('pilates.'):
            return redirect(url_for('food'))
    return None

app.before_request(require_profile)

def get_activity_calendar():
    '''Return {date_iso: [type, ...]} for the past ~54 weeks.'''
    db = get_db()
    rows = db.execute("SELECT date, type FROM sessions WHERE date >= date('now','-371 days') AND profile_id = ? ORDER BY date", (_profile_id(),)).fetchall()
    db.close()
    cal = { }
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


@app.route('/profiles/select', methods=[ 'POST'])
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
    if _profile_id() in (2, 3):
        today = date_type.today()
        today_iso = today.isoformat()
        monday = (today - timedelta(days=today.weekday())).isoformat()
        db = get_db()
        food_row = db.execute('SELECT COALESCE(SUM(calories),0) AS cal, COALESCE(SUM(protein_g),0) AS prot FROM food_log WHERE date=? AND profile_id=?', (today_iso, _profile_id())).fetchone()
        total_cal = round(food_row['cal'])
        total_prot = round(food_row['prot'])
        goals = get_macro_goals(_profile_id())
        today_weight = get_body_weight(today_iso, _profile_id())
        weight_history = get_body_weight_history(_profile_id(), days=30)
        week_rows = db.execute("SELECT DISTINCT date FROM sessions WHERE date BETWEEN ? AND ? AND profile_id=? AND type='gym'", (monday, today_iso, _profile_id())).fetchall()
        week_active = {r['date'] for r in week_rows}
        monday_date = today - timedelta(days=today.weekday())
        week_days = []
        for i in range(7):
            day = monday_date + timedelta(days=i)
            week_days.append({
                'iso': day.isoformat(),
                'day_short': day.strftime('%a'),
                'day_num': day.day,
                'is_today': day == today,
                'is_past': day < today,
                'active': day.isoformat() in week_active })
        last_sess = db.execute("\n            SELECT s.id, s.date, s.notes,\n                   GROUP_CONCAT(DISTINCT e.name) AS exercise_names,\n                   COUNT(DISTINCT sl.id) AS total_sets\n            FROM sessions s\n            LEFT JOIN session_lifts sl ON sl.session_id = s.id\n            LEFT JOIN exercises e ON e.id = sl.exercise_id\n            WHERE s.profile_id = ? AND s.type = 'gym'\n            GROUP BY s.id ORDER BY s.date DESC, s.id DESC LIMIT 1\n        ", (_profile_id(),)).fetchone()
        # All-day "tap a set" panel: exercises grouped by muscle + today's tallies.
        gy_by_group = _gayathri_exercises_by_group()
        gy_tallies, gy_covered = _gayathri_today_tallies(db, today_iso)
        gy_total_sets = sum(t['sets'] for t in gy_tallies.values())
        # Today's session log — one entry per set, newest first
        gy_log_rows = db.execute("""
            SELECT sl.id, e.name AS name, e.muscle_group AS mg, e.is_timed AS timed, sl.reps AS amount
            FROM session_lifts sl
            JOIN exercises e ON e.id = sl.exercise_id
            JOIN sessions s  ON s.id = sl.session_id
            WHERE s.date = ? AND s.type = 'gym' AND s.profile_id = ?
            ORDER BY sl.id DESC
        """, (today_iso, _profile_id())).fetchall()
        gy_log = [{'id': r['id'], 'name': r['name'],
                   'group': _gayathri_display_group(r['mg']),
                   'timed': bool(r['timed']), 'amount': r['amount']}
                  for r in gy_log_rows]
        db.close()
        return render_template('home_gayathri.html', today=today_iso, total_cal=total_cal, total_prot=total_prot, goals=goals, today_weight=today_weight, weight_history=[dict(r) for r in (weight_history or [])], week_days=week_days, last_session=dict(last_sess) if last_sess else None,
                               gy_by_group=gy_by_group, gy_tallies=gy_tallies, gy_covered=sorted(gy_covered), gy_total_sets=gy_total_sets,
                               gy_group_order=[g for g in _GAYATHRI_GROUPS if g in gy_by_group], gy_cardio=_GAYATHRI_CARDIO,
                               gy_log=gy_log)

    today = date_type.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()
    db = get_db()
    last_session = db.execute('\n        SELECT s.*,\n               COALESCE(sw_agg.distance_m, 0) AS distance_m,\n               COALESCE(agg.total_sets,   0)   AS total_sets,\n               COALESCE(agg.total_volume, 0.0) AS total_volume,\n               COALESCE(cd.cardio_dist, 0)     AS cardio_dist,\n               COALESCE(cd.cardio_dur,  0)     AS cardio_dur,\n               (SELECT e.name FROM session_cardio sc JOIN exercises e ON e.id = sc.exercise_id\n                WHERE sc.session_id = s.id ORDER BY sc.id LIMIT 1) AS cardio_name\n        FROM sessions s\n        LEFT JOIN (\n            SELECT session_id, SUM(distance_m) AS distance_m\n            FROM swim_logs GROUP BY session_id\n        ) sw_agg ON sw_agg.session_id = s.id\n        LEFT JOIN (\n            SELECT session_id,\n                   COUNT(*)              AS total_sets,\n                   SUM(reps * weight_kg) AS total_volume\n            FROM session_lifts\n            GROUP BY session_id\n        ) agg ON agg.session_id = s.id\n        LEFT JOIN (\n            SELECT session_id, SUM(distance_m) AS cardio_dist, SUM(duration_s) AS cardio_dur\n            FROM session_cardio GROUP BY session_id\n        ) cd ON cd.session_id = s.id\n        ORDER BY s.date DESC, s.id DESC\n        LIMIT 1\n    ').fetchone()
    weekly_volume = db.execute("\n        SELECT COALESCE(SUM(sl.reps * sl.weight_kg), 0) AS vol\n        FROM session_lifts sl\n        JOIN sessions s ON s.id = sl.session_id\n        WHERE s.type = 'gym' AND s.date >= ? AND s.profile_id = ?\n    ", (week_start, _profile_id())).fetchone()['vol']
    weekly_swim = db.execute('\n        SELECT COALESCE(SUM(sw.distance_m), 0) AS dist\n        FROM swim_logs sw\n        JOIN sessions s ON s.id = sw.session_id\n        WHERE s.date >= ? AND s.profile_id = ?\n    ', (week_start, _profile_id())).fetchone()['dist']
    t1_exercises = get_t1_exercises_with_activity()
    prs = db.execute('\n        SELECT e.name, sl.weight_kg, sl.reps, s.date\n        FROM session_lifts sl\n        JOIN exercises e ON e.id = sl.exercise_id\n        JOIN sessions  s ON s.id = sl.session_id\n        WHERE s.profile_id = ?\n        ORDER BY sl.weight_kg DESC\n        LIMIT 5\n    ', (_profile_id(),)).fetchall()
    today_weight = get_body_weight(today.isoformat(), _profile_id())
    food_goals = get_macro_goals(_profile_id())
    food_rows = get_food_log(today.isoformat(), _profile_id())
    food_totals = {
        'calories': round(sum((r['calories'] or 0) for r in food_rows), 1),
        'protein_g': round(sum((r['protein_g'] or 0) for r in food_rows), 1),
        'carbs_g': round(sum((r['carbs_g'] or 0) for r in food_rows), 1),
        'fat_g': round(sum((r['fat_g'] or 0) for r in food_rows), 1) }
    # Per-item breakdown for the per-meal radial charts. Undefined (0-cal) foods are
    # kept too — they render as grey wedges so nothing logged is hidden.
    _meal_rank = {'breakfast': 0, 'lunch': 1, 'dinner': 2, 'snack': 3}
    food_items = sorted(
        ({'name': r['name'],
          'meal': (r['meal_type'] or 'snack'),
          'cal': round(r['calories'] or 0, 1),
          'p': round(r['protein_g'] or 0, 1),
          'c': round(r['carbs_g'] or 0, 1),
          'f': round(r['fat_g'] or 0, 1)}
         for r in food_rows),
        key=lambda x: _meal_rank.get(x['meal'], 4))
    monday = today - timedelta(days=today.weekday())
    week_session_rows = db.execute('SELECT date, type FROM sessions WHERE date BETWEEN ? AND ? AND profile_id = ?', (monday.isoformat(), (monday + timedelta(days=6)).isoformat(), _profile_id())).fetchall()
    db.close()
    week_activity = { }
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
            'date': day_iso,
            'day_short': day.strftime('%a'),
            'day_num': day.day,
            'is_today': day == today,
            'is_past': day < today,
            'activities': week_activity.get(day_iso, []) })
    return render_template('dashboard.html', last_session=last_session, weekly_volume=weekly_volume, weekly_swim=weekly_swim, today=today.isoformat(), week_days=week_days, today_weight=today_weight, food_goals=food_goals, food_totals=food_totals, food_items_json=_json.dumps(food_items), swimlane_json=_json.dumps(get_muscle_swimlane(_profile_id())), fatigue_json=_json.dumps(get_fatigue_state(_profile_id())))


@app.route('/session/draft', methods=[ 'POST'])
def session_draft_save():
    data = request.get_json(silent=True) or {}
    date_val = _validate_date(data.get('date', ''))
    payload = data.get('payload')
    if isinstance(payload, str) or len(payload) > 2000000:
        return (_json.dumps({
            'error': 'bad payload' }), 400, {
            'Content-Type': 'application/json' })
    save_session_draft(_profile_id(), date_val, payload)
    return (_json.dumps({
        'ok': True }), 200, {
        'Content-Type': 'application/json' })


@app.route('/session/draft/clear', methods=[ 'POST'])
def session_draft_clear():
    data = request.get_json(silent=True) or {}
    date_val = _validate_date(data.get('date', ''))
    clear_session_draft(_profile_id(), date_val)
    return (_json.dumps({
        'ok': True }), 200, {
        'Content-Type': 'application/json' })


@app.route('/session/new', methods=['GET', 'POST'])
def session_new():
    """Arjun's gym-session builder. Reconstructed (clean, functional) after the
    original was lost; mirrors the fields session_new.html posts."""
    pid = _profile_id()

    if request.method == 'POST':
        date_val = _validate_date(request.form.get('date', ''))
        db = get_db()

        # Continue an existing session, or start a new one.
        continue_id = request.form.get('continue_session_id', '').strip()
        if continue_id.isdigit() and db.execute(
                'SELECT id FROM sessions WHERE id=? AND type="gym" AND profile_id=?',
                (int(continue_id), pid)).fetchone():
            sess_id = int(continue_id)
            offsets = {r['exercise_id']: r['mx'] for r in db.execute(
                'SELECT exercise_id, MAX(set_number) AS mx FROM session_lifts WHERE session_id=? GROUP BY exercise_id',
                (sess_id,)).fetchall()}
        else:
            sess_id = db.execute(
                'INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
                (date_val, 'gym', None, _datetime.utcnow().isoformat() + 'Z', pid)).lastrowid
            offsets = {}

        any_lifts = False
        idx = 0
        while True:
            ex_field = request.form.get(f'exercise_id_{idx}')
            if ex_field is None:
                # allow gaps in indices up to a sane cap
                idx += 1
                if idx > 200:
                    break
                continue
            ex_str = (ex_field or '').strip()
            if not ex_str.isdigit():
                idx += 1
                if idx > 200:
                    break
                continue
            exercise_id = int(ex_str)
            scheme_id = _safe_int(request.form.get(f'scheme_id_{idx}'), 0) or None
            weight = _safe_float(request.form.get(f'prog_weight_{idx}'), 0.0)
            sets_done = _safe_int(request.form.get(f'ex_tally_{idx}'), 0)
            last_reps = _safe_int(request.form.get(f'ex_last_reps_{idx}'), 0)
            result = (request.form.get(f'ex_result_{idx}') or '').strip()

            base_set = offsets.get(exercise_id, 0)
            for s in range(1, max(sets_done, 0) + 1):
                db.execute(
                    'INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                    (sess_id, exercise_id, base_set + s, last_reps, weight))
                any_lifts = True

            # progression bookkeeping (best-effort; never blocks the save)
            if result in ('pass', 'fail') and scheme_id:
                try:
                    record_exercise_attempt(exercise_id, scheme_id, result, sets_done,
                                            date_val, conn=db, weight_kg=weight)
                    if result == 'pass':
                        record_stage_completion(exercise_id, scheme_id, conn=db)
                except Exception:
                    pass
            idx += 1
            if idx > 200:
                break

        # Cardio / warm-up blocks
        for key in request.form.keys():
            m = _re.match(r'cardio_exercise_id_(.+)', key)
            if not m:
                continue
            sfx = m.group(1)
            cstr = (request.form.get(key) or '').strip()
            if not cstr.isdigit():
                continue
            cid = int(cstr)
            distance = _safe_float(request.form.get(f'cardio_distance_{sfx}'), None)
            dur_min = _safe_float(request.form.get(f'cardio_duration_{sfx}'), None)
            duration_s = int(dur_min * 60) if dur_min else None
            resistance = _safe_float(request.form.get(f'cardio_resistance_{sfx}'), None)
            speed = _safe_float(request.form.get(f'cardio_speed_{sfx}'), None)
            try:
                log_cardio_session(pid, date_val, cid, distance_m=distance,
                                   duration_s=duration_s, resistance=resistance, speed=speed)
            except Exception:
                pass

        db.execute('UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL',
                   (_datetime.utcnow().isoformat() + 'Z', sess_id))
        db.commit()
        db.close()
        clear_session_draft(pid, date_val)
        if not any_lifts:
            return redirect(url_for('workout'))
        return redirect(url_for('session_detail', session_id=sess_id))

    # ── GET: build the builder context ──
    session_date = _validate_date(request.args.get('date', ''))
    preselect_exid = request.args.get('exercise_id', '')
    preselect_exid = int(preselect_exid) if str(preselect_exid).isdigit() else None

    db = get_db()

    def _tier_list(tier):
        rows = db.execute(
            'SELECT id, name, tier, muscle_group, reps_min, sets_min, is_barbell, reps_only '
            'FROM exercises WHERE tier=? ORDER BY muscle_group, name', (tier,)).fetchall()
        return [{'id': r['id'], 'name': r['name'], 'tier': r['tier'],
                 'muscle_group': r['muscle_group'],
                 'sets': r['sets_min'] or 3, 'reps': r['reps_min'] or 8,
                 'weight_kg': 0,
                 'is_barbell': bool(r['is_barbell']),
                 'reps_only':  bool(r['reps_only'])} for r in rows]

    tier1 = _tier_list(1)
    _t1_act = {r['id']: r for r in get_t1_exercises_with_activity()}
    _t1_last = get_t1_last_weights()
    for ex in tier1:
        act = _t1_act.get(ex['id']) or {}
        lw  = _t1_last.get(ex['id']) or {}
        ex['days_since']  = act.get('days_since')
        ex['last_weight'] = lw.get('last_weight')
        ex['is_pr']       = bool(lw.get('is_pr'))
    tier2 = _tier_list(2)
    tier3 = _tier_list(3)
    tier4 = [{'id': e['id'], 'name': e['name'], 'tier': 4,
              'muscle_group': e['muscle_group'], 'metrics': e.get('metrics', {})}
             for e in get_tier4_exercises()]

    today_gym_rows = db.execute("""
        SELECT s.id, GROUP_CONCAT(DISTINCT e.name) AS exercise_names
        FROM sessions s
        LEFT JOIN session_lifts sl ON sl.session_id = s.id
        LEFT JOIN exercises e ON e.id = sl.exercise_id
        WHERE s.date = ? AND s.type = 'gym' AND s.profile_id = ?
        GROUP BY s.id ORDER BY s.id DESC
    """, (session_date, pid)).fetchall()
    today_sessions = [dict(r) for r in today_gym_rows]

    continue_session = None
    continue_arg = request.args.get('continue', '').strip()
    if continue_arg.isdigit():
        cs = db.execute('SELECT id FROM sessions WHERE id=? AND date=? AND type="gym" AND profile_id=?',
                        (int(continue_arg), session_date, pid)).fetchone()
        if cs:
            continue_session = {'id': cs['id']}
    db.close()

    draft = get_session_draft(pid, session_date)

    return render_template('session_new.html',
        session_date=session_date,
        preselect_exid=_json.dumps(preselect_exid),
        draft_payload=_json.dumps(draft or {}),
        progs_json=_json.dumps(get_all_progressions()),
        tier1_json=_json.dumps(tier1),
        tier2_json=_json.dumps(tier2),
        tier3_json=_json.dumps(tier3),
        tier4_json=_json.dumps(tier4),
        schemes_json=_json.dumps({t: get_schemes(t) for t in (1, 2, 3)}),
        fatigue_json=_json.dumps(get_fatigue_state(pid)),
        ex_muscles_json=_json.dumps(get_exercise_muscle_map()),
        tier_base_json=_json.dumps(FATIGUE_TIER_BASE),
        priority_json=_json.dumps(get_priority_lifts(pid)),
        today_sessions=today_sessions,
        continue_session=continue_session)


def _gayathri_by_muscle():
    def shape(ex, loc):
        timed = bool(ex.get('timed'))
        return {
            'name':         ex['name'],
            'muscle_group': ex.get('muscle_group', 'Other'),
            'timed':        timed,
            'location':     loc,
            'reps':         30 if timed else 8,
            'sets':         3,
            'reps_range':   [20, 45] if timed else [5, 12],
            'sets_range':   [2, 5],
        }
    seen = set()
    by_muscle = {}
    _MG_ORDER = ['Push', 'Pull', 'Legs', 'Core', 'Mobility', 'Balance']
    for mg in _MG_ORDER:
        by_muscle[mg] = []
    for routine in _GAYATHRI_ROUTINES.values():
        loc = routine.get('location', 'Home').lower()
        for ex in routine['exercises']:
            if ex['name'] in seen:
                continue
            seen.add(ex['name'])
            by_muscle.setdefault(ex.get('muscle_group', 'Other'), []).append(shape(ex, loc))
    for ex in _GAYATHRI_EXTRA_EXERCISES:
        if ex['name'] in seen:
            continue
        seen.add(ex['name'])
        by_muscle.setdefault(ex['muscle_group'], []).append(shape(ex, ex.get('location', 'home').lower()))
    return by_muscle


# ── Gayathri: all-day "tap a set" model ─────────────────────────────────────
_GAYATHRI_GROUPS = ['Push', 'Pull', 'Legs', 'Core', 'Mobility', 'Balance', 'Cardio']
_GAYATHRI_GROUP_MAP = {
    'Push': 'Push', 'Chest': 'Push', 'Shoulders': 'Push', 'Triceps': 'Push',
    'Pull': 'Pull', 'Back': 'Pull', 'Upper Back': 'Pull', 'Biceps': 'Pull',
    'Arms': 'Push',
    'Legs': 'Legs', 'Posterior Chain': 'Legs',
    'Core': 'Core', 'Balance': 'Balance', 'Mobility': 'Mobility',
    'Cardio': 'Cardio',
}

def _gayathri_display_group(mg):
    return _GAYATHRI_GROUP_MAP.get(mg, 'Mobility')


def _gayathri_exercises():
    seen, out = set(), []
    def add(ex, loc):
        if ex['name'] in seen:
            return
        seen.add(ex['name'])
        timed = bool(ex.get('timed'))
        step = ex.get('step', 30 if timed else 10)
        out.append({'name': ex['name'], 'group': _gayathri_display_group(ex.get('muscle_group', 'Mobility')),
                    'timed': timed, 'location': loc, 'step': step, 'desc': ex.get('desc', '')})
    for routine in _GAYATHRI_ROUTINES.values():
        loc = routine.get('location', 'Home').lower()
        for ex in routine['exercises']:
            add(ex, loc)
    for ex in _GAYATHRI_EXTRA_EXERCISES:
        add(ex, ex.get('location', 'home').lower())
    return out


def _gayathri_exercises_by_group():
    by = {g: [] for g in _GAYATHRI_GROUPS}
    for ex in _gayathri_exercises():
        by[ex['group']].append(ex)
    return {g: by[g] for g in _GAYATHRI_GROUPS if by[g]}


def _gayathri_today_session(db, date_iso, create=False):
    row = db.execute("SELECT id FROM sessions WHERE date=? AND type='gym' AND profile_id=? ORDER BY id LIMIT 1",
                     (date_iso, _profile_id())).fetchone()
    if row:
        return row['id']
    if not create:
        return None
    return db.execute('INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
                      (date_iso, 'gym', 'daily', _datetime.utcnow().isoformat() + 'Z', _profile_id())).lastrowid


def _gayathri_today_tallies(db, date_iso):
    sid = _gayathri_today_session(db, date_iso, create=False)
    tallies, covered = {}, set()
    if sid is None:
        return tallies, covered
    rows = db.execute("""
        SELECT e.name AS name, e.muscle_group AS mg,
               COUNT(sl.id) AS sets, COALESCE(SUM(sl.reps), 0) AS total
        FROM session_lifts sl JOIN exercises e ON e.id = sl.exercise_id
        WHERE sl.session_id = ?
        GROUP BY sl.exercise_id
    """, (sid,)).fetchall()
    for r in rows:
        tallies[r['name']] = {'sets': r['sets'], 'total': r['total']}
        covered.add(_gayathri_display_group(r['mg']))
    return tallies, covered


def _gayathri_ensure_exercise(db, name):
    row = db.execute('SELECT id FROM exercises WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    if row:
        return row['id']
    spec = next((e for e in _gayathri_exercises() if e['name'].lower() == name.lower()), None)
    if not spec:
        return None
    return db.execute('INSERT INTO exercises (name, tier, muscle_group, day_type, reps_only, is_timed) VALUES (?,?,?,?,?,?)',
                      (spec['name'], 3, spec['group'], 'any', 1, 1 if spec['timed'] else 0)).lastrowid


@app.route('/workout/quick-set', methods=['POST'])
def workout_quick_set():
    if _profile_id() not in (2, 3):
        return {'error': 'not_allowed'}, 403
    data = request.get_json(silent=True) or {}
    name = str(data.get('exercise', '')).strip()
    amount = _safe_int(data.get('amount'), 0)
    if not name or amount <= 0:
        return {'error': 'bad_request'}, 400
    date_iso = date_type.today().isoformat()
    db = get_db()
    ex_id = _gayathri_ensure_exercise(db, name)
    if ex_id is None:
        db.close()
        return {'error': 'unknown_exercise'}, 400
    sid = _gayathri_today_session(db, date_iso, create=True)
    next_set = (db.execute('SELECT COALESCE(MAX(set_number), 0) AS mx FROM session_lifts WHERE session_id=? AND exercise_id=?',
                           (sid, ex_id)).fetchone()['mx']) + 1
    new_id = db.execute('INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                        (sid, ex_id, next_set, amount, 0.0)).lastrowid
    db.execute('UPDATE sessions SET ended_at=? WHERE id=?', (_datetime.utcnow().isoformat() + 'Z', sid))
    db.commit()
    tallies, covered = _gayathri_today_tallies(db, date_iso)
    db.close()
    tally = tallies.get(name, {'sets': 0, 'total': 0})
    return {'id': new_id, 'exercise': name, 'sets': tally['sets'], 'total': tally['total'],
            'covered': sorted(covered), 'covered_count': len(covered),
            'group_count': len(_gayathri_exercises_by_group())}


@app.route('/workout/quick-set/<int:lift_id>', methods=['DELETE'])
def workout_quick_set_delete(lift_id):
    if _profile_id() not in (2, 3):
        return {'error': 'not_allowed'}, 403
    date_iso = date_type.today().isoformat()
    db = get_db()
    row = db.execute("""
        SELECT sl.id, e.name AS name
        FROM session_lifts sl
        JOIN sessions s  ON s.id = sl.session_id
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE sl.id = ? AND s.date = ? AND s.type = 'gym' AND s.profile_id = ?
    """, (lift_id, date_iso, _profile_id())).fetchone()
    if not row:
        db.close()
        return {'error': 'not_found'}, 404
    db.execute('DELETE FROM session_lifts WHERE id = ?', (lift_id,))
    db.commit()
    tallies, covered = _gayathri_today_tallies(db, date_iso)
    db.close()
    tally = tallies.get(row['name'], {'sets': 0, 'total': 0})
    return {'exercise': row['name'], 'sets': tally['sets'], 'total': tally['total'],
            'covered': sorted(covered), 'covered_count': len(covered),
            'group_count': len(_gayathri_exercises_by_group())}


@app.route('/workout/log/new')
def workout_log_new():
    if _profile_id() not in (2, 3):
        return redirect(url_for('dashboard'))
    today_iso = date_type.today().isoformat()
    db = get_db()
    today_gym_rows = db.execute("\n        SELECT s.id, GROUP_CONCAT(DISTINCT e.name) AS exercise_names\n        FROM sessions s\n        LEFT JOIN session_lifts sl ON sl.session_id = s.id\n        LEFT JOIN exercises e ON e.id = sl.exercise_id\n        WHERE s.date = ? AND s.type = 'gym' AND s.profile_id = ?\n        GROUP BY s.id ORDER BY s.id DESC\n    ", (today_iso, _profile_id())).fetchall()
    today_sessions = [ dict(r) for r in today_gym_rows ]
    continue_session = None
    continue_arg = request.args.get('continue', '').strip()
    if continue_arg.isdigit():
        csid = int(continue_arg)
        cs = db.execute('SELECT * FROM sessions WHERE id=? AND date=? AND type="gym" AND profile_id=?', (csid, today_iso, _profile_id())).fetchone()
        if cs:
            done_exs = db.execute('\n                SELECT e.name, COUNT(sl.id) AS sets, MAX(sl.reps) AS reps\n                FROM session_lifts sl JOIN exercises e ON e.id = sl.exercise_id\n                WHERE sl.session_id = ? GROUP BY sl.exercise_id ORDER BY MIN(sl.id)\n            ', (csid,)).fetchall()
            continue_session = {
                'id': cs['id'],
                'exercises': [{'name': r['name'], 'sets': r['sets'], 'reps': r['reps']} for r in done_exs] }
    db.close()
    return render_template('workout_log_new.html', today=today_iso, by_muscle=_gayathri_by_muscle(), cardio=_GAYATHRI_CARDIO, today_sessions=today_sessions, continue_session=continue_session)


def _arjun_workout_data():
    '''Build Train-screen data for the Arjun profile from his real exercise
    library. Arjun has no routines — the Exercises tab is the entire Train UI.
    Returns ({}, by_muscle, cardio).'''
    db = get_db()
    rows = [dict(r) for r in db.execute('SELECT id, name, tier, muscle_group, day_type, is_timed, is_barbell, reps_min, reps_max, sets_min, sets_max FROM exercises').fetchall()]
    db.close()
    
    def shape(r):
        timed = bool(r['is_timed'])
        rmin = r['reps_min'] if r['reps_min'] is not None else (20 if timed else 5)
        rmax = r['reps_max'] if r['reps_max'] is not None else (45 if timed else 12)
        smin = r['sets_min'] if r['sets_min'] is not None else 2
        smax = r['sets_max'] if r['sets_max'] is not None else 5
        reps = r['reps_min'] if r['reps_min'] is not None else (30 if timed else 8)
        sets = r['sets_min'] if r['sets_min'] is not None else 3
        return {
            'name': r['name'],
            'muscle_group': r['muscle_group'],
            'reps': reps,
            'sets': sets,
            'timed': timed,
            'reps_range': [
                rmin,
                rmax],
            'sets_range': [
                smin,
                smax],
            'location': 'home' }

    _MG_ORDER = [
        'Chest',
        'Back',
        'Upper Back',
        'Shoulders',
        'Biceps',
        'Triceps',
        'Legs',
        'Posterior Chain',
        'Core',
        'Mobility']
    by_muscle = { }
    for r in rows:
        mg = r['muscle_group']
        if mg == 'Cardio':
            continue
        by_muscle.setdefault(mg, []).append(shape(r))
    by_muscle = {mg: by_muscle[mg] for mg in _MG_ORDER if mg in by_muscle} | {mg: v for mg, v in by_muscle.items() if mg not in _MG_ORDER}
    cardio = [{'name': r['name'], 'desc': ''} for r in rows if r['muscle_group'] == 'Cardio']
    return ({ }, by_muscle, cardio)


@app.route('/workout')
def workout():
    today = date_type.today().isoformat()
    done = request.args.get('done') == '1'

    # ── Gayathri / Raj: tap-to-log lives on the home dashboard now ────────────────
    if _profile_id() in (2, 3):
        return redirect(url_for('dashboard'))

    # ── Arjun: Exercises tab is the whole Train UI ──
    tab = request.args.get('tab', 'exercises')
    if tab not in ('routines', 'exercises') or tab == 'routines':
        tab = 'exercises'
    (routines, by_muscle, cardio) = _arjun_workout_data()
    priority_json = _json.dumps(get_priority_lifts(_profile_id()))
    t1_choices_json = _json.dumps([{'id': ex['id'], 'name': ex['name'], 'muscle_group': ex['muscle_group']} for ex in get_tier1_suggestion()])
    return render_template('workout.html', routines=routines, today=today, done=done, tab=tab, by_muscle=by_muscle, cardio=cardio, priority_json=priority_json, t1_choices_json=t1_choices_json)


@app.route('/workout/log', methods=[ 'POST'])
def workout_log():
    if _profile_id() not in (2, 3):
        return redirect(url_for('dashboard'))
    routine_key = request.form.get('routine', '').strip()
    date_val = request.form.get('date', date_type.today().isoformat()).strip()
    routine = _GAYATHRI_ROUTINES.get(routine_key)
    if not routine:
        return redirect(url_for('workout'))
    db = get_db()
    warmup = routine.get('warmup')
    notes = 'Warmup: ' + warmup if warmup else None
    sess_id = db.execute('INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
                         (date_val, 'gym', notes, _datetime.utcnow().isoformat() + 'Z', _profile_id())).lastrowid
    any_lifts = False
    for i, ex_spec in enumerate(routine['exercises']):
        ex = db.execute('SELECT id FROM exercises WHERE name=? COLLATE NOCASE', (ex_spec['name'],)).fetchone()
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
            db.execute('INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                       (sess_id, ex['id'], set_num, ex_spec['reps'], weight))
            any_lifts = True
    if not any_lifts:
        db.rollback()
        db.close()
        return redirect(url_for('workout'))
    db.execute('UPDATE sessions SET ended_at=? WHERE id=? AND ended_at IS NULL',
               (_datetime.utcnow().isoformat() + 'Z', sess_id))
    db.commit()
    db.close()
    return redirect(url_for('workout', done=1))


@app.route('/workout/custom/log', methods=[ 'POST'])
def workout_custom_log():
    if _profile_id() not in (2, 3):
        return redirect(url_for('dashboard'))
    date_val = _validate_date(request.form.get('date', ''))
    ex_names = request.form.getlist('ex_name')
    ex_reps = request.form.getlist('ex_reps')
    ex_sets = request.form.getlist('ex_sets')
    db = get_db()
    continuing = False
    existing_max_sets = {}
    continue_id_str = request.form.get('continue_session_id', '').strip()
    if continue_id_str and continue_id_str.isdigit():
        csid = int(continue_id_str)
        existing = db.execute('SELECT id FROM sessions WHERE id=? AND type="gym" AND profile_id=?',
                              (csid, _profile_id())).fetchone()
        if existing:
            sess_id = csid
            continuing = True
            for row in db.execute('SELECT exercise_id, MAX(set_number) AS mx FROM session_lifts WHERE session_id=? GROUP BY exercise_id',
                                  (sess_id,)).fetchall():
                existing_max_sets[row['exercise_id']] = row['mx']
    if not continuing:
        if not ex_names:
            db.close()
            return redirect(url_for('workout_log_new'))
        now = _datetime.utcnow().isoformat() + 'Z'
        sess_id = db.execute('INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)',
                             (date_val, 'gym', 'custom', now, _profile_id())).lastrowid
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
            db.execute('INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                       (sess_id, ex['id'], set_num + set_offset, reps, 0.0))
            any_lifts = True
    if not any_lifts:
        if continuing:
            db.close()
            return redirect(url_for('session_detail', session_id=sess_id))
        db.rollback()
        db.close()
        return redirect(url_for('workout_log_new'))
    db.execute('UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL',
               (_datetime.utcnow().isoformat() + 'Z', sess_id))
    db.commit()
    db.close()
    return redirect(url_for('workout', done=1))


@app.route('/workout/session/start', methods=[ 'POST'])
def workout_session_start():
    if _profile_id() not in (2, 3):
        return redirect(url_for('dashboard'))
    routine_key = request.form.get('routine', '').strip()
    date_val = _validate_date(request.form.get('date', ''))
    if routine_key not in _GAYATHRI_ROUTINES and routine_key != 'custom':
        return redirect(url_for('workout'))
    db = get_db()
    sess_id = db.execute('INSERT INTO sessions (date, type, notes, started_at, profile_id) VALUES (?,?,?,?,?)', (date_val, 'gym', f'''routine:{routine_key}''', _datetime.utcnow().isoformat() + 'Z', _profile_id())).lastrowid
    db.commit()
    db.close()
    return redirect(url_for('workout_session', session_id=sess_id))


@app.route('/workout/session/<int:session_id>')
def workout_session(session_id):
    if _profile_id() not in (2, 3):
        return redirect(url_for('dashboard'))
    db = get_db()
    sess = db.execute('SELECT * FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['type'] != 'gym' or sess['profile_id'] != _profile_id():
        db.close()
        return redirect(url_for('workout'))
    notes = sess['notes'] or ''
    routine_key = notes[len('routine:'):] if notes.startswith('routine:') else None
    routine = _GAYATHRI_ROUTINES.get(routine_key) if routine_key and routine_key != 'custom' else None
    lift_rows = db.execute('''
        SELECT sl.id AS lift_id, sl.exercise_id, sl.set_number, sl.reps, sl.weight_kg,
               e.name AS ex_name, e.muscle_group
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE sl.session_id = ?
        ORDER BY (SELECT MIN(sl2.id) FROM session_lifts sl2
                  WHERE sl2.session_id = sl.session_id
                    AND sl2.exercise_id = sl.exercise_id),
                 sl.set_number
    ''', (session_id,)).fetchall()
    logged_by_exid = {}
    for r in lift_rows:
        eid = r['exercise_id']
        if eid not in logged_by_exid:
            logged_by_exid[eid] = []
        logged_by_exid[eid].append({
            'lift_id': r['lift_id'], 'set_number': r['set_number'],
            'reps': r['reps'], 'weight_kg': r['weight_kg']})
    exercises = []
    routine_ex_ids = set()
    _g_by_name = {ex['name']: ex for rtn in _GAYATHRI_ROUTINES.values() for ex in rtn['exercises']}
    if routine:
        for ex in routine['exercises']:
            row = db.execute('SELECT id FROM exercises WHERE name=? COLLATE NOCASE', (ex['name'],)).fetchone()
            if row:
                eid = row['id']
                routine_ex_ids.add(eid)
                exercises.append({**ex, 'exercise_id': eid,
                                  'sets_logged': logged_by_exid.get(eid, [])})
    for eid, sets in logged_by_exid.items():
        if eid not in routine_ex_ids:
            ex_row = db.execute('SELECT name, muscle_group FROM exercises WHERE id=?', (eid,)).fetchone()
            if ex_row:
                template = _g_by_name.get(ex_row['name'], {})
                exercises.append({
                    'exercise_id': eid, 'name': ex_row['name'], 'muscle_group': ex_row['muscle_group'],
                    'weighted': template.get('weighted', True), 'timed': template.get('timed', False),
                    'reps': template.get('reps', 8), 'sets': template.get('sets', 3),
                    'reps_range': template.get('reps_range', [6, 15]),
                    'sets_range': template.get('sets_range', [2, 4]),
                    'sets_logged': sets})
    in_session = {e['exercise_id'] for e in exercises}
    picker_rows = db.execute('SELECT id, name, muscle_group FROM exercises ORDER BY muscle_group, name').fetchall()
    db.close()
    picker_exs = []
    for r in picker_rows:
        if r['id'] in in_session:
            continue
        tmpl = _g_by_name.get(r['name'], {})
        picker_exs.append({
            'id': r['id'], 'name': r['name'], 'mg': r['muscle_group'],
            'weighted': tmpl.get('weighted', True), 'timed': tmpl.get('timed', False),
            'reps': tmpl.get('reps', 8), 'sets': tmpl.get('sets', 3),
            'reps_range': tmpl.get('reps_range', [6, 15]),
            'sets_range': tmpl.get('sets_range', [2, 4])})
    return render_template('workout_session.html', session=dict(sess), routine=routine,
                           routine_key=routine_key, exercises=exercises,
                           picker_exs_json=_json.dumps(picker_exs),
                           today=date_type.today().isoformat())

@app.route('/sessions')
def sessions():
    _d = date_type
    _td = timedelta
    rows = get_sessions_with_headlines(_profile_id())
    today = _d.today()
    for r in rows:
        d = _d.fromisoformat(r['date'])
        r['date_fmt'] = _fmt_date(r['date'])
        iso = d.isocalendar()
        r['week_group'] = f'''{iso[0]}-W{iso[1]:02d}'''
        monday = d - _td(days=d.weekday())
        sunday = monday + _td(days=6)
        days_ago = (today - monday).days
        if days_ago < 7:
            r['week_display'] = 'This week'
            continue
        if days_ago < 14:
            r['week_display'] = 'Last week'
            continue
        if monday.month == sunday.month:
            r['week_display'] = f'''{_DAYS[0]} {monday.day} – {_DAYS[6]} {sunday.day} {_MONTHS[monday.month - 1]} {monday.year if monday.year != today.year else ''}'''.strip()
            continue
        r['week_display'] = f'''{monday.day} {_MONTHS[monday.month - 1]} – {sunday.day} {_MONTHS[sunday.month - 1]}'''
    return render_template('sessions.html', sessions=rows)


@app.route('/cardio', methods=[ 'GET', 'POST'])
def cardio():
    date_val = _validate_date(request.args.get('date', '') or request.form.get('date', ''))
    if request.method == 'POST':
        ex_str = request.form.get('exercise_id', '').strip()
        if not ex_str.isdigit():
            return redirect(url_for('cardio', date=date_val))
        ex_id = int(ex_str)
        distance = _safe_float(request.form.get('distance_m'), None)
        duration_min = _safe_float(request.form.get('duration_min'), None)
        duration_s = int(duration_min * 60) if duration_min else None
        resistance = _safe_float(request.form.get('resistance'), None)
        speed = _safe_float(request.form.get('speed'), None)
        log_cardio_session(_profile_id(), date_val, ex_id,
                           distance_m=distance, duration_s=duration_s,
                           resistance=resistance, speed=speed)
        return redirect(url_for('dashboard'))
    return render_template('cardio.html', date_str=date_val, choices=get_cardio_choices())


@app.route('/session/<int:session_id>')
def session_detail(session_id):
    (sess, lifts, duration_min) = get_session_detail_with_progression(session_id)
    if sess is None:
        return ('Session not found', 404)
    if sess['profile_id'] != _profile_id():
        abort(403)
    if sess['type'] == 'pilates':
        detail = get_pilates_session(session_id)
        if detail is None:
            return ('Session not found', 404)
        return render_template('pilates/pilates_session.html', session=sess, detail=detail)
    cardio = get_session_cardio(session_id) if sess['type'] in ('gym', 'cardio') else []
    swim_dist = None
    if sess['type'] == 'swim':
        db = get_db()
        rows = db.execute('SELECT distance_m, rep_distance_m, sets FROM swim_logs WHERE session_id = ? ORDER BY id', (session_id,)).fetchall()
        db.close()
        if rows:
            swim_dist = {
                'groups': [{'sets': r['sets'], 'rep_distance': r['rep_distance_m'], 'distance': r['distance_m']} for r in rows],
                'total': sum(r['distance_m'] for r in rows) }
    return render_template('session_detail.html', session=sess, lifts=lifts, cardio=cardio, swim_dist=swim_dist, duration_min=duration_min)


@app.route('/session/<int:session_id>/finish', methods=[ 'POST'])
def session_finish(session_id):
    '''Called by JS when user navigates away from session logging.'''
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        return ('', 403)
    db.execute('UPDATE sessions SET ended_at = ? WHERE id = ? AND ended_at IS NULL', (_datetime.utcnow().isoformat() + 'Z', session_id))
    db.commit()
    db.close()
    return ('', 204)


@app.route('/session/<int:session_id>/lift/<int:lift_id>/edit', methods=[ 'POST'])
def lift_edit(session_id, lift_id):
    """Inline set edit from session detail."""
    data = request.get_json(silent=True) or {}
    try:
        reps = int(data['reps'])
        weight_kg = float(data['weight_kg'])
    except (KeyError, ValueError):
        return _json.dumps({'error': 'invalid'}), 400, {'Content-Type': 'application/json'}
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        return _json.dumps({'error': 'forbidden'}), 403, {'Content-Type': 'application/json'}
    db.execute('UPDATE session_lifts SET reps = ?, weight_kg = ? WHERE id = ? AND session_id = ?',
               (reps, weight_kg, lift_id, session_id))
    db.commit()
    db.close()
    return _json.dumps({'ok': True, 'reps': reps, 'weight_kg': weight_kg}), 200, {'Content-Type': 'application/json'}


@app.route('/session/<int:session_id>/add_set', methods=[ 'POST'])
def add_set(session_id):
    """Add a set to an existing exercise in a session."""
    data = request.get_json(silent=True) or {}
    try:
        exercise_id = int(data['exercise_id'])
        reps = int(data['reps'])
        weight_kg = float(data['weight_kg'])
    except (KeyError, ValueError):
        return _json.dumps({'error': 'invalid'}), 400, {'Content-Type': 'application/json'}
    db = get_db()
    sess = db.execute('SELECT profile_id FROM sessions WHERE id=?', (session_id,)).fetchone()
    if not sess or sess['profile_id'] != _profile_id():
        db.close()
        return _json.dumps({'error': 'forbidden'}), 403, {'Content-Type': 'application/json'}
    next_set = db.execute('SELECT COALESCE(MAX(set_number), 0) + 1 FROM session_lifts WHERE session_id = ? AND exercise_id = ?',
                          (session_id, exercise_id)).fetchone()[0]
    try:
        lift_id = db.execute('INSERT INTO session_lifts (session_id, exercise_id, set_number, reps, weight_kg) VALUES (?,?,?,?,?)',
                             (session_id, exercise_id, next_set, reps, weight_kg)).lastrowid
        db.commit()
    except Exception as e:
        db.rollback()
        db.close()
        return _json.dumps({'error': str(e)}), 400, {'Content-Type': 'application/json'}
    db.close()
    return _json.dumps({'ok': True, 'lift_id': lift_id, 'set_number': next_set}), 201, {'Content-Type': 'application/json'}


@app.route('/session/<int:session_id>/delete', methods=[ 'POST'])
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


@app.route('/exercises', methods=[ 'GET', 'POST'])
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
                db.execute('INSERT INTO exercises (name, tier, muscle_group, day_type, notes) VALUES (?,?,?,?,?)',
                           (name, int(tier), muscle_group, 'any', notes))
                db.commit()
                db.close()
                return redirect(url_for('exercises'))
            except Exception as e:
                error = f'Could not add exercise: {e}'
    rows = db.execute('SELECT * FROM exercises ORDER BY tier, day_type, name').fetchall()
    db.close()
    by_tier = {1: [], 2: [], 3: []}
    for row in rows:
        by_tier.setdefault(row['tier'], []).append(row)
    return render_template('exercises.html', by_tier=by_tier, error=error)


@app.route('/exercises/<int:exercise_id>/delete', methods=[ 'POST'])
def exercise_delete(exercise_id):
    db = get_db()
    db.execute('DELETE FROM exercises WHERE id = ?', (exercise_id,))
    db.commit()
    db.close()
    return redirect(url_for('exercises'))


@app.route('/analytics')
def analytics():
    today = date_type.today()
    db = get_db()
    pid = _profile_id()
    prs = db.execute('\n        SELECT e.name, e.muscle_group, e.tier,\n               sl.weight_kg AS best_weight, sl.reps, s.date\n        FROM session_lifts sl\n        JOIN exercises e ON e.id = sl.exercise_id\n        JOIN sessions  s ON s.id = sl.session_id\n        WHERE s.profile_id = ?\n          AND (sl.exercise_id, sl.weight_kg) IN (\n            SELECT sl2.exercise_id, MAX(sl2.weight_kg)\n            FROM session_lifts sl2\n            JOIN sessions s2 ON s2.id = sl2.session_id\n            WHERE s2.profile_id = ?\n            GROUP BY sl2.exercise_id\n        )\n        GROUP BY sl.exercise_id\n        ORDER BY sl.weight_kg DESC\n    ', (pid, pid)).fetchall()
    prog_rows = db.execute('\n        SELECT e.name, s.date, MAX(sl.weight_kg) AS max_weight\n        FROM session_lifts sl\n        JOIN exercises e ON e.id = sl.exercise_id\n        JOIN sessions  s ON s.id = sl.session_id\n        WHERE s.profile_id = ?\n        GROUP BY e.id, s.date\n        ORDER BY e.name, s.date\n    ', (pid,)).fetchall()
    progression = { }
    for r in prog_rows:
        name = r['name']
        if name not in progression:
            progression[name] = {
                'dates': [],
                'weights': [] }
        progression[name]['dates'].append(r['date'])
        progression[name]['weights'].append(r['max_weight'])
    exercise_names = sorted(progression.keys())
    week_labels = []
    volume_data = []
    swim_data = []
    for i in range(11, -1, -1):
        ws = today - timedelta(days=today.weekday()) - timedelta(weeks=i)
        we = ws + timedelta(days=6)
        week_labels.append(ws.strftime('%b %-d'))
        vol = db.execute("\n            SELECT COALESCE(SUM(sl.reps * sl.weight_kg), 0) AS v\n            FROM session_lifts sl\n            JOIN sessions s ON s.id = sl.session_id\n            WHERE s.type = 'gym' AND s.date BETWEEN ? AND ? AND s.profile_id = ?\n        ", (ws.isoformat(), we.isoformat(), pid)).fetchone()['v']
        volume_data.append(round(float(vol), 1))
        dist = db.execute('\n            SELECT COALESCE(SUM(sw.distance_m), 0) AS d\n            FROM swim_logs sw\n            JOIN sessions s ON s.id = sw.session_id\n            WHERE s.date BETWEEN ? AND ? AND s.profile_id = ?\n        ', (ws.isoformat(), we.isoformat(), pid)).fetchone()['d']
        swim_data.append(int(dist))
    db.close()
    return render_template('analytics.html', prs=prs, progression_json=_json.dumps(progression), exercise_names=exercise_names, week_labels=_json.dumps(week_labels), volume_data=_json.dumps(volume_data), swim_data=_json.dumps(swim_data), heatmap_json=_json.dumps(get_activity_calendar()))


@app.route('/exercise-bank')
def exercise_bank():
    return render_template('exercise_bank.html', bank=get_exercise_bank(), msg=request.args.get('msg'), status=request.args.get('status'))


def _bank_bounds_from_form():

    def _v(k):
        raw = request.form.get(k, '').strip()
        return _safe_int(raw, None) if raw else None

    return (_v('reps_min'), _v('reps_max'), _v('sets_min'), _v('sets_max'))


def _bank_redirect(ok, err):
    if ok:
        return redirect(url_for('exercise_bank', msg='Saved', status='ok'))
    return redirect(url_for('exercise_bank', msg=err or 'Something went wrong', status='err'))


@app.route('/exercise-bank/add', methods=[ 'POST'])
def exercise_bank_add():
    name = request.form.get('name', '').strip()
    tier = request.form.get('tier', '').strip()
    muscle_group = request.form.get('muscle_group', '').strip()
    day_type = request.form.get('day_type', 'any').strip() or 'any'
    bounds = (
        _safe_int(request.form.get('reps_min'), None),
        _safe_int(request.form.get('reps_max'), None),
        _safe_int(request.form.get('sets_min'), None),
        _safe_int(request.form.get('sets_max'), None),
    )
    ok, err = bank_add_exercise(name, tier, muscle_group, day_type, bounds)
    if not ok:
        return redirect(url_for('exercise_bank', msg=err, status='err'))
    return redirect(url_for('exercise_bank'))


@app.route('/exercise-bank/<int:exercise_id>/update', methods=[ 'POST'])
def exercise_bank_update(exercise_id):
    tier = request.form.get('tier', '').strip()
    muscle_group = request.form.get('muscle_group', '').strip()
    day_type = request.form.get('day_type', 'any').strip() or 'any'
    bounds = (
        _safe_int(request.form.get('reps_min'), None),
        _safe_int(request.form.get('reps_max'), None),
        _safe_int(request.form.get('sets_min'), None),
        _safe_int(request.form.get('sets_max'), None),
    )
    ok, err = bank_update_exercise(exercise_id, tier, muscle_group, day_type, bounds)
    if not ok:
        return redirect(url_for('exercise_bank', msg=err, status='err'))
    return redirect(url_for('exercise_bank'))


@app.route('/exercise-bank/<int:exercise_id>/delete', methods=[ 'POST'])
def exercise_bank_delete(exercise_id):
    (ok, err) = bank_delete_exercise(exercise_id)
    return _bank_redirect(ok, err)


@app.route('/progression/arc')
def progression_arc():
    return render_template('progression_arc.html')


@app.route('/progression/api/arc')
def progression_arc_api():
    db = get_db()
    rows = db.execute('\n        SELECT p.exercise_id, p.weight_kg,\n               e.name, e.tier, e.muscle_group, e.reps_only,\n               s.id AS scheme_id, s.reps, s.sets, s.progression_order\n        FROM progression p\n        JOIN exercises e ON e.id = p.exercise_id\n        JOIN schemes   s ON s.id = p.scheme_id\n        ORDER BY e.tier, e.name\n    ').fetchall()
    totals = {r['tier']: r['n'] for r in db.execute('SELECT tier, COUNT(*) AS n FROM schemes GROUP BY tier').fetchall()}
    scheme_rows = db.execute('SELECT id, tier, reps, sets, progression_order FROM schemes ORDER BY tier, progression_order').fetchall()
    db.close()
    schemes_by_tier = { }
    for sr in scheme_rows:
        t = str(sr['tier'])
        schemes_by_tier.setdefault(t, []).append({
            'id': sr['id'],
            'reps': sr['reps'],
            'sets': sr['sets'],
            'progression_order': sr['progression_order'],
            'label': f'''{sr['sets']}×{sr['reps']}''' })
    exercises = []
    for r in rows:
        tier = r['tier']
        total = totals.get(tier, 0)
        cur = r['progression_order']
        exercises.append({
            'id': r['exercise_id'],
            'name': r['name'],
            'tier': tier,
            'muscle_group': r['muscle_group'],
            'reps_only': bool(r['reps_only']),
            'weight_kg': r['weight_kg'],
            'current_scheme': {
                'id': r['scheme_id'],
                'reps': r['reps'],
                'sets': r['sets'],
                'progression_order': cur,
                'label': f'''{r['sets']}×{r['reps']}''' },
            'total_stages': total,
            'cycle_complete': cur >= total })
    return (_json.dumps({
        'exercises': exercises,
        'schemes': schemes_by_tier }), 200, {
        'Content-Type': 'application/json' })


@app.route('/progression')
def progression():
    items = get_all_progressions()
    return render_template('progression.html', items=items)


@app.route('/progression/advance', methods=[ 'POST'])
def progression_advance():
    data = request.get_json()
    result = advance_progression(int(data['exercise_id']), int(data['tier']))
    return (_json.dumps(result), 200, {
        'Content-Type': 'application/json' })


@app.route('/progression/set-weight', methods=[ 'POST'])
def progression_set_weight():
    data = request.get_json()
    result = set_progression_weight(int(data['exercise_id']), float(data['weight_kg']), int(data['tier']), scheme_id=data.get('scheme_id'))
    return (_json.dumps(result), 200, {
        'Content-Type': 'application/json' })


@app.route('/food/shared')
def food_shared():
    date_str = request.args.get('date', date_type.today().isoformat())
    d = date_type.fromisoformat(date_str)
    today = date_type.today()
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
        goals = get_macro_goals(pid)
        entries = [ dict(e) for e in get_food_log(date_str, pid)]
        profiles_data.append({
            'id': pid,
            'name': pname,
            'goals': goals,
            'entries': entries })
    return render_template('food_shared.html', date_str=date_str, date_label=date_label, prev_date=prev_date, next_date=next_date, is_today=d == today, profiles=profiles_data)


@app.route('/food')
def food():
    pid = _profile_id()
    date_str = _validate_date(request.args.get('date', ''))
    d = date_type.fromisoformat(date_str)
    today = date_type.today()
    prev_date = (d - timedelta(days=1)).isoformat()
    next_date = (d + timedelta(days=1)).isoformat()
    if d == today:
        date_label = 'Today'
    elif d == today - timedelta(days=1):
        date_label = 'Yesterday'
    else:
        date_label = d.strftime('%a, %b %-d')
    sync_food_log_from_library(date_str, pid)
    goals = get_macro_goals(pid)
    _rows = get_food_log(date_str, pid)
    entries = [ dict(e) for e in _rows ]
    totals = {
        'calories': round(sum(e['calories'] for e in entries), 1),
        'protein_g': round(sum(e['protein_g'] for e in entries), 1),
        'carbs_g': round(sum(e['carbs_g'] for e in entries), 1),
        'fat_g': round(sum(e['fat_g'] for e in entries), 1) }
    library = [dict(r) for r in get_food_library() if r['calories'] > 0]
    food_map = {r['name'].lower(): r for r in library}
    components = get_food_components()
    today_weight = get_body_weight(date_str, pid)
    weight_history = get_body_weight_history(pid, days=30)
    is_today = d == today
    pending = get_pending_foods(pid)
    history = get_food_history(pid)
    return render_template('food.html', date_str=date_str, date_label=date_label, prev_date=prev_date, next_date=next_date, is_today=is_today, goals=goals, entries=entries, totals=totals, pending=pending, history=history, food_library=library, food_map=food_map, food_components=components, today_weight=today_weight, weight_history=weight_history)


@app.route('/food/log', methods=[ 'POST'])
def food_log_add():
    date_str = _validate_date(request.form.get('date', ''))
    raw_name = request.form.get('food_entry', '').strip()
    quantity = _safe_float(request.form.get('quantity'))
    if not raw_name:
        return redirect(url_for('food', date=date_str))
    pid = _profile_id()
    (name, was_normalized) = _normalize_food_name(raw_name)
    if was_normalized:
        log_food_reconciliation(raw_name, name, pid)
    log_food_entry(date_str, name, profile_id=pid, quantity=quantity or None)
    return redirect(url_for('food', date=date_str))


@app.route('/food/api/entry/<int:entry_id>/meal', methods=[ 'POST'])
def food_api_entry_meal(entry_id):
    data = request.get_json(silent=True) or {}
    meal_type = data.get('meal_type', 'snack')
    if meal_type not in ('breakfast', 'lunch', 'dinner', 'snack'):
        return (_json.dumps({
            'error': 'invalid meal_type' }), 400, {
            'Content-Type': 'application/json' })
    conn = get_db()
    row = conn.execute('SELECT profile_id FROM food_log WHERE id=?', (entry_id,)).fetchone()
    if not row or row['profile_id'] != _profile_id():
        conn.close()
        return (_json.dumps({
            'error': 'forbidden' }), 403, {
            'Content-Type': 'application/json' })
    conn.execute('UPDATE food_log SET meal_type=? WHERE id=?', (meal_type, entry_id))
    conn.commit()
    conn.close()
    return (_json.dumps({
        'ok': True,
        'meal_type': meal_type }), 200, {
        'Content-Type': 'application/json' })


@app.route('/food/api/entry/<int:entry_id>/update', methods=[ 'POST'])
def food_api_entry_update(entry_id):
    """Edit a logged entry's name and/or macros in place (ownership-checked)."""
    data = request.get_json(silent=True) or {}
    conn = get_db()
    row = conn.execute('SELECT * FROM food_log WHERE id=?', (entry_id,)).fetchone()
    if not row or row['profile_id'] != _profile_id():
        conn.close()
        return (_json.dumps({
            'error': 'forbidden' }), 403, {
            'Content-Type': 'application/json' })
    name = row['name']
    cal = round(_safe_float(data.get('calories'), row['calories']), 1)
    pro = round(_safe_float(data.get('protein_g'), row['protein_g']), 1)
    carb = round(_safe_float(data.get('carbs_g'), row['carbs_g']), 1)
    fat = round(_safe_float(data.get('fat_g'), row['fat_g']), 1)
    conn.execute('UPDATE food_log SET name=?, calories=?, protein_g=?, carbs_g=?, fat_g=? WHERE id=?', (name, cal, pro, carb, fat, entry_id))
    conn.commit()
    updated = conn.execute('SELECT * FROM food_log WHERE id=?', (entry_id,)).fetchone()
    conn.close()
    return (_json.dumps({
        'entry': {
            'id': updated['id'],
            'name': updated['name'],
            'meal_type': updated['meal_type'],
            'calories': updated['calories'],
            'protein_g': updated['protein_g'],
            'carbs_g': updated['carbs_g'],
            'fat_g': updated['fat_g'],
            'logged_at': updated['logged_at'] } }), 200, {
        'Content-Type': 'application/json' })


@app.route('/food/api/entry', methods=[ 'POST'])
def food_api_entry():
    data = request.get_json(silent=True) or {}
    pid = _profile_id()
    date_str = _validate_date(data.get('date', ''), date_type.today().isoformat())
    raw_name = (data.get('name') or '').strip()
    meal_type = data.get('meal_type', 'snack')
    if meal_type not in ('breakfast', 'lunch', 'dinner', 'snack'):
        meal_type = 'snack'
    if not raw_name:
        return (_json.dumps({
            'error': 'name required' }), 400, {
            'Content-Type': 'application/json' })
    (name, was_normalized) = _normalize_food_name(raw_name)
    if was_normalized:
        log_food_reconciliation(raw_name, name, pid)
    explicit = {
        'calories': _safe_float(data.get('calories')),
        'protein_g': _safe_float(data.get('protein_g')),
        'carbs_g': _safe_float(data.get('carbs_g')),
        'fat_g': _safe_float(data.get('fat_g')) }
    quantity = data.get('quantity')
    row = log_food_entry(date_str, name, profile_id=pid, meal_type=meal_type, quantity=quantity, explicit_macros=explicit)
    return (_json.dumps({
        'entry': {
            'id': row['id'],
            'name': row['name'],
            'meal_type': row['meal_type'],
            'calories': row['calories'],
            'protein_g': row['protein_g'],
            'carbs_g': row['carbs_g'],
            'fat_g': row['fat_g'],
            'logged_at': row['logged_at'] } }), 201, {
        'Content-Type': 'application/json' })


@app.route('/food/delete/<int:entry_id>', methods=[ 'POST'])
def food_log_delete(entry_id):
    date_str = request.form.get('date', date_type.today().isoformat())
    conn = get_db()
    row = conn.execute('SELECT profile_id FROM food_log WHERE id=?', (entry_id,)).fetchone()
    conn.close()
    if not row or row['profile_id'] != _profile_id():
        abort(403)
    delete_food_entry(entry_id)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return (_json.dumps({
            'ok': True }), 200, {
            'Content-Type': 'application/json' })
    return redirect(url_for('food', date=date_str))


@app.route('/food/goals', methods=[ 'POST'])
def food_goals_set():
    pid = _profile_id()
    current = get_macro_goals(pid)
    set_macro_goals(_safe_float(request.form.get('calories'), current['calories']), _safe_float(request.form.get('protein_g'), current['protein_g']), _safe_float(request.form.get('carbs_g'), current['carbs_g']), _safe_float(request.form.get('fat_g'), current['fat_g']), pid)
    return redirect(url_for('food'))


@app.route('/food/meal', methods=[ 'POST'])
def food_meal_log():
    '''Create/refresh a reusable recipe (whole-recipe compound food) from its
    ingredients, then optionally log a portion today as a percentage of the whole.'''
    date_str = _validate_date(request.form.get('date', ''))
    meal_name = request.form.get('meal_name', '').strip()
    meal_type = request.form.get('meal_type', 'snack')
    if meal_type not in ('breakfast', 'lunch', 'dinner', 'snack'):
        meal_type = 'snack'
    log_percent = _safe_float(request.form.get('log_percent'), 100)
    ings = request.form.getlist('ing')
    qtys = request.form.getlist('qty')
    components = []
    for ing, qty_s in zip(ings, qtys):
        ing = ing.strip()
        qty = _safe_float(qty_s)
        if ing and qty > 0:
            components.append((ing, qty))
    if not meal_name or not components:
        return redirect(url_for('food', date=date_str))
    pid = _profile_id()
    save_food_components(meal_name, components)
    fraction = max(0, log_percent) / 100
    if fraction > 0:
        name = meal_name if abs(fraction - 1) < 1e-09 else f'''{fraction:g}x {meal_name}'''
        log_food_entry(date_str, name, profile_id=pid, meal_type=meal_type)
    return redirect(url_for('food', date=date_str))


@app.route('/food/components/<path:name>', methods=[ 'POST'])
def food_components_save(name):
    date_str = request.form.get('date', date_type.today().isoformat())
    mode = 'pct' if request.form.get('recipe_mode') == 'pct' else 'quantity'
    ings = request.form.getlist('ing')
    if mode == 'pct':
        pcts = request.form.getlist('pct')
        components = [(ing.strip(), _safe_float(p)) for ing, p in zip(ings, pcts)]
        components = [(ing, p) for ing, p in components if ing and p > 0]
        save_food_components_pct(name, components)
    else:
        qtys = request.form.getlist('qty')
        components = [(ing.strip(), _safe_float(q)) for ing, q in zip(ings, qtys)]
        components = [(ing, q) for ing, q in components if ing and q > 0]
        save_food_components(name, components)
    return redirect(url_for('food_edit', name=name))


@app.route('/food/database')
def food_database():
    pid = _profile_id()
    library = [dict(r) for r in get_food_library()]
    lib_names_lower = {item['name'].lower() for item in library}
    for pname in get_pending_foods(pid):
        if pname.lower() not in lib_names_lower:
            library.append({'name': pname, 'calories': 0, 'protein_g': 0, 'carbs_g': 0, 'fat_g': 0, 'unit_type': 'g'})
    comps_map = get_food_components()
    recipe_keys = set(comps_map.keys())
    composite_keys = {k for k, v in comps_map.items() if any((c.get('pct') or 0) > 0 for c in v)}
    for item in library:
        nl = item['name'].lower()
        item['is_pending'] = item['calories'] == 0
        item['is_composite'] = nl in composite_keys
        # 'recipe' now means a fixed-portion recipe; percentage composites get their own badge.
        item['is_recipe'] = nl in recipe_keys and not item['is_composite']
    library.sort(key=lambda x: (not x['is_pending'], x['name'].lower()))
    counts = {'all': len(library), 'defined': sum(1 for i in library if not i['is_pending']), 'pending': sum(1 for i in library if i['is_pending']), 'recipes': sum(1 for i in library if i['is_recipe']), 'composites': sum(1 for i in library if i['is_composite'])}
    return render_template('food_database.html', food_library=library, counts=counts)


@app.route('/food/edit/new', methods=['GET', 'POST'])
def food_edit_new():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return redirect(url_for('food_edit_new'))
        unit_type = 'unit' if request.form.get('unit_type') == 'unit' else 'g'
        define_food_item(name,
            float(request.form.get('calories', 0) or 0),
            float(request.form.get('protein_g', 0) or 0),
            float(request.form.get('carbs_g', 0) or 0),
            float(request.form.get('fat_g', 0) or 0),
            unit_type)
        return redirect(url_for('food_edit', name=name))
    return render_template('food_edit.html', mode='new', name='',
                           existing=None, components=[], food_library=[], macros_json='{}')


@app.route('/food/edit/<path:name>', methods=['GET', 'POST'])
def food_edit(name):
    pid = _profile_id()
    conn = get_db()
    existing = conn.execute('SELECT * FROM food_items WHERE name=? COLLATE NOCASE', (name,)).fetchone()
    conn.close()
    existing_dict = dict(existing) if existing else None
    if request.method == 'POST':
        unit_type = 'unit' if request.form.get('unit_type') == 'unit' else 'g'
        define_food_item(name,
            float(request.form.get('calories', 0) or 0),
            float(request.form.get('protein_g', 0) or 0),
            float(request.form.get('carbs_g', 0) or 0),
            float(request.form.get('fat_g', 0) or 0),
            unit_type)
        sync_food_log_from_library(date_type.today().isoformat(), pid)
        return redirect(url_for('food_edit', name=name))
    comps = get_food_components().get(name.lower(), [])
    recipe_mode = get_food_component_mode(name) or 'pct'
    library = get_food_library()
    macros_json = _json.dumps({item['name'].lower(): {
        'calories': item['calories'], 'protein_g': item['protein_g'],
        'carbs_g': item['carbs_g'], 'fat_g': item['fat_g'],
        'unit_type': item['unit_type']} for item in library})
    if existing_dict is None:
        mode = 'pending'
    elif existing_dict['calories'] == 0:
        mode = 'pending'
    else:
        mode = 'existing'
    return render_template('food_edit.html', mode=mode, name=name,
                           existing=existing_dict, components=comps,
                           recipe_mode=recipe_mode,
                           food_library=library, macros_json=macros_json)


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
    return render_template('command_centre.html', reconciliations=get_food_reconciliations())


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
    date_str = _validate_date(request.form.get('date', ''))
    try:
        weight_kg = float(weight_str)
        if weight_kg > 0:
            log_body_weight(date_str, weight_kg, pid)
    except ValueError:
        pass
    ref = request.referrer or ''
    if ref.startswith(request.host_url):
        return redirect(ref)
    return redirect(url_for('dashboard'))


@app.route('/info')
def info():
    return render_template('info.html')


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
    app.run(host='0.0.0.0', port=5001, debug=True)
