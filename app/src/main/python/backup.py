"""Backup export / import.

A backup is a .zip holding:
  hector.db                      — a consistent snapshot of the whole database
  ingredient_images/<file>       — every uploaded ingredient / recipe photo
  backup.json                    — small manifest (format version, timestamp)

Export uses SQLite's online backup API, so it is safe while the step counter
is writing. Import validates the archive, keeps a copy of the current database
next to it (hector.db.pre-import), then restores the snapshot in place and runs
the normal schema migrations so older backups load into newer app versions.
"""
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

import database

FORMAT_VERSION = 1
DB_NAME = 'hector.db'
IMAGES_PREFIX = 'ingredient_images/'
# Tables a file must contain to be accepted as a Hector database.
REQUIRED_TABLES = {'settings', 'ingredients', 'meals', 'meal_ingredients',
                   'daily_steps', 'diary_entries'}


class BackupError(Exception):
    pass


def export_zip(images_dir):
    """Return (bytes, filename) for a full backup archive."""
    tmp_dir = tempfile.mkdtemp(prefix='hector-export-')
    try:
        snap_path = os.path.join(tmp_dir, DB_NAME)
        src = database.get_db()
        dst = sqlite3.connect(snap_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap_path, DB_NAME)
            if images_dir and os.path.isdir(images_dir):
                for name in sorted(os.listdir(images_dir)):
                    path = os.path.join(images_dir, name)
                    if os.path.isfile(path):
                        # Photos are already compressed — store them as-is.
                        zf.write(path, IMAGES_PREFIX + name, zipfile.ZIP_STORED)
            zf.writestr('backup.json', json.dumps({
                'app': 'hector',
                'format': FORMAT_VERSION,
                'created': datetime.now().isoformat(timespec='seconds'),
            }, indent=2))
        stamp = datetime.now().strftime('%Y-%m-%d_%H%M')
        return buf.getvalue(), f'hector-backup-{stamp}.zip'
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _check_db(path):
    try:
        conn = sqlite3.connect(path)
        try:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise BackupError('The database in this backup is damaged.')
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        raise BackupError('This file does not contain a valid Hector database.')
    missing = REQUIRED_TABLES - tables
    if missing:
        raise BackupError('This is not a Hector backup (missing: %s).' % ', '.join(sorted(missing)))


def import_zip(file_obj, images_dir):
    """Restore a backup archive. Returns a dict with counts for the UI.

    Accepts either a .zip produced by export_zip() or a bare hector.db file
    (e.g. copied from the desktop version)."""
    tmp_dir = tempfile.mkdtemp(prefix='hector-import-')
    try:
        upload_path = os.path.join(tmp_dir, 'upload')
        file_obj.save(upload_path)

        snap_path = os.path.join(tmp_dir, DB_NAME)
        image_members = []
        zf = None
        if zipfile.is_zipfile(upload_path):
            zf = zipfile.ZipFile(upload_path)
            names = zf.namelist()
            if DB_NAME not in names:
                zf.close()
                raise BackupError('This zip has no hector.db inside.')
            with zf.open(DB_NAME) as src, open(snap_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            for n in names:
                base = os.path.basename(n)
                # Only flat files under ingredient_images/ — never trust paths.
                if n.startswith(IMAGES_PREFIX) and base and n == IMAGES_PREFIX + base:
                    image_members.append((n, base))
        else:
            shutil.move(upload_path, snap_path)

        try:
            _check_db(snap_path)

            # Keep the current database so a bad import can be undone by hand.
            live = database.DB_PATH
            if os.path.exists(live):
                safety = sqlite3.connect(live + '.pre-import')
                cur = database.get_db()
                try:
                    cur.backup(safety)
                finally:
                    cur.close()
                    safety.close()

            # Restore in place via the backup API (handles WAL and open readers).
            src = sqlite3.connect(snap_path)
            dst = database.get_db()
            try:
                # A WAL destination rejects a backup whose page size differs;
                # drop out of WAL for the copy (init_db() turns it back on).
                try:
                    dst.execute('PRAGMA journal_mode=DELETE')
                except sqlite3.OperationalError:
                    pass
                src.backup(dst)
            finally:
                dst.close()
                src.close()

            images = 0
            if zf is not None and image_members:
                os.makedirs(images_dir, exist_ok=True)
                for member, base in image_members:
                    with zf.open(member) as s, open(os.path.join(images_dir, base), 'wb') as d:
                        shutil.copyfileobj(s, d)
                    images += 1
        finally:
            if zf is not None:
                zf.close()

        # Bring an older backup's schema up to date.
        database.init_db()

        conn = database.get_db()
        try:
            counts = {
                'ingredients': conn.execute('SELECT COUNT(*) FROM ingredients').fetchone()[0],
                'recipes': conn.execute('SELECT COUNT(*) FROM meals').fetchone()[0],
                'diary': conn.execute('SELECT COUNT(*) FROM diary_entries').fetchone()[0],
                'days': conn.execute('SELECT COUNT(*) FROM daily_steps').fetchone()[0],
            }
        finally:
            conn.close()
        counts['images'] = images
        return counts
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
