"""Home-screen computation: how many steps stand between the user and their
goal weight, and how many they've walked today.

`steps_to_target` mirrors the Analytics "steps to target" figure: it forecasts
the user's current weight from their all-time calorie balance, then sums the
steps needed (bucket by bucket, since calories-per-step changes with weight) to
burn off the remaining kilograms down to the target weight.
"""
from datetime import date

from database import (
    get_db, get_setting, calc_day_totals, calc_bmr, calc_age,
    calories_per_step, get_current_weight, _STEP_TABLE,
)


def _f(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _predicted_current_weight(conn):
    """Replicates the Analytics forecast: actual post-start weight if logged,
    else starting weight adjusted by the all-time calorie balance."""
    starting_weight = _f(get_setting('starting_weight'))
    starting_date = get_setting('starting_date')
    height = _f(get_setting('height_cm'))
    dob = get_setting('dob')
    gender = get_setting('gender')
    age = calc_age(dob) if dob else None

    # A real weight reading logged after the start date wins outright.
    if starting_date:
        row = conn.execute(
            'SELECT weight_kg FROM daily_steps '
            'WHERE weight_kg IS NOT NULL AND date > ? ORDER BY date DESC LIMIT 1',
            (starting_date,)
        ).fetchone()
        if row:
            return float(row['weight_kg'])

    if starting_weight is None:
        return get_current_weight()

    # Days that have steps or diary entries, in order.
    series = conn.execute('''
        SELECT date FROM daily_steps WHERE steps > 0
        UNION
        SELECT DISTINCT date FROM diary_entries
        ORDER BY date
    ''').fetchall()
    step_map = {r['date']: r for r in conn.execute(
        'SELECT date, steps, weight_kg FROM daily_steps').fetchall()}

    last_baseline = starting_weight
    balance = 0.0
    total_intake = 0.0
    total_output = 0.0
    for r in series:
        d = r['date']
        se = step_map.get(d)
        s = se['steps'] if se else 0
        if se and se['weight_kg']:
            w_today = float(se['weight_kg'])
            last_baseline = w_today
            balance = 0.0
        elif last_baseline is not None:
            w_today = last_baseline + balance / 7700
        else:
            w_today = None
        food = calc_day_totals(d)['calories']
        bmr = calc_bmr(w_today, height, age, gender) if (w_today and height and age) else None
        step_cal = s * calories_per_step(w_today) if w_today else 0
        output = (bmr + step_cal) if bmr else step_cal
        bal = (food - output) if output else 0
        balance += bal
        total_intake += food or 0
        total_output += output or 0

    total_change_kg = (total_intake - total_output) / 7700
    return starting_weight + total_change_kg


def _steps_to_target(current_weight, target_weight):
    """Total steps to burn off (current_weight - target_weight), summed over the
    5-kg calorie-per-step buckets. None if inputs missing; 0 if already there."""
    if not target_weight or current_weight is None:
        return None
    if current_weight - target_weight <= 0:
        return 0
    buckets_desc = [(130, float('inf'), 0.076)]
    for w1, c1 in reversed(_STEP_TABLE[:-1]):
        buckets_desc.append((w1, w1 + 5, c1))
    buckets_desc.append((0, 40, 0.023))

    w_walk = current_weight
    total = 0.0
    for lo, up, cps in buckets_desc:
        if w_walk <= lo:
            continue
        top = min(w_walk, up)
        bot = max(lo, target_weight)
        if bot >= top:
            if w_walk <= target_weight:
                break
            continue
        kg = top - bot
        total += (kg * 7700) / cps if cps else 0
        w_walk = bot
        if w_walk <= target_weight:
            break
    return int(round(total))


def _pct_str(p):
    """Format a percentage to 3 decimal places (e.g. 1.0 -> '1.000')."""
    if p is None:
        return None
    return '%.3f' % p


# Profile details the home screen needs before it can show the goal.
REQUIRED_DETAILS = [
    ('dob', 'Date of birth'),
    ('height_cm', 'Height'),
    ('gender', 'Gender'),
    ('starting_date', 'Starting date'),
    ('starting_weight', 'Starting weight'),
    ('target_weight', 'Target weight'),
]

HYDRATION_TARGET_ML = 2000


def _calorie_deficit():
    raw = get_setting('calorie_deficit')
    try:
        return int(float(raw)) if raw not in (None, '') else 250
    except (TypeError, ValueError):
        return 250


def _calorie_target():
    """Auto daily calorie target = BMR(current weight, height, age, gender) minus
    the configured deficit. None if any input is missing."""
    weight = get_current_weight()
    height = _f(get_setting('height_cm'))
    dob = get_setting('dob')
    age = calc_age(dob) if dob else None
    gender = (get_setting('gender') or '').strip().lower() or None
    if not (weight and height and age):
        return None
    try:
        bmr = calc_bmr(weight, float(height), age, gender)
    except (TypeError, ValueError):
        return None
    return max(0.0, bmr - _calorie_deficit())


def compute_home_status():
    conn = get_db()
    try:
        starting_weight = _f(get_setting('starting_weight'))
        starting_date = get_setting('starting_date')
        target_weight = _f(get_setting('target_weight'))
        setup_complete = starting_weight is not None and starting_date is not None

        missing_details = [
            label for key, label in REQUIRED_DETAILS
            if not (get_setting(key) or '').strip()
        ]
        details_complete = not missing_details

        today = date.today().strftime('%Y-%m-%d')
        trow = conn.execute('SELECT steps FROM daily_steps WHERE date=?', (today,)).fetchone()
        today_steps = int(trow['steps']) if trow and trow['steps'] else 0

        # ── Steps tile ──────────────────────────────────────────────────────
        # Left number: steps recorded from the starting date onward.
        if starting_date:
            rec = conn.execute(
                'SELECT COALESCE(SUM(steps), 0) AS s FROM daily_steps WHERE date >= ?',
                (starting_date,)
            ).fetchone()
        else:
            rec = conn.execute('SELECT COALESCE(SUM(steps), 0) AS s FROM daily_steps').fetchone()
        total_recorded = int(rec['s']) if rec and rec['s'] is not None else 0

        # Right number: fixed whole-journey goal (starting weight -> target).
        total_required = None
        percent = None
        goal_reached = False
        steps_left = None
        current_weight = None
        if setup_complete:
            current_weight = _predicted_current_weight(conn)
            if target_weight is not None and starting_weight is not None:
                total_required = _steps_to_target(starting_weight, target_weight)
                if total_required is not None:
                    steps_left = max(0, total_required - total_recorded)
                    if total_required <= 0:
                        goal_reached = True
                        percent = 100.0
                    else:
                        percent = min(100.0, total_recorded / total_required * 100.0)

        # ── Calories tile ──────────────────────────────────────────────────
        day_totals = calc_day_totals(today)
        today_calories = int(round(day_totals['calories']))
        calorie_target = _calorie_target()

        # Full nutrient totals + targets for the Nutrition/Vitamins/Minerals tiles.
        try:
            import app as _hector_app
            targets = _hector_app.get_targets()
        except Exception:
            targets = {}
        nutri_totals = {k: round(v, 3) for k, v in day_totals.items()}
        calorie_target_int = int(round(calorie_target)) if calorie_target is not None else None
        calorie_diff = (today_calories - calorie_target_int) if calorie_target_int is not None else None

        # ── Hydration tile ─────────────────────────────────────────────────
        hrow = conn.execute('SELECT ml FROM daily_hydration WHERE date=?', (today,)).fetchone()
        hydration_ml = int(hrow['ml']) if hrow and hrow['ml'] else 0

        return {
            'setup_complete': setup_complete,
            'details_complete': details_complete,
            'missing_details': missing_details,
            'has_target': target_weight is not None,
            'starting_weight': starting_weight,
            'target_weight': target_weight,
            'current_weight': round(current_weight, 1) if current_weight is not None else None,
            # steps tile
            'total_required': total_required,
            'total_recorded': total_recorded,
            'percent': percent,
            'percent_str': _pct_str(percent),
            'steps_left': steps_left,
            'thousands_left': ((steps_left + 999) // 1000) if steps_left is not None else None,
            'today_steps': today_steps,
            'goal_reached': goal_reached,
            # calories tile
            'today_calories': today_calories,
            'calorie_target': calorie_target_int,
            'calorie_diff': calorie_diff,
            # hydration tile
            'hydration_ml': hydration_ml,
            'hydration_target': HYDRATION_TARGET_ML,
            # nutrition / vitamins / minerals tiles
            'nutri_totals': nutri_totals,
            'targets': targets,
            'today_date': today,
        }
    finally:
        conn.close()
