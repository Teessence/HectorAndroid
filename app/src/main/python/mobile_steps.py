"""Bridge for writing the phone's step count into Hector's daily_steps table.

Called from the Android step-counter service. Replaces the old Garmin sync:
each update upserts today's step total with source='android'. A day that the
user manually edited (source='manual') is left alone, mirroring the old rule.
"""
from datetime import datetime


def set_today_steps(date_str, steps):
    """Upsert steps for date_str (YYYY-MM-DD). Returns True if written."""
    # Imported lazily so mobile_main.configure() has already set the DB path.
    from database import get_db
    try:
        steps = int(steps)
    except (TypeError, ValueError):
        return False
    if steps < 0:
        return False

    conn = get_db()
    try:
        row = conn.execute(
            'SELECT source FROM daily_steps WHERE date=?', (date_str,)
        ).fetchone()
        if row and row['source'] == 'manual':
            return False  # never clobber a manual edit
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('''
            INSERT INTO daily_steps(date, steps, fetched_at, is_locked, source)
            VALUES (?, ?, ?, 0, 'android')
            ON CONFLICT(date) DO UPDATE SET
                steps      = excluded.steps,
                fetched_at = excluded.fetched_at,
                source     = 'android'
        ''', (date_str, steps, ts))
        conn.commit()
        return True
    finally:
        conn.close()
