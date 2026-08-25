import sqlite3
import os
import unicodedata
from datetime import datetime, date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# On Android the app files are read-only, so the live DB lives in writable
# storage. HECTOR_DB_PATH is set by the mobile launcher; desktop falls back to
# the file next to this module.
DB_PATH = os.environ.get('HECTOR_DB_PATH') or os.path.join(BASE_DIR, 'hector.db')

# Polish letters have no NFKD decomposition (ł, ś, ż, etc. — some yes, ł no), so
# we translate them explicitly before folding the rest via NFKD.
_DIACRITIC_MAP = str.maketrans({
    'ł': 'l', 'Ł': 'L',
    'ą': 'a', 'Ą': 'A',
    'ć': 'c', 'Ć': 'C',
    'ę': 'e', 'Ę': 'E',
    'ń': 'n', 'Ń': 'N',
    'ó': 'o', 'Ó': 'O',
    'ś': 's', 'Ś': 'S',
    'ź': 'z', 'Ź': 'Z',
    'ż': 'z', 'Ż': 'Z',
})

def _sql_unaccent(s):
    if s is None:
        return ''
    s = s.translate(_DIACRITIC_MAP)
    nfkd = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in nfkd if not unicodedata.combining(c)).lower()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function('unaccent', 1, _sql_unaccent, deterministic=True)
    return conn


