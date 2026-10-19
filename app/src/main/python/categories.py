"""Ingredient categories (Fruits, Vegetables, …).

An ingredient can belong to any number of categories. Membership can be edited
from either side: a category's page lists ingredients to tick, and the
ingredient form lists categories to tick — both write ingredient_categories.
"""


def list_categories(conn):
    """All categories, A→Z, each with how many ingredients it holds."""
    return conn.execute('''
        SELECT c.id, c.name, COUNT(ic.ingredient_id) AS count
        FROM categories c
        LEFT JOIN ingredient_categories ic ON ic.category_id = c.id
        GROUP BY c.id
        ORDER BY c.name COLLATE NOCASE
    ''').fetchall()


def get_or_create(conn, name):
    """Id of the category called `name` (case-insensitive), creating it if new.
    Returns None for a blank name."""
    name = (name or '').strip()
    if not name:
        return None
    row = conn.execute('SELECT id FROM categories WHERE name = ? COLLATE NOCASE', (name,)).fetchone()
    if row:
        return row['id']
    return conn.execute('INSERT INTO categories(name) VALUES (?)', (name,)).lastrowid


def ids_for_ingredient(conn, ingredient_id):
    return {r['category_id'] for r in conn.execute(
        'SELECT category_id FROM ingredient_categories WHERE ingredient_id = ?', (ingredient_id,))}


def set_for_ingredient(conn, ingredient_id, category_ids):
    """Make the ingredient's categories exactly `category_ids`."""
    conn.execute('DELETE FROM ingredient_categories WHERE ingredient_id = ?', (ingredient_id,))
    conn.executemany(
        'INSERT OR IGNORE INTO ingredient_categories(ingredient_id, category_id) VALUES (?, ?)',
        [(ingredient_id, c) for c in category_ids])


def set_for_category(conn, category_id, ingredient_ids):
    """Make the category's ingredients exactly `ingredient_ids`."""
    conn.execute('DELETE FROM ingredient_categories WHERE category_id = ?', (category_id,))
    conn.executemany(
        'INSERT OR IGNORE INTO ingredient_categories(ingredient_id, category_id) VALUES (?, ?)',
        [(i, category_id) for i in ingredient_ids])


def names_for_ingredients(conn, ingredient_ids):
    """{ingredient_id: [category names, A→Z]} for the given ingredients."""
    ids = list(ingredient_ids)
    out = {i: [] for i in ids}
    if not ids:
        return out
    marks = ','.join('?' * len(ids))
    for r in conn.execute(f'''
        SELECT ic.ingredient_id, c.name
        FROM ingredient_categories ic JOIN categories c ON c.id = ic.category_id
        WHERE ic.ingredient_id IN ({marks})
        ORDER BY c.name COLLATE NOCASE
    ''', ids):
        out[r['ingredient_id']].append(r['name'])
    return out


def parse_form_ids(form, key='category_ids'):
    """Ticked ids from a form's checkboxes, ignoring junk."""
    out = set()
    for v in form.getlist(key):
        try:
            out.add(int(v))
        except (TypeError, ValueError):
            pass
    return out
