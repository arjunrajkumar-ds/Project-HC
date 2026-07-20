import sqlite3
import json as _json
import os
import re as _re_db
from datetime import date

# Multiplier may appear as a prefix ("2x eggs") or a suffix ("eggs x3").
_QTY_PREFIX_RE  = _re_db.compile(r'^(\d*\.?\d+)\s*x\s+(.+)$', _re_db.IGNORECASE)
_QTY_SUFFIX_RE  = _re_db.compile(r'^(.+?)\s+x\s*(\d*\.?\d+)$', _re_db.IGNORECASE)
_GRAM_PREFIX_RE = _re_db.compile(r'^(\d+(?:\.\d+)?)\s*(g|ml|kg|l|oz|lb)\s+(.+)$', _re_db.IGNORECASE)
_UNIT_TO_G      = {'g': 1, 'ml': 1, 'kg': 1000, 'l': 1000, 'oz': 28.35, 'lb': 453.6}


def _parse_qty_name(name):
    """Parse a quantity multiplier from either '<num>x <food>' or '<food> x<num>'.
    Returns (multiplier, base_name). Returns (1.0, name) if no multiplier is present."""
    s = name.strip()
    m = _QTY_PREFIX_RE.match(s)
    if m:
        return float(m.group(1)), m.group(2).strip()
    m = _QTY_SUFFIX_RE.match(s)
    if m:
        return float(m.group(2)), m.group(1).strip()
    return 1.0, s


def _parse_gram_prefix(name):
    """Parse '<num><unit> <food>' → (grams_equivalent, base_name). Returns (None, name) if no match."""
    m = _GRAM_PREFIX_RE.match(name.strip())
    if m:
        grams = float(m.group(1)) * _UNIT_TO_G.get(m.group(2).lower(), 1)
        return grams, m.group(3).strip()
    return None, name.strip()


# ── Plural handling ──────────────────────────────────────────────────────────
# Foods are matched on a canonical "key": lowercased, whitespace-collapsed, with the
# head noun singularised so that "egg"/"eggs", "berry"/"berries" resolve to one food.
# The key is only ever used for matching — the human-entered display name is preserved.
# Correctness of the singular form matters less than determinism: the same rule runs on
# both stored names and lookups, so any consistent transform makes the pair collide.

# Irregular / awkward plurals worth getting right (plural → singular, lowercase).
_PLURAL_OVERRIDES = {
    'leaves': 'leaf', 'loaves': 'loaf', 'halves': 'half', 'calves': 'calf',
    'potatoes': 'potato', 'tomatoes': 'tomato', 'mangoes': 'mango', 'mangos': 'mango',
    'avocados': 'avocado', 'burritos': 'burrito', 'tacos': 'taco',
    'chillies': 'chilli', 'chilies': 'chili', 'berries': 'berry',
    'wraps': 'wrap', 'oats': 'oats',
}

# Words that look plural but are singular/mass nouns — never strip these.
_NO_SINGULAR = {
    'oats', 'hummus', 'asparagus', 'couscous', 'molasses', 'watercress',
    'swiss', 'bass', 'gas', 'lentils',
}


def _singularize(token):
    """Best-effort singular of a single lowercase token (for matching only)."""
    low = token.lower()
    if low in _PLURAL_OVERRIDES:
        return _PLURAL_OVERRIDES[low]
    if low in _NO_SINGULAR or len(low) <= 3:
        return low
    if low.endswith('ss') or low.endswith('us') or low.endswith('is'):
        return low                      # glass, hummus, basis — leave alone
    if low.endswith('ies'):
        return low[:-3] + 'y'           # berries → berry
    if low.endswith(('ches', 'shes', 'xes', 'zes', 'ses')):
        return low[:-2]                 # boxes → box, dishes → dish
    if low.endswith('s'):
        return low[:-1]                 # eggs → egg
    return low


def _food_key(name):
    """Canonical matching key for a food's base name (quantity prefixes already removed)."""
    n = ' '.join(name.strip().lower().split())
    if not n:
        return n
    parts = n.split(' ')
    parts[-1] = _singularize(parts[-1])
    return ' '.join(parts)


# ── Display casing ───────────────────────────────────────────────────────────
# Food names are stored in a consistent Title Case, with sensible exceptions so we
# don't mangle quantity units, acronyms, or brand names.
_TITLE_SMALL   = {'and', 'or', 'of', 'with', 'in', 'a', 'an', 'the', 'to', 'per', 'on', 'for', 'n'}
_TITLE_UNIT_RE = _re_db.compile(r'^\d*\.?\d+\s*(g|ml|kg|l|oz|lb|x)$', _re_db.IGNORECASE)


def _cap_word(word):
    """Capitalise the first letter of each hyphen-separated segment, leaving the rest
    untouched (so 'toby's' → "Toby's", not "Toby'S"; 'high-protein' → 'High-Protein')."""
    def cap_seg(seg):
        for i, ch in enumerate(seg):
            if ch.isalpha():
                return seg[:i] + ch.upper() + seg[i + 1:]
        return seg
    return '-'.join(cap_seg(part) for part in word.split('-'))


def _display_name(name):
    """Normalise a food name to consistent Title Case for storage/display.

    Keeps quantity units lowercase ('200g', '2x'), preserves short all-caps acronyms
    ('BD'), leaves tokens containing digits as typed (brands like "4'n'20"), and
    lowercases small connecting words.
    """
    s = ' '.join((name or '').strip().split())
    if not s:
        return s
    tokens = s.split(' ')
    out = []
    for idx, tok in enumerate(tokens):
        low = tok.lower()
        if _TITLE_UNIT_RE.match(tok):
            out.append(low)                                   # 200G→200g, 2X→2x
        elif tok.isupper() and 1 < len(tok) <= 3 and any(c.isalpha() for c in tok):
            out.append(tok)                                   # keep acronyms (BD, PB)
        elif any(c.isdigit() for c in tok):
            out.append(tok)                                   # brandy tokens (4'n'20, B12)
        elif low in _TITLE_SMALL and idx != 0:
            out.append(low)                                   # small words, not first
        else:
            out.append(_cap_word(tok))
    return ' '.join(out)


def _parse_entry_name(raw_name):
    """Decompose a raw food entry into its scaling components.

    Returns a dict:
        display    : the cleaned name to store/show (quantity prefixes stripped)
        base_name  : food name with quantity/gram prefixes removed
        key        : canonical match key for base_name (plural-aware)
        multiplier : count multiplier from 'Nx'/'xN' (default 1.0)
        gram_qty   : absolute grams from a '<num><unit>' prefix, else None
    """
    raw = ' '.join((raw_name or '').strip().split())
    multiplier, after_mult = _parse_qty_name(raw)
    gram_qty, base_name = _parse_gram_prefix(after_mult)
    if gram_qty is None:
        base_name = after_mult
    return {
        'display': raw,
        'base_name': base_name,
        'key': _food_key(base_name),
        'multiplier': multiplier,
        'gram_qty': gram_qty,
    }


def _lookup_food_item(conn, key):
    """Return the best food_items row matching a canonical key, or None.
    Prefers defined foods (calories > 0) and the richest definition."""
    return conn.execute(
        """SELECT * FROM food_items WHERE name_key = ?
           ORDER BY (calories > 0) DESC, calories DESC, id ASC LIMIT 1""",
        (key,)
    ).fetchone()


def _resolve_food(conn, raw_name, quantity=None):
    """Single source of truth for turning a raw food entry into stored macros.

    Parses quantity/gram/multiplier, looks the food up by canonical (plural-aware)
    key, and scales its per-100g (or per-unit) macros. `quantity` is the optional
    explicit amount from a quantity field (grams for g-type foods, count for units).

    Returns a dict with display name, matched item, computed factor, and macros.
    """
    parsed = _parse_entry_name(raw_name)
    item   = _lookup_food_item(conn, parsed['key'])

    try:
        quantity = float(quantity) if quantity not in (None, '') else None
    except (TypeError, ValueError):
        quantity = None

    macros  = {'calories': 0.0, 'protein_g': 0.0, 'carbs_g': 0.0, 'fat_g': 0.0}
    factor  = parsed['multiplier']

    if item and item['calories'] > 0:
        if item['unit_type'] == 'unit':
            # Count-based food: each unit is one whole serving.
            count = quantity if (quantity and quantity > 0) else 1.0
            factor = parsed['multiplier'] * count
        else:
            # Weight-based food: macros are per 100g/ml.
            if parsed['gram_qty'] is not None:
                grams = parsed['gram_qty']
            elif quantity and quantity > 0:
                grams = quantity
            else:
                grams = 100.0
            factor = parsed['multiplier'] * (grams / 100.0)

        macros = {
            'calories':  round(item['calories']  * factor, 1),
            'protein_g': round(item['protein_g'] * factor, 1),
            'carbs_g':   round(item['carbs_g']   * factor, 1),
            'fat_g':     round(item['fat_g']     * factor, 1),
        }

    return {
        'display':   parsed['display'],
        'base_name': parsed['base_name'],
        'key':       parsed['key'],
        'item':      item,
        'matched':   bool(item and item['calories'] > 0),
        'factor':    factor,
        **macros,
    }

DB_PATH = os.path.join(os.path.dirname(__file__), 'tracker.db')

# ── Progression scheme bounds ──────────────────────────────────────────────────
# Sets = outer (slow) loop, reps = inner (fast) loop.
# Sequence: for each set count min→max, cycle reps min→max.
TIER_BOUNDS = {
    1: {'sets': (3, 5), 'reps': (3, 5)},
    2: {'sets': (3, 4), 'reps': (6, 8)},
    3: {'sets': (3, 4), 'reps': (8, 12)},
}


def _generate_schemes(bounds):
    """Return (tier, reps, sets, progression_order) tuples for all tiers."""
    result = []
    for tier, b in bounds.items():
        order = 1
        for s in range(b['sets'][0], b['sets'][1] + 1):
            for r in range(b['reps'][0], b['reps'][1] + 1):
                result.append((tier, r, s, order))
                order += 1
    return result


# ── Fatigue / cooldown model ────────────────────────────────────────────────
# Fatigue is a 0–100 gauge per muscle group: 0 = fully recovered/ready, 100 = fried.
# Logging an exercise deposits fatigue (scaled by tier and per-muscle engagement);
# it then bleeds off linearly over each muscle's recovery window.

FATIGUE_TIER_BASE = {1: 80.0, 2: 55.0, 3: 40.0, 4: 0.0}  # primary-muscle deposit / hard session

FATIGUE_DEFAULT_RECOVERY = {   # days to bleed from 100 → 0
    'Legs': 3.0, 'Back': 3.0, 'Chest': 2.5, 'Shoulders': 2.0,
    'Biceps': 1.5, 'Triceps': 1.5, 'Core': 1.5,
}

FATIGUE_LEARN_RATE         = 0.15  # gentle nudge: max ±15% shift to recovery_days per correction
FATIGUE_RECOVERY_MIN_FACTOR = 0.5  # clamp learned recovery to 0.5×–2× the default
FATIGUE_RECOVERY_MAX_FACTOR = 2.0

