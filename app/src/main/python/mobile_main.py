"""Android launcher for the Hector Flask app.

`configure()` is called once from the Android Application object with the path
to writable app storage. It points the Flask app + database at that storage
(templates, static files and the SQLite DB are seeded there on first launch by
the Kotlin side). `start_server()` then boots Flask on localhost so the WebView
can load it.
"""
import os
import threading

_server = None
_thread = None
_configured = False


def configure(data_dir):
    """Point Hector at writable storage. Must run before app/database import."""
    global _configured
    db_path = os.path.join(data_dir, 'hector.db')
    os.environ['HECTOR_DB_PATH'] = db_path
    os.environ['HECTOR_STATIC_DIR'] = os.path.join(data_dir, 'static')
    os.environ['HECTOR_IMAGES_DIR'] = os.path.join(data_dir, 'static', 'ingredient_images')
    os.environ['HECTOR_TEMPLATE_DIR'] = os.path.join(data_dir, 'templates')
    # Make sure the image upload target exists.
    os.makedirs(os.environ['HECTOR_IMAGES_DIR'], exist_ok=True)
    # Pin the DB path directly on the module as well, so it can never fall back
    # to the read-only source dir no matter when database.py first gets imported
    # (a fallback there would create a fresh, EMPTY database).
    import database
    database.DB_PATH = db_path
    _configured = True
    return True


def start_server(port=8765):
    """Start Flask on 127.0.0.1:port in a background thread. Idempotent."""
    global _server, _thread
    if _server is not None:
        return True
    # Imported here so the path env vars set in configure() are already in place.
    from app import create_app
    from werkzeug.serving import make_server

    app = create_app()
    # make_server binds the socket synchronously, so once this returns the port
    # is already accepting connections and the WebView can load immediately.
    _server = make_server('127.0.0.1', int(port), app, threaded=True)
    _thread = threading.Thread(target=_server.serve_forever, name='hector-flask', daemon=True)
    _thread.start()
    return True