def init_db():
    conn = get_db()
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            unit TEXT NOT NULL DEFAULT 'g',
            serving_size REAL NOT NULL DEFAULT 100,
            package_size REAL NOT NULL DEFAULT 100,
            package_cost REAL NOT NULL DEFAULT 0,
            protein REAL NOT NULL DEFAULT 0,
            carbs REAL NOT NULL DEFAULT 0,
            fat REAL NOT NULL DEFAULT 0,
            sugar REAL NOT NULL DEFAULT 0,
            fiber REAL NOT NULL DEFAULT 0,
            calories REAL NOT NULL DEFAULT 0,
            shop TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            saturates REAL NOT NULL DEFAULT 0,
            salt REAL NOT NULL DEFAULT 0,
            image_filename TEXT DEFAULT NULL,
            vit_a    REAL NOT NULL DEFAULT 0,
            vit_c    REAL NOT NULL DEFAULT 0,
            vit_d    REAL NOT NULL DEFAULT 0,
            vit_e    REAL NOT NULL DEFAULT 0,
            vit_k    REAL NOT NULL DEFAULT 0,
            vit_b1   REAL NOT NULL DEFAULT 0,
            vit_b2   REAL NOT NULL DEFAULT 0,
            vit_b3   REAL NOT NULL DEFAULT 0,
            vit_b6   REAL NOT NULL DEFAULT 0,
            vit_b9   REAL NOT NULL DEFAULT 0,
            vit_b12  REAL NOT NULL DEFAULT 0,
            calcium     REAL NOT NULL DEFAULT 0,
            iron        REAL NOT NULL DEFAULT 0,
            magnesium   REAL NOT NULL DEFAULT 0,
            phosphorus  REAL NOT NULL DEFAULT 0,
            potassium   REAL NOT NULL DEFAULT 0,
            zinc        REAL NOT NULL DEFAULT 0,
            selenium    REAL NOT NULL DEFAULT 0,
            iodine      REAL NOT NULL DEFAULT 0,
            copper      REAL NOT NULL DEFAULT 0,
            manganese   REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            name TEXT NOT NULL,
            image_filename TEXT DEFAULT NULL,
            instructions TEXT NOT NULL DEFAULT '',
            yields REAL NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS meal_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_id INTEGER NOT NULL,
            ingredient_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            FOREIGN KEY (meal_id) REFERENCES meals(id) ON DELETE CASCADE,
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id)
        );

        CREATE TABLE IF NOT EXISTS daily_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            steps INTEGER NOT NULL DEFAULT 0,
            weight_kg REAL,
            fetched_at TEXT,
            is_locked INTEGER NOT NULL DEFAULT 0,
            source TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_hydration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            ml INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS diary_slot_notes (
            date TEXT NOT NULL,
            slot INTEGER NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, slot)
        );

        CREATE TABLE IF NOT EXISTS diary_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            slot INTEGER NOT NULL DEFAULT 1,
            ingredient_id INTEGER,
            meal_id INTEGER,
            servings REAL NOT NULL DEFAULT 1,
            source_meal_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ingredient_id) REFERENCES ingredients(id),
            FOREIGN KEY (meal_id) REFERENCES meals(id) ON DELETE CASCADE,
            FOREIGN KEY (source_meal_id) REFERENCES meals(id) ON DELETE SET NULL,
            CHECK ((ingredient_id IS NOT NULL) <> (meal_id IS NOT NULL))
        );

        CREATE INDEX IF NOT EXISTS idx_diary_date ON diary_entries(date);
    ''')
    conn.commit()
    # Migrations
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ingredients)").fetchall()]
    if 'calories' not in cols:
        conn.execute("ALTER TABLE ingredients ADD COLUMN calories REAL NOT NULL DEFAULT 0")
        conn.execute("UPDATE ingredients SET calories = protein * 4 + carbs * 4 + fat * 9")
        conn.commit()
    if 'shop' not in cols:
        conn.execute("ALTER TABLE ingredients ADD COLUMN shop TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if 'company' not in cols:
        conn.execute("ALTER TABLE ingredients ADD COLUMN company TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if 'saturates' not in cols:
        conn.execute("ALTER TABLE ingredients ADD COLUMN saturates REAL NOT NULL DEFAULT 0")
        conn.commit()
    if 'salt' not in cols:
        conn.execute("ALTER TABLE ingredients ADD COLUMN salt REAL NOT NULL DEFAULT 0")
        conn.commit()
    if 'image_filename' not in cols:
        conn.execute("ALTER TABLE ingredients ADD COLUMN image_filename TEXT DEFAULT NULL")
        conn.commit()
    for col, typedef in [
        ('vit_a','REAL NOT NULL DEFAULT 0'), ('vit_c','REAL NOT NULL DEFAULT 0'),
        ('vit_d','REAL NOT NULL DEFAULT 0'), ('vit_e','REAL NOT NULL DEFAULT 0'),
        ('vit_k','REAL NOT NULL DEFAULT 0'), ('vit_b1','REAL NOT NULL DEFAULT 0'),
        ('vit_b2','REAL NOT NULL DEFAULT 0'), ('vit_b3','REAL NOT NULL DEFAULT 0'),
        ('vit_b6','REAL NOT NULL DEFAULT 0'), ('vit_b9','REAL NOT NULL DEFAULT 0'),
        ('vit_b12','REAL NOT NULL DEFAULT 0'),
        ('calcium','REAL NOT NULL DEFAULT 0'), ('iron','REAL NOT NULL DEFAULT 0'),
        ('magnesium','REAL NOT NULL DEFAULT 0'), ('phosphorus','REAL NOT NULL DEFAULT 0'),
        ('potassium','REAL NOT NULL DEFAULT 0'), ('zinc','REAL NOT NULL DEFAULT 0'),
        ('selenium','REAL NOT NULL DEFAULT 0'), ('iodine','REAL NOT NULL DEFAULT 0'),
        ('copper','REAL NOT NULL DEFAULT 0'), ('manganese','REAL NOT NULL DEFAULT 0'),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE ingredients ADD COLUMN {col} {typedef}")
    conn.commit()

    # One-shot migration: meals.date NOT NULL → NULL (recipe library).
    # SQLite can't drop NOT NULL with ALTER, so on legacy DBs we rebuild the table.
    meals_info = conn.execute("PRAGMA table_info(meals)").fetchall()
    date_col = next((c for c in meals_info if c[1] == 'date'), None)
    if date_col is not None and date_col[3] == 1:  # notnull=1 → legacy
        conn.executescript('''
            CREATE TABLE meals_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                name TEXT NOT NULL
            );
            INSERT INTO meals_new(id, date, name) SELECT id, NULL, name FROM meals;
            DROP TABLE meals;
            ALTER TABLE meals_new RENAME TO meals;
        ''')
        conn.commit()

    # Migration: add meals.image_filename / instructions if missing
    meal_cols = [r[1] for r in conn.execute("PRAGMA table_info(meals)").fetchall()]
    if 'image_filename' not in meal_cols:
        conn.execute("ALTER TABLE meals ADD COLUMN image_filename TEXT DEFAULT NULL")
        conn.commit()
    if 'instructions' not in meal_cols:
        conn.execute("ALTER TABLE meals ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if 'yields' not in meal_cols:
        conn.execute("ALTER TABLE meals ADD COLUMN yields REAL NOT NULL DEFAULT 1")
        conn.commit()

    # Migration: add diary_entries.source_meal_id / created_at if missing
    diary_cols = [r[1] for r in conn.execute("PRAGMA table_info(diary_entries)").fetchall()]
    if diary_cols and 'source_meal_id' not in diary_cols:
        conn.execute("ALTER TABLE diary_entries ADD COLUMN source_meal_id INTEGER")
        conn.commit()
    if diary_cols and 'created_at' not in diary_cols:
        # SQLite won't let us use CURRENT_TIMESTAMP for the ADD COLUMN default on
        # existing rows, so add the column nullable and backfill from `date`.
        conn.execute("ALTER TABLE diary_entries ADD COLUMN created_at TEXT")
        conn.execute("UPDATE diary_entries SET created_at = date || ' 00:00:00' WHERE created_at IS NULL")
        conn.commit()

    # Migration: ingredients.inventory_amount (for the new Inventory feature)
    ing_cols_now = [r[1] for r in conn.execute("PRAGMA table_info(ingredients)").fetchall()]
    if 'inventory_amount' not in ing_cols_now:
        conn.execute("ALTER TABLE ingredients ADD COLUMN inventory_amount REAL NOT NULL DEFAULT 0")
        conn.commit()

    # Migration: daily_steps.fetched_at / is_locked / source (Garmin integration)
    steps_cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_steps)").fetchall()]
    if 'fetched_at' not in steps_cols:
        conn.execute("ALTER TABLE daily_steps ADD COLUMN fetched_at TEXT")
        conn.commit()
    if 'is_locked' not in steps_cols:
        conn.execute("ALTER TABLE daily_steps ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if 'source' not in steps_cols:
        conn.execute("ALTER TABLE daily_steps ADD COLUMN source TEXT")
        conn.commit()
    conn.close()


# ── Settings helpers ──────────────────────────────────────────────────────────

def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row['value'] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)',
                 (key, str(value) if value is not None else None))
    conn.commit()
    conn.close()


def is_setup_complete():
    return (get_setting('starting_weight') is not None and
            get_setting('starting_date') is not None)


# ── Weight helpers ─────────────────────────────────────────────────────────────

def get_current_weight(for_date=None):
    if for_date is None:
        for_date = date.today().strftime('%Y-%m-%d')
    conn = get_db()
    row = conn.execute(
        'SELECT weight_kg FROM daily_steps '
        'WHERE weight_kg IS NOT NULL AND date <= ? '
        'ORDER BY date DESC LIMIT 1',
        (for_date,)
    ).fetchone()
    conn.close()
    if row:
        return float(row['weight_kg'])
    sw = get_setting('starting_weight')
    return float(sw) if sw else None


# ── Calorie-per-step table (interpolated) ─────────────────────────────────────

_STEP_TABLE = [
    (40, 0.023), (45, 0.026), (50, 0.029), (55, 0.032),
    (60, 0.035), (65, 0.038), (70, 0.041), (75, 0.044),
    (80, 0.047), (85, 0.050), (90, 0.053), (95, 0.055),
    (100, 0.058), (105, 0.061), (110, 0.064), (115, 0.067),
    (120, 0.070), (125, 0.073), (130, 0.076),
]


def calories_per_step(weight_kg):
    """Discrete 5-kg buckets: each weight maps to exactly one rate (lower-bound value)."""
    if weight_kg is None:
        return 0.023
    if weight_kg < 40:
        return 0.023
    if weight_kg >= 130:
        return 0.076
    for i in range(len(_STEP_TABLE) - 1):
        w1, c1 = _STEP_TABLE[i]
        w2, _  = _STEP_TABLE[i + 1]
        if w1 <= weight_kg < w2:
            return c1
    return 0.076


# ── Nutrition calculators ─────────────────────────────────────────────────────

NUTRIENT_FIELDS = [
    'calories','protein','carbs','fat','sugar','fiber','salt','saturates',
    'vit_a','vit_c','vit_d','vit_e','vit_k',
    'vit_b1','vit_b2','vit_b3','vit_b6','vit_b9','vit_b12',
    'calcium','iron','magnesium','phosphorus','potassium',
    'zinc','selenium','iodine','copper','manganese',
]
ALL_TOTAL_KEYS = NUTRIENT_FIELDS + ['cost']


def _empty_totals():
    return {k: 0.0 for k in ALL_TOTAL_KEYS}


def _add(into, other, mul=1.0):
    for k in ALL_TOTAL_KEYS:
        into[k] += (other[k] or 0) * mul


def _ingredient_totals(ing, servings):
    """servings = multiplier of the ingredient's serving_size. Returns totals dict."""
    t = _empty_totals()
    for k in NUTRIENT_FIELDS:
        t[k] = (ing[k] or 0) * servings
    pkg = ing['package_size'] or 0
    if pkg > 0:
        amount = servings * (ing['serving_size'] or 0)
        t['cost'] = amount * (ing['package_cost'] or 0) / pkg
    return t


def calc_meal_totals(meal_id, conn=None):
    """Sum of recipe contents (per the meal_ingredients amounts). Returns full totals dict."""
    own = conn is None
    if own:
        conn = get_db()
    rows = conn.execute(f'''
        SELECT mi.amount, i.*
        FROM meal_ingredients mi
        JOIN ingredients i ON mi.ingredient_id = i.id
        WHERE mi.meal_id = ?
    ''', (meal_id,)).fetchall()
    if own:
        conn.close()
    t = _empty_totals()
    for r in rows:
        ss = r['serving_size'] or 0
        if ss <= 0:
            continue
        servings = r['amount'] / ss
        _add(t, _ingredient_totals(r, servings))
    return t


def calc_diary_entry_totals(entry, conn=None):
    """entry is a dict-like with ingredient_id / meal_id / servings. Returns totals dict."""
    own = conn is None
    if own:
        conn = get_db()
    if entry['ingredient_id']:
        ing = conn.execute('SELECT * FROM ingredients WHERE id=?',
                           (entry['ingredient_id'],)).fetchone()
        t = _ingredient_totals(ing, entry['servings']) if ing else _empty_totals()
    else:
        recipe = calc_meal_totals(entry['meal_id'], conn=conn)
        t = {k: v * entry['servings'] for k, v in recipe.items()}
    if own:
        conn.close()
    return t


def calc_day_totals(date_str):
    """Sum of all diary entries for the given date."""
    conn = get_db()
    entries = conn.execute(
        'SELECT id, slot, ingredient_id, meal_id, servings FROM diary_entries WHERE date=?',
        (date_str,)).fetchall()
    day = _empty_totals()
    for e in entries:
        _add(day, calc_diary_entry_totals(e, conn=conn))
    conn.close()
    return day


# ── BMR ───────────────────────────────────────────────────────────────────────

def calc_age(dob_str):
    try:
        dob = datetime.strptime(dob_str, '%Y-%m-%d').date()
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    except Exception:
        return None


def calc_bmr(weight_kg, height_cm, age_years, gender=None):
    """Mifflin-St Jeor BMR. `gender` is 'female' for the female formula
    (subtracts 161); any other value (including None) uses the male formula
    (adds 5). This preserves backward-compatible behaviour roughly between the
    two: the original code did neither — added 0 — so existing data shifts by
    ±5 / −161 depending on the new gender setting."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age_years
    if gender == 'female':
        return base - 161
    return base + 5


# ── Date helpers ──────────────────────────────────────────────────────────────

def adjacent_dates(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    return (d - timedelta(days=1)).strftime('%Y-%m-%d'), (d + timedelta(days=1)).strftime('%Y-%m-%d')
