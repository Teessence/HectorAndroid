"""Strength training: the barbell plan, guided workouts and progress stats.

The plan (strength_exercises) is the list of exercises with their target
sets, reps, weight and rest, in workout order. A workout (strength_sessions)
logs every set actually done (strength_sets) with its reps and weight; the
plan's targets at that moment are stored alongside, so progress can be judged
later even after the plan changes.

Volume ("kg lifted") is weight × reps summed over the 'load' exercises.
Strength is compared with the estimated one-rep max (Epley: w × (1 + reps/30)),
which lets 40 kg × 8 and 45 kg × 5 be compared fairly.
"""
from datetime import date, datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, jsonify

from database import get_db, get_setting, set_setting

# key, name, sets, reps, weight (kg), rest between sets (s), increment (kg), track
DEFAULT_PLAN = [
    ('fsquat', 'Front squat', 3, 8, 20, 150, 5, 'load'),
    ('floorpress', 'Floor press', 3, 8, 20, 120, 2.5, 'load'),
    ('pushup', 'Push-ups', 3, 10, 0, 90, 0, 'reps'),
    ('row', 'Bent-over row', 3, 10, 20, 120, 2.5, 'load'),
    ('rdl', 'Romanian deadlift', 3, 10, 20, 120, 5, 'load'),
    ('ohp', 'Overhead press', 3, 8, 20, 120, 2.5, 'load'),
    ('rollout', 'Barbell rollout', 3, 8, 20, 90, 0, 'reps'),
]
DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
DEFAULT_DAYS = '0,2,4'           # Mon, Wed, Fri
DEFAULT_REST_BETWEEN = 150       # seconds between exercises
TS = '%Y-%m-%d %H:%M:%S'


def _now():
    return datetime.now().strftime(TS)


def e1rm(weight, reps):
    """Estimated one-rep max (Epley)."""
    if not weight or not reps:
        return 0.0
    return weight if reps == 1 else weight * (1 + reps / 30.0)


def _kg(v):
    """40.0 → '40', 42.5 → '42.5'."""
    return ('%.2f' % (v or 0)).rstrip('0').rstrip('.')


def _week_start(d):
    return d - timedelta(days=d.weekday())


# ── Plan ──────────────────────────────────────────────────────────────────────

def _ensure_plan(conn):
    """Add any default exercise the plan doesn't have yet (all of them on a new
    install; later additions such as push-ups on an existing one), keeping the
    default order. Existing exercises keep their targets."""
    have = {r['key'] for r in conn.execute('SELECT key FROM strength_exercises')}
    missing = [d for d in DEFAULT_PLAN if d[0] not in have]
    if not missing:
        return
    for key, name, sets, reps, w, rest, inc, track in missing:
        conn.execute('''INSERT INTO strength_exercises
                        (key, name, position, sets, reps, weight_kg, rest_s, increment_kg, track)
                        VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)''',
                     (key, name, sets, reps, w, rest, inc, track))
    for i, d in enumerate(DEFAULT_PLAN):
        conn.execute('UPDATE strength_exercises SET position=? WHERE key=?', (i, d[0]))
    conn.commit()


def get_plan():
    conn = get_db()
    try:
        _ensure_plan(conn)
        return [dict(r) for r in conn.execute(
            'SELECT * FROM strength_exercises ORDER BY position, id')]
    finally:
        conn.close()


def training_days():
    raw = get_setting('strength_days', DEFAULT_DAYS) or ''
    days = sorted({int(x) for x in raw.split(',') if x.strip().isdigit() and int(x) < 7})
    return days


def rest_between():
    try:
        return max(0, int(float(get_setting('strength_rest_between', DEFAULT_REST_BETWEEN))))
    except (TypeError, ValueError):
        return DEFAULT_REST_BETWEEN


def next_training_day(today=None):
    """The next training day after today (or None when no days are set)."""
    today = today or date.today()
    days = training_days()
    for i in range(1, 8):
        d = today + timedelta(days=i)
        if d.weekday() in days:
            return d
    return None


