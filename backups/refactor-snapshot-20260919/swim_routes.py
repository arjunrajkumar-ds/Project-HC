import json as _json
from datetime import date as _date, datetime as _datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for, session as _fs

from database import get_db

bp = Blueprint('swim', __name__, url_prefix='/swim')

STROKE_COLOURS = {'Fr': '#378ADD', 'Ba': '#1D9E75', 'Br': '#639922', 'Bt': '#D85A30'}
STROKE_NAMES   = {'Fr': 'Freestyle', 'Ba': 'Backstroke', 'Br': 'Breaststroke', 'Bt': 'Butterfly'}
CAT_NAMES      = {'straight_sets': 'Straight Sets', 'pyramid': 'Pyramid',
                  'descending': 'Descending', 'intervals': 'Intervals'}
DIFF_ORDER     = ['beginner', 'intermediate', 'advanced']
STROKE_ORDER   = ['Fr', 'Ba', 'Br', 'Bt']
CAT_ORDER      = ['straight_sets', 'pyramid', 'descending', 'intervals']


# ── Internal helpers ───────────────────────────────────────────────────────────

def _completed_workout_ids(db):
    rows = db.execute(
        "SELECT DISTINCT workout_id FROM swim_sessions WHERE result='completed'"
    ).fetchall()
    return {r['workout_id'] for r in rows}


def _grouped_workouts():
    db = get_db()
    rows = db.execute('SELECT * FROM swim_workouts ORDER BY stroke, category, difficulty, key').fetchall()
    done = _completed_workout_ids(db)
    db.close()

    buckets = {s: {c: {d: [] for d in DIFF_ORDER} for c in CAT_ORDER} for s in STROKE_ORDER}
    for r in rows:
        w = dict(r)
        w['is_completed'] = w['id'] in done
        w['sets'] = _json.loads(w['sets_json'])
        s, c, d = w['stroke'], w['category'], w['difficulty']
        if s in buckets and c in buckets[s] and d in buckets[s][c]:
            buckets[s][c][d].append(w)

    result = []
    for s in STROKE_ORDER:
        cats = []
        for c in CAT_ORDER:
            diffs = [{'difficulty': d, 'workouts': buckets[s][c][d]}
                     for d in DIFF_ORDER if buckets[s][c][d]]
            if diffs:
                cats.append({'category': c, 'category_name': CAT_NAMES[c], 'diff_groups': diffs})
        if cats:
            result.append({'stroke': s, 'stroke_name': STROKE_NAMES[s], 'cat_groups': cats})
    return result


def _check_and_unlock_workouts(completed_wid, db):
    completed_key = db.execute('SELECT key FROM swim_workouts WHERE id=?', (completed_wid,)).fetchone()
    if not completed_key:
        return
    all_done_keys = {r['key'] for r in db.execute(
        "SELECT w.key FROM swim_workouts w "
        "JOIN swim_sessions ss ON ss.workout_id=w.id WHERE ss.result='completed'"
    ).fetchall()}
    all_done_keys.add(completed_key['key'])

    candidates = db.execute(
        "SELECT * FROM swim_workouts WHERE is_unlocked=0 AND unlock_requires_json != '[]'"
    ).fetchall()
    for w in candidates:
        requires = _json.loads(w['unlock_requires_json'])
        if all(k in all_done_keys for k in requires):
            db.execute('UPDATE swim_workouts SET is_unlocked=1 WHERE id=?', (w['id'],))


