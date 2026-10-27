#!/usr/bin/env python3
"""
qa_stagebar.py — visual QA harness for the stage bar / finish recap / Route view
(docs/HANDOFF-stage-bar-recap-route.md §3.6).

WHAT IT DOES
    1. Copies tracker.db -> /tmp/horsecock-stagebar.db (NEVER touches the real DB).
    2. Starts the Flask app on :5055 against the copy.
    3. Drives Chromium (Playwright) at 375x812 @2x, reproduces each reference
       state by SEEDING THE DB COPY directly (no CSRF needed) + sessionStorage
       for the manual Next/T2 state, screenshots the specified element into
       docs/wireframes/impl/, and stitches each next to its ref as
       <name>-compare.png (ref on the left, your build on the right).

REQUIREMENTS
    pip install playwright pillow && playwright install chromium

RUN
    python docs/wireframes/qa_stagebar.py             # everything
    python docs/wireframes/qa_stagebar.py states      # just the a-f stills
    python docs/wireframes/qa_stagebar.py anim         # the Next filmstrip
    python docs/wireframes/qa_stagebar.py recap        # the finish recap
    python docs/wireframes/qa_stagebar.py route        # the Route view

ADJUST IF NEEDED
    PROFILE_SELECT_URL — how your build selects profile 1 (Arjun / scheme).
    EX_* names         — must exist in the copied bank (resolved by name).
"""
import os, sys, time, shutil, sqlite3, subprocess, signal, socket, json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REF  = REPO / 'docs' / 'wireframes' / 'ref'
OUT  = REPO / 'docs' / 'wireframes' / 'impl'
SRC_DB = REPO / 'tracker.db'
QA_DB  = Path('/tmp/horsecock-stagebar.db')
PORT, BASE = 5055, 'http://127.0.0.1:5055'
PROFILE_SELECT_URL = BASE + '/profiles/select/1'    # <-- adjust to your route
VIEWPORT, SCALE = {'width': 375, 'height': 812}, 2

# Recap / Route QA lifts (HANDOFF §3.6). Names must match the copied bank.
EX_SQUAT, EX_OHP = 'BB Squat', 'BB Overhead Press'
EX_ROW = 'DB Row'
EX_DSP, EX_SK, EX_ABW = 'DB Shoulder Press', 'DB Skullcrusher', 'Ab Wheel'

try:
    from PIL import Image
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("pip install playwright pillow && playwright install chromium")

from gymtracker.gym_bank import TODAY_SQL


# ── DB copy + seeding ──────────────────────────────────────────────────────
def db():
    c = sqlite3.connect(QA_DB); c.row_factory = sqlite3.Row; return c

def exid(conn, name):
    r = conn.execute("SELECT id FROM gym_exercises WHERE name = ?", (name,)).fetchone()
    if not r:
        raise SystemExit("exercise not in copied bank: %r" % name)
    return r['id']

def wipe(conn, day):
    for r in conn.execute("SELECT id FROM gym_sessions "
                          "WHERE DATE(performed_at,'localtime','-4 hours')=? OR DATE(performed_at)=?",
                          (day, day)).fetchall():
        conn.execute("DELETE FROM gym_progression WHERE session_id=?", (r['id'],))
        conn.execute("DELETE FROM gym_sessions WHERE id=?", (r['id'],))
    conn.commit()

def seed(day, sets, finished=0):
    """sets = [(exercise_name, weight, reps, count), ...] on `day`."""
    conn = db(); wipe(conn, day)
    sid = conn.execute("INSERT INTO gym_sessions (performed_at, finished) VALUES (?,?)",
                       (day + " 12:00:00", finished)).lastrowid
    for name, w, reps, n in sets:
        eid = exid(conn, name)
        for _ in range(n):
            conn.execute("INSERT INTO gym_progression (exercise_id, weight_kg, sets, reps, recorded_at, session_id) "
                         "VALUES (?,?,1,?,?,?)", (eid, w, reps, day + " 12:00:00", sid))
    conn.commit(); conn.close(); return sid


