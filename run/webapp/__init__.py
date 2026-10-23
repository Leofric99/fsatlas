"""Flask frontend for FSAtlas - app factory. Runs alongside the original http.server
implementation (run/web_gui.py) until parity is confirmed; start with
``python -m run.webapp``.
"""
from flask import Flask

from run.webapp import data, storage


def create_app():
    app = Flask(__name__)
    # Flight CSVs and exported databases can be hundreds of MB; allow generous uploads to the import drop zone.
    app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024 * 1024

    # Load/cache the flight dataset once at startup, not per-request.
    data.load()
    storage.ensure_data_files()

    from run.webapp.routes import bp
    app.register_blueprint(bp)

    return app