# Weighted exercise → muscle map. (muscle, engagement); engagement 1.0 = prime mover.
EXERCISE_MUSCLE_MAP = {
    # Tier 1 — heavy compounds
    'Barbell Squat':        [('Legs', 1.0), ('Core', 0.4)],
    'BB Hip Thrust':        [('Legs', 1.0), ('Core', 0.3)],
    'BB RDL':               [('Legs', 1.0), ('Back', 0.5), ('Core', 0.4)],
    'DB Lunges':            [('Legs', 1.0), ('Core', 0.3)],
    'Barbell Bench Press':  [('Chest', 1.0), ('Triceps', 0.5), ('Shoulders', 0.4)],
    'Weighted Dips':        [('Chest', 1.0), ('Triceps', 0.6), ('Shoulders', 0.3)],
    'Pendlay Row':          [('Back', 1.0), ('Biceps', 0.4), ('Core', 0.3)],
    'Meadow Row':           [('Back', 1.0), ('Biceps', 0.4)],
    'Deadlift':             [('Back', 1.0), ('Legs', 0.6), ('Core', 0.5)],
    'Trap Bar Deadlift':    [('Back', 1.0), ('Legs', 0.6), ('Core', 0.5)],
    'Barbell OHP':          [('Shoulders', 1.0), ('Triceps', 0.5), ('Core', 0.3)],
    'Ab Wheel':             [('Core', 1.0)],
    # Tier 2 — supporting compounds
    'Bulgarian Split Squat':   [('Legs', 1.0), ('Core', 0.3)],
    'Hamstring Curl':          [('Legs', 1.0)],
    'BB Incline Bench':        [('Chest', 1.0), ('Triceps', 0.5), ('Shoulders', 0.4)],
    'DB Bench Press':          [('Chest', 1.0), ('Triceps', 0.5), ('Shoulders', 0.4)],
    'DB Pullover':             [('Chest', 0.8), ('Back', 0.5)],
    'Lat Pulldown':            [('Back', 1.0), ('Biceps', 0.5)],
    'DB Row':                  [('Back', 1.0), ('Biceps', 0.4)],
    'Weighted Back Extension': [('Back', 1.0), ('Legs', 0.4), ('Core', 0.4)],
    'DB Shoulder Press':       [('Shoulders', 1.0), ('Triceps', 0.5)],
    'Weighted Decline Crunch': [('Core', 1.0)],
    'Weighted Chinups':            [('Back', 1.0), ('Biceps', 0.5)],
    'Weighted Pullup (Wide Grip)': [('Back', 1.0), ('Biceps', 0.4)],
    # Tier 3 — isolation / aesthetics
    'DB Lateral Raises':       [('Shoulders', 1.0)],
    'Banded Kettlebell Raise': [('Shoulders', 1.0)],
    'Cable Rear Delt Fly':     [('Shoulders', 1.0)],
    'DB Rear Delt Fly':        [('Shoulders', 1.0)],
    'DB Curls':                [('Biceps', 1.0)],
    'EZ Bar Curl':             [('Biceps', 1.0)],
    'DB Hammer Curl':          [('Biceps', 1.0)],
    'EZ-bar Skullcrusher':     [('Triceps', 1.0)],
    'DB Skullcrushers':        [('Triceps', 1.0)],
    'Pec Deck':                [('Chest', 1.0)],
    'Banded Reverse Curls':    [('Biceps', 1.0)],
    'Tricep Extensions':       [('Triceps', 1.0)],
}


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS exercises (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            tier        INTEGER NOT NULL CHECK(tier IN (1, 2, 3, 4)),
            muscle_group TEXT NOT NULL,
            day_type    TEXT NOT NULL CHECK(day_type IN ('push','pull','legs','core','any')),
            notes       TEXT,
            is_barbell     INTEGER NOT NULL DEFAULT 0,
            reps_only      INTEGER NOT NULL DEFAULT 0,
            is_timed       INTEGER NOT NULL DEFAULT 0,
            cardio_metrics TEXT NOT NULL DEFAULT '{}',
            reps_min       INTEGER,
            reps_max       INTEGER,
            sets_min       INTEGER,
            sets_max       INTEGER
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            date     TEXT NOT NULL,
            type     TEXT NOT NULL CHECK(type IN ('gym','swim')),
            day_type TEXT CHECK(day_type IN ('push','pull','legs','core')),
            notes    TEXT
        );

        CREATE TABLE IF NOT EXISTS session_lifts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            exercise_id INTEGER NOT NULL REFERENCES exercises(id),
            set_number  INTEGER NOT NULL,
            reps        INTEGER NOT NULL,
            weight_kg   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS swim_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            distance_m      INTEGER NOT NULL,
            rep_distance_m  INTEGER,
            sets            INTEGER
        );
    """)

    # ── Exercise-library seeding guard ────────────────────────────────────
    # The exercise seed + one-time metadata fixes below must run only ONCE so
    # that user edits (add / remove / amend via the Exercise Bank) are durable
    # and never clobbered on startup. Gated by PRAGMA user_version: fresh DBs
    # (version 0) seed then stamp to _LIBRARY_VERSION; existing DBs run the
    # idempotent block once on upgrade, then are stamped and never touched
    # again. Schema (CREATE / ALTER / INDEX) below stays UNGATED and always runs.
    _LIBRARY_VERSION = 1
    _lib_seed = conn.execute('PRAGMA user_version').fetchone()[0] < _LIBRARY_VERSION

    # Canonical exercise list — single source of truth
    exercises = [
        # Tier 1 — Heavy Compounds
        ('Barbell Squat',           1, 'Legs',      'any',  0),
        ('BB Hip Thrust',           1, 'Legs',      'any',  0),
        ('BB RDL',                  1, 'Legs',      'any',  0),
        ('DB Lunges',               1, 'Legs',      'legs', 0),
        ('Barbell Bench Press',     1, 'Chest',     'any',  0),
        ('Weighted Dips',           1, 'Chest',     'any',  0),
        ('Pendlay Row',             1, 'Back',      'any',  0),
        ('Meadow Row',              1, 'Back',      'pull', 0),
        ('Deadlift',                1, 'Back',      'any',  0),
        ('Barbell OHP',             1, 'Shoulders', 'any',  0),
        ('Ab Wheel',                1, 'Core',      'any',  1),
        ('BB Incline Bench',        1, 'Chest',     'push', 0),
        ('Weighted Back Extension', 1, 'Back',      'pull', 0),
        ('Weighted Decline Crunch', 1, 'Core',      'core', 0),
        # Tier 2 — Supporting Compounds
        ('Bulgarian Split Squat',   2, 'Legs',      'legs', 0),
        ('Hamstring Curl',          2, 'Legs',      'legs', 0),
        ('DB Bench Press',          2, 'Chest',     'push', 0),
        ('DB Pullover',             2, 'Chest',     'push', 0),
        ('Lat Pulldown',            2, 'Back',      'pull', 0),
        ('DB Row',                  2, 'Back',      'pull', 0),
        ('DB Shoulder Press',       2, 'Shoulders', 'push', 0),
        # Tier 3 — Aesthetics
        ('DB Lateral Raises',           3, 'Shoulders', 'any',  0),
        ('Banded Kettlebell Raise',      3, 'Shoulders', 'any',  0),
        ('Cable Rear Delt Fly',          3, 'Shoulders', 'any',  0),
        ('DB Rear Delt Fly',             3, 'Shoulders', 'any',  0),
        ('DB Curls',                    3, 'Biceps',    'any',  0),
        ('EZ Bar Curl',                 3, 'Biceps',    'any',  0),
        ('DB Hammer Curl',              3, 'Biceps',    'any',  0),
        ('EZ-bar Skullcrusher',         3, 'Triceps',   'any',  0),
        ('DB Skullcrushers',            3, 'Triceps',   'any',  0),
    ]

    if _lib_seed:
        c.executemany("""
            INSERT OR IGNORE INTO exercises (name, tier, muscle_group, day_type, reps_only)
            VALUES (?, ?, ?, ?, ?)
        """, exercises)

        # Migrate: fix DB Shoulder Press day_type (was incorrectly seeded as 'legs')
        conn.execute("UPDATE exercises SET day_type='push' WHERE name='DB Shoulder Press' AND day_type='legs'")

        # Migrate: remove duplicate 'DB Overhead Press' (same as DB Shoulder Press)
        conn.execute("DELETE FROM exercises WHERE name='DB Overhead Press' AND tier=2")

        # Migrate: remove 'Weighted Pullup' from T2
        conn.execute("DELETE FROM exercises WHERE name='Weighted Pullup' AND tier=2")

        # Migrate: rename legacy 'Lateral Raises' to 'Banded Kettlebell Raise'
        conn.execute("UPDATE exercises SET name='Banded Kettlebell Raise' WHERE name='Lateral Raises' AND tier=3")

        # Migrate: rename 'Cable/DB Curls' to 'DB Curls'
        conn.execute("UPDATE exercises SET name='DB Curls' WHERE name='Cable/DB Curls' AND tier=3")

        # Migrate: remove 'Tricep Pushdowns'
        conn.execute("DELETE FROM exercises WHERE name='Tricep Pushdowns' AND tier=3")

        # Migrate: split 'Arms' into 'Biceps' / 'Triceps'
        conn.execute("UPDATE exercises SET muscle_group='Biceps'  WHERE muscle_group='Arms' AND name IN ('DB Curls','EZ Bar Curl','DB Hammer Curl','Cable/DB Curls')")
        conn.execute("UPDATE exercises SET muscle_group='Triceps' WHERE muscle_group='Arms'")

    # Migrate: add is_barbell column and set for barbell T1 exercises
    _ex_cols = {r[1] for r in conn.execute('PRAGMA table_info(exercises)')}
    if 'is_barbell' not in _ex_cols:
        conn.execute('ALTER TABLE exercises ADD COLUMN is_barbell INTEGER NOT NULL DEFAULT 0')
    _barbell_names = (
        'Barbell Squat', 'Barbell Bench Press', 'Barbell OHP',
        'Pendlay Row', 'Meadow Row', 'Deadlift', 'BB Hip Thrust', 'BB RDL',
    )
    conn.execute(
        f"UPDATE exercises SET is_barbell=1 WHERE name IN ({','.join('?'*len(_barbell_names))})",
        _barbell_names
    )

    # Migrate: add swim rep columns if they don't exist yet
    existing = {row[1] for row in conn.execute('PRAGMA table_info(swim_logs)')}
    for col, typedef in [('rep_distance_m', 'INTEGER'), ('sets', 'INTEGER')]:
        if col not in existing:
            conn.execute(f'ALTER TABLE swim_logs ADD COLUMN {col} {typedef}')

    # Progression tracking table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS progression (
            exercise_id  INTEGER PRIMARY KEY
                         REFERENCES exercises(id) ON DELETE CASCADE,
            weight_kg    REAL    NOT NULL,
            stage        INTEGER NOT NULL DEFAULT 0,
            scheme_id    INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Stage completion counts — incremented each time a stage is logged in a session
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stage_completions (
            exercise_id  INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
            scheme_id    INTEGER NOT NULL,
            count        INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (exercise_id, scheme_id)
        )
    """)

    # ── Swim achievements ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS swim_achievements (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            sets_json    TEXT NOT NULL,
            total_m      INTEGER NOT NULL,
            unlock_after INTEGER REFERENCES swim_achievements(id),
            category     TEXT NOT NULL DEFAULT 'straight',
            sort_order   INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS swim_achievement_completions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            achievement_id INTEGER NOT NULL REFERENCES swim_achievements(id),
            session_id     INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            result         TEXT NOT NULL DEFAULT 'completed',
            sets_done      INTEGER NOT NULL DEFAULT 0,
            date           TEXT NOT NULL
        )
    """)

    # Migrations for pre-existing tables
    _ach_cols  = {r[1] for r in conn.execute('PRAGMA table_info(swim_achievements)')}
    _comp_cols = {r[1] for r in conn.execute('PRAGMA table_info(swim_achievement_completions)')}
    if 'category' not in _ach_cols:
        conn.execute("ALTER TABLE swim_achievements ADD COLUMN category TEXT NOT NULL DEFAULT 'straight'")
        conn.execute("UPDATE swim_achievements SET category='pyramid' WHERE json_array_length(sets_json) > 1")
    if 'result' not in _comp_cols:
        conn.execute("ALTER TABLE swim_achievement_completions ADD COLUMN result TEXT NOT NULL DEFAULT 'completed'")
    if 'sets_done' not in _comp_cols:
        conn.execute("ALTER TABLE swim_achievement_completions ADD COLUMN sets_done INTEGER NOT NULL DEFAULT 0")

    if not conn.execute('SELECT 1 FROM swim_achievements LIMIT 1').fetchone():
        _seed = [
            # (name, sets_json, total_m, category, sort_order)
            ('10×50m',  '[{"sets":10,"rep_m":50}]',                                                500,  'straight', 1),
            ('20×50m',  '[{"sets":20,"rep_m":50}]',                                               1000,  'straight', 2),
            ('30×50m',  '[{"sets":30,"rep_m":50}]',                                               1500,  'straight', 3),
            ('Pyramid', '[{"sets":5,"rep_m":50},{"sets":5,"rep_m":100},{"sets":5,"rep_m":50}]',   1000,  'pyramid',  4),
            ('10×100m', '[{"sets":10,"rep_m":100}]',                                              1000,  'straight', 5),
        ]
        _unlock_chain = {2: 1, 3: 2}  # child sort_order → parent sort_order
        _ids = {}
        for name, sets_json, total_m, category, sort_order in _seed:
            _ids[sort_order] = conn.execute(
                'INSERT INTO swim_achievements (name, sets_json, total_m, category, sort_order) VALUES (?,?,?,?,?)',
                (name, sets_json, total_m, category, sort_order)
            ).lastrowid
        for child_sort, parent_sort in _unlock_chain.items():
            conn.execute(
                'UPDATE swim_achievements SET unlock_after=? WHERE sort_order=?',
                (_ids[parent_sort], child_sort)
            )

    # ── Exercise attempt tracking ─────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS exercise_attempts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
            scheme_id   INTEGER NOT NULL,
            result      TEXT NOT NULL CHECK(result IN ('completed','failed')),
            sets_done   INTEGER NOT NULL,
            date        TEXT NOT NULL,
            weight_kg   REAL
        )
    """)
    _ea_cols = {r[1] for r in conn.execute('PRAGMA table_info(exercise_attempts)')}
    if 'weight_kg' not in _ea_cols:
        conn.execute('ALTER TABLE exercise_attempts ADD COLUMN weight_kg REAL')
        conn.execute("""
            UPDATE exercise_attempts SET weight_kg = (
                SELECT MAX(sl.weight_kg)
                FROM session_lifts sl
                JOIN sessions s ON s.id = sl.session_id
                WHERE sl.exercise_id = exercise_attempts.exercise_id
                  AND s.date = exercise_attempts.date
            )
            WHERE weight_kg IS NULL
        """)

    # ── Profiles ──────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (1, 'Arjun')")
    conn.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (2, 'Gayathri')")
    conn.execute("INSERT OR IGNORE INTO profiles (id, name) VALUES (3, 'Raj')")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS body_weight (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            weight_kg  REAL NOT NULL,
            profile_id INTEGER NOT NULL DEFAULT 1,
            UNIQUE(date, profile_id)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS mission_progress (
            profile_id  INTEGER NOT NULL,
            mission_key TEXT    NOT NULL,
            cleared     INTEGER NOT NULL DEFAULT 0,
            updated_at  TEXT,
            PRIMARY KEY (profile_id, mission_key)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS exercise_tally (
            profile_id    INTEGER NOT NULL,
            exercise_id   INTEGER NOT NULL REFERENCES exercises(id),
            count         INTEGER NOT NULL DEFAULT 0,
            last_prompted INTEGER NOT NULL DEFAULT 0,
            updated_at    TEXT,
            PRIMARY KEY (profile_id, exercise_id)
        )
    """)

    # ── Food tracking ─────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_reconciliations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            original   TEXT NOT NULL,
            normalized TEXT NOT NULL,
            profile_id INTEGER NOT NULL DEFAULT 1,
            logged_at  TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_items (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name_key  TEXT,
            calories  REAL NOT NULL DEFAULT 0,
            protein_g REAL NOT NULL DEFAULT 0,
            carbs_g   REAL NOT NULL DEFAULT 0,
            fat_g     REAL NOT NULL DEFAULT 0,
            unit_type TEXT NOT NULL DEFAULT 'g'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_components (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            food_name       TEXT NOT NULL COLLATE NOCASE,
            ingredient_name TEXT NOT NULL,
            quantity        REAL NOT NULL DEFAULT 0,
            pct             REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS food_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            name       TEXT NOT NULL,
            calories   REAL NOT NULL DEFAULT 0,
            protein_g  REAL NOT NULL DEFAULT 0,
            carbs_g    REAL NOT NULL DEFAULT 0,
            fat_g      REAL NOT NULL DEFAULT 0,
            profile_id INTEGER NOT NULL DEFAULT 1,
            meal_type  TEXT NOT NULL DEFAULT 'snack',
            logged_at  TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS containers (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL UNIQUE,
            weight_g INTEGER NOT NULL
        )
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO containers (name, weight_g) VALUES (?, ?)",
        [('white circle bowl', 175), ('white square bowl', 215)]
    )

    # Migrate food_log: add columns to existing DBs
    _fl_cols = {r[1] for r in conn.execute('PRAGMA table_info(food_log)')}
    if 'profile_id' not in _fl_cols:
        conn.execute('ALTER TABLE food_log ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1')
    if 'meal_type' not in _fl_cols:
        conn.execute("ALTER TABLE food_log ADD COLUMN meal_type TEXT NOT NULL DEFAULT 'snack'")
    if 'logged_at' not in _fl_cols:
        conn.execute('ALTER TABLE food_log ADD COLUMN logged_at TEXT')

    # Migrate food_items: add unit_type / name_key columns to existing DBs
    _fi_cols = {r[1] for r in conn.execute('PRAGMA table_info(food_items)')}
    if 'unit_type' not in _fi_cols:
        conn.execute("ALTER TABLE food_items ADD COLUMN unit_type TEXT NOT NULL DEFAULT 'g'")
    if 'name_key' not in _fi_cols:
        conn.execute("ALTER TABLE food_items ADD COLUMN name_key TEXT")

    # Migrate food_components: add pct column for percentage / by-weight composites.
    _fc_cols = {r[1] for r in conn.execute('PRAGMA table_info(food_components)')}
    if 'pct' not in _fc_cols:
        conn.execute('ALTER TABLE food_components ADD COLUMN pct REAL NOT NULL DEFAULT 0')

    # Keep the canonical (plural-aware) match key in sync for every food. Cheap to
    # recompute on each init, and it self-heals after any rename or rule change.
    for _fi in conn.execute('SELECT id, name FROM food_items').fetchall():
        conn.execute(
            'UPDATE food_items SET name_key = ? WHERE id = ?',
            (_food_key(_fi['name']), _fi['id'])
        )
    conn.execute('CREATE INDEX IF NOT EXISTS idx_food_items_name_key ON food_items(name_key)')

    # Migrate macro_goals: drop CHECK(id=1) constraint so multiple profiles can have goals.
    # Old schema uses column 'id'; new schema uses 'profile_id'.
    _mg_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='macro_goals'"
    ).fetchone()
    if _mg_exists:
        _mg_cols = {r[1] for r in conn.execute('PRAGMA table_info(macro_goals)')}
        if 'id' in _mg_cols and 'profile_id' not in _mg_cols:
            conn.execute("""CREATE TABLE macro_goals_tmp (
                profile_id INTEGER PRIMARY KEY,
                calories   REAL NOT NULL DEFAULT 2000,
                protein_g  REAL NOT NULL DEFAULT 150,
                carbs_g    REAL NOT NULL DEFAULT 200,
                fat_g      REAL NOT NULL DEFAULT 70
            )""")
            conn.execute(
                "INSERT INTO macro_goals_tmp SELECT id, calories, protein_g, carbs_g, fat_g FROM macro_goals"
            )
            conn.execute("DROP TABLE macro_goals")
            conn.execute("ALTER TABLE macro_goals_tmp RENAME TO macro_goals")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_goals (
            profile_id INTEGER PRIMARY KEY,
            calories   REAL NOT NULL DEFAULT 2000,
            protein_g  REAL NOT NULL DEFAULT 150,
            carbs_g    REAL NOT NULL DEFAULT 200,
            fat_g      REAL NOT NULL DEFAULT 70
        )
    """)
    conn.execute("""
        INSERT OR IGNORE INTO macro_goals (profile_id, calories, protein_g, carbs_g, fat_g)
        VALUES (1, 2000, 150, 200, 70)
    """)
    conn.execute("""
        INSERT OR IGNORE INTO macro_goals (profile_id, calories, protein_g, carbs_g, fat_g)
        VALUES (2, 2000, 150, 200, 70)
    """)

    # ── sessions.profile_id ──────────────────────────────────────────────
    _sess_cols = {row[1] for row in conn.execute('PRAGMA table_info(sessions)')}
    if 'profile_id' not in _sess_cols:
        conn.execute('ALTER TABLE sessions ADD COLUMN profile_id INTEGER NOT NULL DEFAULT 1')

    # ── reps_only / is_timed columns ─────────────────────────────────────
    ex_cols = {row[1] for row in conn.execute('PRAGMA table_info(exercises)')}
    if 'reps_only' not in ex_cols:
        conn.execute('ALTER TABLE exercises ADD COLUMN reps_only INTEGER NOT NULL DEFAULT 0')
    if 'is_timed' not in ex_cols:
        conn.execute('ALTER TABLE exercises ADD COLUMN is_timed INTEGER NOT NULL DEFAULT 0')

    # ── per-exercise progression bounds ──────────────────────────────────
    ex_cols = {row[1] for row in conn.execute('PRAGMA table_info(exercises)')}
    if 'reps_min' not in ex_cols:
        for _col in ('reps_min', 'reps_max', 'sets_min', 'sets_max'):
            conn.execute(f'ALTER TABLE exercises ADD COLUMN {_col} INTEGER')

    # ── Gayathri's beginner exercises ─────────────────────────────────────
    if _lib_seed:
        conn.executemany("""
            INSERT OR IGNORE INTO exercises (name, tier, muscle_group, day_type, reps_only, is_timed)
            VALUES (?,?,?,?,?,?)
        """, [
            ('Incline Pushups',       3, 'Chest', 'any',  0, 0),
            ('Incline Reverse Plank', 3, 'Core',  'any',  0, 1),
            ('Neck Rolls',            3, 'Core',  'any',  0, 1),
            ('Box Squats',            3, 'Legs',  'any',  0, 0),
            ('Hip Flexor Stretch',    3, 'Legs',  'any',  0, 1),
            ('Leg Extensions',        3, 'Legs',  'legs', 0, 0),
            ('Bicep Curls',           3, 'Arms',  'any',  0, 0),
            ('Body Dips',             3, 'Arms',  'any',  0, 0),
            ('Squats',                3, 'Legs',  'any',  0, 0),
        ])

    # ── Schemes table ─────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schemes (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            tier              INTEGER NOT NULL CHECK(tier IN (1,2,3)),
            exercise_id       INTEGER REFERENCES exercises(id),
            reps              INTEGER NOT NULL,
            sets              INTEGER NOT NULL,
            progression_order INTEGER NOT NULL
        )
    """)

    # Migrate existing DBs that have old schema (no exercise_id column)
    sch_cols = {row[1] for row in conn.execute('PRAGMA table_info(schemes)')}
    if 'exercise_id' not in sch_cols:
        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')
        try:
            conn.execute("""
                CREATE TABLE schemes_new (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    tier              INTEGER NOT NULL CHECK(tier IN (1,2,3)),
                    exercise_id       INTEGER REFERENCES exercises(id),
                    reps              INTEGER NOT NULL,
                    sets              INTEGER NOT NULL,
                    progression_order INTEGER NOT NULL
                )
            """)
            conn.execute("""
                INSERT INTO schemes_new (id, tier, exercise_id, reps, sets, progression_order)
                SELECT id, tier, NULL, reps, sets, progression_order FROM schemes
            """)
            conn.execute('DROP TABLE schemes')
            conn.execute('ALTER TABLE schemes_new RENAME TO schemes')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute('PRAGMA foreign_keys = ON')

    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schemes_tier
        ON schemes(tier, progression_order) WHERE exercise_id IS NULL
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_schemes_exercise
        ON schemes(exercise_id, progression_order) WHERE exercise_id IS NOT NULL
    """)

    # Seed on first-ever run
    if not conn.execute('SELECT 1 FROM schemes LIMIT 1').fetchone():
        conn.executemany(
            'INSERT INTO schemes (tier, reps, sets, progression_order) VALUES (?,?,?,?)',
            _generate_schemes(TIER_BOUNDS)
        )

    # Migrate if tier-wide stage counts no longer match TIER_BOUNDS
    _expected = {
        t: (b['sets'][1] - b['sets'][0] + 1) * (b['reps'][1] - b['reps'][0] + 1)
        for t, b in TIER_BOUNDS.items()
    }
    _actual = {
        row[0]: row[1]
        for row in conn.execute(
            'SELECT tier, COUNT(*) FROM schemes WHERE exercise_id IS NULL GROUP BY tier'
        ).fetchall()
    }
    if _actual != _expected:
        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')
        try:
            conn.execute('DELETE FROM schemes WHERE exercise_id IS NULL')
            conn.executemany(
                'INSERT INTO schemes (tier, reps, sets, progression_order) VALUES (?,?,?,?)',
                _generate_schemes(TIER_BOUNDS)
            )
            # Reset progression rows that use tier-wide schemes only
            conn.execute("""
                UPDATE progression SET scheme_id = (
                    SELECT s.id FROM schemes s
                    JOIN exercises e ON e.id = progression.exercise_id
                    WHERE s.tier = e.tier AND s.exercise_id IS NULL
                    ORDER BY s.progression_order ASC LIMIT 1
                )
                WHERE exercise_id NOT IN (
                    SELECT DISTINCT exercise_id FROM schemes WHERE exercise_id IS NOT NULL
                )
            """)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute('PRAGMA foreign_keys = ON')

    # Fix metadata for any exercises that pre-exist from an older seeding
    if _lib_seed:
        conn.execute("UPDATE exercises SET tier=1, muscle_group='Chest' WHERE name='Weighted Dips'")
        conn.execute("UPDATE exercises SET muscle_group='Back'  WHERE name IN ('Pendlay Row','Deadlift')")
        conn.execute("UPDATE exercises SET muscle_group='Legs'  WHERE name='Barbell Squat'")
        conn.execute("UPDATE exercises SET reps_only=1          WHERE name='Ab Wheel'")
        conn.execute("UPDATE exercises SET muscle_group='Chest' WHERE name='DB Pullover'")
        conn.execute("UPDATE exercises SET muscle_group='Arms'  WHERE name IN ('Cable/DB Curls','Tricep Pushdowns')")

        # Per-exercise bounds
        conn.execute("UPDATE exercises SET reps_min=6, reps_max=10, sets_min=2, sets_max=5 WHERE name='Ab Wheel'")
        conn.execute("UPDATE exercises SET reps_min=6, reps_max=10, sets_min=2, sets_max=4 WHERE name='DB Lunges'")
        conn.execute("UPDATE exercises SET reps_min=3, reps_max=7,  sets_min=3, sets_max=5 WHERE name='Weighted Dips'")
        conn.execute(
            "UPDATE exercises SET reps_min=6, reps_max=12, sets_min=3, sets_max=4 "
            "WHERE name IN ('DB Curls','EZ Bar Curl','DB Hammer Curl')"
        )

    # Generate exercise-specific scheme rows for any exercise with custom bounds
    for _ex in conn.execute(
        'SELECT id, tier, reps_min, reps_max, sets_min, sets_max FROM exercises WHERE reps_min IS NOT NULL'
    ).fetchall():
        if not conn.execute('SELECT 1 FROM schemes WHERE exercise_id=?', (_ex['id'],)).fetchone():
            _order = 1
            _rows = []
            for _s in range(_ex['sets_min'], _ex['sets_max'] + 1):
                for _r in range(_ex['reps_min'], _ex['reps_max'] + 1):
                    _rows.append((_ex['tier'], _ex['id'], _r, _s, _order))
                    _order += 1
            conn.executemany(
                'INSERT OR IGNORE INTO schemes (tier, exercise_id, reps, sets, progression_order) VALUES (?,?,?,?,?)',
                _rows
            )

    # Migrate any progression rows still pointing at tier-wide schemes for exercises with custom bounds
    for _ex in conn.execute(
        'SELECT id FROM exercises WHERE reps_min IS NOT NULL'
    ).fetchall():
        _prog = conn.execute(
            'SELECT p.scheme_id, s.exercise_id AS sch_ex_id FROM progression p '
            'JOIN schemes s ON s.id = p.scheme_id WHERE p.exercise_id=?',
            (_ex['id'],)
        ).fetchone()
        if _prog and _prog['sch_ex_id'] is None:
            _first = conn.execute(
                'SELECT id FROM schemes WHERE exercise_id=? ORDER BY progression_order ASC LIMIT 1',
                (_ex['id'],)
            ).fetchone()
            if _first:
                conn.execute(
                    'UPDATE progression SET scheme_id=? WHERE exercise_id=?',
                    (_first['id'], _ex['id'])
                )

    # ── Migrate progression table: add scheme_id column if on old schema ──
    prog_cols = {row[1] for row in conn.execute('PRAGMA table_info(progression)')}
    if 'scheme_id' not in prog_cols:
        conn.execute('ALTER TABLE progression ADD COLUMN scheme_id INTEGER NOT NULL DEFAULT 1')
        # Reset all existing progressions to the first scheme for their exercise's tier
        conn.execute("""
            UPDATE progression SET scheme_id = (
                SELECT s.id FROM schemes s
                JOIN exercises e ON e.id = progression.exercise_id
                WHERE s.tier = e.tier ORDER BY s.progression_order ASC LIMIT 1
            )
        """)

    # ── Migrate sessions: add duration columns ────────────────────────
    _sess_cols = {r[1] for r in conn.execute('PRAGMA table_info(sessions)')}
    if 'started_at' not in _sess_cols:
        conn.execute('ALTER TABLE sessions ADD COLUMN started_at TEXT')
    if 'ended_at' not in _sess_cols:
        conn.execute('ALTER TABLE sessions ADD COLUMN ended_at TEXT')

    # ── Tier 4: warmups & cardio ──────────────────────────────────────
    # Per-exercise metric spec, stored as JSON, e.g.
    #   {}                              → done-only (e.g. Fa Jin)
    #   {"distance":"m"}                → distance in metres (Swimming)
    #   {"distance":"km","time":true}   → distance (km) + duration (Running)
    #   {"time":true,"resistance":true} → duration + resistance level (Cycling)
    _ex_cols2 = {r[1] for r in conn.execute('PRAGMA table_info(exercises)')}
    if 'cardio_metrics' not in _ex_cols2:
        conn.execute("ALTER TABLE exercises ADD COLUMN cardio_metrics TEXT NOT NULL DEFAULT '{}'")

    # Relax the tier CHECK constraint to allow tier 4 (SQLite needs a rebuild).
    _ex_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='exercises'"
    ).fetchone()[0]
    if 'tier IN (1, 2, 3)' in _ex_sql or 'tier IN (1,2,3)' in _ex_sql:
        conn.commit()
        conn.execute('PRAGMA foreign_keys = OFF')
        try:
            conn.execute("""
                CREATE TABLE exercises_new (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    name           TEXT NOT NULL UNIQUE,
                    tier           INTEGER NOT NULL CHECK(tier IN (1,2,3,4)),
                    muscle_group   TEXT NOT NULL,
                    day_type       TEXT NOT NULL CHECK(day_type IN ('push','pull','legs','core','any')),
                    notes          TEXT,
                    is_barbell     INTEGER NOT NULL DEFAULT 0,
                    reps_only      INTEGER NOT NULL DEFAULT 0,
                    is_timed       INTEGER NOT NULL DEFAULT 0,
                    cardio_metrics TEXT NOT NULL DEFAULT '{}',
                    reps_min       INTEGER,
                    reps_max       INTEGER,
                    sets_min       INTEGER,
                    sets_max       INTEGER
                )
            """)
            conn.execute("""
                INSERT INTO exercises_new
                    (id, name, tier, muscle_group, day_type, notes,
                     is_barbell, reps_only, is_timed, cardio_metrics,
                     reps_min, reps_max, sets_min, sets_max)
                SELECT id, name, tier, muscle_group, day_type, notes,
                       is_barbell, reps_only, is_timed, cardio_metrics,
                       reps_min, reps_max, sets_min, sets_max
                FROM exercises
            """)
            conn.execute('DROP TABLE exercises')
            conn.execute('ALTER TABLE exercises_new RENAME TO exercises')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute('PRAGMA foreign_keys = ON')

    # Seed Tier 4 exercises (idempotent)
    if _lib_seed:
        conn.executemany("""
            INSERT OR IGNORE INTO exercises
                (name, tier, muscle_group, day_type, reps_only, is_timed, cardio_metrics)
            VALUES (?,?,?,?,?,?,?)
        """, [
            ('Banded Face Pulls',     4, 'Mobility', 'any', 0, 0, '{"sets":20}'),
            ('Pec Deck',              4, 'Chest',    'any', 0, 0, '{"sets":20,"weight":true}'),
            ('Banded Reverse Curls',  4, 'Biceps',   'any', 0, 0, '{"sets":20}'),
            ('Tricep Extensions',     4, 'Triceps',  'any', 0, 0, '{"sets":20}'),
            ('Swimming',  4, 'Cardio',   'any', 0, 0, '{"distance":"m"}'),
            ('Running',   4, 'Cardio',   'any', 0, 0, '{"distance":"km","time":true}'),
            ('Rowing',    4, 'Cardio',   'any', 0, 0, '{"distance":"m","time":true}'),
            ('Muay Thai', 4, 'Cardio',   'any', 0, 0, '{"time":true}'),
            ('Cycling',   4, 'Cardio',   'any', 0, 0, '{"time":true,"resistance":true}'),
            ('Stairmaster', 4, 'Cardio', 'any', 0, 0, '{"time":true,"speed":true}'),
        ])
        # Remove legacy 'Fa Jin' exercise — the term is now the category label, not an exercise.
        # Tolerate session_cardio not existing yet on a brand-new DB (created further below).
        try:
            conn.execute(
                "DELETE FROM exercises WHERE name='Fa Jin' AND id NOT IN "
                "(SELECT exercise_id FROM session_lifts UNION SELECT exercise_id FROM session_cardio)"
            )
        except sqlite3.OperationalError:
            conn.execute(
                "DELETE FROM exercises WHERE name='Fa Jin' AND id NOT IN "
                "(SELECT exercise_id FROM session_lifts)"
            )
        # Migrate any Fa Jin accessories that were briefly seeded at Tier 3 → Tier 4 with correct metric spec
        for _nm, _mg, _spec in [
            ('Pec Deck',             'Chest',   '{"sets":20,"weight":true}'),
            ('Banded Reverse Curls', 'Biceps',  '{"sets":20}'),
            ('Tricep Extensions',    'Triceps', '{"sets":20}'),
        ]:
            conn.execute(
                "UPDATE exercises SET tier=4, muscle_group=?, day_type='any', cardio_metrics=? WHERE name=?",
                (_mg, _spec, _nm)
            )
        # Keep metric specs in sync
        for _nm, _spec in [
            ('Banded Face Pulls', '{"sets":20}'),
            ('Pec Deck', '{"sets":20,"weight":true}'),
            ('Banded Reverse Curls', '{"sets":20}'),
            ('Tricep Extensions', '{"sets":20}'),
            ('Swimming', '{"distance":"m"}'),
            ('Running', '{"distance":"km","time":true}'),
            ('Rowing', '{"distance":"m","time":true}'),
            ('Muay Thai', '{"time":true}'),
            ('Cycling', '{"time":true,"resistance":true}'),
            ('Stairmaster', '{"time":true,"speed":true}'),
        ]:
            conn.execute(
                "UPDATE exercises SET tier=4, is_timed=0, cardio_metrics=? WHERE name=? AND tier=4",
                (_spec, _nm)
            )

    # Tier 4 log rows (cardio / warmup entries per session)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_cardio (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            exercise_id INTEGER NOT NULL REFERENCES exercises(id),
            distance_m  REAL,
            duration_s  INTEGER,
            resistance  REAL,
            done        INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT
        )
    """)
    # Migrate: 'sets' count for one-touch "sets of N" warmups (e.g. face pulls)
    _sc_cols = {r[1] for r in conn.execute('PRAGMA table_info(session_cardio)')}
    if 'sets' not in _sc_cols:
        conn.execute('ALTER TABLE session_cardio ADD COLUMN sets INTEGER')
    if 'weight_kg' not in _sc_cols:
        conn.execute('ALTER TABLE session_cardio ADD COLUMN weight_kg REAL')
    if 'speed' not in _sc_cols:
        conn.execute('ALTER TABLE session_cardio ADD COLUMN speed REAL')

    # In-progress gym session drafts (autosave), one per profile+date.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_drafts (
            profile_id INTEGER NOT NULL,
            date       TEXT NOT NULL,
            payload    TEXT NOT NULL,
            updated_at TEXT,
            PRIMARY KEY (profile_id, date)
        )
    """)

    # ── Fatigue / cooldown model ──────────────────────────────────────────────
    c.executescript("""
        CREATE TABLE IF NOT EXISTS exercise_muscles (
            exercise_id  INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
            muscle_group TEXT NOT NULL,
            engagement   REAL NOT NULL DEFAULT 1.0,
            PRIMARY KEY (exercise_id, muscle_group)
        );

        CREATE TABLE IF NOT EXISTS muscle_recovery (
            profile_id    INTEGER NOT NULL,
            muscle_group  TEXT NOT NULL,
            recovery_days REAL NOT NULL,
            PRIMARY KEY (profile_id, muscle_group)
        );

        CREATE TABLE IF NOT EXISTS fatigue_adjustments (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id   INTEGER NOT NULL,
            muscle_group TEXT NOT NULL,
            date         TEXT NOT NULL,
            kind         TEXT NOT NULL CHECK(kind IN ('reset','add')),
            value        REAL NOT NULL DEFAULT 0,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS priority_lifts (
            profile_id  INTEGER NOT NULL,
            slot        INTEGER NOT NULL CHECK(slot IN (1, 2)),
            exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
            start_date  TEXT NOT NULL,
            weeks       INTEGER NOT NULL DEFAULT 4,
            PRIMARY KEY (profile_id, slot)
        );
    """)

    # ── Muscle-group corrections ──────────────────────────────────────────────
    # 'Bicep Curls' was mis-tagged as Triceps. Hip/neck mobility work was tagged
    # as loaded muscle groups, which would generate phantom fatigue — move it to
    # 'Mobility' so it deposits none.
    if _lib_seed:
        conn.execute("UPDATE exercises SET muscle_group='Biceps'   WHERE name='Bicep Curls'")
        conn.execute("UPDATE exercises SET muscle_group='Mobility' WHERE name IN ('Hip Flexor Stretch','Neck Rolls')")
    # Drop any stale auto-mappings for corrected exercises so they reseed correctly.
    conn.execute("""
        DELETE FROM exercise_muscles WHERE exercise_id IN (
            SELECT id FROM exercises
            WHERE name IN ('Bicep Curls', 'Hip Flexor Stretch', 'Neck Rolls')
        )
    """)

    # Seed the weighted exercise → muscle map (idempotent). Primary mover ~1.0,
    # secondary/stabiliser movers carry a fraction of the load.
    for ex_name, weights in EXERCISE_MUSCLE_MAP.items():
        row = conn.execute("SELECT id FROM exercises WHERE name = ?", (ex_name,)).fetchone()
        if not row:
            continue
        for muscle, eng in weights:
            conn.execute(
                "INSERT OR IGNORE INTO exercise_muscles (exercise_id, muscle_group, engagement) "
                "VALUES (?, ?, ?)", (row['id'], muscle, eng)
            )

    # Fallback: any exercise without a mapping inherits its single muscle_group @ 1.0,
    # normalising stray groups onto the tracked set and skipping non-muscular work.
    conn.execute("""
        INSERT OR IGNORE INTO exercise_muscles (exercise_id, muscle_group, engagement)
        SELECT e.id,
               CASE e.muscle_group
                    WHEN 'Posterior Chain' THEN 'Back'
                    WHEN 'Upper Back'      THEN 'Back'
                    ELSE e.muscle_group
               END,
               1.0
        FROM exercises e
        WHERE e.muscle_group NOT IN ('Cardio', 'Mobility')
          AND NOT EXISTS (
            SELECT 1 FROM exercise_muscles em WHERE em.exercise_id = e.id
        )
    """)

    # Stamp the library version so the guarded seed/fixes never re-run and
    # user edits stay durable.
    if _lib_seed:
        conn.execute(f'PRAGMA user_version = {_LIBRARY_VERSION}')

    # Seed initial benchmark-ladder progress for the Gayathri/Raj profiles
    # (2, 3). INSERT OR IGNORE so this never clobbers real future progress —
    # it only fills the row in if it doesn't already exist.
    _LADDER_INITIAL_CLEARED = {
        'ladder__push_pushups':    1,
        'ladder__pull_deadhang':   0,
        'ladder__legs_squat':      0,
        'ladder__legs_jump':       0,
        'ladder__stability_situp': 0,
    }
    from datetime import datetime as _dt
    _seed_ts = _dt.utcnow().isoformat() + 'Z'
    for _pid in (2, 3):
        for _mkey, _cleared in _LADDER_INITIAL_CLEARED.items():
            conn.execute("""
                INSERT OR IGNORE INTO mission_progress (profile_id, mission_key, cleared, updated_at)
                VALUES (?, ?, ?, ?)
            """, (_pid, _mkey, _cleared, _seed_ts))

    conn.commit()
    _init_swim_v2(conn)
    conn.close()


