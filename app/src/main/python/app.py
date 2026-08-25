import os
import uuid
from datetime import datetime, date, timedelta
from flask import (Flask, render_template, request, redirect, url_for, flash, jsonify)
from werkzeug.utils import secure_filename
from database import (
    get_db, get_setting, set_setting, is_setup_complete,
    get_current_weight, calories_per_step, calc_meal_totals,
    calc_day_totals, calc_age, calc_bmr, adjacent_dates, init_db,
    _empty_totals, _add, NUTRIENT_FIELDS, _STEP_TABLE, _sql_unaccent,
)
import garmin

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# On Android these live in writable storage (set by the mobile launcher);
# desktop falls back to the folders next to this module.
IMAGES_DIR = os.environ.get('HECTOR_IMAGES_DIR') or os.path.join(BASE_DIR, 'static', 'ingredient_images')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
EXEMPT_ENDPOINTS = {'setup', 'static'}

TARGET_KEYS = [
    # macros
    'target_calories','target_protein','target_fat','target_saturates',
    'target_carbs','target_fiber','target_sugar','target_salt',
    # vitamins
    'target_vit_a','target_vit_c','target_vit_d','target_vit_e','target_vit_k',
    'target_vit_b1','target_vit_b2','target_vit_b3','target_vit_b6',
    'target_vit_b9','target_vit_b12',
    # minerals
    'target_calcium','target_iron','target_magnesium','target_phosphorus',
    'target_potassium','target_zinc','target_selenium','target_iodine',
    'target_copper','target_manganese',
]

# EU NRV / general adult daily reference values
TARGET_DEFAULTS = {
    'target_calories': '2200', 'target_protein': '75',
    'target_fat': '70',        'target_saturates': '20',
    'target_carbs': '260',     'target_fiber': '30',
    'target_sugar': '90',      'target_salt': '6',
    'target_vit_a': '800',     'target_vit_c': '80',
    'target_vit_d': '15',      'target_vit_e': '12',
    'target_vit_k': '75',      'target_vit_b1': '1.1',
    'target_vit_b2': '1.4',    'target_vit_b3': '16',
    'target_vit_b6': '1.4',    'target_vit_b9': '200',
    'target_vit_b12': '2.5',
    'target_calcium': '1000',  'target_iron': '14',
    'target_magnesium': '375', 'target_phosphorus': '700',
    'target_potassium': '3500','target_zinc': '10',
    'target_selenium': '55',   'target_iodine': '150',
    'target_copper': '1',      'target_manganese': '2',
}


CALORIE_DEFICIT_DEFAULT = 250  # kcal/day below BMR — the seed value


def get_calorie_deficit():
    raw = get_setting('calorie_deficit')
    try:
        return int(float(raw)) if raw not in (None, '') else CALORIE_DEFICIT_DEFAULT
    except (TypeError, ValueError):
        return CALORIE_DEFICIT_DEFAULT


def compute_calorie_target():
    """Auto target = BMR(current weight, height, age, gender) - configured deficit.
    Returns None if any input is missing."""
    weight = get_current_weight()
    height = get_setting('height_cm')
    dob = get_setting('dob')
    age = calc_age(dob) if dob else None
    gender = (get_setting('gender') or '').strip().lower() or None
    if not (weight and height and age):
        return None
    try:
        bmr = calc_bmr(weight, float(height), age, gender)
    except (TypeError, ValueError):
        return None
    return max(0.0, bmr - get_calorie_deficit())


def format_relative_time(value, now=None):
    """Render a datetime/date string as "5 minutes ago" / "yesterday" / "3 days ago" etc.
    Accepts: 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'. Returns None for empty input."""
    if not value:
        return None
    s = str(value).strip()
    fmt = '%Y-%m-%d %H:%M:%S' if len(s) > 10 else '%Y-%m-%d'
    try:
        dt = datetime.strptime(s[:19], fmt)
    except ValueError:
        return s
    now = now or datetime.now()
    delta_sec = (now - dt).total_seconds()
    if delta_sec < 0:
        # In the future — show the absolute date.
        return dt.strftime('%Y-%m-%d')
    if delta_sec < 45:
        return 'just now'
    if delta_sec < 3600:
        m = max(1, int(delta_sec / 60))
        return f'{m} minute{"s" if m != 1 else ""} ago'
    if delta_sec < 86400:
        h = int(delta_sec / 3600)
        return f'{h} hour{"s" if h != 1 else ""} ago'
    days = int(delta_sec / 86400)
    if days == 1:
        return 'yesterday'
    if days < 30:
        return f'{days} days ago'
    if days < 365:
        months = days // 30
        return f'{months} month{"s" if months != 1 else ""} ago'
    years = days // 365
    return f'{years} year{"s" if years != 1 else ""} ago'


