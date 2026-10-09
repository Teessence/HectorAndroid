"""Home-screen computation: how many steps stand between the user and their
goal weight, and how many they've walked today.

`steps_to_target` mirrors the Analytics "steps to target" figure: it forecasts
the user's current weight from their all-time calorie balance, then sums the
steps needed (bucket by bucket, since calories-per-step changes with weight) to
burn off the remaining kilograms down to the target weight.
"""
from datetime import date, datetime, timedelta

from database import (
    get_db, get_setting, calc_day_totals, calc_bmr, calc_age,
    calories_per_step, get_current_weight, _STEP_TABLE,
)


def _f(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _weight_this_morning(conn, today):
    """Forecast weight at the start of `today`.

    Walks every day from the starting date up to yesterday. A logged weight
    resets the baseline; otherwise the weight drifts by that day's calorie
    balance (food eaten - BMR - steps burned) / 7700. A weight logged today
    wins outright. Today's own steps are deliberately left out — the caller
    subtracts them step-for-step so walking visibly shrinks the target."""
    starting_weight = _f(get_setting('starting_weight'))
    starting_date = get_setting('starting_date')
    if starting_weight is None or not starting_date:
        return get_current_weight()
    height = _f(get_setting('height_cm'))
    dob = get_setting('dob')
    gender = get_setting('gender')
    age = calc_age(dob) if dob else None

    rows = conn.execute(
        'SELECT date, steps, weight_kg FROM daily_steps WHERE date >= ? AND date <= ?',
        (starting_date, today)).fetchall()
    step_map = {r['date']: r for r in rows}
    se = step_map.get(today)
    if se and se['weight_kg']:
        return float(se['weight_kg'])

    days = sorted({r['date'] for r in rows if r['date'] < today and (r['steps'] or r['weight_kg'])} |
                  {r['date'] for r in conn.execute(
                      'SELECT DISTINCT date FROM diary_entries WHERE date >= ? AND date < ?',
                      (starting_date, today))})

    baseline = starting_weight
    balance = 0.0
    for d in days:
        se = step_map.get(d)
        if se and se['weight_kg']:
            baseline = float(se['weight_kg'])
            balance = 0.0
        w_day = baseline + balance / 7700
        s = (se['steps'] or 0) if se else 0
        food = calc_day_totals(d)['calories']
        bmr = calc_bmr(w_day, height, age, gender) if (height and age) else 0
        balance += food - bmr - s * calories_per_step(w_day)
    return baseline + balance / 7700


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


RANGES = [
    ('today', 'Today'),
    ('yesterday', 'Yesterday'),
    ('day', 'Specific day'),
    ('this_week', 'Current week'),
    ('last_week', 'Last week'),
    ('this_month', 'Current month'),
    ('last_month', 'Last month'),
    ('this_year', 'Current year'),
    ('since_start', 'Since starting date'),
    ('custom', 'Custom range'),
]
RANGE_KEYS = {k for k, _ in RANGES}


def _parse(d):
    try:
        return datetime.strptime(d, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def resolve_range(key, start=None, end=None):
    """Map a range key (+ dates for 'day'/'custom') to (key, start_date, end_date).
    Weeks start on Monday; nothing ever extends past today."""
    today = date.today()
    if key == 'yesterday':
        s = e = today - timedelta(days=1)
    elif key == 'day':
        s = _parse(start)
        if s is None:
            return resolve_range('today')
        s = e = min(s, today)
    elif key == 'this_week':
        s, e = today - timedelta(days=today.weekday()), today
    elif key == 'last_week':
        e = today - timedelta(days=today.weekday() + 1)
        s = e - timedelta(days=6)
    elif key == 'this_month':
        s, e = today.replace(day=1), today
    elif key == 'last_month':
        e = today.replace(day=1) - timedelta(days=1)
        s = e.replace(day=1)
    elif key == 'this_year':
        s, e = today.replace(month=1, day=1), today
    elif key == 'since_start':
        s = _parse(get_setting('starting_date')) or today
        s, e = min(s, today), today
    elif key == 'custom':
        s, e = _parse(start), _parse(end)
        if s is None or e is None:
            return resolve_range('today')
        if s > e:
            s, e = e, s
        e = min(e, today)
        s = min(s, e)
    else:
        key, s, e = 'today', today, today
    return key, s, e


def range_label(start, end):
    """Short human label: 'Fri 9 Oct' or '1 Oct – 9 Oct' (years only if needed)."""
    def fmt(d, with_year):
        return f'{d:%a} {d.day} {d:%b}' + (f' {d.year}' if with_year else '')
    if start == end:
        return fmt(start, start.year != date.today().year)
    with_year = start.year != end.year or end.year != date.today().year
    short = lambda d: f'{d.day} {d:%b}' + (f' {d.year}' if with_year else '')
    return f'{short(start)} – {short(end)}'



def _range_totals(conn, start, end):
    """Summed diary nutrient totals over [start, end] (inclusive)."""
    total = calc_day_totals('0000-00-00')  # all-zero totals dict
    for r in conn.execute('SELECT DISTINCT date FROM diary_entries WHERE date BETWEEN ? AND ?',
                          (start, end)).fetchall():
        for k, v in calc_day_totals(r['date']).items():
            total[k] = total.get(k, 0.0) + (v or 0.0)
    return total


def compute_home_status(range_key='today', range_from=None, range_to=None):
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
        range_key, r_start, r_end = resolve_range(range_key, range_from, range_to)
        start_s, end_s = r_start.strftime('%Y-%m-%d'), r_end.strftime('%Y-%m-%d')
        days = (r_end - r_start).days + 1

        trow = conn.execute('SELECT steps FROM daily_steps WHERE date=?', (today,)).fetchone()
        today_steps = int(trow['steps']) if trow and trow['steps'] else 0

        # ── Steps tile ──────────────────────────────────────────────────────
        # Steps still needed from this morning's weight, minus what's been
        # walked today: every step taken today comes straight off the number.
        morning_weight = None
        weight_now = None
        steps_required = None
        percent = None
        if setup_complete and target_weight is not None:
            morning_weight = _weight_this_morning(conn, today)
            if morning_weight is not None:
                from_morning = _steps_to_target(morning_weight, target_weight)
                if from_morning is not None:
                    steps_required = max(0, from_morning - today_steps)
                weight_now = morning_weight - today_steps * calories_per_step(morning_weight) / 7700
                if starting_weight is not None and starting_weight > target_weight:
                    lost = starting_weight - weight_now
                    percent = max(0.0, min(100.0, lost / (starting_weight - target_weight) * 100.0))
                elif steps_required == 0:
                    percent = 100.0

        srow = conn.execute(
            'SELECT COALESCE(SUM(steps), 0) AS s FROM daily_steps WHERE date BETWEEN ? AND ?',
            (start_s, end_s)).fetchone()
        range_steps = int(srow['s'] or 0)

        # ── Calories / nutrients over the selected range ────────────────────
        totals = _range_totals(conn, start_s, end_s)
        range_calories = int(round(totals['calories']))
        calorie_target = _calorie_target()
        try:
            import app as _hector_app
            targets = _hector_app.get_targets()
        except Exception:
            targets = {}
        # Targets are per day; over a range compare against target × days.
        range_targets = {k: v * days for k, v in targets.items()}
        nutri_totals = {k: round(v, 3) for k, v in totals.items()}
        calorie_target_int = int(round(calorie_target * days)) if calorie_target is not None else None
        calorie_diff = (range_calories - calorie_target_int) if calorie_target_int is not None else None

        # ── Hydration over the selected range ───────────────────────────────
        hrow = conn.execute(
            'SELECT COALESCE(SUM(ml), 0) AS ml FROM daily_hydration WHERE date BETWEEN ? AND ?',
            (start_s, end_s)).fetchone()
        hydration_ml = int(hrow['ml'] or 0)

        return {
            'setup_complete': setup_complete,
            'details_complete': details_complete,
            'missing_details': missing_details,
            'has_target': target_weight is not None,
            'starting_weight': starting_weight,
            'starting_date': starting_date,
            'target_weight': target_weight,
            # selected range
            'range_key': range_key,
            'range_from': start_s,
            'range_to': end_s,
            'range_days': days,
            'ranges': RANGES,
            'range_label': range_label(r_start, r_end),
            # steps tile
            'morning_weight': round(morning_weight, 1) if morning_weight is not None else None,
            'steps_required': steps_required,
            'thousands_left': ((steps_required + 999) // 1000) if steps_required is not None else None,
            'percent': percent,
            'percent_str': _pct_str(percent),
            'kg_to_go': round(max(0.0, weight_now - target_weight), 2)
                        if (weight_now is not None and target_weight is not None) else None,
            'today_steps': today_steps,
            'range_steps': range_steps,
            # calories tile
            'today_calories': range_calories,
            'calorie_target': calorie_target_int,
            'calorie_diff': calorie_diff,
            # hydration tile
            'hydration_ml': hydration_ml,
            'hydration_target': HYDRATION_TARGET_ML * days,
            # nutrition / vitamins / minerals tiles
            'nutri_totals': nutri_totals,
            'targets': range_targets,
            'today_date': today,
        }
    finally:
        conn.close()
