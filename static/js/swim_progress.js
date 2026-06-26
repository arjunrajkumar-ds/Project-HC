/* swim_progress.js — skill tree SVG + weekly bar + achievements */

(function () {
'use strict';

const STROKE_COLOURS = { Fr: '#378ADD', Ba: '#1D9E75', Br: '#639922', Bt: '#D85A30' };
const DIFF_STROKE    = { beginner: 1.5, intermediate: 2.5, advanced: 4 };
const CANVAS_W = 900, CANVAS_H = 1100;  // virtual px for layout
let   progressData = null;
let   activeFilter = 'all';

// ── Fetch data ────────────────────────────────────────────────────────────────

async function loadProgress() {
    try {
        const r = await fetch('/swim/api/progress');
        progressData = await r.json();
        renderWeeklyBar(progressData.weekly_goal);
        renderTree(progressData.workouts);
        renderAchievements(progressData.achievements, progressData.pbs);
    } catch (e) {
        console.error('Failed to load swim progress', e);
    }
}

// ── Weekly bar ────────────────────────────────────────────────────────────────

function renderWeeklyBar(goal) {
    if (!goal) return;
    const pct     = Math.min(goal.actual_m / goal.target_m * 100, 100);
    const ws      = goal.week_start;
    const we      = goal.week_end || ws;
    const fmt     = d => { const [y,m,day] = d.split('-'); return `${parseInt(day)} ${['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][parseInt(m)-1]}`; };
    const rangeEl = document.getElementById('sw-week-range');
    if (rangeEl) rangeEl.textContent = `${fmt(ws)} – ${fmt(we)}`;

    const fill = document.getElementById('sw-bar-fill');
    if (fill) { fill.style.width = pct + '%'; }

    const actualEl = document.getElementById('sw-week-actual');
    const targetEl = document.getElementById('sw-week-target');
    if (actualEl) actualEl.textContent = goal.actual_m.toLocaleString() + 'm';
    if (targetEl) targetEl.textContent = goal.target_m.toLocaleString() + 'm';
}

function toggleGoalEdit() {
    document.getElementById('sw-goal-edit').classList.toggle('open');
    const inp = document.getElementById('sw-goal-input');
    if (inp && progressData) {
        inp.value = progressData.weekly_goal.target_m;
        inp.focus();
    }
}

async function saveGoal() {
    const inp = document.getElementById('sw-goal-input');
    const val = parseInt(inp.value, 10);
    if (!val || val < 500) return;
    await fetch('/swim/api/weekly-goal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_m: val }),
    });
    document.getElementById('sw-goal-edit').classList.remove('open');
    const r = await fetch('/swim/api/weekly-goal');
    progressData.weekly_goal = await r.json();
    renderWeeklyBar(progressData.weekly_goal);
}

// ── Skill tree ────────────────────────────────────────────────────────────────

function renderTree(workouts) {
    const svg = document.getElementById('skill-tree-svg');
    if (!svg) return;

    svg.setAttribute('viewBox', `0 0 ${CANVAS_W} ${CANVAS_H}`);

    // Build key→workout map
    const byKey = {};
    workouts.forEach(w => { byKey[w.key] = w; });

    // Compute node positions in virtual px
    const pos = {};
    workouts.forEach(w => {
        pos[w.id] = { x: w.tree_x * CANVAS_W, y: w.tree_y * CANVAS_H };
    });

    // Edges
    const edgeGroup = document.getElementById('tree-edges');
    edgeGroup.innerHTML = '';
    workouts.forEach(w => {
        const reqs = JSON.parse(w.unlock_requires_json || '[]');
        reqs.forEach(reqKey => {
            const reqW = byKey[reqKey];
            if (!reqW) return;
            const p1 = pos[reqW.id], p2 = pos[w.id];
            const locked = !w.is_unlocked;
            const col    = locked ? '#2a2a2e' : (STROKE_COLOURS[w.stroke] + '88');
            const line   = document.createElementNS('http://www.w3.org/2000/svg', 'line');
            line.setAttribute('x1', p1.x); line.setAttribute('y1', p1.y);
            line.setAttribute('x2', p2.x); line.setAttribute('y2', p2.y);
            line.setAttribute('stroke', col);
            line.setAttribute('stroke-width', '2');
            if (locked) line.setAttribute('stroke-dasharray', '5 4');
            // Arrow marker
            line.setAttribute('marker-end', `url(#arr-${w.stroke}${locked ? '-locked' : ''})`);
            if (activeFilter !== 'all' && w.stroke !== activeFilter && reqW.stroke !== activeFilter) {
                line.setAttribute('opacity', '0.08');
            }
            edgeGroup.appendChild(line);
        });
    });

    // Nodes
    const nodeGroup = document.getElementById('tree-nodes');
    nodeGroup.innerHTML = '';
    const R = 26;
    workouts.forEach(w => {
        const { x, y } = pos[w.id];
        const col    = STROKE_COLOURS[w.stroke] || '#888';
        const locked = !w.is_unlocked;
        const done   = w.is_completed;
        const dimmed = activeFilter !== 'all' && w.stroke !== activeFilter;

        const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        g.setAttribute('class', 'tree-node');
        g.setAttribute('data-wid', w.id);
        if (dimmed) g.setAttribute('opacity', '0.15');

        // Circle
        const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
        circle.setAttribute('cx', x); circle.setAttribute('cy', y); circle.setAttribute('r', R);
        circle.setAttribute('fill', locked ? '#1e1e20' : (done ? col : col + '22'));
        circle.setAttribute('stroke', locked ? '#333' : col);
        circle.setAttribute('stroke-width', DIFF_STROKE[w.difficulty] || 2);
        if (locked) circle.setAttribute('opacity', '0.5');
        g.appendChild(circle);

        // Stroke label inside
        const strokeTxt = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        strokeTxt.setAttribute('x', x); strokeTxt.setAttribute('y', y + 4);
        strokeTxt.setAttribute('text-anchor', 'middle');
        strokeTxt.setAttribute('font-size', '11');
        strokeTxt.setAttribute('font-weight', '800');
        strokeTxt.setAttribute('fill', locked ? '#444' : (done ? '#fff' : col));
        strokeTxt.textContent = w.stroke;
        g.appendChild(strokeTxt);

        // Completion badge
        if (done) {
            const badge = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
            badge.setAttribute('cx', x + R - 6); badge.setAttribute('cy', y - R + 6);
            badge.setAttribute('r', 9);
            badge.setAttribute('fill', '#4ade80');
            g.appendChild(badge);
            const tick = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            tick.setAttribute('x', x + R - 6); tick.setAttribute('y', y - R + 10);
            tick.setAttribute('text-anchor', 'middle');
            tick.setAttribute('font-size', '10');
            tick.setAttribute('font-weight', '900');
            tick.setAttribute('fill', '#052e16');
            tick.textContent = '✓';
            g.appendChild(tick);
        }

        // Lock icon for locked nodes
        if (locked) {
            const lk = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            lk.setAttribute('x', x + R - 8); lk.setAttribute('y', y - R + 12);
            lk.setAttribute('text-anchor', 'middle'); lk.setAttribute('font-size', '11');
            lk.setAttribute('fill', '#444'); lk.textContent = '🔒';
            g.appendChild(lk);
        }

        // Name label below node
        const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
        label.setAttribute('x', x); label.setAttribute('y', y + R + 14);
        label.setAttribute('text-anchor', 'middle'); label.setAttribute('font-size', '9');
        label.setAttribute('fill', locked ? '#333' : '#888');
        label.textContent = _truncate(w.name, 20);
        g.appendChild(label);

        g.addEventListener('click', () => openPanel(w));
        nodeGroup.appendChild(g);
    });
}

function _truncate(str, n) {
    return str.length > n ? str.slice(0, n - 1) + '…' : str;
}

function filterTree(stroke) {
    activeFilter = stroke;
    document.querySelectorAll('.sw-tree-pill').forEach(p => {
        p.classList.toggle('active', p.dataset.stroke === stroke);
    });
    if (progressData) renderTree(progressData.workouts);
}

// ── Node detail panel ─────────────────────────────────────────────────────────

function openPanel(w) {
    const col = STROKE_COLOURS[w.stroke] || '#888';
    document.getElementById('sw-panel-name').textContent = w.name;
    document.getElementById('sw-panel-name').style.color = col;

    const diffLabel = w.difficulty.charAt(0).toUpperCase() + w.difficulty.slice(1);
    document.getElementById('sw-panel-meta').textContent =
        `${w.stroke} · ${w.category.replace('_', ' ')} · ${diffLabel} · ${w.target_m.toLocaleString()}m`;

    document.getElementById('sw-panel-desc').textContent = w.description || '';

    const sets = JSON.parse(w.sets_json || '[]');
    document.getElementById('sw-panel-sets').textContent =
        sets.map(s => `${s.reps}×${s.distance_m}m${s.stroke_override ? ' ' + s.stroke_override : ''}`).join(' + ');

    const histEl = document.getElementById('sw-panel-hist-rows');
    if (w.history && w.history.length) {
        histEl.innerHTML = w.history.map(h => {
            const d = h.performed_at.slice(0, 10);
            return `<div class="sw-panel-hist-row">
                <span class="sw-panel-hist-date">${d}</span>
                <span class="sw-panel-hist-dist" style="color:${col}">${h.total_m.toLocaleString()}m</span>
                <span class="sw-panel-hist-result ${h.result}">${h.result}</span>
            </div>`;
        }).join('');
        document.getElementById('sw-panel-hist').style.display = '';
    } else {
        histEl.innerHTML = '';
        document.getElementById('sw-panel-hist').style.display = 'none';
    }

    document.getElementById('sw-node-panel').classList.add('open');
    document.getElementById('sw-node-backdrop').classList.add('open');
}

function closePanel() {
    document.getElementById('sw-node-panel').classList.remove('open');
    document.getElementById('sw-node-backdrop').classList.remove('open');
}

// ── Pan & zoom ────────────────────────────────────────────────────────────────

function initPanZoom() {
    const svg  = document.getElementById('skill-tree-svg');
    const wrap = svg ? svg.parentElement : null;
    if (!svg || !wrap) return;

    let scale = 1, tx = 0, ty = 0;
    let dragging = false, startX = 0, startY = 0, startTx = 0, startTy = 0;
    let lastDist = null;

    function applyTransform() {
        document.getElementById('tree-transform').setAttribute(
            'transform', `translate(${tx},${ty}) scale(${scale})`
        );
    }

    // Pointer drag
    svg.addEventListener('pointerdown', e => {
        if (e.target.closest('.tree-node')) return;
        dragging = true; startX = e.clientX; startY = e.clientY;
        startTx = tx; startTy = ty;
        svg.setPointerCapture(e.pointerId);
    });
    svg.addEventListener('pointermove', e => {
        if (!dragging) return;
        tx = startTx + (e.clientX - startX);
        ty = startTy + (e.clientY - startY);
        applyTransform();
    });
    svg.addEventListener('pointerup', () => { dragging = false; });

    // Wheel zoom
    svg.addEventListener('wheel', e => {
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        scale = Math.min(3, Math.max(0.3, scale * delta));
        applyTransform();
    }, { passive: false });

    // Touch pinch-zoom
    svg.addEventListener('touchstart', e => {
        if (e.touches.length === 2) {
            lastDist = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );
        }
    }, { passive: true });
    svg.addEventListener('touchmove', e => {
        if (e.touches.length === 2 && lastDist !== null) {
            const dist = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );
            const ratio = dist / lastDist;
            scale = Math.min(3, Math.max(0.3, scale * ratio));
            lastDist = dist;
            applyTransform();
        }
    }, { passive: true });
    svg.addEventListener('touchend', () => { lastDist = null; });
}

