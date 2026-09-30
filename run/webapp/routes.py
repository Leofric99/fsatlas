"""Thin HTTP routes - filtering/aggregation logic lives in data.py, persistence logic
lives in storage.py. Expected "not found"/"invalid input" cases return (payload, status)
tuples rather than being caught with try/except.
"""
import json
import os
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

from run import simbrief_api
from run.webapp import data, storage

bp = Blueprint('main', __name__)


def _static_asset_version():
    """Latest mtime of app.js/app.css, used to cache-bust their URLs - a fresh Docker
    build resets COPY'd files' mtimes, so this changes automatically on every deploy
    without needing a manually-bumped version string.
    """
    static_folder = current_app.static_folder or ''
    mtimes = [
        os.path.getmtime(path)
        for path in (os.path.join(static_folder, name) for name in ('app.js', 'app.css'))
        if os.path.exists(path)
    ]
    return int(max(mtimes)) if mtimes else 0


def _parse_filters():
    """The filter tree travels as a JSON-encoded query string (it's an arbitrary AND/OR
    tree, not flat key=value pairs). Missing/malformed input just means "no filters".
    """
    raw = request.args.get('filters')
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


@bp.get('/')
def index():
    return render_template('index.html', asset_version=_static_asset_version())


@bp.get('/api/meta')
def meta():
    settings = storage.load_settings()
    return jsonify({
        "columns": data.get_columns(),
        "map_types": data.get_map_types(),
        "theme": settings["theme"],
        "simbrief_pilot_id": settings["simbrief_pilot_id"],
        "scenery_overlay": settings["scenery_overlay"],
        "airports_overlay": settings["airports_overlay"],
    })


@bp.get('/api/airports')
def get_airports():
    airports, count = data.get_airports(_parse_filters())
    return jsonify({"airports": airports, "count": count})


@bp.get('/api/flights')
def get_flights():
    iata = request.args.get('iata', '')
    if not iata:
        return {"error": "iata query param is required"}, 400
    return jsonify(data.get_flights(iata, _parse_filters()))


@bp.get('/api/settings')
def get_settings():
    return jsonify(storage.load_settings())


@bp.post('/api/settings')
def post_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"error": "Invalid settings payload"}, 400
    settings = storage.load_settings()
    if "theme" in payload:
        if payload["theme"] not in ("dark", "light"):
            return {"error": "Invalid settings payload"}, 400
        settings["theme"] = payload["theme"]
    if "simbrief_pilot_id" in payload:
        pilot_id = payload["simbrief_pilot_id"]
        if not isinstance(pilot_id, str):
            return {"error": "Invalid settings payload"}, 400
        settings["simbrief_pilot_id"] = pilot_id.strip()
    if "scenery_overlay" in payload:
        overlay = payload["scenery_overlay"]
        if not isinstance(overlay, bool):
            return {"error": "Invalid settings payload"}, 400
        settings["scenery_overlay"] = overlay
    if "airports_overlay" in payload:
        overlay = payload["airports_overlay"]
        if not isinstance(overlay, bool):
            return {"error": "Invalid settings payload"}, 400
        settings["airports_overlay"] = overlay
    storage.save_settings(settings)
    return jsonify({"ok": True})


@bp.get('/api/saved-flights')
def get_saved_flights():
    return jsonify(storage.load_saved_items()["saved_flights"])


@bp.post('/api/saved-flights')
def post_saved_flight():
    flight = request.get_json(silent=True)
    if not isinstance(flight, dict):
        return {"error": "Invalid flight payload"}, 400
    items = storage.load_saved_items()
    key = storage.flight_key(flight)
    existing = next((f for f in items["saved_flights"] if storage.flight_key(f) == key), None)
    if existing:
        flight.setdefault("saved_at", existing.get("saved_at"))
    flight = storage.normalize_saved_flight(flight)
    saved = [f for f in items["saved_flights"] if storage.flight_key(f) != key]
    saved.append(flight)
    items["saved_flights"] = saved
    storage.save_saved_items(items)
    return jsonify({"ok": True, "saved_flights": saved})


@bp.delete('/api/saved-flights')
def delete_saved_flight():
    flight = request.get_json(silent=True)
    if not isinstance(flight, dict):
        return {"error": "Invalid flight payload"}, 400
    items = storage.load_saved_items()
    key = storage.flight_key(flight)
    items["saved_flights"] = [f for f in items["saved_flights"] if storage.flight_key(f) != key]
    storage.save_saved_items(items)
    return jsonify({"ok": True, "saved_flights": items["saved_flights"]})


@bp.get('/api/saved-searches')
def get_saved_searches():
    return jsonify(storage.load_saved_items()["saved_searches"])


