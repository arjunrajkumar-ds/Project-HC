/* swim_log.js — workout selection + tracking flow */

(function () {
'use strict';

const DISTS    = [25, 50, 100, 200, 400, 800];
let   WORKOUT  = null;  // currently selected workout object

// ── Stroke filter ────────────────────────────────────────────────────────────

function filterStroke(stroke) {
    document.querySelectorAll('.sw-pill').forEach(p => {
        p.classList.toggle('active', p.dataset.stroke === stroke);
    });
    document.querySelectorAll('.sw-stroke-section').forEach(sec => {
        const show = stroke === 'all' || sec.dataset.stroke === stroke;
        sec.style.display = show ? '' : 'none';
    });
    cancelSelect();
}

// ── Workout selection ─────────────────────────────────────────────────────────

function selectWorkout(card) {
    if (card.classList.contains('locked')) return;

    WORKOUT = window.SW_WORKOUTS[parseInt(card.dataset.wid, 10)];
    if (!WORKOUT) return;

    document.querySelectorAll('.sw-card').forEach(c => c.classList.remove('selected'));
    card.classList.add('selected');

    const bar = document.getElementById('sw-confirm-bar');
    document.getElementById('sw-confirm-name').textContent = WORKOUT.name;
    bar.style.display = 'flex';
    bar.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function cancelSelect() {
    WORKOUT = null;
    document.querySelectorAll('.sw-card').forEach(c => c.classList.remove('selected'));
    const bar = document.getElementById('sw-confirm-bar');
    if (bar) bar.style.display = 'none';
}

function confirmWorkout() {
    if (!WORKOUT) return;

    document.getElementById('sw-workout-id').value = WORKOUT.id;

    // Switch views
    document.getElementById('sw-picker').style.display   = 'none';
    document.getElementById('sw-tracker').style.display  = '';
    document.getElementById('sw-confirm-bar').style.display = 'none';

    // Header
    document.getElementById('sw-track-name').textContent   = WORKOUT.name;
    document.getElementById('sw-track-target').textContent = 'Target: ' + WORKOUT.target_m.toLocaleString() + 'm';

    // Pre-populate sets
    document.getElementById('sw-rows').innerHTML = '';
    const sets = JSON.parse(WORKOUT.sets_json || '[]');
    sets.forEach(s => addSetRow(s.reps, s.distance_m, s.stroke_override || WORKOUT.stroke));
    updateTotal();

    document.getElementById('sw-save-btn').disabled = false;
    document.getElementById('sw-tracker').scrollIntoView({ behavior: 'smooth' });
}

function backToPicker() {
    document.getElementById('sw-picker').style.display  = '';
    document.getElementById('sw-tracker').style.display = 'none';
    WORKOUT = null;
    document.getElementById('sw-workout-id').value = '';
    document.getElementById('sw-save-btn').disabled = true;
}

// ── Set rows ─────────────────────────────────────────────────────────────────

function addSetRow(defaultReps, defaultDist, defaultStroke) {
    const container = document.getElementById('sw-rows');
    const rowNum    = container.querySelectorAll('.sw-set-row').length + 1;
    const stroke    = defaultStroke || (WORKOUT ? WORKOUT.stroke : 'Fr');

    const distOpts = DISTS.map(d =>
        `<option value="${d}"${d === (defaultDist || 50) ? ' selected' : ''}>${d}m</option>`
    ).join('');

    const row = document.createElement('div');
    row.className = 'sw-set-row';
    row.innerHTML =
        `<span class="sw-set-num">${rowNum}</span>` +
        `<input type="number" name="swim_reps" min="1" max="999" inputmode="numeric"
                value="${defaultReps || ''}" placeholder="reps" required
                oninput="swUpdateTotal()">` +
        `<select name="swim_dist" onchange="swUpdateTotal()">${distOpts}</select>` +
        `<span class="sw-set-stroke ${stroke}">${stroke}</span>` +
        `<input type="hidden" name="swim_stroke" value="${stroke}">` +
        `<button type="button" class="sw-rm-btn" onclick="swRemoveRow(this)">✕</button>`;

    container.appendChild(row);
    updateTotal();
}

function swRemoveRow(btn) {
    const container = btn.closest('.sw-set-row').parentElement;
    btn.closest('.sw-set-row').remove();
    container.querySelectorAll('.sw-set-num').forEach((el, i) => { el.textContent = i + 1; });
    updateTotal();
}

function swUpdateTotal() { updateTotal(); }

function updateTotal() {
    const rows  = document.getElementById('sw-rows').querySelectorAll('.sw-set-row');
    let   total = 0;
    rows.forEach(row => {
        const r = parseInt(row.querySelector('input[name="swim_reps"]').value)  || 0;
        const d = parseInt(row.querySelector('select[name="swim_dist"]').value) || 0;
        total  += r * d;
    });

    const el = document.getElementById('sw-total');
    if (total > 0) {
        document.getElementById('sw-total-val').textContent = total.toLocaleString();
        const target = WORKOUT ? WORKOUT.target_m : 0;
        const vsEl   = document.getElementById('sw-total-vs');
        if (target) {
            if (total >= target) {
                vsEl.textContent = '✓ Target met';
                vsEl.className   = 'sw-total-vs sw-vs-ok';
            } else {
                vsEl.textContent = `${(target - total).toLocaleString()}m to go`;
                vsEl.className   = 'sw-total-vs sw-vs-short';
            }
        } else {
            vsEl.textContent = '';
        }
        el.style.display = '';
    } else {
        el.style.display = 'none';
    }
}

// ── Flash banner dismiss ──────────────────────────────────────────────────────

function dismissFlash() {
    const el = document.getElementById('sw-flash');
    if (el) el.remove();
}

// ── Form submit guard ─────────────────────────────────────────────────────────

function initForm() {
    const form = document.getElementById('sw-form');
    if (!form) return;
    form.addEventListener('submit', () => {
        const btn = document.getElementById('sw-save-btn');
        btn.disabled  = true;
        btn.textContent = 'Saving…';
    });
}

// ── Expose to inline onclick ─────────────────────────────────────────────────

window.swFilterStroke  = filterStroke;
window.swSelectWorkout = selectWorkout;
window.swCancelSelect  = cancelSelect;
window.swConfirm       = confirmWorkout;
window.swBack          = backToPicker;
window.swAddRow        = addSetRow;
window.swRemoveRow     = swRemoveRow;
window.swUpdateTotal   = swUpdateTotal;
window.swDismissFlash  = dismissFlash;

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initForm();
    filterStroke('all');
});

})();