def _check_achievements(session_id, db):
    row = db.execute('SELECT * FROM swim_sessions WHERE id=?', (session_id,)).fetchone()
    if not row:
        return []

    earned_ids = {r['achievement_id'] for r in
                  db.execute('SELECT achievement_id FROM swim_achievement_unlocks').fetchall()}
    all_achs   = db.execute('SELECT * FROM swim_achievements').fetchall()
    newly      = []

    for ach in all_achs:
        if ach['id'] in earned_ids:
            continue
        key, cat, thr = ach['key'], ach['category'], ach['threshold']
        earned = False

        if cat == 'session':
            cnt = db.execute(
                "SELECT COUNT(*) FROM swim_sessions WHERE result='completed'"
            ).fetchone()[0]
            earned = cnt >= thr

        elif cat == 'distance':
            if key.startswith('first_'):
                earned = row['total_m'] >= thr
            else:  # total_*
                total = db.execute(
                    "SELECT COALESCE(SUM(total_m),0) FROM swim_sessions WHERE result='completed'"
                ).fetchone()[0]
                earned = total >= thr

        elif cat == 'streak':
            d = _date.fromisoformat(row['performed_at'][:10])
            ws = (d - timedelta(days=d.weekday())).isoformat()
            we = (d + timedelta(days=6 - d.weekday())).isoformat()
            wdist = db.execute(
                "SELECT COALESCE(SUM(total_m),0) FROM swim_sessions "
                "WHERE result='completed' AND date(performed_at) BETWEEN ? AND ?",
                (ws, we)
            ).fetchone()[0]
            earned = wdist >= thr

        elif cat == 'collection':
            done_ids = _completed_workout_ids(db)
            done_ids.add(row['workout_id'])
            filters = {
                'complete_fr_beginner':    ("stroke='Fr' AND difficulty='beginner'",),
                'complete_fr_intermediate':("stroke='Fr' AND difficulty='intermediate'",),
                'complete_fr_advanced':    ("stroke='Fr' AND difficulty='advanced'",),
                'complete_ba_beginner':    ("stroke='Ba' AND difficulty='beginner'",),
                'complete_br_beginner':    ("stroke='Br' AND difficulty='beginner'",),
                'complete_bt_beginner':    ("stroke='Bt' AND difficulty='beginner'",),
            }
            if key in filters:
                needed = {r['id'] for r in db.execute(
                    f"SELECT id FROM swim_workouts WHERE {filters[key][0]}"
                ).fetchall()}
                earned = bool(needed) and needed.issubset(done_ids)

        if earned:
            db.execute(
                'INSERT INTO swim_achievement_unlocks (achievement_id, unlocked_at) VALUES (?,?)',
                (ach['id'], _datetime.now().isoformat())
            )
            newly.append(dict(ach))

    return newly


def _check_pbs(session_id, db):
    row = db.execute('SELECT * FROM swim_sessions WHERE id=?', (session_id,)).fetchone()
    if not row:
        return []

    beaten = []
    now = row['performed_at']

    def _upsert_pb(key, value, label):
        cur = db.execute("SELECT value FROM swim_pbs WHERE pb_key=?", (key,)).fetchone()
        if cur is None or value > cur['value']:
            db.execute(
                "INSERT INTO swim_pbs (pb_key,value,achieved_at,session_id) VALUES (?,?,?,?) "
                "ON CONFLICT(pb_key) DO UPDATE SET value=excluded.value, "
                "achieved_at=excluded.achieved_at, session_id=excluded.session_id",
                (key, value, now, session_id)
            )
            beaten.append({'key': key, 'label': label, 'value': value})

    _upsert_pb('longest_session_m', row['total_m'], 'Longest Session')

    set_count = db.execute(
        'SELECT COUNT(*) FROM swim_sets WHERE session_id=?', (session_id,)
    ).fetchone()[0]
    _upsert_pb('most_sets_session', set_count, 'Most Sets')

    d  = _date.fromisoformat(row['performed_at'][:10])
    ws = (d - timedelta(days=d.weekday())).isoformat()
    we = (d + timedelta(days=6 - d.weekday())).isoformat()
    wdist = db.execute(
        "SELECT COALESCE(SUM(total_m),0) FROM swim_sessions "
        "WHERE result='completed' AND date(performed_at) BETWEEN ? AND ?",
        (ws, we)
    ).fetchone()[0]
    _upsert_pb('most_distance_week', wdist, 'Best Week')

    return beaten


