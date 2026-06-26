"""Reformer Pilates — predefined routines tracked as their own session type.

Mirrors the swim module: routines and their per-exercise guidance live in the
database for repeated use; logging a session dual-writes a row into the main
`sessions` table (type='pilates') so it shows on the heatmap and history.
"""

from datetime import date as _date
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session as _fs, abort)

from database import (get_db, get_pilates_routines, get_pilates_routine,
                      get_pilates_glossary)

bp = Blueprint('pilates', __name__, url_prefix='/pilates')

# Reformer Pilates is an Arjun-only feature (profile 1), like swimming.
_PILATES_PROFILE = 1


def _guard():
    """Block profile 2 (Gayathri) from the pilates section."""
    if _fs.get('profile_id', 1) != _PILATES_PROFILE:
        return redirect(url_for('food'))
    return None


def _profile_id():
    return _fs.get('profile_id', 1)


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route('/', strict_slashes=False)
def pilates_log():
    g = _guard()
    if g:
        return g
    flash = _fs.pop('pilates_flash', None)
    return render_template('pilates/pilates.html',
                           routines=get_pilates_routines(),
                           flash_data=flash)


@bp.route('/glossary')
def pilates_glossary():
    g = _guard()
    if g:
        return g
    return render_template('pilates/pilates_glossary.html',
                           glossary=get_pilates_glossary())


@bp.route('/new', methods=['GET'])
def pilates_new():
    # Alias used by the dashboard quick-log button.
    return redirect(url_for('pilates.pilates_log'))


@bp.route('/routine/<key>')
def pilates_routine(key):
    g = _guard()
    if g:
        return g
    routine = get_pilates_routine(key=key)
    if not routine:
        return redirect(url_for('pilates.pilates_log'))
    return render_template('pilates/pilates_routine.html',
                           routine=routine,
                           session_date=_date.today().isoformat())


@bp.route('/log', methods=['POST'])
def pilates_log_session():
    g = _guard()
    if g:
        return g

    routine_id = request.form.get('routine_id', '').strip()
    if not routine_id.isdigit():
        return redirect(url_for('pilates.pilates_log'))
    routine = get_pilates_routine(routine_id=int(routine_id))
    if not routine:
        return redirect(url_for('pilates.pilates_log'))

    date_val = request.form.get('date', _date.today().isoformat())
    notes    = request.form.get('notes', '').strip() or None

    # Build the list of completed exercises from the form.
    items = []
    for ex in routine['exercises']:
        eid = ex['id']
        done = request.form.get(f'done_{eid}', '').strip() in ('1', 'true', 'on')
        if not done:
            continue
        version = 'hard' if (ex['has_hard'] and
                             request.form.get(f'version_{eid}', 'standard') == 'hard') else 'standard'
        reps_done = ex['hard']['reps'] if (version == 'hard' and ex['hard']) else ex['reps']
        items.append({
            'exercise_id': eid, 'name': ex['name'], 'version': version,
            'reps_done': reps_done, 'ex_order': ex['ex_order'],
        })

    if not items:
        _fs['pilates_flash'] = {'routine_name': routine['name'],
                                'done_count': 0, 'total_count': len(routine['exercises']),
                                'empty': True}
        return redirect(url_for('pilates.pilates_log'))

    db = get_db()
    # Dual-write to the main sessions table (heatmap / history compatibility).
    main_id = db.execute(
        'INSERT INTO sessions (date, type, notes, profile_id) VALUES (?,?,?,?)',
        (date_val, 'pilates', notes, _profile_id())
    ).lastrowid

    ps_id = db.execute(
        'INSERT INTO pilates_sessions (routine_id, session_id, performed_at, notes) '
        'VALUES (?,?,?,?)',
        (routine['id'], main_id, date_val + 'T12:00:00', notes)
    ).lastrowid

    for it in items:
        db.execute(
            'INSERT INTO pilates_session_exercises '
            '(pilates_session_id, exercise_id, name, version, reps_done, done, ex_order) '
            'VALUES (?,?,?,?,?,1,?)',
            (ps_id, it['exercise_id'], it['name'], it['version'], it['reps_done'], it['ex_order'])
        )

    db.commit()
    db.close()

    _fs['pilates_flash'] = {
        'routine_name': routine['name'],
        'done_count':   len(items),
        'total_count':  len(routine['exercises']),
    }
    return redirect(url_for('pilates.pilates_log'))