// ── Achievements ──────────────────────────────────────────────────────────────

function renderAchievements(achievements, pbs) {
    // PBs
    const pbMap = pbs || {};
    const pbDefs = [
        { key: 'longest_session_m', label: 'Longest Session', unit: 'm' },
        { key: 'most_sets_session', label: 'Most Sets',       unit: '' },
        { key: 'most_distance_week', label: 'Best Week',      unit: 'm' },
    ];
    const pbGrid = document.getElementById('sw-pbs-grid');
    if (pbGrid) {
        pbGrid.innerHTML = pbDefs.map(def => {
            const pb = pbMap[def.key];
            const val  = pb ? pb.value.toLocaleString() + def.unit : '—';
            const date = pb ? pb.achieved_at.slice(0, 10) : '';
            return `<div class="sw-pb-card">
                <div class="sw-pb-label">${def.label}</div>
                <div class="sw-pb-val">${val}</div>
                ${date ? `<div class="sw-pb-date">${date}</div>` : ''}
            </div>`;
        }).join('');
    }

    // Achievements grid
    const achGrid = document.getElementById('sw-ach-grid');
    if (!achGrid || !achievements) return;

    const catColour = { distance: 'unlocked-distance', session: 'unlocked-session',
                        streak: 'unlocked-streak', collection: 'unlocked-collection' };

    achGrid.innerHTML = achievements.map(a => {
        const cls = a.is_unlocked ? `sw-ach-card ${catColour[a.category] || ''}` : 'sw-ach-card locked';
        const date = a.is_unlocked && a.unlocked_at ? a.unlocked_at.slice(0, 10) : '';
        return `<div class="${cls}">
            ${!a.is_unlocked ? '<span class="sw-ach-lock">🔒</span>' : ''}
            <div class="sw-ach-icon">${a.icon || ''}</div>
            <div class="sw-ach-name">${a.name}</div>
            <div class="sw-ach-desc">${a.description || ''}</div>
            ${date ? `<div class="sw-ach-date">Unlocked ${date}</div>` : ''}
        </div>`;
    }).join('');
}

// ── Expose globals ────────────────────────────────────────────────────────────

window.swFilterTree    = filterTree;
window.swToggleGoal    = toggleGoalEdit;
window.swSaveGoal      = saveGoal;
window.swClosePanel    = closePanel;

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initPanZoom();
    loadProgress();
});

})();
