"""Energy-unit conversion — the single canonical kcal <-> kJ seam.

The database stores food energy canonically in kilocalories (kcal): every
`calories` column in `food_log`, `food_items` and `macro_goals` is kcal. The
UI may DISPLAY that energy in either kcal or kilojoules (kJ); nothing is ever
stored in kJ. All conversion lives here so the factor is defined exactly once.

Factor: 1 kcal = 4.184 kJ (the thermochemical calorie, which is the convention
used on nutrition labels — AU/NZ/EU included). Keep this as the one source of
truth; template JS is handed the same constant via the home context so the
client-side toggle and any server-side formatting can never drift apart.
"""

# One kilocalorie expressed in kilojoules. Single source of truth for the factor.
KJ_PER_KCAL = 4.184

# Unit identifiers used by the UI toggle / any stored preference. 'kcal' is the
# canonical storage unit and the default display unit.
ENERGY_UNITS = ('kcal', 'kj')


def kcal_to_kj(kcal):
    """Convert a kcal value (the canonical stored unit) to kJ.

    Returns a float; callers decide on rounding/formatting. ``None`` and other
    falsy-but-numeric inputs are treated as 0.0 so display code never blows up
    on a missing total.
    """
    try:
        return float(kcal or 0) * KJ_PER_KCAL
    except (TypeError, ValueError):
        return 0.0


def kj_to_kcal(kj):
    """Convert a kJ value back to canonical kcal (inverse of :func:`kcal_to_kj`).

    Provided for completeness / round-tripping (e.g. if a future input ever
    accepts kJ and must be stored as kcal). Returns a float.
    """
    try:
        return float(kj or 0) / KJ_PER_KCAL
    except (TypeError, ValueError):
        return 0.0


def to_display(kcal, unit='kcal'):
    """Convert a canonical kcal value into the requested DISPLAY unit.

    ``unit`` is one of :data:`ENERGY_UNITS`; anything other than ``'kj'`` is
    treated as kcal (the safe canonical default). Returns a float in the target
    unit; rounding/labelling is the caller's concern.
    """
    return kcal_to_kj(kcal) if str(unit).lower() == 'kj' else float(kcal or 0)