def _num(form, key, cast, lo, hi, default):
    try:
        v = cast(float(form.get(key, '').replace(',', '.')))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def save_plan(form):
    conn = get_db()
    try:
        for ex in conn.execute('SELECT * FROM strength_exercises').fetchall():
            k = ex['key']
            conn.execute('''UPDATE strength_exercises SET sets=?, reps=?, weight_kg=?, rest_s=?, increment_kg=?
                            WHERE id=?''', (
                _num(form, f'sets_{k}', int, 1, 10, ex['sets']),
                _num(form, f'reps_{k}', int, 1, 50, ex['reps']),
                round(_num(form, f'weight_{k}', float, 0, 500, ex['weight_kg']), 2),
                _num(form, f'rest_{k}', int, 0, 900, ex['rest_s']),
                round(_num(form, f'inc_{k}', float, 0, 50, ex['increment_kg']), 2),
                ex['id']))
        conn.commit()
    finally:
        conn.close()
    days = [str(i) for i in range(7) if form.get(f'day_{i}')]
    set_setting('strength_days', ','.join(days))
    set_setting('strength_rest_between', str(_num(form, 'rest_between', int, 0, 900, rest_between())))


def bump_weight(key, direction=1):
    conn = get_db()
    try:
        conn.execute('''UPDATE strength_exercises
                        SET weight_kg = MAX(0, weight_kg + ? * increment_kg) WHERE key=?''',
                     (1 if direction >= 0 else -1, key))
        conn.commit()
    finally:
        conn.close()


# ── Workouts ──────────────────────────────────────────────────────────────────