# ── Schemes-based progression ─────────────────────────────────────────────────

def _first_scheme(conn, tier, exercise_id=None):
    if exercise_id is not None:
        row = conn.execute(
            'SELECT * FROM schemes WHERE exercise_id=? ORDER BY progression_order ASC LIMIT 1',
            (exercise_id,)
        ).fetchone()
        if row:
            return row
    return conn.execute(
        'SELECT * FROM schemes WHERE tier=? AND exercise_id IS NULL ORDER BY progression_order ASC LIMIT 1',
        (tier,)
    ).fetchone()


def _scheme_total(conn, tier, exercise_id=None):
    if exercise_id is not None:
        count = conn.execute(
            'SELECT COUNT(*) FROM schemes WHERE exercise_id=?', (exercise_id,)
        ).fetchone()[0]
        if count > 0:
            return count
    return conn.execute(
        'SELECT COUNT(*) FROM schemes WHERE tier=? AND exercise_id IS NULL', (tier,)
    ).fetchone()[0]


def _build_prog(exercise_id, weight_kg, scheme, total):
    # scheme may be a raw schemes row (has 'id') or a progression JOIN row (has 'scheme_id')
    sid = scheme['id'] if 'id' in scheme.keys() else scheme['scheme_id']
    return {
        'exercise_id':  exercise_id,
        'weight_kg':    weight_kg,
        'scheme_id':    sid,
        'reps':         scheme['reps'],
        'sets':         scheme['sets'],
        'stage':        scheme['progression_order'] - 1,  # 0-indexed for templates
        'total_stages': total,
        'label':        f"{scheme['reps']}×{scheme['sets']}",
        'is_last':      scheme['progression_order'] == total,
    }


def get_progression(exercise_id, tier):
    """Return progression dict from schemes table.
    Auto-initialises from session history if no progression row exists yet.
    Returns None if the exercise has never been logged and no weight is set."""
    conn = get_db()

    row = conn.execute(
        'SELECT p.*, s.reps, s.sets, s.progression_order '
        'FROM progression p JOIN schemes s ON s.id = p.scheme_id '
        'WHERE p.exercise_id = ?', (exercise_id,)
    ).fetchone()

    if row is None:
        # Seed from the most recent session lift for this exercise
        last = conn.execute("""
            SELECT sl.weight_kg
            FROM session_lifts sl
            JOIN sessions s ON s.id = sl.session_id
            WHERE sl.exercise_id = ? AND s.type = 'gym'
            ORDER BY s.date DESC, s.id DESC, sl.set_number DESC
            LIMIT 1
        """, (exercise_id,)).fetchone()

        if last:
            first = _first_scheme(conn, tier, exercise_id)
            if first:
                conn.execute(
                    'INSERT OR IGNORE INTO progression (exercise_id, weight_kg, scheme_id) VALUES (?,?,?)',
                    (exercise_id, last['weight_kg'], first['id'])
                )
                conn.commit()
                row = conn.execute(
                    'SELECT p.*, s.reps, s.sets, s.progression_order '
                    'FROM progression p JOIN schemes s ON s.id = p.scheme_id '
                    'WHERE p.exercise_id = ?', (exercise_id,)
                ).fetchone()

    if row is None:
        conn.close()
        return None

    total = _scheme_total(conn, tier, exercise_id)
    conn.close()
    return _build_prog(exercise_id, row['weight_kg'], row, total)


def advance_progression(exercise_id, tier):
    """Move to next scheme. At the final stage, returns cycle_complete=True without modifying data."""
    conn = get_db()
    row = conn.execute(
        'SELECT p.*, s.progression_order, s.exercise_id AS sch_ex_id '
        'FROM progression p JOIN schemes s ON s.id = p.scheme_id '
        'WHERE p.exercise_id = ?', (exercise_id,)
    ).fetchone()
    if not row:
        conn.close()
        return None

    if row['sch_ex_id'] is not None:
        next_scheme = conn.execute(
            'SELECT * FROM schemes WHERE exercise_id=? AND progression_order > ? ORDER BY progression_order ASC LIMIT 1',
            (row['sch_ex_id'], row['progression_order'])
        ).fetchone()
    else:
        next_scheme = conn.execute(
            'SELECT * FROM schemes WHERE tier=? AND exercise_id IS NULL AND progression_order > ? ORDER BY progression_order ASC LIMIT 1',
            (tier, row['progression_order'])
        ).fetchone()

    if not next_scheme:
        conn.close()
        return {'cycle_complete': True}

    conn.execute(
        'UPDATE progression SET scheme_id=? WHERE exercise_id=?',
        (next_scheme['id'], exercise_id)
    )
    conn.commit()
    conn.close()

    result = get_progression(exercise_id, tier)
    result['weight_bumped'] = False
    return result


def set_progression_weight(exercise_id, weight_kg, tier, scheme_id=None):
    """Set working weight and optionally pick a starting stage (defaults to stage 1)."""
    conn = get_db()
    if scheme_id is None:
        scheme_id = _first_scheme(conn, tier, exercise_id)['id']
    conn.execute("""
        INSERT INTO progression (exercise_id, weight_kg, scheme_id) VALUES (?,?,?)
        ON CONFLICT(exercise_id) DO UPDATE SET weight_kg=excluded.weight_kg, scheme_id=excluded.scheme_id
    """, (exercise_id, weight_kg, scheme_id))
    conn.commit()
    conn.close()
    return get_progression(exercise_id, tier)


def record_stage_completion(exercise_id, scheme_id, conn=None):
    """Increment completion count for an exercise+stage and update current progression stage.
    If conn is provided, caller owns commit/close."""
    _own = conn is None
    if _own:
        conn = get_db()
    try:
        conn.execute("""
            INSERT INTO stage_completions (exercise_id, scheme_id, count) VALUES (?,?,1)
            ON CONFLICT(exercise_id, scheme_id) DO UPDATE SET count = count + 1
        """, (exercise_id, scheme_id))
        conn.execute(
            'UPDATE progression SET scheme_id=? WHERE exercise_id=?',
            (scheme_id, exercise_id)
        )
        if _own:
            conn.commit()
    finally:
        if _own:
            conn.close()


def record_exercise_attempt(exercise_id, scheme_id, result, sets_done, date_val, conn=None, weight_kg=None):
    """Record a completed or failed exercise attempt for analytics.
    If conn is provided, caller owns commit/close."""
    _own = conn is None
    if _own:
        conn = get_db()
    try:
        conn.execute(
            'INSERT INTO exercise_attempts (exercise_id, scheme_id, result, sets_done, date, weight_kg) VALUES (?,?,?,?,?,?)',
            (exercise_id, scheme_id, result, sets_done, date_val, weight_kg)
        )
        if _own:
            conn.commit()
    finally:
        if _own:
            conn.close()


def get_swim_achievements():
    """Return {'straight': [chains], 'pyramid': [chains]} where each chain is a list of achievement dicts.
    Also returns flat 'all' dict keyed by id for JS lookup."""
    conn = get_db()
    rows = conn.execute('SELECT * FROM swim_achievements ORDER BY sort_order').fetchall()

    completed_ids = {r[0] for r in conn.execute(
        "SELECT DISTINCT achievement_id FROM swim_achievement_completions WHERE result='completed'"
    ).fetchall()}
    counts = dict(conn.execute(
        "SELECT achievement_id, COUNT(*) FROM swim_achievement_completions WHERE result='completed' GROUP BY achievement_id"
    ).fetchall())
    conn.close()

    ach_list = []
    for r in rows:
        parent = r['unlock_after']
        ach_list.append({
            'id':           r['id'],
            'name':         r['name'],
            'sets':         _json.loads(r['sets_json']),
            'total_m':      r['total_m'],
            'unlock_after': parent,
            'category':     r['category'],
            'is_unlocked':  parent is None or parent in completed_ids,
            'is_completed': r['id'] in completed_ids,
            'count':        counts.get(r['id'], 0),
        })

    # Build chains
    children = {}
    for a in ach_list:
        if a['unlock_after'] is not None:
            children[a['unlock_after']] = a
    roots = [a for a in ach_list if a['unlock_after'] is None]
    chains = []
    for root in roots:
        chain, cur = [root], root
        while cur['id'] in children:
            cur = children[cur['id']]
            chain.append(cur)
        chains.append(chain)

    by_cat = {'straight': [], 'pyramid': []}
    for chain in chains:
        cat = chain[0]['category']
        by_cat.setdefault(cat, []).append(chain)

    flat = {a['id']: a for a in ach_list}
    return {'straight': by_cat.get('straight', []),
            'pyramid':  by_cat.get('pyramid',  []),
            'flat':     flat}


