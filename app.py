import json as _json
from datetime import date as date_type, timedelta
from flask import Flask, render_template, request, redirect, url_for
from database import (init_db, get_db, get_tier1_suggestion,
                       get_progression, advance_progression,
                       set_progression_weight, get_all_progressions,
                       TIER1_LADDER, TIER2_LADDER)

app = Flask(__name__)
app.jinja_env.filters['enumerate'] = enumerate


def get_activity_calendar():
    """Return {date_iso: [type, ...]} for the past ~54 weeks."""
    db = get_db()
    rows = db.execute(
        "SELECT date, type FROM sessions WHERE date >= date('now','-378 days') ORDER BY date"
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


@app.route('/')
def dashboard():
    today      = date_type.today()
    week_start = (today - timedelta(days=today.weekday())).isoformat()

    db = get_db()

    # 1. Last session with aggregate stats
    last_session = db.execute("""
        SELECT s.*, sw.distance_m,
               COALESCE(agg.total_sets,   0)   AS total_sets,
               COALESCE(agg.total_volume, 0.0) AS total_volume
        FROM sessions s
        LEFT JOIN swim_logs sw ON sw.session_id = s.id
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
        WHERE s.type = 'gym' AND s.date >= ?
    """, (week_start,)).fetchone()['vol']

    # 3. Weekly swim distance
    weekly_swim = db.execute("""
        SELECT COALESCE(SUM(sw.distance_m), 0) AS dist
        FROM swim_logs sw
        JOIN sessions s ON s.id = sw.session_id
        WHERE s.date >= ?
    """, (week_start,)).fetchone()['dist']

    # 4. Tier 1 rotation (sorted least-recently-done first)
    tier1 = get_tier1_suggestion()

    # 5. Next day type from 7-session cycle
    cycle = ['push', 'pull', 'legs', 'core', 'push', 'legs', 'pull']
    gym_count = db.execute(
        "SELECT COUNT(*) AS n FROM sessions WHERE type = 'gym'"
    ).fetchone()['n']
    next_day_type = cycle[gym_count % 7]

    # 6. Top 5 heaviest sets ever
    prs = db.execute("""
        SELECT e.name, sl.weight_kg, sl.reps, s.date
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        JOIN sessions  s ON s.id = sl.session_id
        ORDER BY sl.weight_kg DESC
        LIMIT 5
    """).fetchall()

    db.close()

    return render_template('dashboard.html',
                           last_session=last_session,
                           weekly_volume=weekly_volume,
                           weekly_swim=weekly_swim,
                           tier1=tier1,
                           next_day_type=next_day_type,
                           prs=prs,
                           today=today.isoformat(),
                           heatmap_json=_json.dumps(get_activity_calendar()))


@app.route('/session/new', methods=['GET', 'POST'])
def session_new():
    if request.method == 'POST':
        date_val  = request.form['date']
        day_type  = request.form['day_type']
        notes     = request.form.get('notes', '').strip() or None

        db = get_db()
        session_id = db.execute(
            'INSERT INTO sessions (date, type, day_type, notes) VALUES (?,?,?,?)',
            (date_val, 'gym', day_type, notes)
        ).lastrowid

        for key in request.form:
            if key.startswith('exercise_id_'):
                idx        = key[len('exercise_id_'):]
                ex_id      = request.form[key]
                reps_list  = request.form.getlist(f'reps_{idx}')
                wt_list    = request.form.getlist(f'weight_{idx}')
                for set_num, (r, w) in enumerate(zip(reps_list, wt_list), start=1):
                    if r.strip() and w.strip():
                        db.execute(
                            'INSERT INTO session_lifts '
                            '(session_id, exercise_id, set_number, reps, weight_kg) '
                            'VALUES (?,?,?,?,?)',
                            (session_id, int(ex_id), set_num, int(r), float(w))
                        )

        db.commit()
        db.close()
        return redirect(url_for('session_detail', session_id=session_id))

    # GET ---------------------------------------------------------------
    day_type     = request.args.get('day_type', '')
    session_date = request.args.get('date', date_type.today().isoformat())

    if not day_type:
        return render_template('session_new.html', step=1,
                               session_date=session_date)

    tier1_list = get_tier1_suggestion()

    # Core day: push Weighted Hanging Leg Raise to top of suggestion list
    if day_type == 'core':
        hlr = next((e for e in tier1_list if 'Hanging' in e['name']), None)
        if hlr:
            tier1_list = [hlr] + [e for e in tier1_list if e['id'] != hlr['id']]

    db     = get_db()
    tier2  = [] if day_type == 'core' else db.execute(
        'SELECT * FROM exercises WHERE tier=2 AND day_type=? ORDER BY name',
        (day_type,)
    ).fetchall()
    tier3  = db.execute(
        'SELECT * FROM exercises WHERE tier=3 ORDER BY name'
    ).fetchall()
    db.close()

    import json as _json
    t1_progs = {ex['id']: get_progression(ex['id'], 1) for ex in tier1_list}
    t2_progs = {ex['id']: get_progression(ex['id'], 2) for ex in tier2}

    return render_template('session_new.html', step=2,
                           session_date=session_date, day_type=day_type,
                           tier1_list=tier1_list, tier2=tier2, tier3=tier3,
                           t1_progs_json=_json.dumps(t1_progs),
                           t2_progs=t2_progs)


@app.route('/swim/new', methods=['GET', 'POST'])
def swim_new():
    if request.method == 'POST':
        date_val       = request.form['date']
        rep_distance_m = int(request.form['rep_distance_m'])
        sets           = int(request.form['sets'])
        distance_m     = rep_distance_m * sets
        notes          = request.form.get('notes', '').strip() or None

        db = get_db()
        session_id = db.execute(
            'INSERT INTO sessions (date, type, day_type, notes) VALUES (?,?,?,?)',
            (date_val, 'swim', None, notes)
        ).lastrowid
        db.execute(
            'INSERT INTO swim_logs (session_id, distance_m, rep_distance_m, sets) VALUES (?,?,?,?)',
            (session_id, distance_m, rep_distance_m, sets)
        )
        db.commit()
        db.close()
        return redirect(url_for('session_detail', session_id=session_id))

    return render_template('swim_new.html',
                           session_date=date_type.today().isoformat())


@app.route('/sessions')
def sessions():
    db = get_db()
    rows = db.execute("""
        SELECT
            s.*,
            COALESCE(agg.total_sets,   0)    AS total_sets,
            COALESCE(agg.total_volume, 0.0)  AS total_volume,
            sw.distance_m
        FROM sessions s
        LEFT JOIN (
            SELECT session_id,
                   COUNT(*)                  AS total_sets,
                   SUM(reps * weight_kg)     AS total_volume
            FROM session_lifts
            GROUP BY session_id
        ) agg ON agg.session_id = s.id
        LEFT JOIN swim_logs sw ON sw.session_id = s.id
        ORDER BY s.date DESC
    """).fetchall()
    db.close()
    return render_template('sessions.html', sessions=rows)


@app.route('/session/<int:session_id>')
def session_detail(session_id):
    db = get_db()
    session = db.execute('SELECT * FROM sessions WHERE id = ?',
                         (session_id,)).fetchone()
    if session is None:
        db.close()
        return 'Session not found', 404

    lifts      = []
    swim_dist  = None

    if session['type'] == 'gym':
        rows = db.execute("""
            SELECT sl.set_number, sl.reps, sl.weight_kg,
                   e.id AS exercise_id, e.name, e.tier, e.muscle_group
            FROM session_lifts sl
            JOIN exercises e ON e.id = sl.exercise_id
            WHERE sl.session_id = ?
            ORDER BY e.name, sl.set_number
        """, (session_id,)).fetchall()

        # Group sets by exercise
        grouped = {}
        for r in rows:
            eid = r['exercise_id']
            if eid not in grouped:
                grouped[eid] = {
                    'name': r['name'], 'tier': r['tier'],
                    'muscle_group': r['muscle_group'], 'sets': []
                }
            grouped[eid]['sets'].append({
                'set_number': r['set_number'],
                'reps': r['reps'],
                'weight_kg': r['weight_kg'],
            })
        lifts = list(grouped.values())
    else:
        row = db.execute(
            'SELECT distance_m, rep_distance_m, sets FROM swim_logs WHERE session_id = ?',
            (session_id,)).fetchone()
        if row:
            swim_dist = {
                'total':        row['distance_m'],
                'rep_distance': row['rep_distance_m'],
                'sets':         row['sets'],
            }

    db.close()
    return render_template('session_detail.html',
                           session=session, lifts=lifts, swim_dist=swim_dist)


@app.route('/session/<int:session_id>/delete', methods=['POST'])
def session_delete(session_id):
    db = get_db()
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
        day_type = request.form.get('day_type', '')
        notes = request.form.get('notes', '').strip() or None

        if not name or not tier or not muscle_group or not day_type:
            error = 'Name, tier, muscle group, and day type are required.'
        else:
            try:
                db.execute(
                    'INSERT INTO exercises (name, tier, muscle_group, day_type, notes) VALUES (?,?,?,?,?)',
                    (name, int(tier), muscle_group, day_type, notes)
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
    import json

    today = date_type.today()
    db    = get_db()

    # 1. PRs — best weight per exercise (with reps on that set)
    prs = db.execute("""
        SELECT e.name, e.muscle_group, e.tier,
               sl.weight_kg AS best_weight, sl.reps, s.date
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        JOIN sessions  s ON s.id = sl.session_id
        WHERE (sl.exercise_id, sl.weight_kg) IN (
            SELECT exercise_id, MAX(weight_kg)
            FROM session_lifts
            GROUP BY exercise_id
        )
        GROUP BY sl.exercise_id
        ORDER BY sl.weight_kg DESC
    """).fetchall()

    # 2. Lift progression — max weight per session date per exercise
    prog_rows = db.execute("""
        SELECT e.name, s.date, MAX(sl.weight_kg) AS max_weight
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        JOIN sessions  s ON s.id = sl.session_id
        GROUP BY e.id, s.date
        ORDER BY e.name, s.date
    """).fetchall()

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
            WHERE s.type = 'gym' AND s.date BETWEEN ? AND ?
        """, (ws.isoformat(), we.isoformat())).fetchone()['v']
        volume_data.append(round(float(vol), 1))

        dist = db.execute("""
            SELECT COALESCE(SUM(sw.distance_m), 0) AS d
            FROM swim_logs sw
            JOIN sessions s ON s.id = sw.session_id
            WHERE s.date BETWEEN ? AND ?
        """, (ws.isoformat(), we.isoformat())).fetchone()['d']
        swim_data.append(int(dist))

    db.close()

    return render_template('analytics.html',
                           prs=prs,
                           progression_json=json.dumps(progression),
                           exercise_names=exercise_names,
                           week_labels=json.dumps(week_labels),
                           volume_data=json.dumps(volume_data),
                           swim_data=json.dumps(swim_data),
                           heatmap_json=_json.dumps(get_activity_calendar()))


@app.route('/progression')
def progression():
    items = get_all_progressions()
    return render_template('progression.html', items=items,
                           t1_total=len(TIER1_LADDER),
                           t2_total=len(TIER2_LADDER))


@app.route('/progression/advance', methods=['POST'])
def progression_advance():
    import json as _json
    data   = request.get_json()
    result = advance_progression(int(data['exercise_id']), int(data['tier']))
    return _json.dumps(result), 200, {'Content-Type': 'application/json'}


@app.route('/progression/set-weight', methods=['POST'])
def progression_set_weight():
    import json as _json
    data   = request.get_json()
    result = set_progression_weight(
        int(data['exercise_id']), float(data['weight_kg']), int(data['tier'])
    )
    return _json.dumps(result), 200, {'Content-Type': 'application/json'}


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5001, debug=True)
