"""Handles flight files dropped onto the GUI's Import panel, and builds database exports.

Each uploaded .csv/.json/.db is saved into the import folder, run through the shared import
engine (run/flight_import.py), then filed away: `import/processed/` on success or
`import/failed/` if it could not be imported. Database files (.db) are deleted instead.
The import folder lives under the data directory (`FSATLAS_DATA_DIR`, default `run/`).
"""
import os
import shutil
import sqlite3
import tempfile
import threading
from datetime import datetime

from werkzeug.utils import secure_filename

from run import flight_import, flightdb
from run.webapp import storage

IMPORT_DIR = os.path.join(storage.DATA_DIR, "import")
PROCESSED_DIR = os.path.join(IMPORT_DIR, "processed")
FAILED_DIR = os.path.join(IMPORT_DIR, "failed")
ALLOWED_EXTENSIONS = {".csv", ".json", ".db", ".sqlite", ".sqlite3"}

# Only one import writes to the database at a time.
_import_lock = threading.Lock()


def _file_away(path, folder):
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, os.path.basename(path))
    shutil.move(path, destination)
    return destination


def process_upload(file_storage):
    """Import one uploaded file and return a JSON-serialisable result dict:
    {"file", "ok": True, <import counts>} or {"file", "ok": False, "error"}.
    """
    original = os.path.basename(file_storage.filename or "")
    extension = os.path.splitext(original)[1].lower()
    result = {"file": original or "(unnamed)"}
    if extension not in ALLOWED_EXTENSIONS:
        return {**result, "ok": False, "error": "Only .csv, .json and .db files can be imported."}

    safe = secure_filename(original) or f"upload{extension}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(IMPORT_DIR, exist_ok=True)
    path = os.path.join(IMPORT_DIR, f"{stamp}_{safe}")
    file_storage.save(path)

    # Database files are large and already live wherever the user exported them, so they
    # are deleted after the import instead of being kept as a second copy.
    keep_copy = extension not in flight_import.DATABASE_EXTENSIONS

    def file_away(folder):
        if keep_copy:
            _file_away(path, folder)
        else:
            os.remove(path)

    try:
        with _import_lock:
            report = flight_import.import_file(path)
    except (ValueError, OSError, sqlite3.Error) as exc:
        file_away(FAILED_DIR)
        return {**result, "ok": False, "error": str(exc) or exc.__class__.__name__}

    file_away(PROCESSED_DIR)
    return {**result, "ok": True, **{k: v for k, v in report.items() if k not in ("file", "kind", "dry_run")}}


def create_export():
    """Write a snapshot of the flight database to a temporary folder. Returns
    (folder, file_path, download_name); the caller deletes the folder when it is done.
    """
    if not flightdb.exists():
        raise FileNotFoundError("There is no flight database to export yet.")
    folder = tempfile.mkdtemp(prefix="fsatlas-export-")
    name = f"fsatlas-flights-{datetime.now():%Y%m%d}.db"
    path = os.path.join(folder, name)
    try:
        with _import_lock:  # don't snapshot half-way through an import
            flightdb.export_copy(path)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return folder, path, name