# ── Flask lifecycle ────────────────────────────────────────────────────────
def wait_port():
    for _ in range(100):
        try: socket.create_connection(('127.0.0.1', PORT), 0.2).close(); return
        except OSError: time.sleep(0.1)
    raise SystemExit("Flask never came up on :%d" % PORT)

def start_server():
    env = dict(os.environ, GYMTRACKER_DB=str(QA_DB), SECRET_KEY='qa', TZ='Australia/Melbourne')
    code = ("import gymtracker.database as d; d.DB_PATH=%r;"
            "import gymtracker.gym_bank as b; b.DB_PATH=%r;"
            "from gymtracker.app import app, init_db; init_db();"
            "app.run(host='127.0.0.1', port=%d)" % (str(QA_DB), str(QA_DB), PORT))
    p = subprocess.Popen([sys.executable, '-c', code], cwd=str(REPO), env=env)
    wait_port(); return p


# ── capture + stitch ───────────────────────────────────────────────────────
def manual(page, day, done, t2open):
    page.evaluate("([k,v])=>sessionStorage.setItem(k,v)",
                  ['train_stage_' + day, json.dumps({'done': done, 't2Open': bool(t2open)})])

def shoot(page, selector, name):
    OUT.mkdir(exist_ok=True)
    el = page.query_selector(selector)
    if not el:
        print("  !! selector missing:", selector); return
    el.screenshot(path=str(OUT / (name + '.png')))
    stitch(name)

def stitch(name):
    ref, mine = REF / (name + '.png'), OUT / (name + '.png')
    if not (ref.exists() and mine.exists()): return
    a, b = Image.open(ref), Image.open(mine); gap = 24
    c = Image.new('RGB', (a.width + b.width + gap, max(a.height, b.height)), (10, 11, 13))
    c.paste(a, (0, 0)); c.paste(b, (a.width + gap, 0))
    c.save(OUT / (name + '-compare.png')); print("  ->", name + '-compare.png')


# ── states ─────────────────────────────────────────────────────────────────
def do_states(page, day):
    def load(): page.goto(BASE + '/train'); page.wait_for_selector('#stage-root .sb'); page.wait_for_timeout(900)
    plans = {
        'a': ([], 0, None),
        'b': ([(EX_SQUAT, 100, 5, 2)], 0, None),
        'c': ([(EX_SQUAT, 100, 5, 3), (EX_OHP, 50, 5, 1)], 0, None),
        'd': ([(EX_SQUAT, 100, 5, 1), (EX_OHP, 50, 5, 1), (EX_ROW, 40, 8, 2)], 0, None),
        'e': ([(EX_SQUAT, 100, 5, 1), (EX_OHP, 50, 5, 1), (EX_DSP, 22.5, 6, 3), (EX_SK, 30, 7, 1)], 0, {'done': ['t2'], 't2Open': False}),
        'f': ([(EX_SQUAT, 100, 5, 1), (EX_OHP, 50, 5, 1), (EX_ROW, 40, 8, 1), (EX_DSP, 22.5, 6, 1), (EX_SK, 30, 7, 1), (EX_ABW, 0, 10, 1)], 1, None),
    }
    for key, (sets, fin, man) in plans.items():
        print('[state %s]' % key)
        seed(day, sets, finished=fin)
        page.goto(BASE + '/train'); page.wait_for_selector('#stage-root .sb')
        if man:
            manual(page, day, man['done'], man['t2Open']); load()
        else:
            page.wait_for_timeout(900)
        shoot(page, '#stage-root .sb', '01-stagebar-state-' + key)