def get_targets():
    """Return {column_name: float} for every nutrient with a target set (or default).
    Calories is always auto-computed from BMR (not user-settable)."""
    out = {}
    for key in TARGET_KEYS:
        raw = get_setting(key, TARGET_DEFAULTS.get(key))
        try:
            v = float(raw) if raw not in (None, '') else 0.0
        except (TypeError, ValueError):
            v = 0.0
        # key 'target_vit_a' -> column 'vit_a'
        out[key.removeprefix('target_')] = v
    # Override calories with the auto value (BMR - 250). Falls back to the stored
    # default if BMR can't be computed (e.g. missing DOB/height).
    auto_cal = compute_calorie_target()
    if auto_cal is not None:
        out['calories'] = auto_cal
    return out

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_ingredient_image(file_storage):
    """Save uploaded image, return the filename or None."""
    if not file_storage or file_storage.filename == '':
        return None
    if not allowed_file(file_storage.filename):
        return None
    ext = file_storage.filename.rsplit('.', 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(IMAGES_DIR, exist_ok=True)
    file_storage.save(os.path.join(IMAGES_DIR, filename))
    return filename

def delete_ingredient_image(filename):
    if filename:
        path = os.path.join(IMAGES_DIR, filename)
        if os.path.exists(path):
            os.remove(path)


def create_app():
    init_db()
    app = Flask(
        __name__,
        template_folder=os.environ.get('HECTOR_TEMPLATE_DIR') or os.path.join(BASE_DIR, 'templates'),
        static_folder=os.environ.get('HECTOR_STATIC_DIR') or os.path.join(BASE_DIR, 'static'),
    )
    app.secret_key = 'hector-health-2024-secret'
    app.jinja_env.filters['relative_time'] = format_relative_time

    @app.context_processor
    def globals():
        return {
            'today': date.today().strftime('%Y-%m-%d'),
            'setup_complete': is_setup_complete(),
        }

    @app.before_request
    def check_setup():
        if request.endpoint not in EXEMPT_ENDPOINTS and not is_setup_complete():
            return redirect(url_for('setup'))

    @app.context_processor
    def inject_theme():
        try:
            return {'app_theme': get_setting('theme') or 'dark'}
        except Exception:
            return {'app_theme': 'dark'}

    # ── Setup ─────────────────────────────────────────────────────────────────

    @app.route('/')
    def index():
        import home_calc
        status = home_calc.compute_home_status()
        return render_template('home.html', active_page='home', **status)

    @app.route('/api/home_status')
    def api_home_status():
        import home_calc
        return jsonify(home_calc.compute_home_status())

    @app.route('/dashboard')
    @app.route('/dashboard/<date_str>')
    def dashboard(date_str=None):
        return redirect(url_for('index'))

    @app.route('/setup', methods=['GET', 'POST'])
    def setup():
        if is_setup_complete():
            return redirect(url_for('index'))
        if request.method == 'POST':
            sd = request.form.get('starting_date', '').strip()
            sw = request.form.get('starting_weight', '').strip()
            if not sd or not sw:
                flash('Both fields are required.', 'warning')
                return redirect(url_for('setup'))
            try:
                sw_f = float(sw)
                datetime.strptime(sd, '%Y-%m-%d')
            except ValueError:
                flash('Invalid date or weight.', 'danger')
                return redirect(url_for('setup'))
            set_setting('starting_date', sd)
            set_setting('starting_weight', str(sw_f))
            conn = get_db()
            conn.execute(
                'INSERT OR IGNORE INTO daily_steps(date,steps,weight_kg) VALUES(?,0,?)',
                (sd, sw_f))
            conn.commit()
            conn.close()
            flash('Welcome to Hector! Set your profile in Settings.', 'success')
            return redirect(url_for('index'))
        return render_template('setup.html')

    # ── Settings ──────────────────────────────────────────────────────────────

    @app.route('/settings', methods=['GET', 'POST'])
    def settings():
        profile_keys = ('dob', 'height_cm', 'gender', 'target_weight',
                        'starting_date', 'starting_weight', 'calorie_deficit')
        garmin_text_keys = ('garmin_email', 'garmin_refresh_min')
        if request.method == 'POST':
            for key in profile_keys:
                v = request.form.get(key, '').strip()
                if v:
                    set_setting(key, v)
            for key in TARGET_KEYS:
                if key == 'target_calories':
                    continue  # auto-computed, not user-editable
                v = request.form.get(key, '').strip()
                if v:
                    set_setting(key, v)
            # Garmin: email + refresh interval saved if present;
            # password only overwritten when a non-empty value is submitted
            # (so users can re-save settings without retyping it).
            for key in garmin_text_keys:
                if key in request.form:
                    set_setting(key, request.form.get(key, '').strip())
            pwd = request.form.get('garmin_password', '')
            if pwd.strip():
                set_setting('garmin_password', pwd)
            set_setting('garmin_enabled', '1' if request.form.get('garmin_enabled') else '')
            flash('Settings saved.', 'success')
            return redirect(url_for('settings'))
        s = {k: get_setting(k, '') for k in profile_keys}
        targets = {k: get_setting(k, TARGET_DEFAULTS.get(k, '')) for k in TARGET_KEYS}
        return render_template('settings.html', s=s, targets=targets,
                               auto_calories=compute_calorie_target(),
                               calorie_deficit=get_calorie_deficit(),
                               garmin_status=garmin.get_status(),
                               active_page='settings')

    @app.route('/settings/display', methods=['POST'])
    def settings_display():
        theme = request.form.get('theme', 'dark')
        set_setting('theme', 'light' if theme == 'light' else 'dark')
        flash('Display settings saved.', 'success')
        return redirect(url_for('settings'))

    @app.route('/settings/calibrate', methods=['POST'])
    def settings_calibrate():
        """Wipe all tracked data (diary entries + daily steps) and re-seed a
        single row in daily_steps at the configured starting_date with the
        configured starting_weight. Ingredients and recipes are NOT touched."""
        starting_date  = get_setting('starting_date')
        starting_w_raw = get_setting('starting_weight')
        try:
            sw = float(starting_w_raw) if starting_w_raw else None
        except ValueError:
            sw = None
        conn = get_db()
        conn.execute('DELETE FROM diary_entries')
        conn.execute('DELETE FROM daily_steps')
        if starting_date and sw is not None:
            conn.execute(
                'INSERT INTO daily_steps(date, steps, weight_kg) VALUES (?, 0, ?)',
                (starting_date, sw))
        conn.commit()
        conn.close()
        flash('Calibrated — diary and steps wiped. Starting fresh from your settings.', 'success')
        return redirect(url_for('settings'))

    # ── Ingredients ───────────────────────────────────────────────────────────

    INGREDIENTS_PER_PAGE = 50

    @app.route('/ingredients')
    def ingredients():
        q = request.args.get('q', '').strip()
        sort_col = request.args.get('sort', 'name')
        sort_dir = request.args.get('dir', 'asc')
        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        valid_cols = {
            'name','unit','shop','company','serving_size','package_size','package_cost',
            'calories','protein','fat','saturates','carbs','fiber','sugar','salt',
            'vit_a','vit_c','vit_d','vit_e','vit_k',
            'vit_b1','vit_b2','vit_b3','vit_b6','vit_b9','vit_b12',
            'calcium','iron','magnesium','phosphorus','potassium',
            'zinc','selenium','iodine','copper','manganese',
            'protein_per_calorie', 'last_eaten',
        }
        if sort_col not in valid_cols:
            sort_col = 'name'
        if sort_col == 'protein_per_calorie':
            order_sql = f'CASE WHEN calories > 0 THEN protein / calories ELSE 0 END {"DESC" if sort_dir == "desc" else "ASC"}'
        else:
            order_sql = f'{sort_col} {"DESC" if sort_dir == "desc" else "ASC"}'
        # Subquery joins last diary date per ingredient (covers direct adds + expanded recipe adds)
        base_query = '''
            SELECT i.*, (SELECT MAX(COALESCE(de.created_at, de.date || ' 00:00:00'))
                         FROM diary_entries de
                         WHERE de.ingredient_id = i.id) as last_eaten
            FROM ingredients i
        '''
        offset = (page - 1) * INGREDIENTS_PER_PAGE
        # Fetch one extra to detect whether more pages exist without a COUNT.
        limit_sql = f' LIMIT {INGREDIENTS_PER_PAGE + 1} OFFSET {offset}'
        conn = get_db()
        if q:
            like = f'%{_sql_unaccent(q)}%'
            rows = conn.execute(
                base_query +
                ' WHERE unaccent(i.name) LIKE ? OR unaccent(i.shop) LIKE ? OR unaccent(i.company) LIKE ?'
                ' ORDER BY ' + order_sql + limit_sql,
                (like, like, like)).fetchall()
        else:
            rows = conn.execute(base_query + ' ORDER BY ' + order_sql + limit_sql).fetchall()
        conn.close()

        has_more = len(rows) > INGREDIENTS_PER_PAGE
        rows = rows[:INGREDIENTS_PER_PAGE]

        # AJAX (infinite scroll): return JSON with just the rendered <tr> rows.
        if request.headers.get('X-Requested-With') == 'fetch':
            html = render_template('_ingredients_rows.html',
                                   ingredients=rows, targets=get_targets())
            return jsonify({'html': html, 'has_more': has_more, 'page': page})

        return render_template('ingredients_list.html', ingredients=rows, q=q,
                               sort_col=sort_col, sort_dir=sort_dir,
                               page=page, has_more=has_more,
                               targets=get_targets(), active_page='ingredients')

    VIT_MIN_FIELDS = [
        'vit_a','vit_c','vit_d','vit_e','vit_k',
        'vit_b1','vit_b2','vit_b3','vit_b6','vit_b9','vit_b12',
        'calcium','iron','magnesium','phosphorus','potassium',
        'zinc','selenium','iodine','copper','manganese',
    ]

    def parse_vit_min(f):
        return [float(f.get(k, 0) or 0) for k in VIT_MIN_FIELDS]

    @app.route('/ingredients/add', methods=['GET', 'POST'])
    def ingredient_add():
        if request.method == 'POST':
            f = request.form
            try:
                image_filename = save_ingredient_image(request.files.get('image'))
                vm = parse_vit_min(f)
                cols = ('name,unit,serving_size,package_size,package_cost,'
                        'calories,protein,fat,saturates,carbs,fiber,sugar,salt,shop,company,image_filename,'
                        + ','.join(VIT_MIN_FIELDS))
                placeholders = ','.join(['?'] * (16 + len(VIT_MIN_FIELDS)))
                conn = get_db()
                conn.execute(f'INSERT INTO ingredients({cols}) VALUES({placeholders})',
                    (f['name'].strip(), f['unit'], float(f['serving_size']),
                     float(f['package_size']), float(f['package_cost']),
                     float(f['calories']), float(f['protein']), float(f['fat']),
                     float(f.get('saturates', 0) or 0),
                     float(f['carbs']), float(f['fiber']), float(f['sugar']),
                     float(f.get('salt', 0) or 0),
                     f.get('shop','').strip(), f.get('company','').strip(),
                     image_filename, *vm))
                conn.commit()
                conn.close()
                flash(f'Ingredient "{f["name"]}" added.', 'success')
                return redirect(url_for('ingredients'))
            except Exception as e:
                flash(f'Error: {e}', 'danger')
        return render_template('ingredient_form.html', ing=None, active_page='ingredients')

    @app.route('/ingredients/<int:ing_id>/edit', methods=['GET', 'POST'])
    def ingredient_edit(ing_id):
        conn = get_db()
        ing = conn.execute('SELECT * FROM ingredients WHERE id=?', (ing_id,)).fetchone()
        conn.close()
        if not ing:
            flash('Ingredient not found.', 'danger')
            return redirect(url_for('ingredients'))
        if request.method == 'POST':
            f = request.form
            try:
                new_image = save_ingredient_image(request.files.get('image'))
                if new_image:
                    delete_ingredient_image(ing['image_filename'])
                    image_filename = new_image
                else:
                    image_filename = ing['image_filename']
                if request.form.get('remove_image') == '1':
                    delete_ingredient_image(image_filename)
                    image_filename = None
                vm = parse_vit_min(f)
                set_clause = ('name=?,unit=?,serving_size=?,package_size=?,package_cost=?,'
                              'calories=?,protein=?,fat=?,saturates=?,carbs=?,fiber=?,sugar=?,salt=?,'
                              'shop=?,company=?,image_filename=?,'
                              + ','.join(f'{k}=?' for k in VIT_MIN_FIELDS))
                conn = get_db()
                conn.execute(f'UPDATE ingredients SET {set_clause} WHERE id=?',
                    (f['name'].strip(), f['unit'], float(f['serving_size']),
                     float(f['package_size']), float(f['package_cost']),
                     float(f['calories']), float(f['protein']), float(f['fat']),
                     float(f.get('saturates', 0) or 0),
                     float(f['carbs']), float(f['fiber']), float(f['sugar']),
                     float(f.get('salt', 0) or 0),
                     f.get('shop','').strip(), f.get('company','').strip(),
                     image_filename, *vm, ing_id))
                conn.commit()
                conn.close()
                flash('Ingredient updated.', 'success')
                return redirect(url_for('ingredients'))
            except Exception as e:
                flash(f'Error: {e}', 'danger')
        return render_template('ingredient_form.html', ing=ing, active_page='ingredients')

    @app.route('/ingredients/<int:ing_id>/delete', methods=['POST'])
    def ingredient_delete(ing_id):
        try:
            conn = get_db()
            ing = conn.execute('SELECT image_filename FROM ingredients WHERE id=?', (ing_id,)).fetchone()
            if ing:
                delete_ingredient_image(ing['image_filename'])
            conn.execute('DELETE FROM ingredients WHERE id=?', (ing_id,))
            conn.commit()
            conn.close()
            flash('Ingredient deleted.', 'success')
        except Exception as e:
            flash(f'Cannot delete: {e}', 'danger')
        return redirect(url_for('ingredients'))

    # ── Recipe library ────────────────────────────────────────────────────────

    MEALS_PER_PAGE = 50

    @app.route('/meals')
    def meals_list():
        q = request.args.get('q', '').strip()
        sort_col = request.args.get('sort', 'name')
        sort_dir = request.args.get('dir', 'asc')
        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1

        # All nutrient totals we sum from meal_ingredients × ingredients
        # The values stored on ingredients are per ingredient.serving_size, so each
        # row contributes (mi.amount / i.serving_size) × i.<col>.
        sum_cols = NUTRIENT_FIELDS  # macros + vitamins + minerals
        sum_expr = ',\n    '.join(
            f"COALESCE(SUM(mi.amount / i.serving_size * i.{c}), 0) as total_{c}"
            for c in sum_cols
        )

        valid_cols = ({'name', 'total_cost', 'protein_per_calorie', 'last_eaten'}
                      | {f'total_{c}' for c in sum_cols})
        if sort_col not in valid_cols:
            sort_col = 'name'
        if sort_col == 'protein_per_calorie':
            order_col = ('CASE WHEN total_calories > 0 '
                         'THEN total_protein / total_calories ELSE 0 END')
        elif sort_col == 'name':
            order_col = 'm.name'
        else:
            order_col = sort_col
        order = f'{order_col} {"DESC" if sort_dir == "desc" else "ASC"}'
        # Match the recipe name OR any ingredient inside the recipe (so the user
        # can search "oats" and find every recipe containing oats).
        if q:
            like = f'%{_sql_unaccent(q)}%'
            where = ('WHERE unaccent(m.name) LIKE ? OR EXISTS ('
                     ' SELECT 1 FROM meal_ingredients mi2'
                     ' JOIN ingredients i2 ON i2.id = mi2.ingredient_id'
                     ' WHERE mi2.meal_id = m.id'
                     '   AND (unaccent(i2.name) LIKE ? OR unaccent(i2.shop) LIKE ? OR unaccent(i2.company) LIKE ?)'
                     ')')
            args = (like, like, like, like)
        else:
            where = ''
            args = ()
        offset = (page - 1) * MEALS_PER_PAGE
        limit_sql = f' LIMIT {MEALS_PER_PAGE + 1} OFFSET {offset}'
        conn = get_db()
        meals = conn.execute(f'''
            SELECT m.id, m.name, m.image_filename, m.yields,
                   {sum_expr},
                   COALESCE(SUM(mi.amount * i.package_cost / i.package_size), 0) as total_cost,
                   (SELECT MAX(COALESCE(de.created_at, de.date || ' 00:00:00')) FROM diary_entries de
                    WHERE de.source_meal_id = m.id OR de.meal_id = m.id) as last_eaten
            FROM meals m
            LEFT JOIN meal_ingredients mi ON mi.meal_id = m.id
            LEFT JOIN ingredients i ON i.id = mi.ingredient_id
            {where}
            GROUP BY m.id
            ORDER BY {order}
            {limit_sql}
        ''', args).fetchall()
        conn.close()

        has_more = len(meals) > MEALS_PER_PAGE
        meals = meals[:MEALS_PER_PAGE]

        if request.headers.get('X-Requested-With') == 'fetch':
            html = render_template('_meals_rows.html',
                                   meals=meals, targets=get_targets())
            return jsonify({'html': html, 'has_more': has_more, 'page': page})

        return render_template('meals_list.html', meals=meals, q=q,
                               sort_col=sort_col, sort_dir=sort_dir,
                               page=page, has_more=has_more,
                               targets=get_targets(), active_page='meals')

    @app.route('/meals/add', methods=['GET', 'POST'])
    def meal_create():
        if request.method == 'POST':
            name = request.form.get('name', '').strip()
            if not name:
                flash('Recipe name is required.', 'warning')
                return redirect(url_for('meal_create'))
            instructions = request.form.get('instructions', '').strip()
            try:
                yields = float(request.form.get('yields', '1') or 1)
                if yields <= 0:
                    yields = 1.0
            except ValueError:
                yields = 1.0
            image_filename = save_ingredient_image(request.files.get('image'))
            conn = get_db()
            cur = conn.execute(
                'INSERT INTO meals(name, image_filename, instructions, yields) VALUES(?,?,?,?)',
                (name, image_filename, instructions, yields))
            new_id = cur.lastrowid
            conn.commit()
            conn.close()
            return redirect(url_for('meal_edit', meal_id=new_id))
        return render_template('meal_form.html', meal=None, active_page='meals')

    @app.route('/meals/<int:meal_id>/edit', methods=['GET', 'POST'])
    def meal_edit(meal_id):
        conn = get_db()
        meal = conn.execute('SELECT * FROM meals WHERE id=?', (meal_id,)).fetchone()
        if not meal:
            conn.close()
            flash('Recipe not found.', 'danger')
            return redirect(url_for('meals_list'))
        if request.method == 'POST':
            new_name = (request.form.get('name', '') or '').strip()
            if not new_name:
                flash('Recipe name is required.', 'warning')
                conn.close()
                return redirect(url_for('meal_edit', meal_id=meal_id))
            instructions = request.form.get('instructions', '').strip()
            try:
                yields = float(request.form.get('yields', '1') or 1)
                if yields <= 0:
                    yields = 1.0
            except ValueError:
                yields = 1.0
            # Photo handling
            image_filename = meal['image_filename']
            new_image = save_ingredient_image(request.files.get('image'))
            if new_image:
                delete_ingredient_image(image_filename)
                image_filename = new_image
            if request.form.get('remove_image') == '1':
                delete_ingredient_image(image_filename)
                image_filename = None
            conn.execute(
                'UPDATE meals SET name=?, image_filename=?, instructions=?, yields=? WHERE id=?',
                (new_name, image_filename, instructions, yields, meal_id))
            conn.commit()
            conn.close()
            flash('Recipe saved.', 'success')
            return redirect(url_for('meal_edit', meal_id=meal_id))

        ings = conn.execute('''
            SELECT mi.id as mi_id, mi.amount, i.name, i.unit, i.serving_size, i.shop, i.company,
                   i.image_filename,
                   i.protein, i.carbs, i.fat, i.calories, i.fiber, i.sugar,
                   i.package_cost, i.package_size
            FROM meal_ingredients mi
            JOIN ingredients i ON i.id = mi.ingredient_id
            WHERE mi.meal_id=?
        ''', (meal_id,)).fetchall()
        all_ings = conn.execute('SELECT * FROM ingredients ORDER BY name').fetchall()
        conn.close()
        totals = calc_meal_totals(meal_id)
        ing_rows = []
        for r in ings:
            ss = r['serving_size'] or 0
            f = r['amount'] / ss if ss else 0
            ing_rows.append({
                'mi_id': r['mi_id'], 'name': r['name'],
                'shop': r['shop'] or '', 'company': r['company'] or '',
                'image_filename': r['image_filename'],
                'amount': r['amount'], 'unit': r['unit'],
                'serving_size': ss, 'servings': f,
                'protein': (r['protein'] or 0) * f,
                'carbs':   (r['carbs']   or 0) * f,
                'fat':     (r['fat']     or 0) * f,
                'calories':(r['calories']or 0) * f,
                'cost': r['amount'] * r['package_cost'] / r['package_size'] if r['package_size'] else 0,
            })
        return render_template('meal_edit.html', meal=meal, ingredients=ing_rows,
                               all_ings=all_ings, totals=totals,
                               targets=get_targets(), active_page='meals')

    @app.route('/meals/<int:meal_id>/add-ingredient', methods=['POST'])
    def meal_add_ingredient(meal_id):
        conn = get_db()
        meal = conn.execute('SELECT id FROM meals WHERE id=?', (meal_id,)).fetchone()
        if not meal:
            conn.close()
            return redirect(url_for('meals_list'))
        try:
            ing_id = int(request.form['ingredient_id'])
            servings = float(request.form['servings'])
            # Resolve serving_size of the ingredient to convert servings -> amount
            ing = conn.execute('SELECT serving_size FROM ingredients WHERE id=?', (ing_id,)).fetchone()
            ss = (ing['serving_size'] or 0) if ing else 0
            amount = servings * ss if ss else servings
            conn.execute(
                'INSERT INTO meal_ingredients(meal_id,ingredient_id,amount) VALUES(?,?,?)',
                (meal_id, ing_id, amount))
            conn.commit()
        except Exception as e:
            flash(f'Error adding ingredient: {e}', 'danger')
        conn.close()
        return redirect(url_for('meal_edit', meal_id=meal_id))

    @app.route('/meal-ingredients/<int:mi_id>/update', methods=['POST'])
    def meal_ingredient_update(mi_id):
        try:
            servings = float(request.form.get('servings', '0') or 0)
        except ValueError:
            servings = 0.0
        conn = get_db()
        row = conn.execute('''
            SELECT mi.meal_id, i.serving_size
            FROM meal_ingredients mi
            JOIN ingredients i ON i.id = mi.ingredient_id
            WHERE mi.id = ?
        ''', (mi_id,)).fetchone()
        if not row:
            conn.close()
            return ('', 404)
        if servings > 0:
            new_amount = servings * (row['serving_size'] or 0)
            conn.execute('UPDATE meal_ingredients SET amount=? WHERE id=?',
                         (new_amount, mi_id))
            conn.commit()
        meal_id = row['meal_id']
        conn.close()
        if request.headers.get('X-Requested-With') == 'fetch':
            return ('', 204)
        return redirect(url_for('meal_edit', meal_id=meal_id))

    @app.route('/meal-ingredients/<int:mi_id>/delete', methods=['POST'])
    def meal_ingredient_delete(mi_id):
        conn = get_db()
        row = conn.execute('SELECT meal_id FROM meal_ingredients WHERE id=?', (mi_id,)).fetchone()
        meal_id = row['meal_id'] if row else None
        conn.execute('DELETE FROM meal_ingredients WHERE id=?', (mi_id,))
        conn.commit()
        conn.close()
        if meal_id:
            return redirect(url_for('meal_edit', meal_id=meal_id))
        return redirect(url_for('meals_list'))

    @app.route('/meals/<int:meal_id>/delete', methods=['POST'])
    def meal_delete(meal_id):
        conn = get_db()
        conn.execute('DELETE FROM meals WHERE id=?', (meal_id,))
        conn.commit()
        conn.close()
        flash('Meal deleted.', 'success')
        return redirect(url_for('meals_list'))

    # ── Inventory + Recipe Matcher ────────────────────────────────────────────

    @app.route('/inventory', methods=['GET', 'POST'])
    def inventory_index():
        conn = get_db()
        if request.method == 'POST':
            for key, val in request.form.items():
                if not key.startswith('inv_'):
                    continue
                try:
                    ing_id = int(key[4:])
                    amount = max(0.0, float(val or 0))
                except (TypeError, ValueError):
                    continue
                conn.execute('UPDATE ingredients SET inventory_amount=? WHERE id=?',
                             (amount, ing_id))
            conn.commit()
            conn.close()
            flash('Inventory saved.', 'success')
            return redirect(url_for('inventory_index'))

        # Render: list every ingredient with its current inventory amount
        q = request.args.get('q', '').strip()
        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        INV_PER_PAGE = 50
        offset = (page - 1) * INV_PER_PAGE
        limit_sql = f' LIMIT {INV_PER_PAGE + 1} OFFSET {offset}'
        ing_query = ('SELECT id, name, unit, serving_size, shop, company, '
                     'image_filename, inventory_amount FROM ingredients')
        if q:
            like = f'%{_sql_unaccent(q)}%'
            ings = conn.execute(
                ing_query + ' WHERE unaccent(name) LIKE ? OR unaccent(shop) LIKE ? OR unaccent(company) LIKE ? ORDER BY name' + limit_sql,
                (like, like, like)).fetchall()
        else:
            ings = conn.execute(ing_query + ' ORDER BY name' + limit_sql).fetchall()

        inv_has_more = len(ings) > INV_PER_PAGE
        ings = ings[:INV_PER_PAGE]

        # AJAX (Stock tab infinite scroll) — return just the rows.
        if request.headers.get('X-Requested-With') == 'fetch':
            conn.close()
            html = render_template('_inventory_rows.html', ings=ings)
            return jsonify({'html': html, 'has_more': inv_has_more, 'page': page})

        # Recipe match: for each recipe, walk its ingredients and check inventory.
        recipes_full = []      # ingredients all on-hand
        recipes_partial = []   # one or more ingredients missing
        meals = conn.execute('SELECT id, name, image_filename FROM meals ORDER BY name').fetchall()
        for m in meals:
            rows = conn.execute('''
                SELECT mi.amount, i.id as ing_id, i.name, i.unit, i.inventory_amount
                FROM meal_ingredients mi
                JOIN ingredients i ON i.id = mi.ingredient_id
                WHERE mi.meal_id = ?
            ''', (m['id'],)).fetchall()
            if not rows:
                continue
            missing = []
            for r in rows:
                have = r['inventory_amount'] or 0
                need = r['amount']
                if have < need:
                    missing.append({
                        'ing_id': r['ing_id'], 'name': r['name'], 'unit': r['unit'],
                        'need': need, 'have': have, 'short_by': need - have,
                    })
            entry = {
                'id': m['id'], 'name': m['name'], 'image_filename': m['image_filename'],
                'total_ingredients': len(rows),
                'missing': missing,
                'have_count': len(rows) - len(missing),
            }
            if not missing:
                recipes_full.append(entry)
            else:
                recipes_partial.append(entry)
        # Sort partial recipes: fewest missing first
        recipes_partial.sort(key=lambda r: len(r['missing']))
        conn.close()
        return render_template('inventory.html',
                               ings=ings, q=q,
                               page=page, has_more=inv_has_more,
                               recipes_full=recipes_full,
                               recipes_partial=recipes_partial,
                               active_page='inventory')

    # ── Hydration ─────────────────────────────────────────────────────────────

    @app.route('/hydration')
    def hydration_index():
        today = date.today()
        today_str = today.strftime('%Y-%m-%d')
        conn = get_db()
        rows = conn.execute(
            'SELECT date, ml FROM daily_hydration').fetchall()
        conn.close()
        existing = {r['date']: r['ml'] for r in rows}

        start_raw = get_setting('starting_date')
        try:
            start = datetime.strptime(start_raw, '%Y-%m-%d').date() if start_raw else today
        except ValueError:
            start = today
        if existing:
            earliest_logged = min(datetime.strptime(d, '%Y-%m-%d').date() for d in existing)
            start = min(start, earliest_logged)

        all_dates = []
        d = start
        while d <= today:
            all_dates.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)

        entries = [{
            'date': d_str,
            'ml': existing.get(d_str, 0),
            'is_today': d_str == today_str,
        } for d_str in sorted(all_dates, reverse=True)]

        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        HYDRATION_PER_PAGE = 50
        start_idx = (page - 1) * HYDRATION_PER_PAGE
        end_idx = start_idx + HYDRATION_PER_PAGE
        page_entries = entries[start_idx:end_idx]
        has_more = end_idx < len(entries)

        if request.headers.get('X-Requested-With') == 'fetch':
            html = render_template('_hydration_rows.html', entries=page_entries)
            return jsonify({'html': html, 'has_more': has_more, 'page': page})

        return render_template('hydration_index.html', entries=page_entries,
                               today=today_str, page=page, has_more=has_more,
                               active_page='hydration')

    @app.route('/hydration/<date_str>', methods=['POST'])
    def hydration_save(date_str):
        try:
            ml = max(0, int(request.form.get('ml', 0) or 0))
        except ValueError:
            ml = 0
        conn = get_db()
        conn.execute('''
            INSERT INTO daily_hydration(date, ml, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET ml=excluded.ml, updated_at=excluded.updated_at
        ''', (date_str, ml))
        conn.commit()
        conn.close()
        flash('Hydration saved.', 'success')
        return redirect(url_for('hydration_index'))

    # ── Food Diary ────────────────────────────────────────────────────────────

    DIARY_SLOT_COUNT = 8

    @app.route('/diary')
    def diary_today():
        return redirect(url_for('diary_day', date_str=date.today().strftime('%Y-%m-%d')))

    @app.route('/diary/<date_str>')
    def diary_day(date_str):
        from database import (calc_diary_entry_totals, calc_meal_totals,
                              _empty_totals, _add, NUTRIENT_FIELDS)
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return redirect(url_for('diary_today'))
        prev_d, next_d = adjacent_dates(date_str)
        today_d = date.today()
        yesterday_str = (today_d - timedelta(days=1)).strftime('%Y-%m-%d')
        tomorrow_str  = (today_d + timedelta(days=1)).strftime('%Y-%m-%d')

        conn = get_db()
        rows = conn.execute('''
            SELECT d.id, d.slot, d.ingredient_id, d.meal_id, d.servings,
                   i.name as ing_name, i.unit as ing_unit, i.serving_size as ing_serving_size,
                   i.shop as ing_shop, i.company as ing_company,
                   i.image_filename as ing_image,
                   m.name as meal_name, m.image_filename as meal_image
            FROM diary_entries d
            LEFT JOIN ingredients i ON i.id = d.ingredient_id
            LEFT JOIN meals m       ON m.id = d.meal_id
            WHERE d.date = ?
            ORDER BY d.slot, d.id
        ''', (date_str,)).fetchall()
        all_ings  = conn.execute(
            'SELECT id, name, serving_size, unit, shop, company FROM ingredients ORDER BY name'
        ).fetchall()
        all_meals = conn.execute('SELECT id, name FROM meals ORDER BY name').fetchall()

        notes_rows = conn.execute(
            'SELECT slot, note FROM diary_slot_notes WHERE date = ?',
            (date_str,)).fetchall()
        notes_by_slot = {r['slot']: r['note'] for r in notes_rows}
        slots = {n: {'entries': [], 'totals': _empty_totals(),
                     'note': notes_by_slot.get(n, '')}
                 for n in range(1, DIARY_SLOT_COUNT + 1)}
        day_totals = _empty_totals()
        for r in rows:
            entry_totals = calc_diary_entry_totals(r, conn=conn)
            label = r['ing_name'] if r['ingredient_id'] else r['meal_name']
            shop = company = ''
            if r['ingredient_id']:
                ss = r['ing_serving_size'] or 0
                total_amount = r['servings'] * ss
                detail = (f"{r['servings']:g} × {ss:g}{r['ing_unit']} "
                          f"({total_amount:g}{r['ing_unit']})")
                kind = 'ingredient'
                shop = r['ing_shop'] or ''
                company = r['ing_company'] or ''
            else:
                detail = f"{r['servings']:g} serving"
                kind = 'meal'
            slot_num = r['slot'] if 1 <= r['slot'] <= DIARY_SLOT_COUNT else 1
            image_filename = (r['ing_image'] if r['ingredient_id'] else r['meal_image'])
            slots[slot_num]['entries'].append({
                'id': r['id'], 'kind': kind, 'label': label, 'detail': detail,
                'shop': shop, 'company': company,
                'image_filename': image_filename,
                'servings': r['servings'], 'totals': entry_totals,
                # For the per-row Amount column (None on meals — no g equivalent)
                'serving_size': r['ing_serving_size'] if kind == 'ingredient' else None,
                'unit':         r['ing_unit']         if kind == 'ingredient' else '',
            })
            _add(slots[slot_num]['totals'], entry_totals)
            _add(day_totals, entry_totals)
        conn.close()

        targets = get_targets()
        # daily calorie target (used for goal/remaining row)
        cal_target = targets.get('calories') or 0

        # Smart-expand rule: collapse every populated slot; expand exactly one
        # empty slot (the first one) so the user has a visible "Add Food" form.
        # If every slot is populated, nothing is auto-expanded.
        first_empty_slot = next(
            (n for n in range(1, DIARY_SLOT_COUNT + 1) if not slots[n]['entries']),
            None,
        )

        return render_template('diary_day.html',
            date_str=date_str, prev_d=prev_d, next_d=next_d,
            yesterday=yesterday_str, tomorrow=tomorrow_str,
            slot_count=DIARY_SLOT_COUNT,
            slots=slots, day_totals=day_totals,
            first_empty_slot=first_empty_slot,
            targets=targets, cal_target=cal_target,
            all_ings=all_ings, all_meals=all_meals,
            active_page='diary')

    @app.route('/diary/<date_str>/add', methods=['POST'])
    def diary_add(date_str):
        slot = int(request.form.get('slot', 1) or 1)
        slot = max(1, min(DIARY_SLOT_COUNT, slot))
        try:
            servings = float(request.form.get('servings', '1') or 1)
        except ValueError:
            servings = 1.0
        if servings <= 0:
            flash('Servings must be positive.', 'warning')
            return redirect(url_for('diary_day', date_str=date_str))
        item = request.form.get('item', '').strip()  # "ingredient:5" or "meal:3"
        if ':' not in item:
            flash('Pick an ingredient or meal.', 'warning')
            return redirect(url_for('diary_day', date_str=date_str))
        kind, _, raw_id = item.partition(':')
        try:
            target_id = int(raw_id)
        except ValueError:
            flash('Invalid selection.', 'danger')
            return redirect(url_for('diary_day', date_str=date_str))
        conn = get_db()
        now_ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if kind == 'ingredient':
            conn.execute(
                'INSERT INTO diary_entries(date,slot,ingredient_id,servings,created_at)'
                ' VALUES(?,?,?,?,?)',
                (date_str, slot, target_id, servings, now_ts))
        elif kind == 'meal':
            # Expand the recipe into individual ingredient entries, scaled by
            # (chosen servings / recipe yields). Each ingredient's servings =
            # (mi.amount / i.serving_size) × (user_servings / yields).
            meal_row = conn.execute('SELECT yields FROM meals WHERE id=?', (target_id,)).fetchone()
            if not meal_row:
                flash('Recipe not found.', 'danger')
            else:
                yields = meal_row['yields'] or 1.0
                ing_rows = conn.execute('''
                    SELECT mi.ingredient_id, mi.amount, i.serving_size
                    FROM meal_ingredients mi
                    JOIN ingredients i ON i.id = mi.ingredient_id
                    WHERE mi.meal_id = ?
                ''', (target_id,)).fetchall()
                if not ing_rows:
                    flash('That recipe has no ingredients yet — nothing added.', 'warning')
                inserted = 0
                for r in ing_rows:
                    ss = r['serving_size'] or 1
                    per_ing_servings = (r['amount'] / ss) * (servings / yields)
                    if per_ing_servings <= 0:
                        continue
                    conn.execute(
                        'INSERT INTO diary_entries(date,slot,ingredient_id,servings,source_meal_id,created_at)'
                        ' VALUES(?,?,?,?,?,?)',
                        (date_str, slot, r['ingredient_id'], per_ing_servings, target_id, now_ts))
                    inserted += 1
                if inserted:
                    flash(f'Added {inserted} ingredient{"s" if inserted != 1 else ""} from recipe.', 'success')
        else:
            flash('Unknown item type.', 'danger')
        conn.commit()
        conn.close()
        # Anchor back to the slot's Add Food form so the user lands where
        # they were and can add the next item without scrolling.
        return redirect(url_for('diary_day', date_str=date_str) + f'#slot-{slot}-addfood')

    @app.route('/diary/<date_str>/slot/<int:slot>/note', methods=['POST'])
    def diary_slot_note_save(date_str, slot):
        """Auto-save the per-slot note. Empty text deletes the row."""
        note = (request.form.get('note', '') or '').strip()
        conn = get_db()
        if note:
            conn.execute('''
                INSERT INTO diary_slot_notes(date, slot, note, updated_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(date, slot) DO UPDATE SET
                    note       = excluded.note,
                    updated_at = excluded.updated_at
            ''', (date_str, slot, note))
        else:
            conn.execute('DELETE FROM diary_slot_notes WHERE date=? AND slot=?',
                         (date_str, slot))
        conn.commit()
        conn.close()
        # Async fetch caller — 204 No Content keeps the page from reloading.
        if request.headers.get('X-Requested-With') == 'fetch':
            return ('', 204)
        return redirect(url_for('diary_day', date_str=date_str))

    @app.route('/diary/<date_str>/slot/<int:slot>/save-as-recipe', methods=['POST'])
    def diary_slot_save_as_recipe(date_str, slot):
        """Snapshot a diary slot's ingredients into a new entry in the recipe
        library. Ingredient `servings` are converted back to absolute amounts
        and duplicates are summed."""
        name = (request.form.get('name', '') or '').strip()
        if not name:
            flash('Recipe name is required.', 'warning')
            return redirect(url_for('diary_day', date_str=date_str))
        try:
            yields = float(request.form.get('yields', '1') or 1)
            if yields <= 0:
                yields = 1.0
        except ValueError:
            yields = 1.0
        conn = get_db()
        entries = conn.execute('''
            SELECT d.ingredient_id, d.servings, i.serving_size
            FROM diary_entries d
            JOIN ingredients i ON i.id = d.ingredient_id
            WHERE d.date = ? AND d.slot = ? AND d.ingredient_id IS NOT NULL
        ''', (date_str, slot)).fetchall()
        if not entries:
            conn.close()
            flash(f'Meal {slot} has no ingredient entries to save.', 'warning')
            return redirect(url_for('diary_day', date_str=date_str))
        # Aggregate amounts per ingredient (sum dupes).
        totals = {}
        for r in entries:
            ss = r['serving_size'] or 0
            amount = (r['servings'] or 0) * ss
            if amount <= 0:
                continue
            totals[r['ingredient_id']] = totals.get(r['ingredient_id'], 0.0) + amount
        if not totals:
            conn.close()
            flash(f'Meal {slot} had no measurable amounts to save.', 'warning')
            return redirect(url_for('diary_day', date_str=date_str))
        cur = conn.execute(
            'INSERT INTO meals(name, yields) VALUES(?, ?)', (name, yields))
        meal_id = cur.lastrowid
        for ing_id, amount in totals.items():
            conn.execute(
                'INSERT INTO meal_ingredients(meal_id, ingredient_id, amount)'
                ' VALUES(?, ?, ?)', (meal_id, ing_id, amount))
        conn.commit()
        conn.close()
        flash(f'Saved Meal {slot} as recipe "{name}". Open to refine.', 'success')
        return redirect(url_for('meal_edit', meal_id=meal_id))

    @app.route('/diary-entries/<int:entry_id>/delete', methods=['POST'])
    def diary_entry_delete(entry_id):
        conn = get_db()
        row = conn.execute('SELECT date FROM diary_entries WHERE id=?', (entry_id,)).fetchone()
        date_str = row['date'] if row else date.today().strftime('%Y-%m-%d')
        conn.execute('DELETE FROM diary_entries WHERE id=?', (entry_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('diary_day', date_str=date_str))

    @app.route('/diary-entries/<int:entry_id>/update', methods=['POST'])
    def diary_entry_update(entry_id):
        conn = get_db()
        row = conn.execute('SELECT date FROM diary_entries WHERE id=?', (entry_id,)).fetchone()
        if not row:
            conn.close()
            return redirect(url_for('diary_today'))
        date_str = row['date']
        try:
            servings = float(request.form.get('servings', '1') or 1)
        except ValueError:
            servings = 1.0
        slot = int(request.form.get('slot', 1) or 1)
        slot = max(1, min(DIARY_SLOT_COUNT, slot))
        if servings > 0:
            conn.execute('UPDATE diary_entries SET servings=?, slot=? WHERE id=?',
                         (servings, slot, entry_id))
            conn.commit()
        conn.close()
        return redirect(url_for('diary_day', date_str=date_str))

    # ── Steps ─────────────────────────────────────────────────────────────────

    @app.route('/steps')
    def steps_index():
        # Opportunistic Garmin sync if the refresh interval has elapsed. This
        # is a no-op when Garmin is disabled, not installed, or not yet due.
        garmin.maybe_sync()

        today = date.today()
        today_str = today.strftime('%Y-%m-%d')
        conn = get_db()
        rows = conn.execute(
            'SELECT date, steps, weight_kg, fetched_at, is_locked, source FROM daily_steps').fetchall()
        conn.close()
        existing = {r['date']: r for r in rows}

        # Walk every date from starting_date (in Settings) up to today.
        start_raw = get_setting('starting_date')
        try:
            start = datetime.strptime(start_raw, '%Y-%m-%d').date() if start_raw else today
        except ValueError:
            start = today
        if existing:
            earliest_logged = min(datetime.strptime(d, '%Y-%m-%d').date() for d in existing)
            start = min(start, earliest_logged)

        all_dates = []
        d = start
        while d <= today:
            all_dates.append(d.strftime('%Y-%m-%d'))
            d += timedelta(days=1)

        # Profile for BMR — needed to walk the predicted weight forward.
        starting_weight_str = get_setting('starting_weight')
        starting_w = float(starting_weight_str) if starting_weight_str else None
        dob       = get_setting('dob')
        height    = get_setting('height_cm')
        age       = calc_age(dob) if dob else None
        height_f  = float(height) if height else None
        gender    = (get_setting('gender') or '').strip().lower() or None

        # Iterate chronologically so each day's predicted weight reflects the
        # accumulated calorie balance up to (but not including) that day. When
        # the user logs an actual weight reading, that becomes the new baseline.
        last_baseline_w = starting_w
        balance_since_baseline = 0.0
        entries_chrono = []
        for d_str in all_dates:
            r = existing.get(d_str)
            steps = r['steps'] if r else 0
            if r and r['weight_kg']:
                w = float(r['weight_kg'])
                last_baseline_w = w
                balance_since_baseline = 0.0
            elif last_baseline_w is not None:
                w = last_baseline_w + balance_since_baseline / 7700
            else:
                w = None
            cps_today = calories_per_step(w) if w else 0
            step_cal = (steps * cps_today) if w else 0
            # accumulate balance for the next iteration's weight prediction
            food_cal = calc_day_totals(d_str)['calories']
            bmr      = calc_bmr(w, height_f, age, gender) if (w and height_f and age) else None
            output   = (bmr + step_cal) if bmr else step_cal
            balance  = (food_cal - output) if output else 0
            balance_since_baseline += balance
            entries_chrono.append({
                'date': d_str, 'steps': steps,
                'weight_kg': w, 'cps': cps_today,
                'step_calories': step_cal,
                'is_today': d_str == today_str,
                'fetched_at': r['fetched_at'] if r else None,
                'is_locked':  bool(r['is_locked']) if r else False,
                'source':     r['source'] if r else None,
            })
        entries = list(reversed(entries_chrono))  # newest first

        # Paginate the reversed view. We must compute the full chronological
        # walk first because each day's predicted weight depends on previous
        # days' balance — we just slice the display window after the math.
        try:
            page = max(1, int(request.args.get('page', 1)))
        except ValueError:
            page = 1
        STEPS_PER_PAGE = 50
        start_idx = (page - 1) * STEPS_PER_PAGE
        end_idx = start_idx + STEPS_PER_PAGE
        page_entries = entries[start_idx:end_idx]
        has_more = end_idx < len(entries)

        if request.headers.get('X-Requested-With') == 'fetch':
            html = render_template('_steps_rows.html', entries=page_entries)
            return jsonify({'html': html, 'has_more': has_more, 'page': page})

        return render_template('steps_index.html', entries=page_entries,
                               today=today_str, page=page, has_more=has_more,
                               garmin_status=garmin.get_status(),
                               active_page='steps')

    @app.route('/steps/<date_str>', methods=['GET', 'POST'])
    def steps_day(date_str):
        if request.method == 'POST':
            steps = int(request.form.get('steps', 0) or 0)
            conn = get_db()
            # Manual edits win: source='manual' clears any prior lock so future
            # Garmin syncs skip this row.
            conn.execute('''
                INSERT INTO daily_steps(date, steps, source, is_locked)
                VALUES(?, ?, 'manual', 0)
                ON CONFLICT(date) DO UPDATE SET
                    steps     = excluded.steps,
                    source    = 'manual',
                    is_locked = 0
            ''', (date_str, steps))
            conn.commit()
            conn.close()
            flash('Steps saved.', 'success')
            if request.form.get('return_to') == 'index':
                return redirect(url_for('steps_index'))
            return redirect(url_for('steps_day', date_str=date_str))
        conn = get_db()
        entry = conn.execute('SELECT * FROM daily_steps WHERE date=?', (date_str,)).fetchone()
        conn.close()
        prev_d, next_d = adjacent_dates(date_str)
        w = (entry['weight_kg'] if entry else None) or get_current_weight(date_str)
        step_cal = ((entry['steps'] if entry else 0) * calories_per_step(w)) if w else 0
        return render_template('steps_day.html', date_str=date_str, entry=entry,
                               prev_d=prev_d, next_d=next_d, step_calories=step_cal,
                               current_weight=w, active_page='steps')

    # ── Garmin ───────────────────────────────────────────────────────────────

    @app.route('/garmin/sync-now', methods=['POST'])
    def garmin_sync_now():
        if not garmin.is_available():
            flash(f'garminconnect not installed. Run: pip install garminconnect '
                  f'({garmin.import_error() or "no details"})', 'danger')
            return redirect(request.referrer or url_for('settings'))
        # Explicit click → force-refresh today even if it's marked manual.
        result = garmin.sync_all_pending(force_today=True)
        if result['ok']:
            parts = [f'{result["count"]} day(s) updated']
            if result.get('skipped_manual'):
                parts.append(f'{len(result["skipped_manual"])} past day(s) skipped (manual)')
            flash('Garmin sync: ' + ', '.join(parts) + '.', 'success')
        else:
            errs = '; '.join(result['errors']) or 'Unknown error'
            flash(f'Garmin sync failed: {errs}', 'danger')
        return redirect(request.referrer or url_for('settings'))

    # ── Analytics (all-time) ─────────────────────────────────────────────────

    @app.route('/analytics')
    def analytics():
        conn = get_db()
        dob = get_setting('dob')
        height = get_setting('height_cm')
        age = calc_age(dob) if dob else None
        height_f = float(height) if height else None
        gender   = (get_setting('gender') or '').strip().lower() or None

        # ── All-time analytics ────────────────────────────────────────────────
        step_stats = conn.execute('''
            SELECT SUM(steps) as total, AVG(steps) as avg_day
            FROM daily_steps WHERE steps > 0
        ''').fetchone()
        all_step_rows = conn.execute(
            'SELECT date, steps FROM daily_steps WHERE steps > 0').fetchall()
        total_step_cals = 0
        for row in all_step_rows:
            w = get_current_weight(row['date'])
            if w:
                total_step_cals += row['steps'] * calories_per_step(w)

        # Aggregate totals across every day that has any diary entries
        all_dates = conn.execute(
            'SELECT DISTINCT date FROM diary_entries'
        ).fetchall()
        total_nutrition = _empty_totals()
        for d_row in all_dates:
            day_t = calc_day_totals(d_row['date'])
            _add(total_nutrition, day_t)
        total_intake_cal = total_nutrition['calories']
        avg_daily_intake = total_intake_cal / len(all_dates) if all_dates else 0

        starting_weight_str = get_setting('starting_weight')
        starting_date_str   = get_setting('starting_date')
        target_weight_str   = get_setting('target_weight')
        target_weight  = float(target_weight_str)   if target_weight_str   else None

        # Number of days the user has been tracking — used by All-Time Nutrition
        # to scale daily targets into a cumulative target (target × days). Day 1
        # is the starting day itself, so we add 1 to the delta. Falls back to 1
        # if no starting date is set (avoids a divide-by-zero feel; the bar will
        # just compare against a single day's target).
        days_since_start = 1
        if starting_date_str:
            try:
                _sd = datetime.strptime(starting_date_str, '%Y-%m-%d').date()
                days_since_start = max(1, (date.today() - _sd).days + 1)
            except Exception:
                pass

        # Has the user logged an actual weight reading AFTER the starting date?
        actual_post_start_weight = None
        if starting_date_str:
            row = conn.execute(
                'SELECT weight_kg FROM daily_steps '
                'WHERE weight_kg IS NOT NULL AND date > ? '
                'ORDER BY date DESC LIMIT 1',
                (starting_date_str,)
            ).fetchone()
            if row:
                actual_post_start_weight = float(row['weight_kg'])

        weight_history = conn.execute(
            'SELECT date, weight_kg FROM daily_steps WHERE weight_kg IS NOT NULL ORDER BY date'
        ).fetchall()

        # Hydration per day
        hydration_rows = conn.execute(
            'SELECT date, ml FROM daily_hydration WHERE ml > 0 ORDER BY date'
        ).fetchall()
        hydration_dates  = [r['date'] for r in hydration_rows]
        hydration_series = [r['ml']   for r in hydration_rows]
        total_hydration_ml = sum(hydration_series)
        avg_hydration_ml   = (total_hydration_ml / len(hydration_series)) if hydration_series else 0

        # ── Daily series for charts (steps / intake / balance) ───────────────
        series_dates = conn.execute('''
            SELECT date FROM daily_steps WHERE steps > 0
            UNION
            SELECT DISTINCT date FROM diary_entries
            ORDER BY date
        ''').fetchall()
        step_rows_map = {r['date']: r for r in conn.execute(
            'SELECT date, steps, weight_kg FROM daily_steps').fetchall()}
        conn.close()

        # Walk forward, with predicted weight reflecting accumulated balance.
        starting_w = float(starting_weight_str) if starting_weight_str else None
        last_baseline_w = starting_w
        balance_since_baseline = 0.0
        daily_dates = []
        daily_steps_series = []
        daily_intake = []
        daily_balance = []
        total_output_cal_all = 0.0
        total_intake_cal_all = 0.0
        total_bmr_all = 0.0
        total_step_burn_all = 0.0
        for r in series_dates:
            d = r['date']
            se = step_rows_map.get(d)
            s = se['steps'] if se else 0
            if se and se['weight_kg']:
                w_today = float(se['weight_kg'])
                last_baseline_w = w_today
                balance_since_baseline = 0.0
            elif last_baseline_w is not None:
                w_today = last_baseline_w + balance_since_baseline / 7700
            else:
                w_today = None
            food_cal = calc_day_totals(d)['calories']
            bmr_d    = calc_bmr(w_today, height_f, age, gender) if (w_today and height_f and age) else None
            step_cal = (s * calories_per_step(w_today)) if w_today else 0
            output   = (bmr_d + step_cal) if bmr_d else step_cal
            bal      = (food_cal - output) if output else 0
            balance_since_baseline += bal
            daily_dates.append(d)
            daily_steps_series.append(s)
            daily_intake.append(food_cal)
            daily_balance.append(bal)
            total_output_cal_all += output or 0
            total_intake_cal_all += food_cal or 0
            total_bmr_all += bmr_d or 0
            total_step_burn_all += step_cal or 0
        total_balance_cal = total_intake_cal_all - total_output_cal_all
        total_weight_change_kg = total_balance_cal / 7700

        # ── Display "current weight" ────────────────────────────────────────
        # If the user has logged an actual reading after the starting date, use
        # it. Otherwise compute a predicted current weight from the all-time
        # calorie balance (= starting + Δkcal / 7700). Falls back to the raw
        # starting weight only if neither is available.
        starting_w_f = float(starting_weight_str) if starting_weight_str else None
        predicted_current_weight = (
            starting_w_f + total_weight_change_kg if starting_w_f is not None else None
        )
        if actual_post_start_weight is not None:
            current_weight = actual_post_start_weight
            current_weight_is_predicted = False
        elif predicted_current_weight is not None:
            current_weight = predicted_current_weight
            current_weight_is_predicted = True
        else:
            current_weight = get_current_weight()
            current_weight_is_predicted = False
        weight_lost = ((starting_w_f - current_weight)
                       if (starting_w_f is not None and current_weight is not None) else None)

        # Goal-progress numbers — now driven off the (possibly predicted) current weight
        pct_to_target = steps_to_target = days_to_target = target_date_at_deficit = None
        steps_breakdown_rows = []
        if target_weight and current_weight and starting_w_f is not None:
            total_to_lose = starting_w_f - target_weight
            already_lost  = starting_w_f - current_weight
            if total_to_lose > 0:
                pct_to_target = max(0, min(100, already_lost / total_to_lose * 100))
            remaining_kg = current_weight - target_weight
            if remaining_kg > 0:
                remaining_cals = remaining_kg * 7700
                # Bucket-by-bucket breakdown: as weight drops, the cps from the
                # Settings step-table changes. Sum the steps required in each
                # 5-kg slice between current_weight and target_weight.
                buckets_desc = [(130, float('inf'), 0.076)]
                for w1, c1 in reversed(_STEP_TABLE[:-1]):
                    buckets_desc.append((w1, w1 + 5, c1))
                buckets_desc.append((0, 40, 0.023))
                w_walk = current_weight
                steps_breakdown_rows = []
                steps_to_target = 0.0
                for lo, up, cps_b in buckets_desc:
                    if w_walk <= lo:
                        continue
                    top = min(w_walk, up)
                    bot = max(lo, target_weight)
                    if bot >= top:
                        if w_walk <= target_weight:
                            break
                        continue
                    kg_in_bucket = top - bot
                    s = (kg_in_bucket * 7700) / cps_b if cps_b else 0
                    steps_breakdown_rows.append({
                        'top': top, 'bottom': bot,
                        'kg': kg_in_bucket, 'cps': cps_b, 'steps': s,
                    })
                    steps_to_target += s
                    w_walk = bot
                    if w_walk <= target_weight:
                        break
                # Daily-average deficit. Divisor must match the *same* days the
                # numerator was summed over — i.e. every tracked day (steps OR
                # diary entries), not just days with diary entries.
                tracked_day_count = len(series_dates)
                if tracked_day_count:
                    total_deficit_all = total_output_cal_all - total_intake_cal_all
                    avg_deficit = total_deficit_all / tracked_day_count
                    if avg_deficit > 0:
                        days_to_target = remaining_cals / avg_deficit
                        target_date_at_deficit = (
                            date.today() + timedelta(days=int(round(days_to_target)))
                        ).strftime('%Y-%m-%d')

        # ── Daily Projection (now a tab inside Analytics) ───────────────────
        # Direction towards target: -1 = losing weight, +1 = gaining, 0 = no target or already there.
        target_direction = 0
        if target_weight is not None and current_weight is not None:
            if current_weight > target_weight:   target_direction = -1
            elif current_weight < target_weight: target_direction = +1

        def _cals_left_to_target(w):
            """Remaining caloric distance to the target weight, clamped at 0
            once the target is crossed in the direction of travel."""
            if not target_weight or w is None:
                return None
            if target_direction == -1:
                return max(0.0, (w - target_weight) * 7700)
            if target_direction == +1:
                return max(0.0, (target_weight - w) * 7700)
            return abs(w - target_weight) * 7700

        def _steps_for_cals(cals, w):
            """Steps needed to burn `cals` at the walker's weight `w`."""
            if cals is None or not w:
                return None
            cps = calories_per_step(w)
            return (cals / cps) if cps else None

        import math
        def _steps_to_next_milestone(w):
            """Steps to burn the calories between the current projected weight
            and the next whole-kg boundary in the direction of the target
            (clamped so it never overshoots the target)."""
            if not target_weight or w is None or target_direction == 0:
                return None
            if target_direction == -1:
                nb = max(math.ceil(w) - 1, target_weight)   # next kg down
                cals = max(0.0, (w - nb) * 7700)
            else:
                nb = min(math.floor(w) + 1, target_weight)  # next kg up
                cals = max(0.0, (nb - w) * 7700)
            cps = calories_per_step(w)
            return (cals / cps) if cps else None

        def _milestone_crossed(prev_w, curr_w):
            """Whole-kg boundary crossed between consecutive rows (e.g. 94 when
            weight walks 94.13 → 93.97). None when no boundary was crossed."""
            if prev_w is None or curr_w is None:
                return None
            fp, fc = math.floor(prev_w), math.floor(curr_w)
            if fc < fp:
                return fp   # descending: crossed fp.0
            if fc > fp:
                return fc   # ascending: crossed fc.0
            return None

        # Historical day-by-day, walking the predicted weight forward.
        proj_historical = []
        prev_w_milestone = None
        running_balance = 0.0
        last_proj_baseline_w = starting_w
        proj_balance_since_baseline = 0.0
        last_proj_w = starting_w
        hist_steps_for_avg = []
        hist_deltas_for_avg = []
        for r in series_dates:
            d = r['date']
            se = step_rows_map.get(d)
            steps_d = se['steps'] if se else 0
            if se and se['weight_kg']:
                w_today = float(se['weight_kg'])
                last_proj_baseline_w = w_today
                proj_balance_since_baseline = 0.0
            elif last_proj_baseline_w is not None:
                w_today = last_proj_baseline_w + proj_balance_since_baseline / 7700
            else:
                w_today = None
            if w_today:
                last_proj_w = w_today
            food_cal = calc_day_totals(d)['calories']
            bmr_d    = calc_bmr(w_today, height_f, age, gender) if (w_today and height_f and age) else None
            step_cal = (steps_d * calories_per_step(w_today)) if w_today else 0
            output   = (bmr_d + step_cal) if bmr_d else step_cal
            balance  = (food_cal - output) if output else 0
            running_balance += balance
            proj_balance_since_baseline += balance
            proj_w_today = ((starting_w + running_balance / 7700)
                            if starting_w is not None else None)
            ctl = _cals_left_to_target(proj_w_today)
            ms = _milestone_crossed(prev_w_milestone, proj_w_today)
            proj_historical.append({
                'date': d, 'steps': steps_d, 'weight': w_today,
                'food': food_cal, 'bmr': bmr_d, 'step_cal': step_cal,
                'output': output, 'balance': balance,
                'cum_balance': running_balance, 'projected_weight': proj_w_today,
                'cals_to_target': ctl,
                'steps_to_target': _steps_for_cals(ctl, proj_w_today),
                'steps_to_next_milestone': _steps_to_next_milestone(proj_w_today),
                'milestone': ms,
                # Countdown label: kg remaining from this crossed boundary to
                # the target (e.g. crossing 94 with target 84 → "milestone 10").
                'milestone_num': (abs(ms - target_weight) if (ms is not None and target_weight) else ms),
                'weight_delta': ((proj_w_today - prev_w_milestone)
                                 if (proj_w_today is not None and prev_w_milestone is not None) else None),
            })
            if proj_w_today is not None:
                prev_w_milestone = proj_w_today
            if steps_d > 0:
                hist_steps_for_avg.append(steps_d)
            if food_cal > 0 and bmr_d:
                hist_deltas_for_avg.append(food_cal - bmr_d)

        # Form inputs (with sensible defaults)
        default_steps = int(sum(hist_steps_for_avg) / len(hist_steps_for_avg)) if hist_steps_for_avg else 8000
        default_delta = (int(sum(hist_deltas_for_avg) / len(hist_deltas_for_avg))
                         if hist_deltas_for_avg else -get_calorie_deficit())
        try:
            avg_steps  = int(request.args.get('avg_steps', default_steps))
            bmr_delta  = int(request.args.get('bmr_delta', default_delta))
            days_fwd   = int(request.args.get('days', 1095))
        except ValueError:
            avg_steps, bmr_delta, days_fwd = default_steps, default_delta, 1095
        days_fwd = max(1, min(3650, days_fwd))

        # Forward projection
        if proj_historical:
            last_proj_date = datetime.strptime(proj_historical[-1]['date'], '%Y-%m-%d').date()
        else:
            last_proj_date = date.today()
        proj_projected = []
        # Seed the forward projection from the displayed current weight (which
        # falls back to predicted when no actual readings exist) — otherwise
        # BMR for projected days would be computed against the starting weight.
        future_weight = current_weight if current_weight else last_proj_w
        future_running = running_balance
        for i in range(1, days_fwd + 1):
            d = (last_proj_date + timedelta(days=i)).strftime('%Y-%m-%d')
            if not future_weight or not (height_f and age):
                proj_projected.append({'date': d})
                continue
            bmr_d    = calc_bmr(future_weight, height_f, age, gender)
            food_cal = bmr_d + bmr_delta
            step_cal = avg_steps * calories_per_step(future_weight)
            output   = bmr_d + step_cal
            balance  = food_cal - output
            future_running += balance
            new_w = (starting_w_f + future_running / 7700) if starting_w_f else (future_weight + balance / 7700)
            ctl = _cals_left_to_target(new_w)
            ms = _milestone_crossed(prev_w_milestone, new_w)
            proj_projected.append({
                'date': d, 'steps': avg_steps, 'weight': future_weight,
                'food': food_cal, 'bmr': bmr_d, 'step_cal': step_cal,
                'output': output, 'balance': balance,
                'cum_balance': future_running, 'projected_weight': new_w,
                'cals_to_target': ctl,
                'steps_to_target': _steps_for_cals(ctl, new_w),
                'steps_to_next_milestone': _steps_to_next_milestone(new_w),
                'milestone': ms,
                'milestone_num': (abs(ms - target_weight) if (ms is not None and target_weight) else ms),
                'weight_delta': ((new_w - prev_w_milestone)
                                 if (new_w is not None and prev_w_milestone is not None) else None),
                'is_projection': True,
            })
            if new_w is not None:
                prev_w_milestone = new_w
            future_weight = new_w
            # Stop once we cross (or reach) the target — keep the crossing row
            # so the user sees the arrival day itself.
            if target_direction == -1 and new_w <= target_weight:
                break
            if target_direction == +1 and new_w >= target_weight:
                break

        eta_target = None
        eta_days_from_today = None
        if target_weight:
            for row in proj_projected:
                if row.get('projected_weight') is not None and row['projected_weight'] <= target_weight:
                    eta_target = row['date']
                    eta_days_from_today = (datetime.strptime(row['date'], '%Y-%m-%d').date()
                                           - date.today()).days
                    break

        # Decide which tab is active. ?tab=… wins; otherwise any projection
        # query param flips the user into the projection tab.
        active_tab = request.args.get('tab', '').strip().lower()
        if active_tab not in ('overview', 'nutrition', 'projection'):
            active_tab = 'projection' if any(
                k in request.args for k in ('avg_steps', 'bmr_delta', 'days')
            ) else 'overview'

        return render_template('analytics.html',
            today=date.today().strftime('%Y-%m-%d'),
            step_stats=step_stats,
            total_step_cals=total_step_cals,
            total_intake_cal=total_intake_cal,
            avg_daily_intake=avg_daily_intake,
            starting_weight=starting_weight_str,
            current_weight=current_weight,
            weight_lost=weight_lost,
            pct_to_target=pct_to_target,
            steps_to_target=steps_to_target,
            steps_breakdown_rows=steps_breakdown_rows,
            days_to_target=days_to_target,
            target_date_at_deficit=target_date_at_deficit,
            weight_history=weight_history,
            daily_dates=daily_dates,
            daily_steps_series=daily_steps_series,
            daily_intake=daily_intake,
            daily_balance=daily_balance,
            hydration_dates=hydration_dates,
            hydration_series=hydration_series,
            total_hydration_ml=total_hydration_ml,
            avg_hydration_ml=avg_hydration_ml,
            total_intake_all=total_intake_cal_all,
            total_output_all=total_output_cal_all,
            total_bmr_all=total_bmr_all,
            total_step_burn_all=total_step_burn_all,
            total_balance_cal=total_balance_cal,
            total_weight_change_kg=total_weight_change_kg,
            current_weight_is_predicted=current_weight_is_predicted,
            total_nutrition=total_nutrition,
            targets=get_targets(),
            days_since_start=days_since_start,
            # Projection tab data
            proj_historical=proj_historical, proj_projected=proj_projected,
            avg_steps=avg_steps, bmr_delta=bmr_delta, days_fwd=days_fwd,
            default_steps=default_steps, default_delta=default_delta,
            target_weight=target_weight, eta_target=eta_target,
            eta_days_from_today=eta_days_from_today,
            height=height, age=age,
            active_tab=active_tab,
            active_page='analytics')

    # /analytics/projection redirects to the Projection tab inside Analytics.
    @app.route('/analytics/projection')
    def analytics_projection():
        qs = request.query_string.decode('utf-8')
        url = '/analytics?tab=projection'
        if qs:
            url += '&' + qs
        return redirect(url)

    # ── Notes: a single rich-text scratch document ───────────────────────────
    @app.route('/notes')
    def notes():
        return render_template('notes.html',
                               notes_html=get_setting('user_notes') or '',
                               active_page='notes')

    @app.route('/notes/save', methods=['POST'])
    def notes_save():
        data = request.get_json(silent=True) or {}
        set_setting('user_notes', data.get('html', ''))
        return jsonify({'ok': True})

    # ── Admin: delete any ingredient by name (bypasses FK) ───────────────────
    @app.route('/admin/delete-ingredient', methods=['POST'])
    def admin_delete_ingredient():
        name = request.form.get('name', '').strip()
        if not name:
            flash('No name provided.', 'warning')
            return redirect(url_for('ingredients'))
        conn = get_db()
        ing = conn.execute('SELECT id FROM ingredients WHERE name=?', (name,)).fetchone()
        if not ing:
            conn.close()
            flash(f'Ingredient "{name}" not found.', 'danger')
            return redirect(url_for('ingredients'))
        conn.execute('DELETE FROM meal_ingredients WHERE ingredient_id=?', (ing['id'],))
        conn.execute('DELETE FROM ingredients WHERE id=?', (ing['id'],))
        conn.commit()
        conn.close()
        flash(f'Deleted "{name}" and all meal references to it.', 'success')
        return redirect(url_for('ingredients'))

    _start_garmin_scheduler()
    return app


# ── Background Garmin auto-sync ──────────────────────────────────────────────
# Previously the only trigger for an auto-sync was loading the /steps page, so
# the "Next refresh: due now" status could sit indefinitely while you were on
# any other page. This daemon thread ticks every minute and lets
# garmin.maybe_sync() decide when the configured interval has elapsed, so the
# sync happens on its own without visiting /steps or clicking Sync Now.
_garmin_thread_started = False

def _start_garmin_scheduler():
    global _garmin_thread_started
    if _garmin_thread_started:
        return
    _garmin_thread_started = True

    import threading, time

    def _loop():
        while True:
            try:
                garmin.maybe_sync()  # no-op unless enabled AND the interval elapsed
            except Exception:
                pass  # never let a sync error kill the scheduler
            time.sleep(60)

    t = threading.Thread(target=_loop, name='garmin-autosync', daemon=True)
    t.start()


if __name__ == '__main__':
    init_db()
    app = create_app()
    app.run(debug=True, port=6001)
