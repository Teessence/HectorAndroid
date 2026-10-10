"""Bridge for writing the phone's step count into Hector's daily_steps table.

Called from the Android step-counter service. Replaces the old Garmin sync:
each raw counter reading adds the newly walked steps to the day (source='android').
A number typed in by hand becomes the day's new starting point; later steps add on top.
"""
import json
from datetime import datetime


def log_walk(start_at, end_at, steps):
    """Record a continuous walk ('YYYY-MM-DD HH:MM:SS' bounds) detected by the
    step service. Purely a log — daily step totals are untouched."""
    from database import get_db
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        return False
    conn = get_db()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO walking_sessions(start_at, end_at, steps) VALUES (?, ?, ?)',
            (str(start_at), str(end_at), steps))
        conn.commit()
        return True
    finally:
        conn.close()


# The last raw counter reading is stored *in the database* (settings table), so
# it's part of every backup. After uninstall → install → import, the first new
# reading is compared against the reading saved at export time and every step
# walked in between is counted.
SENSOR_STATE_KEY = 'step_sensor_state'
CONTINUITY_S = 30 * 60      # a reading from yesterday this recent still counts
MAX_DELTA = 100_000         # sanity cap for one update


def record_reading(date_str, raw, boot):
    """Take a raw step-counter reading (steps since boot) for date_str
    (YYYY-MM-DD) and add the steps walked since the previous reading to that
    day. Returns the day's total. A number typed in by hand (source='manual')
    is kept as the starting point and new steps are added to it."""
    # Imported lazily so mobile_main.configure() has already set the DB path.
    from database import get_db
    try:
        raw, boot = int(raw), int(boot)
    except (TypeError, ValueError):
        return 0
    now = datetime.now()

    conn = get_db()
    try:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (SENSOR_STATE_KEY,)).fetchone()
        try:
            prev = json.loads(row['value']) if row and row['value'] else None
        except ValueError:
            prev = None

        delta = 0
        if prev:
            same_boot = prev.get('boot') == boot and raw >= int(prev.get('raw', 0))
            if prev.get('day') == date_str:
                # Same boot: the difference. Rebooted since: everything since boot.
                delta = raw - int(prev['raw']) if same_boot else raw
            elif same_boot:
                # Day rolled over between two close readings: count them today.
                try:
                    at = datetime.strptime(prev.get('at', ''), '%Y-%m-%d %H:%M:%S')
                    if 0 <= (now - at).total_seconds() <= CONTINUITY_S:
                        delta = raw - int(prev['raw'])
                except ValueError:
                    pass
        # No previous reading (fresh install with no import): this one is the baseline.
        delta = max(0, min(delta, MAX_DELTA))

        ts = now.strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)',
                     (SENSOR_STATE_KEY, json.dumps({'day': date_str, 'raw': raw, 'boot': boot, 'at': ts})))

        # New steps are always added on top of what's stored — including a
        # number typed in by hand, which acts as the corrected starting point
        # (the row stays marked 'manual').
        day = conn.execute('SELECT steps FROM daily_steps WHERE date=?', (date_str,)).fetchone()
        current = int(day['steps'] or 0) if day else 0
        if delta > 0:
            conn.execute('''
                INSERT INTO daily_steps(date, steps, fetched_at, is_locked, source)
                VALUES (?, ?, ?, 0, 'android')
                ON CONFLICT(date) DO UPDATE SET
                    steps      = daily_steps.steps + excluded.steps,
                    fetched_at = excluded.fetched_at
            ''', (date_str, delta, ts))
            current += delta
        conn.commit()
        return current
    finally:
        conn.close()
