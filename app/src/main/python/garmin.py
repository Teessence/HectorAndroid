"""Garmin integration — disabled stub for the Android build.

The Android app fills daily steps from the phone's own hardware step counter
(see mobile_steps.py), so the Garmin Connect integration is removed. This stub
keeps app.py's `import garmin` and its calls valid without pulling in the
`garminconnect` dependency. Everything reports "unavailable / disabled".
"""


def is_available():
    return False


def import_error():
    return "Garmin is disabled in the Android build (steps come from the phone sensor)."


def is_enabled():
    return False


def get_refresh_minutes():
    return 30


def get_status():
    return {
        'enabled': False,
        'available': False,
        'import_error': import_error(),
        'email': '',
        'has_password': False,
        'refresh_min': 30,
        'last_run': None,
        'last_error': '',
        'next_refresh_seconds': None,
    }


def sync_all_pending(force_today=False):
    return {'ok': False, 'fetched': [], 'skipped_manual': [],
            'errors': [import_error()], 'count': 0}


def maybe_sync():
    return None