def open_session(today=None):
    """Today's unfinished workout, if any."""
    today = (today or date.today()).strftime('%Y-%m-%d')
    conn = get_db()
    try:
        r = conn.execute('''SELECT * FROM strength_sessions WHERE date=? AND finished_at IS NULL
                            ORDER BY id DESC LIMIT 1''', (today,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def start_session():
    s = open_session()
    if s:
        return s['id']
    conn = get_db()
    try:
        cur = conn.execute('INSERT INTO strength_sessions(date, started_at) VALUES (?, ?)',
                           (date.today().strftime('%Y-%m-%d'), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def log_set(session_id, key, reps, weight):
    """Record one set; returns the new row as a dict (or None if invalid)."""
    plan = {p['key']: p for p in get_plan()}
    ex = plan.get(key)
    if not ex:
        return None
    reps = max(0, min(100, int(reps)))
    weight = max(0.0, min(1000.0, float(weight)))
    conn = get_db()
    try:
        if not conn.execute('SELECT 1 FROM strength_sessions WHERE id=?', (session_id,)).fetchone():
            return None
        n = conn.execute('SELECT COUNT(*) FROM strength_sets WHERE session_id=? AND exercise_key=?',
                         (session_id, key)).fetchone()[0]
        cur = conn.execute('''INSERT INTO strength_sets
            (session_id, exercise_key, exercise_name, track, set_no, reps, weight_kg,
             target_reps, target_weight, logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (session_id, key, ex['name'], ex['track'], n + 1, reps, weight,
             ex['reps'], ex['weight_kg'], _now()))
        conn.commit()
        row = conn.execute('SELECT * FROM strength_sets WHERE id=?', (cur.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def delete_set(set_id):
    conn = get_db()
    try:
        r = conn.execute('SELECT session_id, exercise_key FROM strength_sets WHERE id=?', (set_id,)).fetchone()
        if not r:
            return False
        conn.execute('DELETE FROM strength_sets WHERE id=?', (set_id,))
        # Renumber what's left of that exercise so set numbers stay 1..n.
        rows = conn.execute('''SELECT id FROM strength_sets WHERE session_id=? AND exercise_key=?
                               ORDER BY set_no, id''', (r['session_id'], r['exercise_key'])).fetchall()
        for i, row in enumerate(rows, 1):
            conn.execute('UPDATE strength_sets SET set_no=? WHERE id=?', (i, row['id']))
        conn.commit()
        return True
    finally:
        conn.close()


def finish_session(session_id):
    conn = get_db()
    try:
        conn.execute('UPDATE strength_sessions SET finished_at=? WHERE id=? AND finished_at IS NULL',
                     (_now(), session_id))
        conn.commit()
    finally:
        conn.close()


def delete_session(session_id):
    conn = get_db()
    try:
        conn.execute('DELETE FROM strength_sets WHERE session_id=?', (session_id,))
        conn.execute('DELETE FROM strength_sessions WHERE id=?', (session_id,))
        conn.commit()
    finally:
        conn.close()


def _all_sets(conn):
    return [dict(r) for r in conn.execute('''
        SELECT st.*, ss.date, ss.started_at, ss.finished_at
        FROM strength_sets st JOIN strength_sessions ss ON ss.id = st.session_id
        ORDER BY ss.date, st.session_id, st.id''')]


def _minutes(a, b):
    try:
        return max(0, int(round((datetime.strptime(b, TS) - datetime.strptime(a, TS)).total_seconds() / 60)))
    except (TypeError, ValueError):
        return None


def session_detail(session_id):
    """A workout with its sets grouped by exercise, totals and any records set."""
    conn = get_db()
    try:
        s = conn.execute('SELECT * FROM strength_sessions WHERE id=?', (session_id,)).fetchone()
        if not s:
            return None
        s = dict(s)
        all_sets = _all_sets(conn)
    finally:
        conn.close()
    mine = [x for x in all_sets if x['session_id'] == session_id]
    # Best estimated max per exercise from every earlier workout, for records.
    before = {}
    for x in all_sets:
        if (x['date'], x['session_id']) < (s['date'], session_id) and x['track'] == 'load':
            before[x['exercise_key']] = max(before.get(x['exercise_key'], 0), e1rm(x['weight_kg'], x['reps']))
    groups, order = {}, []
    for x in mine:
        g = groups.get(x['exercise_key'])
        if not g:
            g = groups[x['exercise_key']] = {'key': x['exercise_key'], 'name': x['exercise_name'],
                                            'track': x['track'], 'sets': [], 'volume': 0.0,
                                            'reps': 0, 'best_e1rm': 0.0, 'record': False}
            order.append(x['exercise_key'])
        g['sets'].append(x)
        g['reps'] += x['reps']
        if x['track'] == 'load':
            g['volume'] += x['weight_kg'] * x['reps']
            g['best_e1rm'] = max(g['best_e1rm'], e1rm(x['weight_kg'], x['reps']))
    for k in order:
        g = groups[k]
        prev = before.get(k)
        g['record'] = bool(g['track'] == 'load' and prev and g['best_e1rm'] > prev + 0.01)
    s['exercises'] = [groups[k] for k in order]
    s['volume'] = sum(g['volume'] for g in s['exercises'])
    s['set_count'] = len(mine)
    s['rep_count'] = sum(x['reps'] for x in mine)
    s['minutes'] = _minutes(s['started_at'], s['finished_at'] or (mine[-1]['logged_at'] if mine else None))
    s['records'] = [g['name'] for g in s['exercises'] if g['record']]
    return s


# ── Progress ──────────────────────────────────────────────────────────────────

def stats(today=None, weeks=8):
    """Everything the Strength page and Home tile show about progress."""
    today = today or date.today()
    plan = get_plan()
    conn = get_db()
    try:
        all_sets = _all_sets(conn)
        sessions = [dict(r) for r in conn.execute('SELECT * FROM strength_sessions ORDER BY date, id')]
    finally:
        conn.close()

    by_session = {}
    for x in all_sets:
        by_session.setdefault(x['session_id'], []).append(x)
    # A workout counts once at least one set was logged.
    done = [s for s in sessions if s['id'] in by_session]

    def vol(rows):
        return sum(r['weight_kg'] * r['reps'] for r in rows if r['track'] == 'load')

    # Weekly totals, Monday to Sunday, oldest first.
    this_week = _week_start(today)
    week_rows = []
    for i in range(weeks - 1, -1, -1):
        ws = this_week - timedelta(weeks=i)
        we = ws + timedelta(days=6)
        lo, hi = ws.strftime('%Y-%m-%d'), we.strftime('%Y-%m-%d')
        rows = [x for x in all_sets if lo <= x['date'] <= hi]
        week_rows.append({
            'start': lo, 'label': ws.strftime('%d %b'),
            'volume': round(vol(rows)), 'sets': len(rows), 'reps': sum(r['reps'] for r in rows),
            'sessions': len({r['session_id'] for r in rows}),
            'by_ex': {p['key']: round(vol([r for r in rows if r['exercise_key'] == p['key']])) for p in plan},
        })
    cur, prev = week_rows[-1], week_rows[-2] if len(week_rows) > 1 else None
    change = None
    if prev and prev['volume']:
        change = round((cur['volume'] - prev['volume']) / prev['volume'] * 100)

    planned = len(training_days())
    # Weeks in a row with every planned workout done (this week counts once complete).
    streak = 0
    if planned:
        counts = {}
        for s in done:
            ws = _week_start(datetime.strptime(s['date'], '%Y-%m-%d').date())
            counts[ws] = counts.get(ws, 0) + 1
        w = this_week if counts.get(this_week, 0) >= planned else this_week - timedelta(weeks=1)
        while counts.get(w, 0) >= planned:
            streak += 1
            w -= timedelta(weeks=1)

    # Per exercise, in plan order.
    exercises = []
    for p in plan:
        rows = [x for x in all_sets if x['exercise_key'] == p['key']]
        sess_ids = []
        for x in rows:
            if x['session_id'] not in sess_ids:
                sess_ids.append(x['session_id'])
        per_session = []
        for sid in sess_ids:
            rs = [x for x in rows if x['session_id'] == sid]
            top = max(rs, key=lambda r: (r['weight_kg'], r['reps']))
            per_session.append({
                'session_id': sid, 'date': rs[0]['date'], 'sets': rs,
                'top_weight': top['weight_kg'], 'top_reps': top['reps'],
                'best_e1rm': max(e1rm(r['weight_kg'], r['reps']) for r in rs),
                'max_reps': max(r['reps'] for r in rs),
                'volume': vol(rs), 'reps': sum(r['reps'] for r in rs),
            })
        last = per_session[-1] if per_session else None
        first = per_session[0] if per_session else None
        if p['track'] == 'load':
            metric = [round(s['best_e1rm'], 1) for s in per_session]
        else:
            metric = [s['max_reps'] for s in per_session]
        best = max(metric) if metric else None
        gain = None
        if first and last and metric and metric[0]:
            gain = round((metric[-1] - metric[0]) / metric[0] * 100)
        # Ready to add weight: last time, every planned set hit the target reps
        # at (or above) the planned weight.
        ready = False
        if last and p['track'] == 'load' and p['increment_kg'] > 0:
            hits = [r for r in last['sets'] if r['reps'] >= p['reps'] and r['weight_kg'] >= p['weight_kg'] - 0.01]
            ready = len(hits) >= p['sets']
        week_lo = this_week.strftime('%Y-%m-%d')
        exercises.append({
            'key': p['key'], 'name': p['name'], 'track': p['track'], 'plan': p,
            'sessions': len(per_session), 'last': last,
            'best_weight': max((r['weight_kg'] for r in rows), default=None),
            'best_metric': best, 'metric': metric[-12:], 'gain_pct': gain,
            'history': [{'date': s['date'], 'value': m, 'top': '%s × %d' % (_kg(s['top_weight']), s['top_reps'])}
                        for s, m in list(zip(per_session, metric))[-20:]],
            'first_metric': metric[0] if metric else None, 'last_metric': metric[-1] if metric else None,
            'week_volume': round(vol([r for r in rows if r['date'] >= week_lo])),
            'week_reps': sum(r['reps'] for r in rows if r['date'] >= week_lo),
            'total_volume': round(vol(rows)),
            'ready': ready, 'next_weight': round(p['weight_kg'] + p['increment_kg'], 2),
        })

    recent = []
    for s in reversed(done[-10:]):
        rs = by_session[s['id']]
        recent.append({
            'id': s['id'], 'date': s['date'], 'day': datetime.strptime(s['date'], '%Y-%m-%d').strftime('%a %d %b'),
            'finished': bool(s['finished_at']), 'volume': round(vol(rs)), 'sets': len(rs),
            'reps': sum(r['reps'] for r in rs),
            'minutes': _minutes(s['started_at'], s['finished_at'] or rs[-1]['logged_at']),
            'exercises': len({r['exercise_key'] for r in rs}),
        })

    nxt = next_training_day(today)
    return {
        'plan': plan,
        'days': training_days(),
        'day_names': DAY_NAMES,
        'rest_between': rest_between(),
        'is_training_day': today.weekday() in training_days(),
        'open_session': open_session(today),
        'done_today': any(s['date'] == today.strftime('%Y-%m-%d') and s['finished_at'] for s in done),
        'next_day': DAY_NAMES[nxt.weekday()] if nxt else None,
        'day_list': ', '.join(DAY_NAMES[d][:3] for d in training_days()),
        'next_date': nxt.strftime('%Y-%m-%d') if nxt else None,
        'weeks': week_rows,
        'week': cur, 'last_week': prev, 'change_pct': change,
        'planned': planned, 'streak': streak,
        'exercises': exercises,
        'recent': recent,
        'total_sessions': len(done),
        'total_volume': round(vol(all_sets)),
        'total_reps': sum(x['reps'] for x in all_sets),
        'last_date': done[-1]['date'] if done else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

def register(app):
    app.jinja_env.filters['kg'] = _kg

    @app.route('/strength')
    def strength_index():
        return render_template('strength.html', active_page='strength', s=stats())

    @app.route('/strength/plan', methods=['POST'])
    def strength_plan_save():
        save_plan(request.form)
        flash('Plan saved.', 'success')
        return redirect(url_for('strength_index') + '#plan')

    @app.route('/strength/plan/<key>/bump', methods=['POST'])
    def strength_plan_bump(key):
        bump_weight(key, 1 if request.form.get('dir', 'up') == 'up' else -1)
        return redirect(request.form.get('next') or url_for('strength_index'))

    @app.route('/strength/start', methods=['POST'])
    def strength_start():
        # Any day is allowed; the page warns before starting on a rest day.
        return redirect(url_for('strength_session', session_id=start_session()))

    @app.route('/strength/session/<int:session_id>')
    def strength_session(session_id):
        detail = session_detail(session_id)
        if not detail:
            return redirect(url_for('strength_index'))
        return render_template('strength_session.html', active_page='strength', w=detail,
                               plan=get_plan(), rest_between=rest_between())

    @app.route('/api/strength/session/<int:session_id>/set', methods=['POST'])
    def strength_api_log_set(session_id):
        d = request.get_json(silent=True) or {}
        try:
            row = log_set(session_id, d.get('key'), d.get('reps'), d.get('weight'))
        except (TypeError, ValueError):
            row = None
        if not row:
            return jsonify({'ok': False, 'error': 'Could not save that set.'}), 400
        return jsonify({'ok': True, 'set': row})

    @app.route('/api/strength/set/<int:set_id>/delete', methods=['POST'])
    def strength_api_delete_set(set_id):
        return jsonify({'ok': delete_set(set_id)})

    @app.route('/strength/session/<int:session_id>/finish', methods=['POST'])
    def strength_finish(session_id):
        finish_session(session_id)
        return redirect(url_for('strength_session', session_id=session_id))

    @app.route('/strength/session/<int:session_id>/delete', methods=['POST'])
    def strength_delete(session_id):
        delete_session(session_id)
        flash('Workout deleted.', 'success')
        return redirect(url_for('strength_index'))
