"""Food suggestions for one nutrient, shown when a Home nutrient row is expanded.

Ranks ingredients (per their serving) and recipes (per portion = whole recipe /
yields) by how much of the nutrient they provide, either per serving or per
100 kcal (nutrient density — the better pick while eating in a deficit).
"""
from datetime import date

from database import get_db, calc_day_totals, NUTRIENT_FIELDS

# Daily *limits* rather than goals — suggesting foods rich in them makes no sense.
LIMIT_NUTRIENTS = {'sugar', 'salt', 'saturates'}
TOP_N = 10


def _rank(items, mode):
    if mode == 'density':
        items = [x for x in items if x['calories'] > 0]
        key = lambda x: x['value'] / x['calories']
    else:
        key = lambda x: x['value']
    return sorted(items, key=key, reverse=True)[:TOP_N]


def nutrient_suggestions(col, targets, mode='serving'):
    if col not in NUTRIENT_FIELDS:
        raise ValueError(col)
    if col == 'calories':
        mode = 'serving'
    today = date.today().strftime('%Y-%m-%d')
    eaten = calc_day_totals(today).get(col, 0.0) or 0.0
    target = targets.get(col) or 0.0
    remaining = max(0.0, target - eaten) if target else None

    out = {
        'col': col,
        'mode': mode,
        'is_limit': col in LIMIT_NUTRIENTS,
        'eaten': round(eaten, 3),
        'target': target,
        'remaining': round(remaining, 3) if remaining is not None else None,
        'ingredients': [],
        'recipes': [],
    }
    if out['is_limit']:
        return out

    conn = get_db()
    try:
        ing_rows = conn.execute(f'''
            SELECT id, name, unit, serving_size, image_filename,
                   {col} AS value, calories
            FROM ingredients
            WHERE {col} > 0
        ''').fetchall()
        meal_rows = conn.execute(f'''
            SELECT m.id, m.name, m.yields, m.image_filename,
                   SUM(mi.amount / i.serving_size * i.{col}) AS value,
                   SUM(mi.amount / i.serving_size * i.calories) AS calories
            FROM meals m
            JOIN meal_ingredients mi ON mi.meal_id = m.id
            JOIN ingredients i ON i.id = mi.ingredient_id
            WHERE i.serving_size > 0
            GROUP BY m.id
        ''').fetchall()
    finally:
        conn.close()

    ings = [{
        'id': r['id'], 'name': r['name'], 'image': r['image_filename'],
        'portion': f"{r['serving_size']:g} {r['unit']}",
        'value': r['value'] or 0.0, 'calories': r['calories'] or 0.0,
    } for r in ing_rows]

    meals = []
    for r in meal_rows:
        y = r['yields'] or 1.0
        v = (r['value'] or 0.0) / y
        if v <= 0:
            continue
        meals.append({
            'id': r['id'], 'name': r['name'], 'image': r['image_filename'],
            'portion': '1 portion' if y == 1 else f'1 of {y:g} portions',
            'value': v, 'calories': (r['calories'] or 0.0) / y,
        })

    def finish(x):
        x['pct_target'] = round(x['value'] / target * 100, 1) if target else None
        x['pct_remaining'] = (round(min(100.0, x['value'] / remaining * 100), 1)
                              if remaining else None)
        x['per_100kcal'] = round(x['value'] / x['calories'] * 100, 3) if x['calories'] > 0 else None
        x['value'] = round(x['value'], 3)
        x['calories'] = round(x['calories'])
        return x

    out['ingredients'] = [finish(x) for x in _rank(ings, mode)]
    out['recipes'] = [finish(x) for x in _rank(meals, mode)]
    return out