def do_anim(page, day):
    print('[anim] Next pressed on a full T1 #1')
    seed(day, [(EX_SQUAT, 100, 5, 3)])
    page.goto(BASE + '/train'); page.wait_for_selector('#stage-root .sb'); page.wait_for_timeout(700)
    page.click('.sb-next')
    prev = 0
    for ms in (0, 120, 320, 480, 640, 800, 1000, 1300):
        page.wait_for_timeout(ms - prev); prev = ms
        page.query_selector('#stage-root .sb').screenshot(path=str(OUT / ('01-stagebar-anim-%04dms.png' % ms)))
        stitch('01-stagebar-anim-%04dms.png'.replace('.png', ''))
    # T2 unfold stills
    print('[anim] T2 unfold')
    seed(day, [(EX_SQUAT, 100, 5, 1), (EX_OHP, 50, 5, 1)])
    page.goto(BASE + '/train'); page.wait_for_selector('#stage-root .sb'); page.wait_for_timeout(700)
    page.click('.cv.fold')                      # tap folded T2
    page.wait_for_timeout(250)
    page.query_selector('#stage-root .sb').screenshot(path=str(OUT / '01-stagebar-t2-unfolding-250ms.png')); stitch('01-stagebar-t2-unfolding-250ms')
    page.wait_for_timeout(600)
    page.query_selector('#stage-root .sb').screenshot(path=str(OUT / '01-stagebar-t2-open.png')); stitch('01-stagebar-t2-open')

def do_recap(page, day):
    print('[recap] finish OHP 4x5@50, SK 3x7@30, DSP 3x6@22.5')
    seed(day, [(EX_OHP, 50, 5, 4), (EX_SK, 30, 7, 3), (EX_DSP, 22.5, 6, 3)])
    page.goto(BASE + '/train'); page.wait_for_selector('#stage-root .sb')
    # Mark T1/T2 done so the bar is at T3, then two-tap Finish.
    manual(page, day, ['warm', 't1a', 't1b', 't2'], False)
    page.goto(BASE + '/train'); page.wait_for_selector('.sb-next'); page.wait_for_timeout(400)
    page.click('.sb-next'); page.wait_for_timeout(300); page.click('.sb-next')   # arm + confirm
    page.wait_for_selector('#recap-sheet.on', timeout=4000)
    for ms, nm in [(900, '02-recap-block-1'), (1600, '02-recap-block-2'),
                   (2600, '02-recap-midflight-2600ms'), (2400, '02-recap-block-3')]:
        pass
    # capture at the documented offsets from sheet-open
    offsets = [(1000, '02-recap-block-1'), (1700, '02-recap-block-2'),
               (2600, '02-recap-midflight-2600ms'), (3600, '02-recap-block-3'), (6000, '02-recap-end')]
    prev = 0
    for ms, nm in offsets:
        page.wait_for_timeout(max(0, ms - prev)); prev = ms
        el = page.query_selector('#recap-panel')
        if el: el.screenshot(path=str(OUT / (nm + '.png'))); stitch(nm)

def do_route(page):
    print('[route] home accumulation Route view')
    page.goto(BASE + '/'); page.wait_for_selector('#sa-card')
    page.click('#sa-view button[data-view="route"]'); page.wait_for_timeout(300)
    for name, ref in [(EX_SK, '03-route-sk'), (EX_OHP, '03-route-ohp'), (EX_DSP, '03-route-dsp')]:
        page.select_option('#sa-ex', label=name); page.wait_for_timeout(400)
        el = page.query_selector('#sa-card')
        if el: el.screenshot(path=str(OUT / (ref + '.png'))); stitch(ref)


def main(only=None):
    shutil.copy2(SRC_DB, QA_DB)
    mt = os.path.getmtime(SRC_DB)
    srv = start_server()
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            ctx = br.new_context(viewport=VIEWPORT, device_scale_factor=SCALE)
            page = ctx.new_page()
            page.goto(PROFILE_SELECT_URL)
            conn = db(); day = conn.execute("SELECT " + TODAY_SQL).fetchone()[0]; conn.close()
            if only in (None, 'states'): do_states(page, day)
            if only in (None, 'anim'):   do_anim(page, day)
            if only in (None, 'recap'):  do_recap(page, day)
            if only in (None, 'route'):  do_route(page)
            br.close()
    finally:
        srv.send_signal(signal.SIGINT)
        try: srv.wait(timeout=5)
        except Exception: srv.kill()
        assert abs(os.path.getmtime(SRC_DB) - mt) < 1e-6, "tracker.db was modified!"
        print("tracker.db untouched. Comparisons in", OUT)


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    main(sys.argv[1] if len(sys.argv) > 1 else None)
