"""Walks, built on the fly from the per-minute step log (step_minutes).

A walk is a run of minutes with steps where no pause between them is longer
than the user's "pause" setting. It counts as a walk once it's at least the
"minimum length" long at the "minimum pace". Because walks are worked out
from the minutes every time, changing the settings regroups all past walks.

The newest run is the *active* walk while the last step is more recent than
the pause setting; it shows when it will end if you stop moving.
"""
import time
from datetime import datetime, timedelta

from database import get_db, get_setting, get_current_weight, calories_per_step

EPOCH = datetime(1970, 1, 1)

# (setting key, label, default, lowest allowed)
WALK_SETTINGS = [
    ('walk_min_minutes', 'Minimum walk length (minutes)', 5, 1),
    ('walk_gap_minutes', 'Longest pause inside a walk (minutes)', 2, 1),
    ('walk_min_pace', 'Minimum pace (steps per minute)', 40, 0),
]


def get_walk_settings():
    out = {}
    for key, _, default, lowest in WALK_SETTINGS:
        try:
            v = float(get_setting(key) or default)
        except (TypeError, ValueError):
            v = default
        out[key] = max(lowest, v)
    return out


def _local(minute):
    return EPOCH + timedelta(minutes=minute)


def _segments(rows, gap):
    """Group (minute, steps) rows into runs; a pause of more than `gap` idle
    minutes between two active minutes starts a new run."""
    runs, cur = [], None
    for m, s in rows:
        if cur and m - cur['last'] - 1 <= gap:
            cur['last'] = m
            cur['steps'] += s
        else:
            cur = {'first': m, 'last': m, 'steps': s}
            runs.append(cur)
    return runs


def _describe(run, weights):
    start, end = _local(run['first']), _local(run['last'] + 1)
    minutes = run['last'] - run['first'] + 1
    day = start.strftime('%Y-%m-%d')
    if day not in weights:
        weights[day] = get_current_weight(day)
    kcal = run['steps'] * calories_per_step(weights[day])
    return {
        'date': start.strftime('%a %d %b %Y'),
        'day': day,
        'start_at': start.strftime('%Y-%m-%d %H:%M:%S'),
        'start': start.strftime('%H:%M'),
        'end': end.strftime('%H:%M'),
        'minutes': minutes,
        'steps': run['steps'],
        'per_min': int(round(run['steps'] / minutes)),
        'per_hour': int(round(run['steps'] / minutes * 60)),
        'kcal': int(round(kcal)),
        'kcal_per_min': round(kcal / minutes, 1),
        'kcal_per_hour': int(round(kcal / minutes * 60)),
    }


def get_walks(limit=200):
    """Returns (active, walks, settings). `active` is the walk in progress or
    None; `walks` are finished qualifying walks, newest first."""
    cfg = get_walk_settings()
    gap = cfg['walk_gap_minutes']
    conn = get_db()
    try:
        rows = conn.execute(
            'SELECT minute, steps FROM step_minutes WHERE steps > 0 ORDER BY minute').fetchall()
    finally:
        conn.close()
    runs = _segments([(r['minute'], r['steps']) for r in rows], gap)

    def qualifies(run):
        minutes = run['last'] - run['first'] + 1
        return minutes >= cfg['walk_min_minutes'] and run['steps'] / minutes >= cfg['walk_min_pace']

    # Active: the newest run, while the last step is within the pause window.
    active = None
    try:
        last_step_ms = int(float(get_setting('walk_last_step_ms') or 0))
    except (TypeError, ValueError):
        last_step_ms = 0
    gap_ms = int(gap * 60_000)
    now_ms = int(time.time() * 1000)
    weights = {}
    if runs and last_step_ms and now_ms - last_step_ms <= gap_ms:
        run = runs.pop()
        active = _describe(run, weights)
        minutes = active['minutes']
        active.update({
            'qualifies': qualifies(run),
            'minutes_to_go': max(0, int(round(cfg['walk_min_minutes'] - minutes))),
            'last_step_ms': last_step_ms,
            'ends_at_ms': last_step_ms + gap_ms,
        })

    finished = [r for r in reversed(runs) if qualifies(r)][:limit]
    walks = [_describe(r, weights) for r in finished]
    return active, walks, cfg
