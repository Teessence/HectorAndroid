"""Food suggestions for one nutrient, shown when a Home nutrient row is expanded.

For every ingredient and recipe that contains the nutrient we work out how much
of it you'd need to eat to cover what's still missing for the selected period
(grams/ml for ingredients, portions for recipes), what that costs in calories,
and its caloric efficiency (nutrient per 100 kcal). The most efficient foods —
most nutrient for the fewest calories — come first.
"""
from database import get_db, NUTRIENT_FIELDS
from home_calc import resolve_range, _range_totals

# Daily *limits* rather than goals — suggesting foods rich in them makes no sense.
LIMIT_NUTRIENTS = {'sugar', 'salt', 'saturates'}
TOP_N = 10


def nutrient_suggestions(col, daily_targets, range_key='today', range_from=None, range_to=None):
    if col not in NUTRIENT_FIELDS:
        raise ValueError(col)
    _, start, end = resolve_range(range_key, range_from, range_to)
    days = (end - start).days + 1
    start_s, end_s = start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')

    conn = get_db()
    try:
        eaten = _range_totals(conn, start_s, end_s).get(col, 0.0) or 0.0
        target = (daily_targets.get(col) or 0.0) * days
        remaining = max(0.0, target - eaten) if target else None
        out = {
            'col': col,
            'is_limit': col in LIMIT_NUTRIENTS,
            'eaten': round(eaten, 3),
            'target': round(target, 3),
            'remaining': round(remaining, 3) if remaining is not None else None,
            'ingredients': [],
            'recipes': [],
        }
        if out['is_limit']:
            return out

        ing_rows = conn.execute(f'''
            SELECT id, name, unit, serving_size, image_filename,
                   {col} AS value, calories
            FROM ingredients
            WHERE {col} > 0 AND serving_size > 0
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

    # How much to cover: what's left, or the whole target once it's reached.
    need = remaining if remaining else (target or None)
    out['need_basis'] = 'remaining' if remaining else ('target' if target else None)

    def build(item_id, name, image, value, calories, unit_amount, unit):
        """value/calories are per one `unit_amount` of `unit`."""
        x = {
            'id': item_id, 'name': name, 'image': image,
            'value': round(value, 3),
            'calories': round(calories),
            'portion': '1 portion' if unit == 'portions' else f'{unit_amount:g} {unit}',
            'per_100kcal': round(value / calories * 100, 3) if calories > 0 else None,
            'need_amount': None, 'need_unit': unit, 'need_kcal': None,
        }
        if need:
            factor = need / value
            x['need_amount'] = round(unit_amount * factor, 1 if unit == 'portions' else 0)
            x['need_kcal'] = round(calories * factor)
        return x

    ings = [build(r['id'], r['name'], r['image_filename'], r['value'] or 0.0,
                  r['calories'] or 0.0, r['serving_size'], r['unit'])
            for r in ing_rows]
    meals = []
    for r in meal_rows:
        y = r['yields'] or 1.0
        v = (r['value'] or 0.0) / y
        if v > 0:
            meals.append(build(r['id'], r['name'], r['image_filename'], v,
                               (r['calories'] or 0.0) / y, 1, 'portions'))

    def rank(items):
        if col == 'calories':
            return sorted(items, key=lambda x: x['value'], reverse=True)[:TOP_N]
        # Most nutrient per calorie first; zero-calorie sources (water, salt
        # substitutes, supplements) are the most efficient of all.
        return sorted(items, key=lambda x: (x['per_100kcal'] is None,
                                            x['per_100kcal'] or 0), reverse=True)[:TOP_N]

    out['ingredients'] = rank(ings)
    out['recipes'] = rank(meals)
    return out