def record_swim_achievement(achievement_id, session_id, result, sets_done, date_val):
    conn = get_db()
    conn.execute(
        'INSERT INTO swim_achievement_completions '
        '(achievement_id, session_id, result, sets_done, date) VALUES (?,?,?,?,?)',
        (achievement_id, session_id, result, sets_done, date_val)
    )
    conn.commit()
    conn.close()


def get_schemes(tier, exercise_id=None):
    """Return schemes for a tier (or exercise-specific if exercise_id given), ordered by progression_order."""
    conn = get_db()
    if exercise_id is not None:
        rows = conn.execute(
            'SELECT id, reps, sets, progression_order FROM schemes WHERE exercise_id=? ORDER BY progression_order',
            (exercise_id,)
        ).fetchall()
        if rows:
            conn.close()
            return [dict(r) for r in rows]
    rows = conn.execute(
        'SELECT id, reps, sets, progression_order FROM schemes WHERE tier=? AND exercise_id IS NULL ORDER BY progression_order',
        (tier,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_progressions():
    """Return progression dicts for all T1/T2 exercises that have a progression row."""
    conn = get_db()
    rows = conn.execute("""
        SELECT p.exercise_id, p.weight_kg,
               e.name, e.tier, e.muscle_group, e.day_type,
               s.id AS scheme_id, s.reps, s.sets, s.progression_order,
               s.exercise_id AS sch_ex_id
        FROM progression p
        JOIN exercises e ON e.id = p.exercise_id
        JOIN schemes s   ON s.id = p.scheme_id
        WHERE e.tier IN (1, 2)
        ORDER BY e.tier, e.name
    """).fetchall()

    tier_totals = {r['tier']: r['n'] for r in conn.execute(
        'SELECT tier, COUNT(*) AS n FROM schemes WHERE exercise_id IS NULL GROUP BY tier'
    ).fetchall()}
    ex_totals = {r['eid']: r['n'] for r in conn.execute(
        'SELECT exercise_id AS eid, COUNT(*) AS n FROM schemes WHERE exercise_id IS NOT NULL GROUP BY exercise_id'
    ).fetchall()}

    all_ex_schemes = {}
    for r in conn.execute(
        'SELECT exercise_id, id, reps, sets, progression_order FROM schemes WHERE exercise_id IS NOT NULL ORDER BY progression_order'
    ).fetchall():
        all_ex_schemes.setdefault(r['exercise_id'], []).append(dict(r))

    conn.close()

    result = []
    for r in rows:
        tier = r['tier']
        eid  = r['exercise_id']
        sch_ex_id = r['sch_ex_id']
        if sch_ex_id is not None:
            total   = ex_totals.get(sch_ex_id, 0)
            schemes = all_ex_schemes.get(sch_ex_id, [])
        else:
            total   = tier_totals.get(tier, 0)
            schemes = get_schemes(tier)
        d = _build_prog(eid, r['weight_kg'], r, total)
        d.update({'name': r['name'], 'tier': tier,
                  'muscle_group': r['muscle_group'], 'day_type': r['day_type'],
                  'schemes': schemes})
        result.append(d)
    return result


def get_tier1_suggestion():
    """Return all Tier 1 exercises ordered by days-since-last-performed descending.

    Never-performed exercises sort first (highest priority).
    Each row includes: id, name, muscle_group, last_performed (ISO date or None), days_since (int or None).
    The first row is the suggested next lift.
    """
    conn = get_db()
    today = date.today().isoformat()

    rows = conn.execute("""
        SELECT
            e.id,
            e.name,
            e.muscle_group,
            e.is_barbell,
            e.reps_only,
            MAX(s.date) AS last_performed
        FROM exercises e
        LEFT JOIN session_lifts sl ON sl.exercise_id = e.id
        LEFT JOIN sessions      s  ON s.id = sl.session_id AND s.type = 'gym'
        WHERE e.tier = 1
        GROUP BY e.id
        ORDER BY last_performed ASC NULLS FIRST
    """).fetchall()
    conn.close()

    # Annotate with days_since (None if never performed)
    result = []
    for row in rows:
        lp = row['last_performed']
        if lp:
            delta = (date.fromisoformat(today) - date.fromisoformat(lp)).days
        else:
            delta = None
        result.append({
            'id':             row['id'],
            'name':           row['name'],
            'muscle_group':   row['muscle_group'],
            'is_barbell':     row['is_barbell'],
            'reps_only':      row['reps_only'],
            'last_performed': lp,
            'days_since':     delta,
        })

    return result


def get_tier4_exercises():
    """Tier 4 warmup / cardio exercises with their metric spec and days-since-last.

    Each row: id, name, muscle_group, metrics (dict), days_since (int or None).
    """
    conn = get_db()
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT e.id, e.name, e.muscle_group, e.cardio_metrics,
               MAX(s.date) AS last_performed
        FROM exercises e
        LEFT JOIN session_cardio sc ON sc.exercise_id = e.id
        LEFT JOIN sessions      s  ON s.id = sc.session_id AND s.type = 'gym'
        WHERE e.tier = 4
        GROUP BY e.id
        ORDER BY e.muscle_group, e.name
    """).fetchall()
    conn.close()

    result = []
    for r in rows:
        lp = r['last_performed']
        delta = (date.fromisoformat(today) - date.fromisoformat(lp)).days if lp else None
        try:
            metrics = _json.loads(r['cardio_metrics'] or '{}')
        except Exception:
            metrics = {}
        result.append({
            'id': r['id'], 'name': r['name'], 'muscle_group': r['muscle_group'],
            'metrics': metrics, 'days_since': delta,
        })
    return result


def get_t1_last_weights():
    """Per Tier-1 exercise: last session's max weight and whether it beat the prior session."""
    conn = get_db()
    out = {}
    for r in conn.execute("SELECT id FROM exercises WHERE tier=1").fetchall():
        eid = r['id']
        rows = conn.execute("""
            SELECT MAX(sl.weight_kg) AS w
            FROM session_lifts sl
            JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
            WHERE sl.exercise_id = ?
            GROUP BY s.id
            ORDER BY s.date DESC, s.id DESC
            LIMIT 2
        """, (eid,)).fetchall()
        last  = rows[0]['w'] if rows else None
        prior = rows[1]['w'] if len(rows) > 1 else None
        out[eid] = {
            'last_weight': last,
            'is_pr': bool(prior is not None and last is not None and last > prior),
        }
    conn.close()
    return out


def save_session_draft(profile_id, date_str, payload):
    """Upsert the in-progress session draft for a profile+date (autosave)."""
    from datetime import datetime as _dt
    conn = get_db()
    conn.execute("""
        INSERT INTO session_drafts (profile_id, date, payload, updated_at)
        VALUES (?,?,?,?)
        ON CONFLICT(profile_id, date) DO UPDATE SET
            payload = excluded.payload, updated_at = excluded.updated_at
    """, (profile_id, date_str, payload, _dt.utcnow().isoformat() + 'Z'))
    conn.commit()
    conn.close()


def get_session_draft(profile_id, date_str):
    conn = get_db()
    row = conn.execute(
        'SELECT payload FROM session_drafts WHERE profile_id=? AND date=?',
        (profile_id, date_str)
    ).fetchone()
    conn.close()
    return row['payload'] if row else None


def clear_session_draft(profile_id, date_str):
    conn = get_db()
    conn.execute('DELETE FROM session_drafts WHERE profile_id=? AND date=?',
                 (profile_id, date_str))
    conn.commit()
    conn.close()


def get_session_cardio(session_id):
    """Tier 4 entries logged in a session, for the detail view."""
    conn = get_db()
    rows = conn.execute("""
        SELECT sc.distance_m, sc.duration_s, sc.resistance, sc.done, sc.sets,
               sc.weight_kg, sc.speed,
               e.name, e.muscle_group, e.cardio_metrics
        FROM session_cardio sc
        JOIN exercises e ON e.id = sc.exercise_id
        WHERE sc.session_id = ?
        ORDER BY sc.id
    """, (session_id,)).fetchall()
    conn.close()

    out = []
    for r in rows:
        try:
            metrics = _json.loads(r['cardio_metrics'] or '{}')
        except Exception:
            metrics = {}
        out.append({
            'name': r['name'], 'muscle_group': r['muscle_group'], 'metrics': metrics,
            'distance_m': r['distance_m'], 'duration_s': r['duration_s'],
            'resistance': r['resistance'], 'done': r['done'], 'sets': r['sets'],
            'weight_kg': r['weight_kg'], 'speed': r['speed'],
        })
    return out


def get_cardio_choices():
    """Tier 4 cardio exercises (muscle_group='Cardio') with their metric spec, for the
    standalone cardio logger."""
    return [e for e in get_tier4_exercises() if e['muscle_group'] == 'Cardio']


def log_cardio_session(profile_id, date_str, exercise_id,
                       distance_m=None, duration_s=None, resistance=None,
                       speed=None):
    """Create a standalone cardio session (its own activity type) with one entry."""
    from datetime import datetime as _dt
    now = _dt.utcnow().isoformat() + 'Z'
    conn = get_db()
    session_id = conn.execute(
        "INSERT INTO sessions (date, type, profile_id, started_at, ended_at) "
        "VALUES (?, 'cardio', ?, ?, ?)",
        (date_str, profile_id, now, now)
    ).lastrowid
    conn.execute(
        'INSERT INTO session_cardio '
        '(session_id, exercise_id, distance_m, duration_s, resistance, speed, done, created_at) '
        'VALUES (?,?,?,?,?,?,1,?)',
        (session_id, exercise_id, distance_m, duration_s, resistance, speed, now)
    )
    conn.commit()
    conn.close()
    return session_id


# ── Reformer Pilates helpers ──────────────────────────────────────────────────

# Canonical display order for the per-exercise guidance fields.
PILATES_GUIDE_ORDER = ['resistance', 'general', 'feet', 'knees', 'back', 'hands']
PILATES_GUIDE_LABELS = {
    'resistance': 'Resistance', 'general': 'Position', 'feet': 'Feet',
    'knees': 'Knees', 'back': 'Back', 'hands': 'Hands',
}


def _ordered_guidance(raw):
    """Parse a guidance JSON string into an ordered [(label, value), ...] list."""
    try:
        d = _json.loads(raw or '{}')
    except Exception:
        d = {}
    out = []
    for k in PILATES_GUIDE_ORDER:
        if d.get(k):
            out.append({'key': k, 'label': PILATES_GUIDE_LABELS.get(k, k.title()), 'value': d[k]})
    # any extra keys not in the canonical order
    for k, v in d.items():
        if k not in PILATES_GUIDE_ORDER and v:
            out.append({'key': k, 'label': k.replace('_', ' ').title(), 'value': v})
    return out


def get_pilates_routines():
    """All routines with an exercise count and last-performed date."""
    conn = get_db()
    rows = conn.execute("""
        SELECT r.*,
               (SELECT COUNT(*) FROM pilates_exercises e WHERE e.routine_id = r.id) AS ex_count,
               (SELECT MAX(p.performed_at) FROM pilates_sessions p WHERE p.routine_id = r.id) AS last_done
        FROM pilates_routines r
        ORDER BY r.sort_order, r.name
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pilates_routine(key=None, routine_id=None):
    """A single routine with its exercises (guidance + hard version parsed)."""
    conn = get_db()
    if routine_id is not None:
        r = conn.execute('SELECT * FROM pilates_routines WHERE id=?', (routine_id,)).fetchone()
    else:
        r = conn.execute('SELECT * FROM pilates_routines WHERE key=?', (key,)).fetchone()
    if not r:
        conn.close()
        return None
    routine = dict(r)
    ex_rows = conn.execute(
        'SELECT * FROM pilates_exercises WHERE routine_id=? ORDER BY ex_order, id', (routine['id'],)
    ).fetchall()
    conn.close()

    exercises = []
    for e in ex_rows:
        ex = dict(e)
        ex['guidance'] = _ordered_guidance(ex['guidance_json'])
        hard = None
        if ex['hard_json']:
            try:
                hj = _json.loads(ex['hard_json'])
            except Exception:
                hj = None
            if hj:
                hard = {
                    'reps':     hj.get('reps'),
                    'notes':    hj.get('notes'),
                    'guidance': _ordered_guidance(_json.dumps(hj.get('guidance', {}))),
                }
        ex['hard'] = hard
        ex['has_hard'] = hard is not None
        exercises.append(ex)
    routine['exercises'] = exercises
    return routine


def get_pilates_session(session_id):
    """A logged pilates session keyed by the *main* sessions.id, for the detail view."""
    conn = get_db()
    ps = conn.execute(
        'SELECT * FROM pilates_sessions WHERE session_id=?', (session_id,)
    ).fetchone()
    if not ps:
        conn.close()
        return None
    routine = conn.execute(
        'SELECT * FROM pilates_routines WHERE id=?', (ps['routine_id'],)
    ).fetchone()
    items = conn.execute(
        'SELECT * FROM pilates_session_exercises WHERE pilates_session_id=? ORDER BY ex_order, id',
        (ps['id'],)
    ).fetchall()
    conn.close()
    return {
        'routine_name': routine['name'] if routine else 'Reformer Pilates',
        'routine_key':  routine['key'] if routine else None,
        'notes':        ps['notes'],
        'exercises':    [dict(i) for i in items],
        'done_count':   sum(1 for i in items if i['done']),
        'total_count':  len(items),
    }


def get_pilates_glossary():
    """Glossary terms grouped by category, for the reference page."""
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM pilates_glossary ORDER BY sort_order, term'
    ).fetchall()
    conn.close()
    groups = {}
    order = []
    for r in rows:
        cat = r['category'] or 'Other'
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append({'term': r['term'], 'definition': r['definition']})
    return [{'category': c, 'terms': groups[c]} for c in order]


def get_t1_exercises_with_activity():
    """All Tier 1 exercises with days-since-last, status, and current scheme.

    Used by the Priorities panel. Returns every T1 exercise so the user can
    pick their two tracked lifts.
    """
    conn = get_db()
    today_iso = date.today().isoformat()

    rows = conn.execute("""
        SELECT
            e.id, e.name, e.muscle_group, e.day_type, e.is_barbell,
            MAX(s.date) AS last_performed,
            p.weight_kg,
            sc.reps AS scheme_reps, sc.sets AS scheme_sets
        FROM exercises e
        LEFT JOIN session_lifts sl ON sl.exercise_id = e.id
        LEFT JOIN sessions      s  ON s.id = sl.session_id AND s.type = 'gym'
        LEFT JOIN progression   p  ON p.exercise_id = e.id
        LEFT JOIN schemes       sc ON sc.id = p.scheme_id
        WHERE e.tier = 1
        GROUP BY e.id
        ORDER BY e.name
    """).fetchall()
    conn.close()

    result = []
    for row in rows:
        lp = row['last_performed']
        if lp:
            delta = (date.fromisoformat(today_iso) - date.fromisoformat(lp)).days
        else:
            delta = None

        if delta is None:
            status = 'never'
        elif delta <= 6:
            status = 'fresh'
        elif delta <= 10:
            status = 'due'
        else:
            status = 'overdue'

        result.append({
            'id':             row['id'],
            'name':           row['name'],
            'muscle_group':   row['muscle_group'],
            'day_type':       row['day_type'],
            'is_barbell':     bool(row['is_barbell']),
            'last_performed': lp,
            'days_since':     delta,
            'status':         status,
            'weight_kg':      row['weight_kg'],
            'scheme_reps':    row['scheme_reps'],
            'scheme_sets':    row['scheme_sets'],
        })

    return result


def get_muscle_group_activity(days=21):
    """Per-muscle-group activity over the last `days` days.

    Returns a dict keyed by muscle group name, each value a list of `days`
    booleans (index 0 = oldest day, last index = today).
    """
    from datetime import timedelta
    conn = get_db()
    today = date.today()
    start = (today - timedelta(days=days - 1)).isoformat()

    rows = conn.execute("""
        SELECT s.date, e.muscle_group
        FROM session_lifts sl
        JOIN sessions  s ON s.id = sl.session_id AND s.type = 'gym'
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE s.date >= ?
        GROUP BY s.date, e.muscle_group
    """, (start,)).fetchall()

    groups_rows = conn.execute(
        "SELECT DISTINCT muscle_group FROM exercises ORDER BY muscle_group"
    ).fetchall()
    conn.close()

    hit_set = {(r['date'], r['muscle_group']) for r in rows}
    groups  = [r['muscle_group'] for r in groups_rows]

    result = {}
    for g in groups:
        arr = []
        for i in range(days):
            d = (today - timedelta(days=days - 1 - i)).isoformat()
            arr.append((d, g) in hit_set)
        result[g] = arr

    return result


def get_exercise_decay(exercise_id, days=21):
    """Day-by-day activity for a single exercise over the last `days` days.

    Returns a dict with exercise metadata and an `activity` list of `days`
    entries (index 0 = oldest). Each entry: {hit, date, weight_kg, total_reps}.
    """
    from datetime import timedelta
    conn = get_db()
    today = date.today()
    start = (today - timedelta(days=days - 1)).isoformat()

    ex = conn.execute(
        "SELECT id, name, muscle_group, tier FROM exercises WHERE id = ?",
        (exercise_id,)
    ).fetchone()

    rows = conn.execute("""
        SELECT s.date,
               MAX(sl.weight_kg) AS max_weight,
               SUM(sl.reps)      AS total_reps
        FROM session_lifts sl
        JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
        WHERE sl.exercise_id = ? AND s.date >= ?
        GROUP BY s.date
    """, (exercise_id, start)).fetchall()

    activity_map = {
        r['date']: {'weight': r['max_weight'], 'reps': r['total_reps']}
        for r in rows
    }

    activity = []
    for i in range(days):
        d = (today - timedelta(days=days - 1 - i)).isoformat()
        if d in activity_map:
            activity.append({
                'hit':        True,
                'date':       d,
                'weight_kg':  activity_map[d]['weight'],
                'total_reps': activity_map[d]['reps'],
            })
        else:
            activity.append({'hit': False, 'date': d})

    # All-time last performed — used by frontend when >21 days since last session
    all_time = conn.execute("""
        SELECT s.date, MAX(sl.weight_kg) AS max_weight
        FROM session_lifts sl
        JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
        WHERE sl.exercise_id = ?
        ORDER BY s.date DESC LIMIT 1
    """, (exercise_id,)).fetchone()

    # Last attempt and last completed (from exercise_attempts + schemes)
    def _fmt_attempt(row):
        if not row:
            return None
        return {
            'date':        row['date'],
            'result':      row['result'],
            'sets_done':   row['sets_done'],
            'sets_target': row['sets'],
            'reps':        row['reps'],
        }

    last_attempt_row = conn.execute("""
        SELECT ea.date, ea.result, ea.sets_done, sc.reps, sc.sets
        FROM exercise_attempts ea
        JOIN schemes sc ON sc.id = ea.scheme_id
        WHERE ea.exercise_id = ?
        ORDER BY ea.date DESC, ea.id DESC LIMIT 1
    """, (exercise_id,)).fetchone()

    last_completed_row = conn.execute("""
        SELECT ea.date, ea.result, ea.sets_done, sc.reps, sc.sets
        FROM exercise_attempts ea
        JOIN schemes sc ON sc.id = ea.scheme_id
        WHERE ea.exercise_id = ? AND ea.result = 'completed'
        ORDER BY ea.date DESC, ea.id DESC LIMIT 1
    """, (exercise_id,)).fetchone()

    # Per-session weight history (last 30 sessions)
    hist_rows = conn.execute("""
        SELECT s.date, sl.weight_kg,
               COUNT(DISTINCT sl.set_number) AS sets,
               MAX(sl.reps)                  AS reps
        FROM session_lifts sl
        JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
        WHERE sl.exercise_id = ?
        GROUP BY s.id, sl.weight_kg
        ORDER BY s.date DESC, s.id DESC
        LIMIT 30
    """, (exercise_id,)).fetchall()

    # Per-scheme outcomes keyed by weight — {weight_str: {scheme_id_str: 'completed'|'failed'}}
    # 'completed' beats 'failed' for the same weight+scheme
    outcome_rows = conn.execute("""
        SELECT scheme_id, result, weight_kg
        FROM exercise_attempts
        WHERE exercise_id = ? AND weight_kg IS NOT NULL
        ORDER BY date ASC, id ASC
    """, (exercise_id,)).fetchall()

    scheme_outcomes_by_weight = {}
    for row in outcome_rows:
        wkey = str(round(row['weight_kg'], 2))
        skey = str(row['scheme_id'])
        bucket = scheme_outcomes_by_weight.setdefault(wkey, {})
        if bucket.get(skey) != 'completed':
            bucket[skey] = row['result']

    conn.close()

    days_since_all  = None
    last_weight_all = None
    if all_time and all_time['date']:
        days_since_all  = (today - date.fromisoformat(all_time['date'])).days
        last_weight_all = all_time['max_weight']

    return {
        'id':                   ex['id'],
        'name':                 ex['name'],
        'muscle_group':         ex['muscle_group'],
        'tier':                 ex['tier'],
        'activity':             activity,
        'days_since_all_time':  days_since_all,
        'last_weight_all_time': last_weight_all,
        'last_attempt':            _fmt_attempt(last_attempt_row),
        'last_completed':          _fmt_attempt(last_completed_row),
        'history':                 [{'date': r['date'], 'weight_kg': r['weight_kg'],
                                     'sets': r['sets'], 'reps': r['reps']}
                                    for r in hist_rows],
        'scheme_outcomes_by_weight': scheme_outcomes_by_weight,
    }


# ── Fatigue / cooldown calculation ────────────────────────────────────

def _recovery_days(conn, profile_id):
    """Per-muscle recovery window in days, learned values overriding defaults."""
    rec = dict(FATIGUE_DEFAULT_RECOVERY)
    for r in conn.execute(
        "SELECT muscle_group, recovery_days FROM muscle_recovery WHERE profile_id = ?",
        (profile_id,)
    ).fetchall():
        rec[r['muscle_group']] = r['recovery_days']
    return rec


def _muscle_deposits_by_date(conn, profile_id, start_iso):
    """{date_iso: {muscle: total_deposit}} from logged gym sessions since start_iso.

    Each (date, exercise) deposits tier_base × engagement into every muscle it works.
    """
    em = {}
    for r in conn.execute(
        "SELECT exercise_id, muscle_group, engagement FROM exercise_muscles"
    ).fetchall():
        em.setdefault(r['exercise_id'], []).append((r['muscle_group'], r['engagement']))

    rows = conn.execute("""
        SELECT s.date AS date, sl.exercise_id AS exercise_id, e.tier AS tier
        FROM session_lifts sl
        JOIN sessions  s ON s.id = sl.session_id AND s.type = 'gym'
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE s.date >= ? AND s.profile_id = ?
        GROUP BY s.date, sl.exercise_id
    """, (start_iso, profile_id)).fetchall()

    deposits = {}
    for r in rows:
        base = FATIGUE_TIER_BASE.get(r['tier'], 30.0)
        if base <= 0:
            continue
        for muscle, eng in em.get(r['exercise_id'], []):
            day = deposits.setdefault(r['date'], {})
            day[muscle] = day.get(muscle, 0.0) + base * eng
    return deposits


def get_fatigue_state(profile_id, history_days=4, project_days=2):
    """Per-muscle fatigue simulated day-by-day over a rolling window.

    Returns dates, labels, today_index, a per-muscle series (0–100), and an
    `up_next` ranking of exercises by how recovered their target muscles are.
    """
    from datetime import timedelta
    conn = get_db()
    today = date.today()
    # Look back far enough that older stimuli have fully decayed before the window starts.
    sim_start = today - timedelta(days=history_days + 7)
    rec = _recovery_days(conn, profile_id)
    muscles = list(FATIGUE_DEFAULT_RECOVERY.keys())

    deposits = _muscle_deposits_by_date(conn, profile_id, sim_start.isoformat())

    adjustments = {}
    for r in conn.execute("""
        SELECT date, muscle_group, kind, value FROM fatigue_adjustments
        WHERE profile_id = ? AND date >= ? ORDER BY date, id
    """, (profile_id, sim_start.isoformat())).fetchall():
        adjustments.setdefault(r['date'], []).append(r)

    # Exercise → muscle weights for the up-next ranking
    ex_rows = conn.execute("""
        SELECT e.id, e.name, e.tier, em.muscle_group, em.engagement
        FROM exercises e
        JOIN exercise_muscles em ON em.exercise_id = e.id
        WHERE e.tier IN (1, 2, 3)
    """).fetchall()
    conn.close()

    total = (today - sim_start).days + 1 + project_days
    sim_dates = [(sim_start + timedelta(days=i)).isoformat() for i in range(total)]

    fat = {m: 0.0 for m in muscles}
    daily = {m: [] for m in muscles}
    for d in sim_dates:
        for m in muscles:
            fat[m] = max(0.0, fat[m] - 100.0 / rec[m])
        for m, dep in deposits.get(d, {}).items():
            if m in fat:
                fat[m] = min(100.0, fat[m] + dep)
        for a in adjustments.get(d, []):
            m = a['muscle_group']
            if m not in fat:
                continue
            fat[m] = 0.0 if a['kind'] == 'reset' else min(100.0, fat[m] + a['value'])
        for m in muscles:
            daily[m].append(round(fat[m]))

    # Trim to the visible window: last `history_days` days + today + `project_days`
    today_pos = (today - sim_start).days
    lo = today_pos - history_days
    hi = today_pos + project_days + 1
    window = list(range(lo, hi))
    today_index = history_days

    _short = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    labels, vis_dates = [], []
    for i in window:
        dd = sim_start + timedelta(days=i)
        labels.append({'short': _short[dd.weekday()], 'num': dd.day})
        vis_dates.append(dd.isoformat())

    fatigue_now = {m: daily[m][today_pos] for m in muscles}
    muscle_out = [{
        'muscle': m,
        'recovery_days': round(rec[m], 2),
        'current': fatigue_now[m],
        'series': [daily[m][i] for i in window],
        'hits': [bool(m in deposits.get(d, {})) for d in vis_dates],
    } for m in muscles]

    by_ex = {}
    for r in ex_rows:
        by_ex.setdefault((r['id'], r['name'], r['tier']), []).append(
            (r['muscle_group'], r['engagement']))
    up_next = []
    for (eid, name, tier), ws in by_ex.items():
        den = sum(e for _, e in ws)
        wf = sum(e * fatigue_now.get(m, 0) for m, e in ws) / den if den else 0
        primary = max(ws, key=lambda x: x[1])[0]
        up_next.append({'id': eid, 'name': name, 'tier': tier,
                        'primary': primary, 'fatigue': round(wf)})
    up_next.sort(key=lambda x: (x['fatigue'], x['tier']))

    return {
        'dates': vis_dates,
        'labels': labels,
        'today_index': today_index,
        'muscles': muscle_out,
        'up_next': up_next,
    }


def adjust_fatigue(profile_id, muscle_group, kind, value=0.0):
    """Log a manual correction and gently nudge that muscle's recovery rate.

    kind='reset' → muscle feels fresh (fatigue → 0); 'add' → still sore (+value).
    A reset while the model predicted fatigue implies faster recovery (shrink the
    window); an add implies slower recovery (grow it). Capped to ±15% per nudge
    and clamped to 0.5×–2× the default window.
    """
    from datetime import datetime as _dt
    if kind not in ('reset', 'add'):
        raise ValueError('kind must be reset or add')

    predicted = next((m['current'] for m in get_fatigue_state(profile_id)['muscles']
                      if m['muscle'] == muscle_group), 0)

    conn = get_db()
    conn.execute(
        "INSERT INTO fatigue_adjustments (profile_id, muscle_group, date, kind, value, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (profile_id, muscle_group, date.today().isoformat(), kind, value,
         _dt.utcnow().isoformat() + 'Z')
    )

    default = FATIGUE_DEFAULT_RECOVERY.get(muscle_group, 2.0)
    rec = _recovery_days(conn, profile_id)
    R = rec.get(muscle_group, default)
    if kind == 'reset':
        R_new = R * (1 - FATIGUE_LEARN_RATE * (predicted / 100.0))
    else:
        R_new = R * (1 + FATIGUE_LEARN_RATE * (min(value, 100.0) / 100.0))
    R_new = max(default * FATIGUE_RECOVERY_MIN_FACTOR,
                min(default * FATIGUE_RECOVERY_MAX_FACTOR, R_new))

    conn.execute("""
        INSERT INTO muscle_recovery (profile_id, muscle_group, recovery_days)
        VALUES (?, ?, ?)
        ON CONFLICT(profile_id, muscle_group)
        DO UPDATE SET recovery_days = excluded.recovery_days
    """, (profile_id, muscle_group, R_new))
    conn.commit()
    conn.close()
    return R_new


def get_exercise_muscle_map():
    """{exercise_id: [[muscle_group, engagement], ...]} for client-side fatigue preview."""
    conn = get_db()
    rows = conn.execute(
        "SELECT exercise_id, muscle_group, engagement FROM exercise_muscles"
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r['exercise_id'], []).append([r['muscle_group'], r['engagement']])
    return out


# ── Priority lifts (focus block) ──────────────────────────────────────

def _lift_month_points(conn, exercise_id, profile_id, month_start_iso):
    """Progress series for one lift over the current month: the best set per session
    day, expressed as estimated 1RM (Epley) for weighted lifts, or top reps for
    bodyweight lifts. Returns (points, unit) with points=[{date, day, value}]."""
    rows = conn.execute("""
        SELECT s.date AS d, sl.reps AS reps, sl.weight_kg AS w
        FROM session_lifts sl
        JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
        WHERE sl.exercise_id = ? AND s.profile_id = ? AND s.date >= ?
        ORDER BY s.date
    """, (exercise_id, profile_id, month_start_iso)).fetchall()

    by_date, any_weight = {}, False
    for r in rows:
        w, reps = (r['w'] or 0), (r['reps'] or 0)
        if w > 0:
            any_weight = True
        e1  = w * (1 + reps / 30.0) if w > 0 else 0      # Epley estimated 1RM
        cur = by_date.setdefault(r['d'], {'e1': 0.0, 'reps': 0})
        cur['e1']   = max(cur['e1'], e1)
        cur['reps'] = max(cur['reps'], reps)

    unit   = 'kg' if any_weight else 'reps'
    points = []
    for d in sorted(by_date):
        value = round(by_date[d]['e1'], 1) if unit == 'kg' else by_date[d]['reps']
        points.append({'date': d, 'day': int(d[8:10]), 'value': value})
    return points, unit


def get_priority_lifts(profile_id):
    """Up to two focus lifts framed as a rolling monthly goal (resets on the 1st).
    Each returns its progress series for the current calendar month."""
    conn  = get_db()
    today = date.today()
    month_start = today.replace(day=1)
    next_month  = (date(today.year + 1, 1, 1) if today.month == 12
                   else date(today.year, today.month + 1, 1))
    days_total  = (next_month - month_start).days
    days_left   = (next_month - today).days
    month_label = today.strftime('%B')

    rows = conn.execute("""
        SELECT p.slot, p.exercise_id, e.name, e.muscle_group, e.tier
        FROM priority_lifts p
        JOIN exercises e ON e.id = p.exercise_id
        WHERE p.profile_id = ?
        ORDER BY p.slot
    """, (profile_id,)).fetchall()

    out = []
    for r in rows:
        points, unit = _lift_month_points(conn, r['exercise_id'], profile_id,
                                          month_start.isoformat())
        pm = conn.execute(
            "SELECT muscle_group FROM exercise_muscles WHERE exercise_id = ? "
            "ORDER BY engagement DESC LIMIT 1", (r['exercise_id'],)
        ).fetchone()
        last = conn.execute("""
            SELECT MAX(s.date) AS d
            FROM session_lifts sl
            JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
            WHERE sl.exercise_id = ? AND s.profile_id = ?
        """, (r['exercise_id'], profile_id)).fetchone()['d']
        vals = [p['value'] for p in points]
        out.append({
            'slot':                r['slot'],
            'exercise_id':         r['exercise_id'],
            'name':                r['name'],
            'tier':                r['tier'],
            'primary':             pm['muscle_group'] if pm else r['muscle_group'],
            'month_label':         month_label,
            'day_of_month':        today.day,
            'days_total':          days_total,
            'days_left':           days_left,
            'sessions_this_month': len(points),
            'history':             points,
            'unit':                unit,
            'first':               vals[0]  if vals else None,
            'best':                max(vals) if vals else None,
            'latest':              vals[-1] if vals else None,
            'last_performed':      last,
        })
    conn.close()
    return out


def set_priority_lift(profile_id, slot, exercise_id, weeks=4):
    """Pin (or replace) a focus lift in a slot, resetting the block to start today."""
    conn = get_db()
    conn.execute("""
        INSERT INTO priority_lifts (profile_id, slot, exercise_id, start_date, weeks)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(profile_id, slot) DO UPDATE SET
            exercise_id = excluded.exercise_id,
            start_date  = excluded.start_date,
            weeks       = excluded.weeks
    """, (profile_id, int(slot), int(exercise_id), date.today().isoformat(), int(weeks)))
    conn.commit()
    conn.close()


def clear_priority_lift(profile_id, slot):
    conn = get_db()
    conn.execute("DELETE FROM priority_lifts WHERE profile_id = ? AND slot = ?",
                 (profile_id, int(slot)))
    conn.commit()
    conn.close()


# ── History & detail helpers ──────────────────────────────────────────

def _compute_duration_min(started_at, ended_at):
    """Duration in minutes from ISO timestamps. Returns None if invalid or >5 h."""
    if not started_at or not ended_at:
        return None
    try:
        from datetime import datetime as _dt
        s = _dt.fromisoformat(started_at.replace('Z', '+00:00'))
        e = _dt.fromisoformat(ended_at.replace('Z', '+00:00'))
        mins = (e - s).total_seconds() / 60
        if mins < 0 or mins > 300:
            return None
        return round(mins)
    except Exception:
        return None


def get_sessions_with_headlines(profile_id=1):
    """Sessions list with top-2 headline lifts and PR flags. Used by /sessions."""
    conn = get_db()

    sessions = conn.execute("""
        SELECT s.*,
               COALESCE(agg.total_sets,   0)   AS total_sets,
               COALESCE(agg.total_volume, 0.0) AS total_volume,
               COALESCE(sw.distance_m,    0)   AS distance_m
        FROM sessions s
        LEFT JOIN (
            SELECT session_id,
                   COUNT(*)              AS total_sets,
                   SUM(reps * weight_kg) AS total_volume
            FROM session_lifts GROUP BY session_id
        ) agg ON agg.session_id = s.id
        LEFT JOIN (
            SELECT session_id, SUM(distance_m) AS distance_m
            FROM swim_logs GROUP BY session_id
        ) sw ON sw.session_id = s.id
        WHERE s.profile_id = ?
        ORDER BY s.date DESC, s.id DESC
    """, (profile_id,)).fetchall()

    # Build per-exercise running max to detect PRs (iterate chronologically)
    all_weights = conn.execute("""
        SELECT sl.exercise_id, s.id AS session_id, MAX(sl.weight_kg) AS max_weight
        FROM session_lifts sl
        JOIN sessions s ON s.id = sl.session_id AND s.type = 'gym'
        GROUP BY sl.exercise_id, s.id
        ORDER BY s.date ASC, s.id ASC
    """).fetchall()

    running_max = {}
    session_prs = {}   # session_id -> set of exercise_ids that set a PR
    for row in all_weights:
        eid, sid, mw = row['exercise_id'], row['session_id'], row['max_weight']
        prev = running_max.get(eid)
        if prev is None or mw > prev:
            session_prs.setdefault(sid, set()).add(eid)
        running_max[eid] = max(running_max.get(eid, 0.0), mw)

    # Top 2 lifts per gym session, ordered by weight desc
    top_rows = conn.execute("""
        SELECT sl.session_id, sl.exercise_id, e.name,
               MAX(sl.weight_kg) AS max_weight
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        GROUP BY sl.session_id, sl.exercise_id
        ORDER BY sl.session_id, MAX(sl.weight_kg) DESC
    """).fetchall()

    session_top = {}
    for r in top_rows:
        sid = r['session_id']
        if sid not in session_top:
            session_top[sid] = []
        if len(session_top[sid]) < 2:
            session_top[sid].append({
                'name':      r['name'],
                'weight_kg': r['max_weight'],
                'is_pr':     r['exercise_id'] in session_prs.get(sid, set()),
            })

    # First cardio activity name per session (for cardio-session headlines)
    cardio_label = {}
    for r in conn.execute("""
        SELECT sc.session_id, e.name
        FROM session_cardio sc JOIN exercises e ON e.id = sc.exercise_id
        ORDER BY sc.id
    """).fetchall():
        cardio_label.setdefault(r['session_id'], r['name'])

    conn.close()

    result = []
    for s in sessions:
        d = dict(s)
        d['headline_lifts'] = session_top.get(s['id'], [])
        d['cardio_label']   = cardio_label.get(s['id'])
        d['duration_min']   = _compute_duration_min(
            d.get('started_at'), d.get('ended_at')
        )
        result.append(d)
    return result


def get_session_detail_with_progression(session_id):
    """Session detail: exercises with grouped sets, pip-bar stage, and PR flags.

    Returns (session_dict, lifts_list, duration_min).
    lifts_list entries contain: exercise_id, name, tier, muscle_group, is_pr,
    scheme_label, stage (1-indexed), total_stages, sets[].
    """
    conn = get_db()

    session = conn.execute(
        'SELECT * FROM sessions WHERE id = ?', (session_id,)
    ).fetchone()
    if session is None:
        conn.close()
        return None, [], None

    if session['type'] != 'gym':
        conn.close()
        return dict(session), [], None

    session_date = session['date']

    totals = {
        r['tier']: r['n']
        for r in conn.execute(
            'SELECT tier, COUNT(*) AS n FROM schemes GROUP BY tier'
        ).fetchall()
    }

    # All lifts, ordered by first-set insertion order then set_number
    rows = conn.execute("""
        SELECT sl.id AS lift_id, sl.set_number, sl.reps, sl.weight_kg,
               e.id AS exercise_id, e.name, e.tier, e.muscle_group
        FROM session_lifts sl
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE sl.session_id = ?
        ORDER BY (SELECT MIN(sl2.id) FROM session_lifts sl2
                  WHERE sl2.session_id = sl.session_id
                    AND sl2.exercise_id = sl.exercise_id) ASC,
                 sl.set_number ASC
    """, (session_id,)).fetchall()

    # PR detection per exercise
    pr_rows = conn.execute("""
        SELECT sl.exercise_id,
               MAX(sl.weight_kg) AS session_max,
               (SELECT MAX(sl2.weight_kg)
                FROM session_lifts sl2
                JOIN sessions s2 ON s2.id = sl2.session_id
                WHERE sl2.exercise_id = sl.exercise_id
                  AND s2.date < ?) AS prev_max
        FROM session_lifts sl
        WHERE sl.session_id = ?
        GROUP BY sl.exercise_id
    """, (session_date, session_id)).fetchall()

    pr_map = {
        r['exercise_id']: (r['prev_max'] is None or r['session_max'] > r['prev_max'])
        for r in pr_rows
    }

    # Scheme at time of session (from exercise_attempts recorded on that date)
    attempts = conn.execute("""
        SELECT ea.exercise_id,
               s.id AS scheme_id, s.reps, s.sets, s.progression_order
        FROM exercise_attempts ea
        JOIN schemes s ON s.id = ea.scheme_id
        WHERE ea.date = ?
    """, (session_date,)).fetchall()
    scheme_at = {r['exercise_id']: dict(r) for r in attempts}

    # Fallback: current progression row
    fallback = conn.execute("""
        SELECT p.exercise_id, s.id AS scheme_id,
               s.reps, s.sets, s.progression_order
        FROM progression p
        JOIN schemes s ON s.id = p.scheme_id
    """).fetchall()
    scheme_fb = {r['exercise_id']: dict(r) for r in fallback}

    # Duration — stitch same-day gym sessions
    day_rows = conn.execute(
        'SELECT started_at, ended_at FROM sessions WHERE date = ? AND type = "gym"',
        (session_date,)
    ).fetchall()
    total_min, all_valid = 0, True
    for dr in day_rows:
        m = _compute_duration_min(dr['started_at'], dr['ended_at'])
        if m is None:
            all_valid = False
            break
        total_min += m
    duration_min = total_min if (all_valid and total_min > 0) else None

    conn.close()

    # Group lifts by exercise, preserving insertion order
    grouped, order = {}, []
    for r in rows:
        eid = r['exercise_id']
        if eid not in grouped:
            tier = r['tier']
            sch = scheme_at.get(eid) or scheme_fb.get(eid) or {
                'scheme_id': None, 'reps': '?', 'sets': '?', 'progression_order': 1
            }
            grouped[eid] = {
                'exercise_id':  eid,
                'name':         r['name'],
                'tier':         tier,
                'muscle_group': r['muscle_group'],
                'is_pr':        pr_map.get(eid, False),
                'scheme_label': f"{sch['reps']}×{sch['sets']}",
                'stage':        sch['progression_order'],
                'total_stages': totals.get(tier, 1),
                'sets':         [],
            }
            order.append(eid)
        grouped[eid]['sets'].append({
            'lift_id':    r['lift_id'],
            'set_number': r['set_number'],
            'reps':       r['reps'],
            'weight_kg':  r['weight_kg'],
        })

    return dict(session), [grouped[eid] for eid in order], duration_min


# ── Food tracking ──────────────────────────────────────────────────────

def get_macro_goals(profile_id=1):
    conn = get_db()
    row = conn.execute('SELECT * FROM macro_goals WHERE profile_id = ?', (profile_id,)).fetchone()
    conn.close()
    return dict(row) if row else {'calories': 2000, 'protein_g': 150, 'carbs_g': 200, 'fat_g': 70}


def set_macro_goals(calories, protein_g, carbs_g, fat_g, profile_id=1):
    conn = get_db()
    conn.execute("""
        INSERT INTO macro_goals (profile_id, calories, protein_g, carbs_g, fat_g) VALUES (?,?,?,?,?)
        ON CONFLICT(profile_id) DO UPDATE SET
            calories=excluded.calories, protein_g=excluded.protein_g,
            carbs_g=excluded.carbs_g,   fat_g=excluded.fat_g
    """, (profile_id, calories, protein_g, carbs_g, fat_g))
    conn.commit()
    conn.close()


def sync_food_log_from_library(date_str, profile_id=1):
    """Backfill any 0-calorie food_log entries for this date/profile where food_items now
    has values. Quantity prefixes ('2x eggs', '100g rice') and plurals are resolved
    through the shared _resolve_food() so backfill matches live logging exactly."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name FROM food_log WHERE date=? AND profile_id=? AND calories=0",
        (date_str, profile_id)
    ).fetchall()
    for row in rows:
        resolved = _resolve_food(conn, row['name'])
        if resolved['matched']:
            conn.execute("""
                UPDATE food_log SET calories=?, protein_g=?, carbs_g=?, fat_g=?
                WHERE id=?
            """, (
                resolved['calories'], resolved['protein_g'],
                resolved['carbs_g'],  resolved['fat_g'], row['id']
            ))
    conn.commit()
    conn.close()


def get_food_log(date_str, profile_id=1):
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM food_log WHERE date=? AND profile_id=?
           ORDER BY CASE meal_type
               WHEN 'breakfast' THEN 1
               WHEN 'lunch'     THEN 2
               WHEN 'dinner'    THEN 3
               ELSE 4
           END, id""", (date_str, profile_id)
    ).fetchall()
    conn.close()
    return rows


def log_food_entry(date_str, raw_name, profile_id=1, meal_type='snack',
                   quantity=None, explicit_macros=None, logged_at=None):
    """Single entry point for logging a food. Resolves quantity prefixes and plurals,
    scales macros from the library (unless explicit_macros override them), inserts one
    food_log row, and registers unknown foods as pending stubs in the library.

    `explicit_macros` is an optional dict (calories/protein_g/carbs_g/fat_g) used when
    the caller already has macros (e.g. the browser computed them or the user typed
    custom values). Returns the inserted food_log row as a dict.
    """
    if logged_at is None:
        from datetime import datetime as _dt
        logged_at = _dt.now().strftime('%H:%M')

    conn     = get_db()
    resolved = _resolve_food(conn, raw_name, quantity)
    display  = _display_name(resolved['display'])

    if explicit_macros and (float(explicit_macros.get('calories') or 0) > 0
                            or float(explicit_macros.get('protein_g') or 0) > 0):
        cal  = round(float(explicit_macros.get('calories')  or 0), 1)
        pro  = round(float(explicit_macros.get('protein_g') or 0), 1)
        carb = round(float(explicit_macros.get('carbs_g')   or 0), 1)
        fat  = round(float(explicit_macros.get('fat_g')     or 0), 1)
    else:
        cal, pro, carb, fat = (resolved['calories'], resolved['protein_g'],
                               resolved['carbs_g'],  resolved['fat_g'])

    entry_id = conn.execute(
        'INSERT INTO food_log (date,name,calories,protein_g,carbs_g,fat_g,profile_id,meal_type,logged_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (date_str, display, cal, pro, carb, fat, profile_id, meal_type, logged_at)
    ).lastrowid

    # Unknown food with no macros → register a pending 0-cal stub under its canonical
    # base name (never the quantity/gram-prefixed form) so it surfaces in the Food DB.
    if not resolved['matched'] and cal == 0 and resolved['base_name']:
        conn.execute("""
            INSERT INTO food_items (name, name_key, calories, protein_g, carbs_g, fat_g, unit_type)
            VALUES (?, ?, 0, 0, 0, 0, 'g')
            ON CONFLICT(name) DO NOTHING
        """, (_display_name(resolved['base_name']), resolved['key']))

    conn.commit()
    row = conn.execute('SELECT * FROM food_log WHERE id=?', (entry_id,)).fetchone()
    conn.close()
    return dict(row)


def add_food_entry(date_str, name, calories, protein_g, carbs_g, fat_g, profile_id=1,
                   meal_type='snack', logged_at=None):
    """Insert a pre-computed food_log row (used for composite meals with known macros).
    Updates the library only for plain food names — never quantity/gram-prefixed ones —
    so '2x eggs'-style entries don't pollute food_items."""
    if logged_at is None:
        from datetime import datetime as _dt
        logged_at = _dt.now().strftime('%H:%M')
    name = _display_name(name)
    conn = get_db()
    conn.execute(
        'INSERT INTO food_log (date,name,calories,protein_g,carbs_g,fat_g,profile_id,meal_type,logged_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (date_str, name, calories, protein_g, carbs_g, fat_g, profile_id, meal_type, logged_at)
    )
    parsed = _parse_entry_name(name)
    is_prefixed = (parsed['multiplier'] != 1.0 or parsed['gram_qty'] is not None)
    if not is_prefixed:
        # Keep library up-to-date — don't overwrite a defined food with zeros
        conn.execute("""
            INSERT INTO food_items (name,name_key,calories,protein_g,carbs_g,fat_g) VALUES (?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                calories  = CASE WHEN excluded.calories  > 0 THEN excluded.calories  ELSE food_items.calories  END,
                protein_g = CASE WHEN excluded.protein_g > 0 THEN excluded.protein_g ELSE food_items.protein_g END,
                carbs_g   = CASE WHEN excluded.carbs_g   > 0 THEN excluded.carbs_g   ELSE food_items.carbs_g   END,
                fat_g     = CASE WHEN excluded.fat_g     > 0 THEN excluded.fat_g     ELSE food_items.fat_g     END
        """, (name, parsed['key'], calories, protein_g, carbs_g, fat_g))
    conn.commit()
    conn.close()


def delete_food_entry(entry_id):
    conn = get_db()
    conn.execute('DELETE FROM food_log WHERE id=?', (entry_id,))
    conn.commit()
    conn.close()


def get_pending_foods(profile_id=1):
    """Food names logged with 0 calories that still need defining. Surfaces the base
    food name (quantity prefixes stripped) and de-duplicates plural variants so
    'egg' and 'eggs' collapse to a single pending entry."""
    conn = get_db()
    rows = conn.execute("""
        SELECT DISTINCT fl.name
        FROM food_log fl
        WHERE fl.calories = 0 AND fl.profile_id = ?
        ORDER BY fl.name COLLATE NOCASE
    """, (profile_id,)).fetchall()
    conn.close()
    seen, result = set(), []
    for r in rows:
        parsed       = _parse_entry_name(r['name'])
        pending_name = parsed['base_name'] or r['name']
        key          = parsed['key']
        if key not in seen:
            seen.add(key)
            result.append(pending_name)
    result.sort(key=str.lower)
    return result


def define_food_item(name, calories, protein_g, carbs_g, fat_g, unit_type='g'):
    """Create or update a library food, then backfill any 0-cal log rows that resolve
    to it (including quantity-prefixed and plural variants)."""
    name = _display_name(name)
    conn = get_db()
    key  = _food_key(name)
    conn.execute("""
        INSERT INTO food_items (name, name_key, calories, protein_g, carbs_g, fat_g, unit_type)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(name) DO UPDATE SET
            name_key=excluded.name_key,
            calories=excluded.calories, protein_g=excluded.protein_g,
            carbs_g=excluded.carbs_g,   fat_g=excluded.fat_g,
            unit_type=excluded.unit_type
    """, (name, key, calories, protein_g, carbs_g, fat_g, unit_type))
    # Backfill existing 0-cal log rows that resolve to this food (any date/profile).
    if calories > 0:
        pending = conn.execute(
            "SELECT id, name FROM food_log WHERE calories = 0"
        ).fetchall()
        for row in pending:
            resolved = _resolve_food(conn, row['name'])
            if resolved['matched'] and resolved['key'] == key:
                conn.execute("""
                    UPDATE food_log SET calories=?, protein_g=?, carbs_g=?, fat_g=?
                    WHERE id=?
                """, (resolved['calories'], resolved['protein_g'],
                      resolved['carbs_g'], resolved['fat_g'], row['id']))
    conn.commit()
    conn.close()


def delete_food_item(name):
    conn = get_db()
    conn.execute('DELETE FROM food_items WHERE name=? COLLATE NOCASE', (name,))
    conn.execute('DELETE FROM food_components WHERE food_name=? COLLATE NOCASE', (name,))
    conn.commit()
    conn.close()


def get_containers():
    conn = get_db()
    rows = conn.execute('SELECT id, name, weight_g FROM containers ORDER BY name').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_container(name, weight_g):
    conn = get_db()
    conn.execute(
        "INSERT INTO containers (name, weight_g) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET weight_g=excluded.weight_g",
        (name, weight_g)
    )
    conn.commit()
    conn.close()


def delete_container(container_id):
    conn = get_db()
    conn.execute('DELETE FROM containers WHERE id=?', (container_id,))
    conn.commit()
    conn.close()


def get_food_components():
    """Returns {food_name_lower: [{ingredient_name, quantity, pct}]} for all composite foods.

    Two composition modes share this table:
      • Fixed portion — `quantity` holds absolute grams/units, parent is unit_type='unit'.
      • By weight (%) — `pct` holds each ingredient's weight share, parent is unit_type='g'.
    A component list is a percentage composite when any row carries a pct > 0.
    """
    conn = get_db()
    rows = conn.execute(
        'SELECT food_name, ingredient_name, quantity, pct FROM food_components ORDER BY id'
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r['food_name'].lower(), []).append(
            {'ingredient_name': r['ingredient_name'],
             'quantity': r['quantity'], 'pct': r['pct']}
        )
    return result


def get_food_component_mode(food_name):
    """Return 'pct' for a percentage/by-weight composite, 'quantity' for a fixed-portion
    recipe, or None when the food has no components."""
    comps = get_food_components().get(food_name.lower())
    if not comps:
        return None
    return 'pct' if any((c.get('pct') or 0) > 0 for c in comps) else 'quantity'


def save_food_components(food_name, components):
    """Persist component list, ensure ingredients exist in food_items, recalculate parent macros."""
    food_name  = _display_name(food_name)
    components = [(_display_name(ing), qty) for ing, qty in components]
    conn = get_db()
    # Mark parent food as unit type (recipe foods are logged as whole servings)
    conn.execute("""
        INSERT INTO food_items (name, name_key, calories, protein_g, carbs_g, fat_g, unit_type)
        VALUES (?, ?, 0, 0, 0, 0, 'unit')
        ON CONFLICT(name) DO UPDATE SET unit_type = 'unit', name_key = excluded.name_key
    """, (food_name, _food_key(food_name)))
    conn.execute('DELETE FROM food_components WHERE food_name = ? COLLATE NOCASE', (food_name,))
    total_cal = total_pro = total_carb = total_fat = 0.0
    for ing_name, qty in components:
        if not ing_name or qty <= 0:
            continue
        conn.execute(
            'INSERT INTO food_components (food_name, ingredient_name, quantity) VALUES (?,?,?)',
            (food_name, ing_name, qty)
        )
        # Ensure ingredient exists in food_items; if unknown, insert as pending (0 cal).
        # Look up by canonical key so plural variants reuse the same definition.
        conn.execute("""
            INSERT INTO food_items (name, name_key, calories, protein_g, carbs_g, fat_g, unit_type)
            VALUES (?, ?, 0, 0, 0, 0, 'g')
            ON CONFLICT(name) DO NOTHING
        """, (ing_name, _food_key(ing_name)))
        item = _lookup_food_item(conn, _food_key(ing_name))
        if item and item['calories'] > 0:
            factor = qty if item['unit_type'] == 'unit' else qty / 100.0
            total_cal  += item['calories']  * factor
            total_pro  += item['protein_g'] * factor
            total_carb += item['carbs_g']   * factor
            total_fat  += item['fat_g']     * factor
    if total_cal > 0:
        conn.execute("""
            UPDATE food_items SET calories=?, protein_g=?, carbs_g=?, fat_g=?
            WHERE name=? COLLATE NOCASE
        """, (round(total_cal, 1), round(total_pro, 1), round(total_carb, 1), round(total_fat, 1), food_name))
        # Backfill any 0-cal log rows that resolve to this recipe — including fractional
        # portions like '0.2x Chili' — via the shared resolver.
        recipe_key = _food_key(food_name)
        for row in conn.execute("SELECT id, name FROM food_log WHERE calories = 0").fetchall():
            resolved = _resolve_food(conn, row['name'])
            if resolved['matched'] and resolved['key'] == recipe_key:
                conn.execute("""
                    UPDATE food_log SET calories=?, protein_g=?, carbs_g=?, fat_g=?
                    WHERE id=?
                """, (resolved['calories'], resolved['protein_g'],
                      resolved['carbs_g'], resolved['fat_g'], row['id']))
    conn.commit()
    conn.close()


def save_food_components_pct(food_name, components):
    """Persist a percentage / by-weight composite (e.g. curd rice = 60% rice + 40% yogurt).

    Because the shares are by weight, they *are* the per-100g composition: 100 g of the
    dish contains `pct` grams of each ingredient. So the parent is stored as an ordinary
    weight-based food (unit_type='g') whose per-100g macros are the weighted average of
    the ingredients' per-100g macros. Logging any gram amount then scales through the
    existing _resolve_food() pipeline with no special-casing.

    `components` is a list of (ingredient_name, pct). Shares are normalised to their own
    total, so both exact (60/40) and ratio (3/1) entry work. Only weight-based (g-type)
    ingredients contribute — unit-type ingredients have no per-gram basis and are skipped.
    """
    food_name  = _display_name(food_name)
    components = [(_display_name(ing), pct) for ing, pct in components]
    components = [(ing, pct) for ing, pct in components if ing and pct > 0]
    conn = get_db()

    conn.execute('DELETE FROM food_components WHERE food_name = ? COLLATE NOCASE', (food_name,))

    total_pct = sum(pct for _, pct in components)
    cal = pro = carb = fat = 0.0
    for ing_name, pct in components:
        conn.execute(
            'INSERT INTO food_components (food_name, ingredient_name, quantity, pct) VALUES (?,?,0,?)',
            (food_name, ing_name, pct)
        )
        # Ensure the ingredient exists in the library; unknown ones land as pending stubs.
        conn.execute("""
            INSERT INTO food_items (name, name_key, calories, protein_g, carbs_g, fat_g, unit_type)
            VALUES (?, ?, 0, 0, 0, 0, 'g')
            ON CONFLICT(name) DO NOTHING
        """, (ing_name, _food_key(ing_name)))
        item = _lookup_food_item(conn, _food_key(ing_name))
        # Only weight-based ingredients carry a per-100g basis usable by weight share.
        if item and item['calories'] > 0 and item['unit_type'] == 'g' and total_pct > 0:
            w = pct / total_pct                 # weight fraction of the dish
            cal  += item['calories']  * w
            pro  += item['protein_g'] * w
            carb += item['carbs_g']   * w
            fat  += item['fat_g']     * w

    # Store the composite as a normal per-100g food so logging scales it by grams.
    conn.execute("""
        INSERT INTO food_items (name, name_key, calories, protein_g, carbs_g, fat_g, unit_type)
        VALUES (?, ?, ?, ?, ?, ?, 'g')
        ON CONFLICT(name) DO UPDATE SET
            unit_type='g', name_key=excluded.name_key,
            calories=excluded.calories, protein_g=excluded.protein_g,
            carbs_g=excluded.carbs_g,   fat_g=excluded.fat_g
    """, (food_name, _food_key(food_name),
          round(cal, 1), round(pro, 1), round(carb, 1), round(fat, 1)))

    # Backfill any 0-cal log rows that resolve to this composite (incl. '150g Curd Rice').
    if cal > 0:
        parent_key = _food_key(food_name)
        for row in conn.execute("SELECT id, name FROM food_log WHERE calories = 0").fetchall():
            resolved = _resolve_food(conn, row['name'])
            if resolved['matched'] and resolved['key'] == parent_key:
                conn.execute("""
                    UPDATE food_log SET calories=?, protein_g=?, carbs_g=?, fat_g=?
                    WHERE id=?
                """, (resolved['calories'], resolved['protein_g'],
                      resolved['carbs_g'], resolved['fat_g'], row['id']))
    conn.commit()
    conn.close()


def log_food_reconciliation(original, normalized, profile_id=1):
    from datetime import datetime as _dt
    conn = get_db()
    conn.execute(
        'INSERT INTO food_reconciliations (original, normalized, profile_id, logged_at) VALUES (?,?,?,?)',
        (original, normalized, profile_id, _dt.utcnow().isoformat() + 'Z')
    )
    conn.commit()
    conn.close()


def get_food_reconciliations():
    conn = get_db()
    rows = conn.execute("""
        SELECT fr.*, p.name AS profile_name
        FROM food_reconciliations fr
        LEFT JOIN profiles p ON p.id = fr.profile_id
        ORDER BY fr.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_food_library():
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM food_items ORDER BY name COLLATE NOCASE'
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_foods(profile_id=1, limit=8):
    """Most-recently-logged distinct foods for quick re-logging. Returns clean base
    names (quantity prefixes stripped), de-duplicated by canonical key, newest first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT name FROM food_log WHERE profile_id=? ORDER BY id DESC LIMIT 200",
        (profile_id,)
    ).fetchall()
    conn.close()
    seen, result = set(), []
    for r in rows:
        parsed = _parse_entry_name(r['name'])
        base   = parsed['base_name'] or r['name']
        key    = parsed['key']
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(base)
        if len(result) >= limit:
            break
    return result


def get_food_history(profile_id=1):
    """Daily totals for this profile, newest first, last 60 days."""
    conn = get_db()
    rows = conn.execute("""
        SELECT date,
               ROUND(SUM(calories),1)  AS calories,
               ROUND(SUM(protein_g),1) AS protein_g,
               ROUND(SUM(carbs_g),1)   AS carbs_g,
               ROUND(SUM(fat_g),1)     AS fat_g
        FROM food_log
        WHERE profile_id = ?
        GROUP BY date
        ORDER BY date DESC
        LIMIT 60
    """, (profile_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_profiles():
    conn = get_db()
    rows = conn.execute('SELECT * FROM profiles ORDER BY id').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_body_weight(date_str, weight_kg, profile_id=1):
    conn = get_db()
    conn.execute("""
        INSERT INTO body_weight (date, weight_kg, profile_id)
        VALUES (?, ?, ?)
        ON CONFLICT(date, profile_id) DO UPDATE SET weight_kg=excluded.weight_kg
    """, (date_str, weight_kg, profile_id))
    conn.commit()
    conn.close()


def get_body_weight(date_str, profile_id=1):
    conn = get_db()
    row = conn.execute(
        "SELECT weight_kg FROM body_weight WHERE date=? AND profile_id=?",
        (date_str, profile_id)
    ).fetchone()
    conn.close()
    return row['weight_kg'] if row else None


def get_body_weight_history(profile_id=1, days=30):
    conn = get_db()
    rows = conn.execute("""
        SELECT date, weight_kg FROM body_weight
        WHERE profile_id=?
        ORDER BY date DESC LIMIT ?
    """, (profile_id, days)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Missions (Gayathri gamified milestones) ─────────────────────────────────

def get_mission_progress(profile_id):
    """Return {mission_key: cleared_stage_count} for a profile."""
    conn = get_db()
    rows = conn.execute(
        'SELECT mission_key, cleared FROM mission_progress WHERE profile_id=?',
        (profile_id,)).fetchall()
    conn.close()
    return {r['mission_key']: r['cleared'] for r in rows}


def clear_mission_stage(profile_id, mission_key, stage_index):
    """Forward-only: record that stages 0..stage_index are cleared.
    stage_index is 0-based; stored 'cleared' = number of stages done = stage_index + 1.
    Never decreases."""
    from datetime import datetime as _dt
    cleared = stage_index + 1
    conn = get_db()
    conn.execute("""
        INSERT INTO mission_progress (profile_id, mission_key, cleared, updated_at)
        VALUES (?,?,?,?)
        ON CONFLICT(profile_id, mission_key)
        DO UPDATE SET cleared = MAX(cleared, excluded.cleared), updated_at = excluded.updated_at
    """, (profile_id, mission_key, cleared, _dt.utcnow().isoformat() + 'Z'))
    conn.commit()
    row = conn.execute('SELECT cleared FROM mission_progress WHERE profile_id=? AND mission_key=?',
                       (profile_id, mission_key)).fetchone()
    conn.close()
    return row['cleared'] if row else cleared


# ── Exercise tally (lifetime 10-box progress, forward-from-today only) ─────

def get_exercise_tallies(profile_id):
    """Return {exercise_id: {'count': n, 'last_prompted': n}} for a profile."""
    conn = get_db()
    rows = conn.execute(
        'SELECT exercise_id, count, last_prompted FROM exercise_tally WHERE profile_id=?',
        (profile_id,)).fetchall()
    conn.close()
    return {r['exercise_id']: {'count': r['count'], 'last_prompted': r['last_prompted']} for r in rows}


def bump_exercise_tally(profile_id, exercise_id, by=1):
    """Forward-increment the lifetime tally count by `by` (default 1) and
    return the new total count."""
    from datetime import datetime as _dt
    conn = get_db()
    conn.execute("""
        INSERT INTO exercise_tally (profile_id, exercise_id, count, last_prompted, updated_at)
        VALUES (?,?,?,0,?)
        ON CONFLICT(profile_id, exercise_id)
        DO UPDATE SET count = count + excluded.count, updated_at = excluded.updated_at
    """, (profile_id, exercise_id, by, _dt.utcnow().isoformat() + 'Z'))
    conn.commit()
    row = conn.execute('SELECT count FROM exercise_tally WHERE profile_id=? AND exercise_id=?',
                       (profile_id, exercise_id)).fetchone()
    conn.close()
    return row['count'] if row else by


def ack_exercise_tally(profile_id, exercise_id):
    """Mark the current count as acknowledged so the 10-multiple dialog won't
    reappear until the count advances another 10."""
    conn = get_db()
    conn.execute('UPDATE exercise_tally SET last_prompted = count WHERE profile_id=? AND exercise_id=?',
                (profile_id, exercise_id))
    conn.commit()
    conn.close()




# ══════════════════════════════════════════════════════════════════════════════
# ── Swim v2 ──────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

_SWIM_WORKOUTS_SEED = [
    # (key, name, category, stroke, difficulty, sets_json, target_m, unlock_requires_json, is_unlocked, description, tree_x, tree_y)
    # Freestyle — Straight Sets
    ('Fr_SS_B1', '8×50m Freestyle',        'straight_sets', 'Fr', 'beginner',
     '[{"reps":8,"distance_m":50}]',                                                              400,  '[]',           1,
     'Eight quick 50m repeats. A solid starting point.', 0.12, 0.03),
    ('Fr_SS_B2', '12×50m Freestyle',       'straight_sets', 'Fr', 'beginner',
     '[{"reps":12,"distance_m":50}]',                                                             600,  '["Fr_SS_B1"]', 0,
     'Extend your repeats. Endurance builds.', 0.12, 0.11),
    ('Fr_SS_B3', '8×100m Freestyle',       'straight_sets', 'Fr', 'beginner',
     '[{"reps":8,"distance_m":100}]',                                                             800,  '["Fr_SS_B2"]', 0,
     'Step up to 100m sets. A new challenge.', 0.12, 0.19),
    ('Fr_SS_I1', '10×100m Freestyle',      'straight_sets', 'Fr', 'intermediate',
     '[{"reps":10,"distance_m":100}]',                                                           1000,  '["Fr_SS_B3"]', 0,
     'The kilometre benchmark. Ten clean hundreds.', 0.12, 0.30),
    ('Fr_SS_I2', '6×100m + Br Break + 6×100m', 'straight_sets', 'Fr', 'intermediate',
     '[{"reps":6,"distance_m":100},{"reps":4,"distance_m":50,"stroke_override":"Br"},{"reps":6,"distance_m":100}]',
                                                                                                 1400,  '["Fr_SS_I1"]', 0,
     'Breaststroke recovery mid-set. Active rest.', 0.12, 0.38),
    ('Fr_SS_I3', '15×100m Freestyle',      'straight_sets', 'Fr', 'intermediate',
     '[{"reps":15,"distance_m":100}]',                                                           1500,  '["Fr_SS_I2"]', 0,
     'Fifteen hundreds. Distance swimmer territory.', 0.12, 0.46),
    ('Fr_SS_A1', '20×100m Freestyle',      'straight_sets', 'Fr', 'advanced',
     '[{"reps":20,"distance_m":100}]',                                                           2000,  '["Fr_SS_I3"]', 0,
     'Two kilometres of hundreds. Elite endurance.', 0.12, 0.57),
    ('Fr_SS_A2', '10×200m Freestyle',      'straight_sets', 'Fr', 'advanced',
     '[{"reps":10,"distance_m":200}]',                                                           2000,  '["Fr_SS_A1"]', 0,
     'Long sets. Your lungs will know.', 0.12, 0.65),
    ('Fr_SS_A3', '50×100m Freestyle',      'straight_sets', 'Fr', 'advanced',
     '[{"reps":50,"distance_m":100}]',                                                           5000,  '["Fr_SS_A2"]', 0,
     'The five kilometre wall. Few reach this far.', 0.12, 0.73),
    # Freestyle — Pyramid
    ('Fr_PY_B1', '4×50m + 4×100m + 4×50m',          'pyramid', 'Fr', 'beginner',
     '[{"reps":4,"distance_m":50},{"reps":4,"distance_m":100},{"reps":4,"distance_m":50}]',      800,  '[]',           1,
     'Build up and back down. Classic pyramid.', 0.38, 0.03),
    ('Fr_PY_B2', '4×50m + 6×100m + 4×50m',          'pyramid', 'Fr', 'beginner',
     '[{"reps":4,"distance_m":50},{"reps":6,"distance_m":100},{"reps":4,"distance_m":50}]',     1000,  '["Fr_PY_B1"]', 0,
     'Wider peak. More time at the top.', 0.38, 0.11),
    ('Fr_PY_I1', '2×50 + 4×100 + 2×200 + 4×100 + 2×50', 'pyramid', 'Fr', 'intermediate',
     '[{"reps":2,"distance_m":50},{"reps":4,"distance_m":100},{"reps":2,"distance_m":200},{"reps":4,"distance_m":100},{"reps":2,"distance_m":50}]',
                                                                                                 1400,  '["Fr_PY_B2"]', 0,
     'True pyramid structure with 200m peak.', 0.38, 0.22),
    ('Fr_PY_I2', '2×50 + 4×100 + 2×200 + 2×400 + …', 'pyramid', 'Fr', 'intermediate',
     '[{"reps":2,"distance_m":50},{"reps":4,"distance_m":100},{"reps":2,"distance_m":200},{"reps":2,"distance_m":400},{"reps":2,"distance_m":200},{"reps":4,"distance_m":100},{"reps":2,"distance_m":50}]',
                                                                                                 2200,  '["Fr_PY_I1"]', 0,
     'Full pyramid with 400m summit. Demanding.', 0.38, 0.30),
    ('Fr_PY_A1', '2×100 + 4×200 + 2×400 + 4×200 + 2×100', 'pyramid', 'Fr', 'advanced',
     '[{"reps":2,"distance_m":100},{"reps":4,"distance_m":200},{"reps":2,"distance_m":400},{"reps":4,"distance_m":200},{"reps":2,"distance_m":100}]',
                                                                                                 3000,  '["Fr_PY_I2"]', 0,
     'The big pyramid. Three kilometres of structured pain.', 0.38, 0.41),
    # Freestyle — Descending
    ('Fr_DE_B1', '4×200m + 4×100m + 4×50m',          'descending', 'Fr', 'beginner',
     '[{"reps":4,"distance_m":200},{"reps":4,"distance_m":100},{"reps":4,"distance_m":50}]',    1400,  '[]',           1,
     'Distance drops each group. Gets easier as you go.', 0.62, 0.03),
    ('Fr_DE_I1', '4×400m + 4×200m + 4×100m',          'descending', 'Fr', 'intermediate',
     '[{"reps":4,"distance_m":400},{"reps":4,"distance_m":200},{"reps":4,"distance_m":100}]',   2800,  '["Fr_DE_B1"]', 0,
     'Long descending sets. Pace the 400s well.', 0.62, 0.14),
    ('Fr_DE_A1', '2×400 + 4×200 + 6×100 + 8×50',      'descending', 'Fr', 'advanced',
     '[{"reps":2,"distance_m":400},{"reps":4,"distance_m":200},{"reps":6,"distance_m":100},{"reps":8,"distance_m":50}]',
                                                                                                 2800,  '["Fr_DE_I1"]', 0,
     'Descending sets with increasing count. Complex.', 0.62, 0.25),
    # Freestyle — Intervals
    ('Fr_IN_B1', '8×(50m Fr + 50m Br)',              'intervals', 'Fr', 'beginner',
     '[{"reps":8,"distance_m":50},{"reps":8,"distance_m":50,"stroke_override":"Br"}]',           800,  '[]',           1,
     'Alternating freestyle and breaststroke. Active recovery.', 0.88, 0.03),
    ('Fr_IN_B2', '6×(100m Fr + 50m Br)',             'intervals', 'Fr', 'beginner',
     '[{"reps":6,"distance_m":100},{"reps":6,"distance_m":50,"stroke_override":"Br"}]',          900,  '["Fr_IN_B1"]', 0,
     'Longer work intervals. Breaststroke brings you back.', 0.88, 0.11),
    ('Fr_IN_I1', '8×(100m Fr + 50m Br)',             'intervals', 'Fr', 'intermediate',
     '[{"reps":8,"distance_m":100},{"reps":8,"distance_m":50,"stroke_override":"Br"}]',         1200,  '["Fr_IN_B2"]', 0,
     'Eight rounds of hard/easy. Lactate training.', 0.88, 0.22),
    ('Fr_IN_I2', '6×(200m Fr + 100m Br)',            'intervals', 'Fr', 'intermediate',
     '[{"reps":6,"distance_m":200},{"reps":6,"distance_m":100,"stroke_override":"Br"}]',        1800,  '["Fr_IN_I1"]', 0,
     'Long intervals. Each 200 should be fast.', 0.88, 0.30),
    ('Fr_IN_A1', '8×(200m Fr + 100m Br)',            'intervals', 'Fr', 'advanced',
     '[{"reps":8,"distance_m":200},{"reps":8,"distance_m":100,"stroke_override":"Br"}]',        2400,  '["Fr_IN_I2"]', 0,
     'Eight rounds of 300m. Sustained high intensity.', 0.88, 0.41),
    # Backstroke — Straight Sets
    ('Ba_SS_B1', '6×50m Backstroke',   'straight_sets', 'Ba', 'beginner',
     '[{"reps":6,"distance_m":50}]',   300,  '[]',           1,
     'Eyes up, stay straight. Backstroke fundamentals.', 0.25, 0.85),
    ('Ba_SS_B2', '10×50m Backstroke',  'straight_sets', 'Ba', 'beginner',
     '[{"reps":10,"distance_m":50}]',  500,  '["Ba_SS_B1"]', 0,
     'More reps, same distance. Build the rhythm.', 0.25, 0.91),
    ('Ba_SS_B3', '6×100m Backstroke',  'straight_sets', 'Ba', 'beginner',
     '[{"reps":6,"distance_m":100}]',  600,  '["Ba_SS_B2"]', 0,
     'Step up to 100m. Arms keep turning.', 0.25, 0.97),
    # Breaststroke — Straight Sets
    ('Br_SS_B1', '6×50m Breaststroke', 'straight_sets', 'Br', 'beginner',
     '[{"reps":6,"distance_m":50}]',   300,  '[]',           1,
     'Kick, pull, glide. Breaststroke basics.', 0.50, 0.85),
    ('Br_SS_B2', '10×50m Breaststroke','straight_sets', 'Br', 'beginner',
     '[{"reps":10,"distance_m":50}]',  500,  '["Br_SS_B1"]', 0,
     'More sets. Find your timing.', 0.50, 0.91),
    ('Br_SS_B3', '6×100m Breaststroke','straight_sets', 'Br', 'beginner',
     '[{"reps":6,"distance_m":100}]',  600,  '["Br_SS_B2"]', 0,
     'Full hundreds. Every stroke counts.', 0.50, 0.97),
    # Butterfly — Straight Sets
    ('Bt_SS_B1', '4×25m Butterfly',  'straight_sets', 'Bt', 'beginner',
     '[{"reps":4,"distance_m":25}]',  100,  '[]',           1,
     'Short bursts. Butterfly is all power.', 0.75, 0.85),
    ('Bt_SS_B2', '6×25m Butterfly',  'straight_sets', 'Bt', 'beginner',
     '[{"reps":6,"distance_m":25}]',  150,  '["Bt_SS_B1"]', 0,
     'Two more 25s. Your shoulders will notice.', 0.75, 0.91),
    ('Bt_SS_B3', '4×50m Butterfly',  'straight_sets', 'Bt', 'beginner',
     '[{"reps":4,"distance_m":50}]',  200,  '["Bt_SS_B2"]', 0,
     'Full 50m butterfly. The ultimate test.', 0.75, 0.97),
]

_SWIM_ACHIEVEMENTS_SEED = [
    # (key, name, description, category, threshold, icon)
    ('first_session',          'First Lap',            'Complete your first swim session',           'session',    1,     '🏊'),
    ('sessions_10',            'Ten Sessions',          'Complete 10 swim sessions',                  'session',   10,     '🔟'),
    ('sessions_50',            'Fifty Sessions',        'Complete 50 swim sessions',                  'session',   50,     '🏆'),
    ('first_1km_session',      'Kilometre Club',        'First session of 1km or more',               'distance', 1000,   '🎯'),
    ('first_2km_session',      'Double Kilometre',      'First session of 2km or more',               'distance', 2000,   '⭐'),
    ('first_5km_session',      'Five K',                'First session of 5km or more',               'distance', 5000,   '🌟'),
    ('total_10km',             '10km Total',            'Swim 10km cumulatively',                     'distance', 10000,  '📏'),
    ('total_50km',             '50km Total',            'Swim 50km cumulatively',                     'distance', 50000,  '🚀'),
    ('week_5km',               'Weekly 5K',             'Swim 5km in a single week',                  'streak',   5000,   '📅'),
    ('week_10km',              'Weekly 10K',            'Swim 10km in a single week',                 'streak',   10000,  '🔥'),
    ('complete_fr_beginner',   'Freestyle Foundation',  'Complete all beginner Freestyle workouts',   'collection', 0,    '🌊'),
    ('complete_fr_intermediate','Freestyle Ascent',     'Complete all intermediate Freestyle workouts','collection', 0,   '🏄'),
    ('complete_fr_advanced',   'Freestyle Elite',       'Complete all advanced Freestyle workouts',   'collection', 0,    '👑'),
    ('complete_ba_beginner',   'Back to Front',         'Complete all beginner Backstroke workouts',  'collection', 0,    '🔄'),
    ('complete_br_beginner',   'Breaststroke Badge',    'Complete all beginner Breaststroke workouts','collection', 0,    '🐸'),
    ('complete_bt_beginner',   'Flutter First',         'Complete all beginner Butterfly workouts',   'collection', 0,    '🦋'),
]


def _init_swim_v2(conn):
    """Create and seed swim v2 tables. Called from init_db()."""

    # ── Migrate old swim_achievements if it has the old schema ────────────────
    _old_ach_cols = {r[1] for r in conn.execute('PRAGMA table_info(swim_achievements)').fetchall()}
    if 'sets_json' in _old_ach_cols:
        # Old schema — rename to legacy before creating new one
        conn.execute('ALTER TABLE swim_achievements RENAME TO swim_achievement_goals_legacy')
        if 'swim_achievement_completions' in {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}:
            conn.execute('ALTER TABLE swim_achievement_completions RENAME TO swim_achievement_completions_legacy')

    # ── New tables ────────────────────────────────────────────────────────────
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS swim_workouts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            key                 TEXT    NOT NULL UNIQUE,
            name                TEXT    NOT NULL,
            category            TEXT    NOT NULL,
            stroke              TEXT    NOT NULL,
            difficulty          TEXT    NOT NULL,
            sets_json           TEXT    NOT NULL,
            target_m            INTEGER NOT NULL,
            unlock_requires_json TEXT   NOT NULL DEFAULT '[]',
            is_unlocked         INTEGER NOT NULL DEFAULT 0,
            description         TEXT,
            tree_x              REAL    NOT NULL DEFAULT 0.5,
            tree_y              REAL    NOT NULL DEFAULT 0.5
        );

        CREATE TABLE IF NOT EXISTS swim_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workout_id   INTEGER NOT NULL REFERENCES swim_workouts(id),
            performed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            total_m      INTEGER NOT NULL DEFAULT 0,
            result       TEXT    NOT NULL DEFAULT 'completed',
            notes        TEXT
        );

        CREATE TABLE IF NOT EXISTS swim_sets (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES swim_sessions(id) ON DELETE CASCADE,
            reps       INTEGER NOT NULL,
            distance_m INTEGER NOT NULL,
            stroke     TEXT    NOT NULL,
            set_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS swim_achievements (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT    NOT NULL UNIQUE,
            name        TEXT    NOT NULL,
            description TEXT,
            category    TEXT    NOT NULL,
            threshold   INTEGER NOT NULL DEFAULT 0,
            icon        TEXT
        );

        CREATE TABLE IF NOT EXISTS swim_achievement_unlocks (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            achievement_id INTEGER NOT NULL REFERENCES swim_achievements(id),
            unlocked_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS swim_pbs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pb_key      TEXT    NOT NULL UNIQUE,
            value       REAL    NOT NULL,
            achieved_at DATETIME NOT NULL,
            session_id  INTEGER REFERENCES swim_sessions(id)
        );

        CREATE TABLE IF NOT EXISTS swim_weekly_goals (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start       DATE    NOT NULL UNIQUE,
            target_m         INTEGER NOT NULL,
            is_user_override INTEGER NOT NULL DEFAULT 0
        );
    """)

    # ── Seed workouts (once only) ─────────────────────────────────────────────
    if not conn.execute('SELECT 1 FROM swim_workouts LIMIT 1').fetchone():
        conn.executemany(
            '''INSERT OR IGNORE INTO swim_workouts
               (key, name, category, stroke, difficulty, sets_json, target_m,
                unlock_requires_json, is_unlocked, description, tree_x, tree_y)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            _SWIM_WORKOUTS_SEED
        )

    # ── Seed achievements (once only) ─────────────────────────────────────────
    if not conn.execute('SELECT 1 FROM swim_achievements LIMIT 1').fetchone():
        conn.executemany(
            'INSERT OR IGNORE INTO swim_achievements (key,name,description,category,threshold,icon) VALUES (?,?,?,?,?,?)',
            _SWIM_ACHIEVEMENTS_SEED
        )

    # ══ Session types ═════════════════════════════════════════════════════════
    # Allow 'pilates' and 'cardio' as tracked session types. The original sessions
    # table has CHECK(type IN ('gym','swim')); rebuild it once to widen the CHECK.
    _sess_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sessions'"
    ).fetchone()
    if _sess_sql and ("'pilates'" not in _sess_sql[0] or "'cardio'" not in _sess_sql[0]):
        conn.executescript("""
            PRAGMA foreign_keys=OFF;
            CREATE TABLE sessions_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT NOT NULL,
                type       TEXT NOT NULL CHECK(type IN ('gym','swim','pilates','cardio')),
                day_type   TEXT CHECK(day_type IN ('push','pull','legs','core')),
                notes      TEXT,
                started_at TEXT,
                ended_at   TEXT,
                profile_id INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO sessions_new (id,date,type,day_type,notes,started_at,ended_at,profile_id)
                SELECT id,date,type,day_type,notes,started_at,ended_at,profile_id FROM sessions;
            DROP TABLE sessions;
            ALTER TABLE sessions_new RENAME TO sessions;
            PRAGMA foreign_keys=ON;
        """)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pilates_routines (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            key         TEXT NOT NULL UNIQUE,
            name        TEXT NOT NULL,
            description TEXT,
            sort_order  INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS pilates_exercises (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            routine_id    INTEGER NOT NULL REFERENCES pilates_routines(id) ON DELETE CASCADE,
            ex_order      INTEGER NOT NULL DEFAULT 0,
            name          TEXT    NOT NULL,
            reps          TEXT,
            guidance_json TEXT    NOT NULL DEFAULT '{}',
            notes         TEXT,
            hard_json     TEXT
        );

        CREATE TABLE IF NOT EXISTS pilates_sessions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            routine_id   INTEGER NOT NULL REFERENCES pilates_routines(id),
            session_id   INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
            performed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes        TEXT
        );

        CREATE TABLE IF NOT EXISTS pilates_session_exercises (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            pilates_session_id INTEGER NOT NULL REFERENCES pilates_sessions(id) ON DELETE CASCADE,
            exercise_id        INTEGER REFERENCES pilates_exercises(id),
            name               TEXT    NOT NULL,
            version            TEXT    NOT NULL DEFAULT 'standard',
            reps_done          TEXT,
            done               INTEGER NOT NULL DEFAULT 1,
            ex_order           INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS pilates_glossary (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            term       TEXT NOT NULL UNIQUE,
            definition TEXT NOT NULL,
            category   TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
    """)

    # ── Seed the glossary of reformer forms & positions (once only) ───────────
    if not conn.execute('SELECT 1 FROM pilates_glossary LIMIT 1').fetchone():
        conn.executemany(
            'INSERT OR IGNORE INTO pilates_glossary (term,definition,category,sort_order) '
            'VALUES (?,?,?,?)',
            [
                ('Tabletop position',
                 'Legs up, knees at 90 degrees.', 'Position', 0),
                ('Hundreds position',
                 'Sliding your neck and shoulders forward to a half crunch (holding), arms floating parallel.',
                 'Position', 1),
                ('Calf Raise',
                 'Knees straight but not hyperextended/locked, inner thighs together.', 'Form', 2),
                ('Wide Squat',
                 'Middle of heels on footbar. Feel how everything previously ties into a full squat — '
                 'a mind-muscle builder.', 'Form', 3),
                ('Sit Up Hold',
                 'Shift down from the shoulder rests, back flat, and slide up into Hundreds — up and down '
                 'for reps with your legs in tabletop. Easy mode: feet on footbar.', 'Form', 4),
                ('Strapped Squat',
                 'Resistance 2x red. Hook feet into straps and squat for reps, slow and controlled — '
                 'knees together, feet together, core engaged. Keep your pelvis still, but move your '
                 'hips and knees.', 'Form', 5),
                ('Straight Leg Hamstring Curl',
                 'Knees together, feet together, back flat. Feel it in your glutes and hamstrings.',
                 'Form', 6),
                ('Inner Thigh Stretch',
                 'Feet still strapped, legs straight at 90 degrees. Push feet out for reps. Back flat.',
                 'Form', 7),
                ('Seated Straight-Arm Row',
                 'Resistance 1x red. Turn to face away from the footbar, seated with some space behind '
                 'you. Grab the strap and row with straight arms — hands out to the side, then down and '
                 'back. Back straight, good posture. Hard mode: sit with straight legs.',
                 'Form', 8),
                ('Open Elbows — Upper Back Row',
                 'Straps held at the elbows, arms up at right angles. Arms are open-palm, rotating to '
                 'face you as you row.',
                 'Form', 9),
                ('Cross Raise',
                 'Seated facing the footbar, butt at the shoulder pads, straps in hands, arms straight. '
                 'Front raise motion.',
                 'Form', 10),
                ('Cable Fly',
                 '(Details to be added.)',
                 'Form', 11),
                ('Lateral Stretches',
                 'Bring arms up and stretch to either side. Footbar side: press the seat away while '
                 'reaching towards the footbar. Shoulder-pad side: put one arm on the shoulder pad while '
                 'the other arm stretches to the pulley.',
                 'Form', 12),
                ('Legs Locked',
                 'Facing one side, one shin across the shoulder bar, the other shin across the edge of '
                 'the seat.',
                 'Position', 13),
            ])

    # ── Seed the first reformer routine (once only) ───────────────────────────
    if not conn.execute('SELECT 1 FROM pilates_routines LIMIT 1').fetchone():
        _rid = conn.execute(
            'INSERT INTO pilates_routines (key,name,description,sort_order) VALUES (?,?,?,?)',
            ('reformer_squats', 'Reformer — Footwork Squats',
             'Footbar squat variations on the reformer. Add exercises as you go.', 0)
        ).lastrowid
        _PILATES_SEED = [
            (_rid, 0, 'V-Toe Squats', '10', _json.dumps({
                'resistance': '2x red, 1x blue',
                'general':    'Lay down',
                'feet':       'Balls of your feet against footbar, heels together — make a V',
                'knees':      'Shoulder width',
                'back':       'Flat',
                'hands':      'On your stomach',
            }), 'Constant smooth motion — try not to stop or snap. Should feel it in feet and ankles.', None),
            (_rid, 1, 'Sissy Squats', '10', _json.dumps({
                'resistance': '2x red, 1x blue',
                'general':    'Lay down',
                'feet':       'Centre of your heels against footbar, feet shoulder-width apart',
                'knees':      'Shoulder width',
                'back':       'Flat',
                'hands':      'On stomach',
            }), 'Should feel it in thighs and hamstrings.', None),
            (_rid, 2, 'Calf-Flexed Squats', '10', _json.dumps({
                'resistance': '2x red, 1x blue',
                'general':    'Lay down',
                'feet':       'Balls of feet against footbar, feet shoulder width',
                'back':       'Flat',
                'hands':      'On stomach',
            }), 'Keep calf flexed (toes pointed) throughout.', None),
        ]
        conn.executemany(
            'INSERT INTO pilates_exercises '
            '(routine_id,ex_order,name,reps,guidance_json,notes,hard_json) '
            'VALUES (?,?,?,?,?,?,?)', _PILATES_SEED)

    conn.commit()


# ── Muscle-group swimlane (home screen) ────────────────────────────────
# Collapses the granular exercise muscle_group values onto the display
# lanes shown on the dashboard. Cardio is populated from session_cardio;
# Core / Mobility are intentionally excluded.
_SWIMLANE_GROUPS = ['Chest', 'Back', 'Legs', 'Shoulders', 'Arms', 'Cardio']
_SWIMLANE_MAP = {
    'Chest': 'Chest',
    'Back': 'Back', 'Upper Back': 'Back', 'Posterior Chain': 'Back',
    'Legs': 'Legs',
    'Shoulders': 'Shoulders',
    'Biceps': 'Arms', 'Triceps': 'Arms', 'Arms': 'Arms',
}


def get_muscle_swimlane(profile_id, days=7):
    """7-day rolling activity per display muscle group, split by exercise tier.

    Returns a JSON-ready dict:
        {
          "days":   [{short, num, iso, is_today}, ...],   # oldest -> today
          "groups": ["Chest", "Back", "Legs", "Shoulders", "Arms"],
          "cells":  { group: [ [ {tier, name, sets}, ... ] x days ] },
          "summary": {"sessions": int, "exercises": int, "top_group": str|None}
        }
    Tier is the exercise's fixed movement classification (1 heavy compound
    through 4 finisher/accessory).
    """
    from datetime import timedelta
    conn = get_db()
    today = date.today()
    start = today - timedelta(days=days - 1)

    rows = conn.execute("""
        SELECT s.date, e.muscle_group, e.tier, e.name, COUNT(*) AS sets
        FROM session_lifts sl
        JOIN sessions  s ON s.id = sl.session_id AND s.type = 'gym'
        JOIN exercises e ON e.id = sl.exercise_id
        WHERE s.profile_id = ? AND s.date BETWEEN ? AND ?
        GROUP BY s.date, e.id
        ORDER BY s.date, e.tier, e.name
    """, (profile_id, start.isoformat(), today.isoformat())).fetchall()

    cardio_rows = conn.execute("""
        SELECT s.date, e.name, COUNT(*) AS sets
        FROM session_cardio sc
        JOIN sessions  s ON s.id = sc.session_id
        JOIN exercises e ON e.id = sc.exercise_id
        WHERE s.profile_id = ? AND s.date BETWEEN ? AND ?
        GROUP BY s.date, e.id
        ORDER BY s.date, e.name
    """, (profile_id, start.isoformat(), today.isoformat())).fetchall()
    conn.close()

    day_list  = [start + timedelta(days=i) for i in range(days)]
    day_index = {d.isoformat(): i for i, d in enumerate(day_list)}

    cells         = {g: [[] for _ in range(days)] for g in _SWIMLANE_GROUPS}
    active_dates  = set()
    exercise_cnt  = 0
    group_sets    = {g: 0 for g in _SWIMLANE_GROUPS}

    for r in rows:
        group = _SWIMLANE_MAP.get(r['muscle_group'])
        if group is None:
            continue
        di = day_index.get(r['date'])
        if di is None:
            continue
        cells[group][di].append({
            'tier': r['tier'],
            'name': r['name'],
            'sets': r['sets'],
        })
        active_dates.add(r['date'])
        exercise_cnt += 1
        group_sets[group] += r['sets']

    for r in cardio_rows:
        di = day_index.get(r['date'])
        if di is None:
            continue
        cells['Cardio'][di].append({
            'tier': 4,
            'name': r['name'],
            'sets': r['sets'],
        })
        active_dates.add(r['date'])
        exercise_cnt += 1
        group_sets['Cardio'] += r['sets']

    top_group = max(group_sets, key=group_sets.get) if any(group_sets.values()) else None

    return {
        'days': [
            {'short': d.strftime('%a'), 'num': d.day,
             'iso': d.isoformat(), 'is_today': d == today}
            for d in day_list
        ],
        'groups':  _SWIMLANE_GROUPS,
        'cells':   cells,
        'summary': {
            'sessions':  len(active_dates),
            'exercises': exercise_cnt,
            'top_group': top_group,
        },
    }


# ── Exercise Bank (Stats page) ─────────────────────────────────────────
# Read-only, tier-grouped view of every defined exercise with its set scheme.
_BANK_META = {
    1: {'label': 'Tier 1 — Heavy compounds',         'range': '3–5 sets × 3–5 reps'},
    2: {'label': 'Tier 2 — Supporting compounds',    'range': '3–4 sets × 6–8 reps'},
    3: {'label': 'Tier 3 — Aesthetics / isolation',  'range': '3–4 sets × 8–12 reps'},
    4: {'label': 'Tier 4 — Finishers / cardio',      'range': 'No progression scheme'},
}
_BANK_TIER_DEFAULT = {1: '3–5 × 3–5', 2: '3–4 × 6–8', 3: '3–4 × 8–12'}


def get_exercise_bank():
    """Every defined exercise grouped by tier, with set scheme + current working set.

    Returns {'meta', 'tiers': {1..4: [rows]}, 'counts', 'total'}. Each row carries
    name, muscle_group, day_type, scheme (sets × reps band), current (working set @
    load), and flags (reps-only / timed). Scheme uses the exercise's custom bounds
    when set, else the tier default; Tier 4 has no progression scheme.
    """
    conn = get_db()
    rows = conn.execute("""
        SELECT e.id, e.name, e.tier, e.muscle_group, e.day_type, e.reps_only, e.is_timed,
               e.reps_min, e.reps_max, e.sets_min, e.sets_max,
               p.weight_kg, sc.sets AS cur_sets, sc.reps AS cur_reps
        FROM exercises e
        LEFT JOIN progression p ON p.exercise_id = e.id
        LEFT JOIN schemes     sc ON sc.id = p.scheme_id
        ORDER BY e.tier, e.muscle_group, e.name
    """).fetchall()
    conn.close()

    tiers = {1: [], 2: [], 3: [], 4: []}
    for r in rows:
        t = r['tier']
        if r['sets_min'] is not None:
            scheme = f"{r['sets_min']}–{r['sets_max']} × {r['reps_min']}–{r['reps_max']}"
        else:
            scheme = _BANK_TIER_DEFAULT.get(t, '—')

        if r['cur_sets']:
            w = r['weight_kg']
            if w is None:
                load = ''
            elif w == 0:
                load = ' @ BW'
            else:
                load = f" @ {w:g} kg"
            current = f"{r['cur_sets']}×{r['cur_reps']}{load}"
        else:
            current = '—'

        flags = []
        if r['reps_only']:
            flags.append('reps-only')
        if r['is_timed']:
            flags.append('timed')

        tiers.setdefault(t, []).append({
            'id':           r['id'],
            'tier':         t,
            'name':         r['name'],
            'muscle_group': r['muscle_group'],
            'day_type':     r['day_type'],
            'scheme':       scheme,
            'current':      current,
            'flags':        flags,
            'reps_min':     r['reps_min'],
            'reps_max':     r['reps_max'],
            'sets_min':     r['sets_min'],
            'sets_max':     r['sets_max'],
        })

    return {
        'meta':   _BANK_META,
        'tiers':  tiers,
        'counts': {t: len(v) for t, v in tiers.items()},
        'total':  len(rows),
    }


# ── Exercise Bank — mutations (add / amend / remove) ───────────────────
_BANK_VALID_TIERS = {1, 2, 3, 4}
_BANK_VALID_DAYS  = {'push', 'pull', 'legs', 'core', 'any'}


def _bank_validate(tier, muscle_group, day_type, bounds):
    """Return (clean_bounds|None, error|None). bounds is (rmin,rmax,smin,smax) or all-None."""
    if tier not in _BANK_VALID_TIERS:
        return None, 'Tier must be 1–4.'
    if not (muscle_group or '').strip():
        return None, 'Muscle group is required.'
    if day_type not in _BANK_VALID_DAYS:
        return None, 'Day must be one of push, pull, legs, core, any.'
    rmin, rmax, smin, smax = bounds
    provided = [v for v in bounds if v not in (None, '')]
    if not provided:
        return (None, None, None, None), None          # no custom bounds → tier default
    if len(provided) != 4:
        return None, 'Set all four scheme bounds, or leave them all blank.'
    try:
        rmin, rmax, smin, smax = int(rmin), int(rmax), int(smin), int(smax)
    except (TypeError, ValueError):
        return None, 'Scheme bounds must be whole numbers.'
    if not (1 <= rmin <= rmax <= 50 and 1 <= smin <= smax <= 20):
        return None, 'Scheme bounds out of range (reps 1–50, sets 1–20, min ≤ max).'
    if tier == 4:
        return (None, None, None, None), None           # tier 4 has no progression ladder
    return (rmin, rmax, smin, smax), None


def _regen_exercise_schemes(conn, ex_id):
    """Rebuild an exercise's progression ladder after a tier/bounds change.

    Regenerates exercise-specific schemes from custom bounds (tiers 1–3), repoints
    any existing progression row to the first applicable scheme, and clears stale
    stage/attempt bookkeeping so it restarts cleanly on the new scheme.
    """
    ex = conn.execute(
        'SELECT tier, reps_min, reps_max, sets_min, sets_max FROM exercises WHERE id=?', (ex_id,)
    ).fetchone()
    if not ex:
        return
    tier = ex['tier']
    conn.execute('DELETE FROM schemes WHERE exercise_id=?', (ex_id,))
    if ex['reps_min'] is not None and tier in (1, 2, 3):
        order, rows = 1, []
        for s in range(ex['sets_min'], ex['sets_max'] + 1):
            for r in range(ex['reps_min'], ex['reps_max'] + 1):
                rows.append((tier, ex_id, r, s, order)); order += 1
        conn.executemany(
            'INSERT INTO schemes (tier, exercise_id, reps, sets, progression_order) VALUES (?,?,?,?,?)',
            rows
        )
    if conn.execute('SELECT 1 FROM progression WHERE exercise_id=?', (ex_id,)).fetchone() and tier in (1, 2, 3):
        first = _first_scheme(conn, tier, ex_id)
        if first:
            conn.execute('UPDATE progression SET scheme_id=?, stage=0 WHERE exercise_id=?', (first['id'], ex_id))
    conn.execute('DELETE FROM stage_completions WHERE exercise_id=?', (ex_id,))
    conn.execute('DELETE FROM exercise_attempts  WHERE exercise_id=?', (ex_id,))


def bank_add_exercise(name, tier, muscle_group, day_type, bounds=(None, None, None, None)):
    """Create a new exercise. Returns (ok, error)."""
    name = (name or '').strip()
    if not name:
        return False, 'Name is required.'
    clean, err = _bank_validate(tier, muscle_group, day_type, bounds)
    if err:
        return False, err
    conn = get_db()
    try:
        cur = conn.execute(
            'INSERT INTO exercises (name, tier, muscle_group, day_type, reps_min, reps_max, sets_min, sets_max) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (name, tier, muscle_group.strip(), day_type, *clean)
        )
        _regen_exercise_schemes(conn, cur.lastrowid)
        conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, f'An exercise named “{name}” already exists.'
    finally:
        conn.close()


def bank_update_exercise(ex_id, tier, muscle_group, day_type, bounds=(None, None, None, None)):
    """Amend an exercise's tier / muscle group / day / scheme bounds. Returns (ok, error)."""
    clean, err = _bank_validate(tier, muscle_group, day_type, bounds)
    if err:
        return False, err
    conn = get_db()
    try:
        if not conn.execute('SELECT 1 FROM exercises WHERE id=?', (ex_id,)).fetchone():
            return False, 'Exercise not found.'
        conn.execute(
            'UPDATE exercises SET tier=?, muscle_group=?, day_type=?, '
            'reps_min=?, reps_max=?, sets_min=?, sets_max=? WHERE id=?',
            (tier, muscle_group.strip(), day_type, *clean, ex_id)
        )
        _regen_exercise_schemes(conn, ex_id)
        conn.commit()
        return True, None
    finally:
        conn.close()


def bank_delete_exercise(ex_id):
    """Delete an exercise. Refuses if it has logged history. Returns (ok, error)."""
    conn = get_db()
    try:
        row = conn.execute('SELECT name FROM exercises WHERE id=?', (ex_id,)).fetchone()
        if not row:
            return False, 'Exercise not found.'
        hist = conn.execute('SELECT COUNT(*) FROM session_lifts WHERE exercise_id=?', (ex_id,)).fetchone()[0]
        hist += conn.execute('SELECT COUNT(*) FROM session_cardio WHERE exercise_id=?', (ex_id,)).fetchone()[0]
        if hist:
            return False, f'“{row["name"]}” has {hist} logged set(s); remove or reassign its history first.'
        # schemes.exercise_id has no ON DELETE CASCADE — clear it explicitly; the rest cascade.
        conn.execute('DELETE FROM schemes WHERE exercise_id=?', (ex_id,))
        conn.execute('DELETE FROM exercises WHERE id=?', (ex_id,))
        conn.commit()
        return True, None
    finally:
        conn.close()
