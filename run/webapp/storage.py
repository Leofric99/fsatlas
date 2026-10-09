"""Owns persistence of user settings and saved items (bookmarked flights/searches) in
two small JSON files. Ported as-is from run/web_gui.py so both the old and new
frontends share the same on-disk files/format during the migration.
"""
import json
import os
import secrets
from datetime import datetime, timezone

# Defaults to run/ (one level up from run/webapp/) so a plain checkout keeps settings
# where the original http.server app already puts them; FSATLAS_DATA_DIR overrides both.
DATA_DIR = os.environ.get("FSATLAS_DATA_DIR") or os.path.dirname(os.path.dirname(__file__))

SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
DEFAULT_SETTINGS = {"theme": "dark", "simbrief_pilot_id": "", "scenery_overlay": False, "airports_overlay": True}

SAVED_ITEMS_FILE = os.path.join(DATA_DIR, 'saved_items.json')
DEFAULT_SAVED_ITEMS = {"saved_flights": [], "saved_searches": []}

SCENERY_FILE = os.path.join(DATA_DIR, 'installed_scenery.json')
DEFAULT_SCENERY = {"sceneries": [], "imported_at": None, "unmatched_count": 0, "errors": []}


def normalize_saved_flight(flight):
    """Ensure `saved_at` exists. Tags are not a supported feature - any legacy `tags`
    field from an older saved_items.json is dropped rather than carried forward.
    """
    flight.pop("tags", None)
    if not isinstance(flight.get("saved_at"), str) or not flight["saved_at"]:
        flight["saved_at"] = datetime.now(timezone.utc).isoformat()
    return flight


def normalize_saved_search(search):
    """Ensure a saved-search dict has an id/description/filters/saved_at."""
    if not isinstance(search.get("id"), str) or not search["id"]:
        search["id"] = secrets.token_urlsafe(8)
    if not isinstance(search.get("description"), str):
        search["description"] = ""
    search["description"] = search["description"].strip()
    if not isinstance(search.get("filters"), (dict, list)):
        search["filters"] = {"kind": "group", "logic": "AND", "children": []}
    if not isinstance(search.get("saved_at"), str) or not search["saved_at"]:
        search["saved_at"] = datetime.now(timezone.utc).isoformat()
    return search


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        settings = {}
    if settings.get("theme") not in ("dark", "light"):
        settings["theme"] = DEFAULT_SETTINGS["theme"]
    if not isinstance(settings.get("simbrief_pilot_id"), str):
        settings["simbrief_pilot_id"] = DEFAULT_SETTINGS["simbrief_pilot_id"]
    if not isinstance(settings.get("scenery_overlay"), bool):
        settings["scenery_overlay"] = DEFAULT_SETTINGS["scenery_overlay"]
    if not isinstance(settings.get("airports_overlay"), bool):
        settings["airports_overlay"] = DEFAULT_SETTINGS["airports_overlay"]
    return settings


def save_settings(settings):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f)


def load_saved_items():
    try:
        with open(SAVED_ITEMS_FILE, "r", encoding="utf-8") as f:
            items = json.load(f)
    except (OSError, json.JSONDecodeError):
        items = {}
    if not isinstance(items.get("saved_flights"), list):
        items["saved_flights"] = []
    if not isinstance(items.get("saved_searches"), list):
        items["saved_searches"] = []
    items["saved_flights"] = [normalize_saved_flight(f) for f in items["saved_flights"] if isinstance(f, dict)]
    items["saved_searches"] = [normalize_saved_search(s) for s in items["saved_searches"] if isinstance(s, dict)]
    return items


def save_saved_items(items):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SAVED_ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f)


def load_scenery():
    try:
        with open(SCENERY_FILE, "r", encoding="utf-8") as f:
            scenery = json.load(f)
    except (OSError, json.JSONDecodeError):
        scenery = {}
    if not isinstance(scenery.get("sceneries"), list):
        scenery["sceneries"] = []
    if not isinstance(scenery.get("imported_at"), str):
        scenery["imported_at"] = None
    if not isinstance(scenery.get("unmatched_count"), int):
        scenery["unmatched_count"] = 0
    if not isinstance(scenery.get("errors"), list):
        scenery["errors"] = []
    return scenery


def save_scenery(scenery):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SCENERY_FILE, "w", encoding="utf-8") as f:
        json.dump(scenery, f)


def ensure_data_files():
    """Create settings.json / saved_items.json / installed_scenery.json with defaults on
    first run. Migrates a legacy 'saved_flights' list out of settings.json into the new
    file, if found.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SETTINGS_FILE):
        save_settings(dict(DEFAULT_SETTINGS))
    if not os.path.exists(SCENERY_FILE):
        save_scenery(dict(DEFAULT_SCENERY))
    if not os.path.exists(SAVED_ITEMS_FILE):
        legacy_flights = []
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw_settings = json.load(f)
        except (OSError, json.JSONDecodeError):
            raw_settings = {}
        if isinstance(raw_settings.get("saved_flights"), list):
            legacy_flights = raw_settings.pop("saved_flights")
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(raw_settings, f)
        save_saved_items({"saved_flights": legacy_flights, "saved_searches": []})


def flight_key(flight):
    """Stable identity for a route record - shared with the client-side `flightKey` in
    app.js so save/unsave requests and the "already saved" check agree on duplicates.
    """
    return "|".join(str(flight.get(field, "")) for field in ("reg", "flight", "dep", "arr", "date"))