@bp.post('/api/saved-searches')
def post_saved_search():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"error": "Invalid saved search payload"}, 400
    description = payload.get("description", "")
    filter_tree = payload.get("filters")
    if not isinstance(description, str) or not description.strip():
        return {"error": "Invalid saved search payload"}, 400
    if not isinstance(filter_tree, (dict, list)):
        return {"error": "Invalid saved search payload"}, 400
    items = storage.load_saved_items()
    search = storage.normalize_saved_search({"description": description, "filters": filter_tree})
    items["saved_searches"].append(search)
    storage.save_saved_items(items)
    return jsonify({"ok": True, "saved_searches": items["saved_searches"]})


@bp.delete('/api/saved-searches')
def delete_saved_search():
    payload = request.get_json(silent=True)
    search_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(search_id, str):
        return {"error": "Invalid saved search payload"}, 400
    items = storage.load_saved_items()
    items["saved_searches"] = [s for s in items["saved_searches"] if s["id"] != search_id]
    storage.save_saved_items(items)
    return jsonify({"ok": True, "saved_searches": items["saved_searches"]})


@bp.post('/api/simbrief/verify')
def simbrief_verify():
    payload = request.get_json(silent=True) or {}
    ok, message = simbrief_api.verify_pilot_id(str(payload.get("pilot_id", "")))
    return jsonify({"ok": ok, "message": message})


@bp.post('/api/simbrief/export')
def simbrief_export():
    flight = request.get_json(silent=True)
    if not isinstance(flight, dict):
        return {"error": "Invalid flight payload"}, 400
    pilot_id = storage.load_settings().get("simbrief_pilot_id", "")
    return jsonify({"url": simbrief_api.build_export_url(flight, pilot_id)})


@bp.get('/api/scenery')
def get_scenery():
    return jsonify(storage.load_scenery())


@bp.get('/api/scenery/airports')
def get_scenery_airports():
    airports = sorted(data.get_airport_directory(), key=lambda airport: (airport['city'], airport['name']))
    return jsonify(airports)


@bp.post('/api/scenery/import')
def import_scenery():
    """Body: {candidates: [{icao?, lat?, lon?, source}]} - raw scenery locations already
    scanned client-side (see app.js scanSceneryFiles), matched here against the real
    airport directory and written to installed_scenery.json, REPLACING any previous
    import wholesale (the client is expected to warn the user about this before calling).
    """
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        return {"error": "Invalid scenery import payload"}, 400
    matched, match_errors = data.match_scenery(payload["candidates"])
    scan_errors = payload.get("scan_errors", [])
    if not isinstance(scan_errors, list):
        return {"error": "Invalid scenery scan errors"}, 400
    scan_errors = [
        {
            "severity": "warning",
            "stage": str(error.get("stage") or "scan")[:80],
            "source": str(error.get("source") or "Unknown package")[:300],
            "message": str(error.get("message") or "A scenery scan issue occurred.")[:500],
            "details": str(error.get("details") or "")[:2000],
        }
        for error in scan_errors if isinstance(error, dict)
    ]
    errors = scan_errors + match_errors
    scenery = {
        "sceneries": matched,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "unmatched_count": len(match_errors),
        "errors": errors,
    }
    storage.save_scenery(scenery)
    return jsonify({
        "ok": True, "matched": len(matched), "unmatched": len(match_errors),
        "sceneries": matched, "errors": errors,
    })


@bp.post('/api/scenery/resolve')
def resolve_scenery_error():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return {"error": "Invalid scenery resolution payload"}, 400
    source = payload.get("source")
    airport_id = str(payload.get("airport_id") or "").strip().upper()
    if not isinstance(source, str) or not source or not airport_id:
        return {"error": "A scenery package and airport are required"}, 400

    scenery = storage.load_scenery()
    errors = scenery.get("errors", [])
    if not any(error.get("source") == source and error.get("stage") == "airport_match" for error in errors):
        return {"error": "That scenery package is not awaiting airport resolution"}, 404

    airport = next((
        airport for airport in data.get_airport_directory()
        if airport_id in (str(airport.get("icao") or "").upper(), str(airport.get("iata") or "").upper())
    ), None)
    if airport is None:
        return {"error": "Selected airport was not found in the loaded flight data"}, 404

    sceneries = {item.get("icao") or item.get("iata"): item for item in scenery.get("sceneries", [])}
    sceneries[airport.get("icao") or airport["iata"]] = airport
    scenery["sceneries"] = list(sceneries.values())
    scenery["errors"] = [
        error for error in errors
        if not (error.get("source") == source and error.get("stage") == "airport_match")
    ]
    scenery["unmatched_count"] = sum(error.get("stage") == "airport_match" for error in scenery["errors"])
    scenery["imported_at"] = datetime.now(timezone.utc).isoformat()
    storage.save_scenery(scenery)
    return jsonify({"ok": True, **scenery})