def _ensure_weekly_goal(db):
    today = _date.today()
    ws    = (today - timedelta(days=today.weekday())).isoformat()
    row   = db.execute('SELECT * FROM swim_weekly_goals WHERE week_start=?', (ws,)).fetchone()
    if row:
        return dict(row)

    avg = db.execute("""
        SELECT COALESCE(AVG(wt),3000) FROM (
            SELECT SUM(total_m) AS wt
            FROM swim_sessions
            WHERE result='completed' AND date(performed_at) < ?
            GROUP BY strftime('%Y-%W', performed_at)
            ORDER BY strftime('%Y-%W', performed_at) DESC
            LIMIT 4
        )
    """, (ws,)).fetchone()[0]

    target = max(int(round(avg / 500)) * 500, 1000)
    db.execute(
        'INSERT OR IGNORE INTO swim_weekly_goals (week_start,target_m,is_user_override) VALUES (?,?,0)',
        (ws, target)
    )
    return {'week_start': ws, 'target_m': target, 'is_user_override': 0}


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route('/', strict_slashes=False)
def swim_log():
    flash = _fs.pop('swim_flash', None)
    return render_template('swim/swim.html',
                           tab='log',
                           session_date=_date.today().isoformat(),
                           grouped_workouts=_grouped_workouts(),
                           stroke_colours=STROKE_COLOURS,
                           flash_data=flash)


@bp.route('/progress')
def swim_progress():
    return render_template('swim/swim.html',
                           tab='progress',
                           session_date=_date.today().isoformat(),
                           grouped_workouts=[],
                           stroke_colours=STROKE_COLOURS,
                           flash_data=None)


@bp.route('/new', methods=['GET', 'POST'])
def swim_new_session():
    if request.method == 'GET':
        return redirect(url_for('swim.swim_log'))

    date_val   = request.form.get('date', _date.today().isoformat())
    workout_id = request.form.get('workout_id', '').strip()
    notes      = request.form.get('notes', '').strip() or None

    if not workout_id:
        return redirect(url_for('swim.swim_log'))
    workout_id = int(workout_id)

    db = get_db()
    workout = db.execute('SELECT * FROM swim_workouts WHERE id=?', (workout_id,)).fetchone()
    if not workout or not workout['is_unlocked']:
        db.close()
        return redirect(url_for('swim.swim_log'))

    reps_list   = request.form.getlist('swim_reps')
    dist_list   = request.form.getlist('swim_dist')
    stroke_list = request.form.getlist('swim_stroke')

    sets = []
    for i, (r, d, s) in enumerate(zip(reps_list, dist_list, stroke_list)):
        try:
            reps, dist = int(r), int(d)
        except (ValueError, TypeError):
            continue
        if reps > 0 and dist > 0:
            sets.append({'reps': reps, 'distance_m': dist,
                         'stroke': s or workout['stroke'], 'order': i})

    if not sets:
        db.close()
        return redirect(url_for('swim.swim_log'))

    total_m = sum(s['reps'] * s['distance_m'] for s in sets)
    result  = 'completed' if total_m >= workout['target_m'] else 'failed'
    perf_at = date_val + 'T12:00:00'

    # Dual-write to main sessions table (heatmap/dashboard compatibility)
    db.execute('INSERT INTO sessions (date, type, notes, profile_id) VALUES (?,?,?,?)',
               (date_val, 'swim', notes, _fs.get('profile_id', 1)))

    session_id = db.execute(
        'INSERT INTO swim_sessions (workout_id, performed_at, total_m, result, notes) '
        'VALUES (?,?,?,?,?)',
        (workout_id, perf_at, total_m, result, notes)
    ).lastrowid

    for s in sets:
        db.execute(
            'INSERT INTO swim_sets (session_id, reps, distance_m, stroke, set_order) '
            'VALUES (?,?,?,?,?)',
            (session_id, s['reps'], s['distance_m'], s['stroke'], s['order'])
        )

    db.commit()

    new_ach = []
    beaten  = []
    if result == 'completed':
        _check_and_unlock_workouts(workout_id, db)
    new_ach = _check_achievements(session_id, db)
    beaten  = _check_pbs(session_id, db)
    _ensure_weekly_goal(db)
    db.commit()
    db.close()

    _fs['swim_flash'] = {
        'total_m':      total_m,
        'result':       result,
        'workout_name': workout['name'],
        'achievements': [a['name'] for a in new_ach],
        'pbs':          [p['label'] for p in beaten],
    }
    return redirect(url_for('swim.swim_log'))


@bp.route('/api/workouts')
def api_workouts():
    return _json.dumps(_grouped_workouts()), 200, {'Content-Type': 'application/json'}


@bp.route('/api/progress')
def api_progress():
    db = get_db()
    workouts = db.execute('SELECT * FROM swim_workouts').fetchall()
    done_ids = _completed_workout_ids(db)
    cnt_map  = {r['workout_id']: r['c'] for r in db.execute(
        "SELECT workout_id, COUNT(*) AS c FROM swim_sessions WHERE result='completed' GROUP BY workout_id"
    ).fetchall()}

    workouts_data = []
    for w in workouts:
        wid = w['id']
        hist = db.execute(
            'SELECT performed_at, total_m, result FROM swim_sessions '
            'WHERE workout_id=? ORDER BY performed_at DESC LIMIT 10', (wid,)
        ).fetchall()
        workouts_data.append({
            'id': wid, 'key': w['key'], 'name': w['name'],
            'category': w['category'], 'stroke': w['stroke'], 'difficulty': w['difficulty'],
            'sets_json': w['sets_json'], 'target_m': w['target_m'],
            'unlock_requires_json': w['unlock_requires_json'],
            'is_unlocked': bool(w['is_unlocked']),
            'description': w['description'],
            'tree_x': w['tree_x'], 'tree_y': w['tree_y'],
            'is_completed': wid in done_ids,
            'completion_count': cnt_map.get(wid, 0),
            'history': [dict(h) for h in hist],
        })

    unlocked_map = {r['achievement_id']: r['unlocked_at'] for r in
                    db.execute('SELECT * FROM swim_achievement_unlocks').fetchall()}
    achievements = [{
        'id': a['id'], 'key': a['key'], 'name': a['name'],
        'description': a['description'], 'category': a['category'],
        'threshold': a['threshold'], 'icon': a['icon'],
        'is_unlocked': a['id'] in unlocked_map,
        'unlocked_at': unlocked_map.get(a['id'], ''),
    } for a in db.execute('SELECT * FROM swim_achievements').fetchall()]

    pbs = {p['pb_key']: {'value': p['value'], 'achieved_at': p['achieved_at']}
           for p in db.execute('SELECT * FROM swim_pbs').fetchall()}

    today = _date.today()
    ws    = (today - timedelta(days=today.weekday())).isoformat()
    we    = (today + timedelta(days=6 - today.weekday())).isoformat()
    goal  = _ensure_weekly_goal(db)
    actual_m = db.execute(
        "SELECT COALESCE(SUM(total_m),0) FROM swim_sessions "
        "WHERE result='completed' AND date(performed_at) BETWEEN ? AND ?",
        (ws, we)
    ).fetchone()[0]
    db.commit()
    db.close()

    return _json.dumps({
        'workouts': workouts_data,
        'achievements': achievements,
        'pbs': pbs,
        'weekly_goal': {**goal, 'actual_m': actual_m, 'week_end': we},
        'stroke_colours': STROKE_COLOURS,
    }), 200, {'Content-Type': 'application/json'}


@bp.route('/api/weekly-goal', methods=['GET', 'POST'])
def api_weekly_goal():
    today = _date.today()
    ws    = (today - timedelta(days=today.weekday())).isoformat()
    we    = (today + timedelta(days=6 - today.weekday())).isoformat()

    if request.method == 'POST':
        data     = request.get_json() or {}
        target_m = max(int(data.get('target_m', 3000)), 500)
        db = get_db()
        db.execute(
            'INSERT INTO swim_weekly_goals (week_start,target_m,is_user_override) VALUES (?,?,1) '
            'ON CONFLICT(week_start) DO UPDATE SET target_m=excluded.target_m, is_user_override=1',
            (ws, target_m)
        )
        db.commit()
        db.close()
        return _json.dumps({'ok': True}), 200, {'Content-Type': 'application/json'}

    db = get_db()
    goal     = _ensure_weekly_goal(db)
    actual_m = db.execute(
        "SELECT COALESCE(SUM(total_m),0) FROM swim_sessions "
        "WHERE result='completed' AND date(performed_at) BETWEEN ? AND ?",
        (ws, we)
    ).fetchone()[0]
    db.commit()
    db.close()
    return _json.dumps({**goal, 'actual_m': actual_m, 'week_end': we}), 200, \
           {'Content-Type': 'application/json'}
