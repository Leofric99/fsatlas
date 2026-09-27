"""Browser-based UI for FSAtlas.

Run with ``python -m run``, ``python -m run.web_gui``, or the installed ``fsatlas``
command (see README for setting it up as a uv tool).
"""

import argparse
import json
import os
import secrets
import sys
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from run import config, data_loader, filtering, mapping, simbrief_api
from run.single_instance import SingleInstance, running_url

LOGO_FILE = os.path.join(os.path.dirname(__file__), 'images', 'FSAtlas Logo.png')

# Where the two small persisted JSON files below live. Defaults to run/ (so a plain `uv run`/
# `python -m run` checkout keeps settings.json exactly where it's always lived) but can be
# pointed elsewhere - the Docker image sets this to a dedicated /data directory so both files
# can be bind-mounted from the host without shadowing the app code in /app/run (see
# docker-compose.yml).
DATA_DIR = os.environ.get("FSATLAS_DATA_DIR") or os.path.dirname(__file__)

# Persisted UI settings (theme preference and the SimBrief Pilot ID/username used to pre-fill
# exports) - tracked in git with default values, but local writes are excluded via
# `git update-index --skip-worktree` so a user's runtime settings never show up as an
# uncommitted change.
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
DEFAULT_SETTINGS = {"theme": "dark", "simbrief_pilot_id": ""}

# Saved flights (bookmarked routes) and saved searches (named filter sets) - split out of
# settings.json into their own file since they're user data rather than app preferences.
SAVED_ITEMS_FILE = os.path.join(DATA_DIR, 'saved_items.json')
DEFAULT_SAVED_ITEMS = {"saved_flights": [], "saved_searches": []}


def normalize_saved_flight(flight):
    """Sanitize a saved-flight dict's `tags` (deduped, trimmed, sorted case-insensitively)
    and ensure `saved_at` exists - called on load (for entries saved before these fields
    existed) and again just before writing so both callers agree on the shape.
    """
    tags = flight.get("tags", [])
    if not isinstance(tags, list):
        tags = []
    seen = {}
    for tag in tags:
        if isinstance(tag, str) and tag.strip():
            seen.setdefault(tag.strip().lower(), tag.strip())
    flight["tags"] = sorted(seen.values(), key=str.lower)
    if not isinstance(flight.get("saved_at"), str) or not flight["saved_at"]:
        flight["saved_at"] = datetime.now(timezone.utc).isoformat()
    return flight


def normalize_saved_search(search):
    """Ensure a saved-search dict has an id/description/filters/saved_at - mirrors
    normalize_saved_flight's role, called on load and again just before writing.
    """
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


def ensure_data_files():
    """Create settings.json / saved_items.json with defaults on first run (of a native
    checkout, or a fresh Docker bind-mounted data dir). Migrates a legacy 'saved_flights'
    list out of settings.json (where earlier versions stored it) into the new file, if found.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SETTINGS_FILE):
        save_settings(dict(DEFAULT_SETTINGS))
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
    map.html so save/unsave requests and the "already saved" check agree on duplicates.
    """
    return "|".join(str(flight.get(field, "")) for field in ("reg", "flight", "dep", "arr", "date"))


def column_example(series):
    """Pick a real sample value from a column to preview the expected filter format."""
    sample = series.dropna()
    if sample.empty:
        return ""
    value = sample.iloc[0]
    if pd.api.types.is_float_dtype(series):
        value = float(value)
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    if pd.api.types.is_integer_dtype(series):
        return str(int(value))
    return str(value).strip()[:24]


def format_value(value, series):
    """Format a single value the same way column_example formats its sample."""
    if pd.api.types.is_float_dtype(series):
        value = float(value)
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    if pd.api.types.is_integer_dtype(series):
        return str(int(value))
    return str(value).strip()


def column_options(series, limit=15):
    """Return sorted distinct values for low-cardinality columns, so the filter UI can use a
    dropdown instead of a free-text field. Returns None when there are no or too many options.
    """
    uniques = series.dropna().unique().tolist()
    if not pd.api.types.is_numeric_dtype(series):
        uniques = [v for v in uniques if str(v).strip() != ""]
    # Check the count before sorting/formatting so high-cardinality columns (which get
    # discarded anyway) don't pay for a sort of every distinct value.
    if not (0 < len(uniques) < limit):
        return None
    uniques.sort()
    return [format_value(v, series) for v in uniques]


# Columns hidden entirely from the filter dropdown.
HIDDEN_COLUMNS = {"timestamp_read"}

# Non-directional columns get bucketed into a couple of small logical groups.
COMPANY_COLUMNS = {"owner", "calsign", "flight_number"}
EQUIPMENT_COLUMNS = {"reg", "type", "type_icao"}
OTHER_COLUMNS = {"distance", "rough_flight_time"}


def build_columns(df):
    """Build the filter column list, grouped into "Company", "Equipment", "Departure",
    "Arrival", a synthetic "Departure or Arrival X" per dep_/arr_ column pair (matches rows
    where either side matches), and "Other" - in that order. Timestamp is hidden entirely.
    """
    ungrouped, company, equipment, departure, arrival, combined, other = [], [], [], [], [], [], []
    seen_dep = {}
    for column in df.columns:
        if column in HIDDEN_COLUMNS:
            continue

        display_name = config.COLUMN_DISPLAY_NAMES.get(column, column)
        entry = {
            "id": column, "name": display_name,
            "numeric": bool(pd.api.types.is_numeric_dtype(df[column])),
            "example": column_example(df[column]),
            "options": column_options(df[column]),
        }

        if column.startswith("dep_"):
            entry["group"] = "Departure"
            entry["name"] = display_name.removeprefix("Departure ")
            departure.append(entry)
            seen_dep[column.removeprefix("dep_")] = column
            continue

        if column.startswith("arr_"):
            entry["group"] = "Arrival"
            entry["name"] = display_name.removeprefix("Arrival ")
            arrival.append(entry)
            dep_col = seen_dep.get(column.removeprefix("arr_"))
            if dep_col:
                dep_name = config.COLUMN_DISPLAY_NAMES.get(dep_col, dep_col)
                base_name = dep_name.removeprefix("Departure ")
                combined_series = pd.concat([df[dep_col], df[column]], ignore_index=True)
                combined.append({
                    "id": f"combined:{dep_col}:{column}",
                    "name": base_name,
                    "numeric": bool(pd.api.types.is_numeric_dtype(df[dep_col])),
                    "example": column_example(df[dep_col]),
                    "options": column_options(combined_series),
                    "group": "Departure or Arrival",
                })
            continue

        if column in COMPANY_COLUMNS:
            entry["group"] = "Company"
            company.append(entry)
        elif column in EQUIPMENT_COLUMNS:
            entry["group"] = "Equipment"
            equipment.append(entry)
        elif column in OTHER_COLUMNS:
            entry["group"] = "Other"
            other.append(entry)
        else:
            entry["group"] = None
            ungrouped.append(entry)

    return ungrouped + company + equipment + departure + arrival + combined + other


def route_records(df, source):
    matches = df[(df["dep_airport_iata"] == source) | (df["arr_airport_iata"] == source)]
    if matches.empty:
        return []
    result = pd.DataFrame({
        "dep": matches["dep_airport_iata"].astype(str),
        "arr": matches["arr_airport_iata"].astype(str),
        "flight": matches["flight_number"].astype(str),
        "type": matches["type"].astype(str),
        "callsign": matches["calsign"].astype(str),
        "type_icao": matches["type_icao"].astype(str),
        "reg": matches["reg"].astype(str),
        "dep_icao": matches["dep_airport_icao"].astype(str),
        "arr_icao": matches["arr_airport_icao"].astype(str),
        "dep_city": matches["dep_airport_city"].astype(str),
        "arr_city": matches["arr_airport_city"].astype(str),
        "airline": matches["owner"].astype(str),
        "date": matches["timestamp_read"].astype(str).str.slice(0, 10),
        # NaN (unrecognised type_icao / no cruise speed on import) -> JSON null,
        # not float('nan') which isn't valid JSON.
        "flight_time_hours": matches["rough_flight_time"].astype(object).where(matches["rough_flight_time"].notna(), None),
        "distance": matches["distance"].astype(object).where(matches["distance"].notna(), None),
    })
    return result.to_dict("records")


class AtlasState:
    def __init__(self):
        self.df = data_loader.load_data()
        self.airport_counts = data_loader.get_airport_destination_counts(self.df)
        self.views = {}

    def create_view(self, filters, map_type):
        filtered_df = filtering.apply_filters(self.df, filters)
        view_id = secrets.token_urlsafe(12)
        self.views[view_id] = (filtered_df, map_type)
        if len(self.views) > 20:
            self.views.pop(next(iter(self.views)))
        return view_id, len(filtered_df)


def index_html(columns, settings):
    columns_json = json.dumps(columns)
    map_types_json = json.dumps(list(config.TILES.keys()))
    theme = settings.get('theme', 'dark')
    theme_attr = ' data-theme="light"' if theme == 'light' else ''
    pilot_id_json = json.dumps(settings.get('simbrief_pilot_id', ''))
    return f"""<!doctype html>
<html lang="en"{theme_attr}>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Flightsim Atlas</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg-grad: radial-gradient(circle at 18% -10%, rgba(63, 208, 255, 0.14), transparent 45%),
                 radial-gradient(circle at 100% 0%, rgba(124, 92, 255, 0.12), transparent 42%),
                 #0b0d10;
      --surface: rgba(22, 25, 29, 0.68);
      --surface-solid: rgba(23, 27, 32, 0.9);
      --border: rgba(255, 255, 255, 0.09);
      --text: #f5f6f7;
      --muted: rgba(245, 246, 247, 0.6);
      --accent: #3fd0ff;
      --accent-2: #7c5cff;
      --danger: #ff8078;
      --shadow: 0 10px 34px rgba(0, 0, 0, 0.42);
      --radius: 14px;
    }}
    :root[data-theme="light"] {{
      color-scheme: light;
      --bg-grad: radial-gradient(circle at 18% -10%, rgba(10, 132, 255, 0.10), transparent 45%),
                 radial-gradient(circle at 100% 0%, rgba(124, 92, 255, 0.08), transparent 42%),
                 #eef1f5;
      --surface: rgba(255, 255, 255, 0.66);
      --surface-solid: rgba(255, 255, 255, 0.92);
      --border: rgba(15, 23, 42, 0.09);
      --text: #14181d;
      --muted: rgba(20, 24, 29, 0.6);
      --accent: #0a84ff;
      --accent-2: #7c5cff;
      --danger: #e5484d;
      --shadow: 0 10px 30px rgba(15, 23, 42, 0.14);
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif; }}
    body {{
      margin: 0; min-height: 100vh;
      background: var(--bg-grad); background-attachment: fixed; color: var(--text);
      transition: background .3s ease, color .3s ease;
    }}
    .toolbar {{
      background: var(--surface);
      backdrop-filter: blur(22px) saturate(160%);
      -webkit-backdrop-filter: blur(22px) saturate(160%);
      border: 1px solid var(--border);
      padding: 14px 20px; display: grid; gap: 12px; min-width: 0;
      border-radius: 20px;
      box-shadow: var(--shadow);
      /* Fixed (not sticky) - .toolbar ends up being the only element left in the document's
         normal flow (the map iframe, modals, toast, etc are all position:fixed already), so
         its containing block is barely taller than itself, leaving position:sticky almost no
         room to actually stick before un-sticking again on the very next scroll tick. Fixed
         positioning pins it solidly regardless, with max-height + overflow-y below letting a
         long filter list scroll *inside* the toolbar instead of relying on page-level scroll,
         which no longer exists at all. (Scrolling .toolbar's own overflow, rather than
         constraining #filters-wrap's grid row to a 1fr/min-height:0 track, avoids that track
         squashing every filter row down to a few px instead of actually overflowing -
         .filters-tab, positioned absolute against .toolbar, isn't affected by this internal
         scroll since out-of-flow descendants don't move with an ancestor's own overflow.) */
      position: fixed; top: 14px; left: 20px; right: 20px; z-index: 5;
      max-height: calc(100vh - 28px);
      overflow-y: auto;
    }}
    .toolbar-head {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; row-gap: 8px; }}
    .brand {{ display: flex; align-items: center; gap: 10px; }}
    h1 {{ font-size: 17px; margin: 0; font-weight: 600; letter-spacing: -0.01em; }}
    /* Taken out of the toolbar-head flow and tracked by positionLogo() (see script below) so it
       can float centered above the first filter row's column select, regardless of where that
       select ends up as the filter fields resize/wrap/collapse. */
    .logo {{
      height: 44px; width: auto; display: block;
      position: fixed; z-index: 6; pointer-events: none;
      transition: left .2s ease, top .2s ease;
    }}
    .map-type {{ display: none; }}
    select, input, button {{
      font: inherit; color: inherit; background: var(--surface-solid);
      border: 1px solid var(--border); border-radius: 10px; min-height: 34px; box-sizing: border-box;
      transition: border-color .15s ease, background .15s ease, transform .1s ease, box-shadow .15s ease;
    }}
    select, input {{ padding: 5px 10px; width: 100%; }}
    select:focus, input:focus, button:focus-visible {{
      outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent);
    }}
    select:disabled {{ opacity: 0.6; cursor: not-allowed; }}
    button {{
      cursor: pointer; padding: 5px 14px; font-weight: 600; white-space: nowrap; flex-shrink: 0;
      background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #fff; border: none;
      box-shadow: 0 4px 14px color-mix(in srgb, var(--accent) 35%, transparent);
    }}
    button:hover {{ transform: translateY(-1px); filter: brightness(1.08); }}
    button:active {{ transform: translateY(0); }}
    button.icon {{
      width: 30px; padding: 0; background: var(--surface-solid); color: var(--text); font-size: 16px;
      line-height: 1; box-shadow: none; border: 1px solid var(--border);
    }}
    button.icon:hover {{ background: var(--border); transform: none; filter: none; }}
    button.remove {{ color: var(--danger); }}
    button.danger {{
      padding: 5px 10px; white-space: nowrap;
      background: linear-gradient(135deg, var(--danger), color-mix(in srgb, var(--danger) 55%, black));
      box-shadow: 0 4px 14px color-mix(in srgb, var(--danger) 35%, transparent);
    }}
    /* .map-type used to carry this (pushing itself + everything after it right, leaving the
       logo alone on the left) - now that it's always hidden, #apply is the first surviving
       toolbar-head button, so the auto-margin moves here to keep the same grouping. */
    #apply {{ margin-left: auto; }}
    #theme-toggle {{
      width: 34px; height: 34px; padding: 0; background: var(--surface-solid); border: 1px solid var(--border);
      box-shadow: none; display: flex; align-items: center; justify-content: center; color: var(--text);
    }}
    #theme-toggle:hover {{ background: var(--border); transform: none; filter: none; }}
    #theme-toggle svg {{ width: 17px; height: 17px; transition: transform .3s ease; }}
    /* Small icon button, matching the map-style/theme/saved/settings group it now sits
       alongside, rather than a full text button. */
    #save-search-btn {{
      width: 34px; height: 34px; padding: 0; background: var(--surface-solid); border: 1px solid var(--border);
      box-shadow: none; display: flex; align-items: center; justify-content: center; color: var(--text);
    }}
    #save-search-btn:hover {{ background: var(--border); transform: none; filter: none; }}
    #save-search-btn svg {{ width: 16px; height: 16px; }}
    #saved-toggle {{
      width: 34px; height: 34px; padding: 0; background: var(--surface-solid); border: 1px solid var(--border);
      box-shadow: none; display: flex; align-items: center; justify-content: center; color: var(--text);
    }}
    #saved-toggle:hover {{ background: var(--border); transform: none; filter: none; }}
    #saved-toggle svg {{ width: 17px; height: 17px; }}
    #settings-toggle {{
      width: 34px; height: 34px; padding: 0; background: var(--surface-solid); border: 1px solid var(--border);
      box-shadow: none; display: flex; align-items: center; justify-content: center; color: var(--text);
    }}
    #settings-toggle:hover {{ background: var(--border); transform: none; filter: none; }}
    #settings-toggle svg {{ width: 17px; height: 17px; }}
    /* Map type lives as a single button (matches theme/saved/settings' shape) that opens a
       small popover instead of a permanent "Map Type: X" label + <select> - same on desktop
       and mobile. */
    #map-type-fab-wrap {{ display: flex; position: relative; flex-shrink: 0; }}
    #map-type-fab {{
      width: 34px; height: 34px; padding: 0;
      background: var(--surface-solid); color: var(--text);
      border: 1px solid var(--border); box-shadow: none;
      display: flex; align-items: center; justify-content: center;
    }}
    #map-type-fab:hover {{ background: var(--border); transform: none; filter: none; }}
    #map-type-fab svg {{ width: 16px; height: 16px; }}
    .map-type-menu {{
      display: none; position: absolute; top: calc(100% + 8px); right: 0; min-width: 152px;
      padding: 6px; border-radius: 12px; background: var(--surface-solid);
      border: 1px solid var(--border); box-shadow: var(--shadow); z-index: 8;
    }}
    .map-type-menu.open {{ display: block; }}
    .map-type-menu-item {{
      display: block; width: 100%; text-align: left; padding: 8px 10px; border-radius: 8px;
      background: transparent; color: var(--text); box-shadow: none; font-size: 13px; font-weight: 500;
    }}
    .map-type-menu-item:hover {{ background: var(--border); transform: none; filter: none; }}
    .map-type-menu-item.active {{ color: var(--accent); font-weight: 700; }}
    /* Flight count: hidden until a real filter is applied, then a transient bubble bottom-
       left instead of a permanent inline count - same on desktop and mobile. */
    #flights-toast {{
      display: flex; align-items: center; gap: 10px; position: fixed; z-index: 30;
      left: 16px; bottom: calc(16px + env(safe-area-inset-bottom, 0px));
      padding: 10px 14px; border-radius: 14px; overflow: hidden;
      background: color-mix(in srgb, var(--accent) 24%, var(--surface-solid) 76%);
      border: 1px solid color-mix(in srgb, var(--accent) 45%, var(--border));
      backdrop-filter: blur(18px) saturate(160%); -webkit-backdrop-filter: blur(18px) saturate(160%);
      box-shadow: var(--shadow); color: var(--text); font-size: 13px; font-weight: 600;
      opacity: 0; transform: translateY(10px); pointer-events: none;
      transition: opacity .2s ease, transform .2s ease;
    }}
    #flights-toast.open {{ opacity: 1; transform: translateY(0); pointer-events: auto; }}
    #flights-toast-close {{
      width: 18px; height: 18px; padding: 0; border-radius: 50%; flex-shrink: 0;
      background: transparent; color: var(--text); box-shadow: none; border: none; opacity: 0.7;
      display: flex; align-items: center; justify-content: center; font-size: 14px; line-height: 1;
    }}
    #flights-toast-close:hover {{ opacity: 1; transform: none; filter: none; background: transparent; }}
    .flights-toast-bar {{ position: absolute; left: 0; bottom: 0; height: 3px; width: 100%; background: var(--accent); transform-origin: left; transform: scaleX(0); }}
    #flights-toast.open .flights-toast-bar {{ animation: flights-toast-shrink 3s linear forwards; }}
    @keyframes flights-toast-shrink {{ from {{ transform: scaleX(1); }} to {{ transform: scaleX(0); }} }}
    .modal-overlay {{
      position: fixed; inset: 0; background: rgba(8, 10, 14, 0.45);
      display: none; align-items: center; justify-content: center; z-index: 50; padding: 20px;
    }}
    .modal-overlay.open {{ display: flex; }}
    .modal {{
      background: var(--surface-solid); border: 1px solid var(--border); border-radius: var(--radius);
      box-shadow: var(--shadow); width: 400px; max-width: 100%; padding: 18px 20px;
    }}
    .modal-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }}
    .modal-header h2 {{ font-size: 15px; margin: 0; font-weight: 600; }}
    .modal-body label {{
      display: block; font-size: 11px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase;
      color: var(--muted); margin-bottom: 6px;
    }}
    .modal-body input {{ margin-bottom: 8px; }}
    .modal-hint {{ font-size: 12px; color: var(--muted); line-height: 1.5; margin: 0 0 14px; }}
    .modal-hint a {{ color: var(--accent); }}
    .modal-actions {{ display: flex; align-items: center; gap: 10px; }}
    #simbrief-status {{ font-size: 12px; color: var(--muted); }}
    .modal-footer {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 18px; }}
    button.secondary {{ background: var(--surface-solid); color: var(--text); box-shadow: none; border: 1px solid var(--border); }}
    button.secondary:hover {{ background: var(--border); filter: none; transform: none; }}
    .connection-modal .modal-body {{ display: flex; flex-direction: column; align-items: center; text-align: center; gap: 10px; padding-top: 4px; }}
    .connection-modal svg {{ width: 40px; height: 40px; color: var(--danger); flex-shrink: 0; }}
    .modal-message {{ font-size: 13px; color: var(--muted); line-height: 1.5; margin: 0; }}
    .saved-list {{ display: flex; flex-direction: column; gap: 8px; max-height: 50vh; overflow-y: auto; }}
    .saved-empty {{ font-size: 13px; color: var(--muted); text-align: center; padding: 18px 4px; line-height: 1.5; }}
    #saved-modal .modal {{ width: 460px; }}
    /* Two independently expandable/collapsible sections (Saved Routes / Saved Searches) in
       the Saved Items modal - same grid-rows collapse trick used for #filters-wrap. */
    .saved-section {{ border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }}
    .saved-section + .saved-section {{ margin-top: 10px; }}
    .saved-section-toggle {{
      width: 100%; display: flex; align-items: center; gap: 8px; padding: 10px 12px;
      background: var(--surface-solid); border: none; border-radius: 0; box-shadow: none;
      color: var(--text); font-weight: 600; font-size: 13px; justify-content: flex-start;
    }}
    .saved-section-toggle:hover {{ background: var(--border); transform: none; filter: none; }}
    .saved-section-chevron {{ width: 14px; height: 14px; flex-shrink: 0; transition: transform .2s ease; }}
    .saved-section-toggle.collapsed .saved-section-chevron {{ transform: rotate(-90deg); }}
    .saved-section-count {{ margin-left: auto; font-size: 11px; color: var(--muted); font-weight: 500; }}
    /* Plain display:none toggle rather than the grid-rows/overflow animation used elsewhere
       (e.g. #filters-wrap) - that trick left a sliver of the "Sort by" label peeking through
       the collapsed box here, since flex children don't reliably shrink to a 0-height track. */
    .saved-section-wrap.collapsed {{ display: none; }}
    .saved-section-inner {{ padding: 12px; }}
    .saved-toolbar {{ display: flex; justify-content: flex-end; margin-bottom: 10px; }}
    .saved-sort-label {{ display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }}
    .saved-sort-label select {{ font-size: 12px; padding: 6px 8px; margin-bottom: 0; }}
    .saved-row {{
      display: flex; flex-direction: column; gap: 8px;
      padding: 10px; border: 1px solid var(--border); border-radius: 10px;
      background: var(--surface-solid); cursor: pointer; transition: background .15s ease;
    }}
    .saved-row:hover {{ background: var(--border); }}
    .saved-row-top {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }}
    .saved-route {{ font-weight: 600; font-size: 13px; line-height: 1.35; }}
    .saved-type {{ font-size: 11px; color: var(--muted); }}
    .saved-remove {{
      width: 26px; height: 26px; padding: 0; background: transparent; color: var(--danger);
      box-shadow: none; border: none; font-size: 16px; line-height: 1; flex-shrink: 0;
    }}
    .saved-remove:hover {{ background: color-mix(in srgb, var(--danger) 18%, transparent); filter: none; transform: none; }}
    .saved-tags {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
    .tag-chip {{
      display: inline-flex; align-items: center; gap: 2px;
      background: color-mix(in srgb, var(--accent) 16%, transparent); color: var(--accent);
      border-radius: 999px; padding: 3px 4px 3px 10px; font-size: 11px; font-weight: 500;
    }}
    .tag-chip button {{
      background: none; border: none; color: inherit; cursor: pointer; box-shadow: none;
      padding: 0 6px; font-size: 12px; line-height: 1;
    }}
    .tag-chip button:hover {{ opacity: 0.7; transform: none; filter: none; }}
    .tag-add-btn {{
      display: inline-flex; align-items: center; gap: 3px; background: transparent;
      border: 1px dashed var(--border); color: var(--muted); border-radius: 999px;
      padding: 3px 10px; font-size: 11px; box-shadow: none; cursor: pointer;
    }}
    .tag-add-btn:hover {{ border-color: var(--accent); color: var(--accent); background: transparent; transform: none; filter: none; }}
    .tag-editor {{ display: none; flex-direction: column; gap: 6px; }}
    .tag-editor.open {{ display: flex; }}
    .tag-editor input {{ font-size: 12px; padding: 7px 9px; margin-bottom: 0; }}
    .tag-suggestions {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .tag-suggestion {{
      font-size: 11px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border);
      background: var(--surface); color: var(--text); cursor: pointer; box-shadow: none;
    }}
    .tag-suggestion:hover {{ background: var(--border); transform: none; filter: none; }}
    .tag-suggestion.create {{ border-style: dashed; color: var(--accent); border-color: var(--accent); }}
    .tag-suggestion-empty {{ font-size: 11px; color: var(--muted); padding: 4px 0; }}
    #filters-wrap {{ display: grid; grid-template-rows: 1fr; min-width: 0; transition: grid-template-rows .28s ease; }}
    #filters-wrap.collapsed {{ grid-template-rows: 0fr; }}
    #filters-wrap > #filters {{ overflow: hidden; min-height: 0; }}
    #filters, .filters-list {{ display: grid; gap: 8px; min-width: 0; }}
    .filter-row {{
      display: grid; min-width: 0; overflow-x: auto; overscroll-behavior-x: contain;
      grid-template-columns: var(--w-logic, 60px) var(--w-col, 150px) var(--w-op, 110px) minmax(var(--min-field, 90px), 1fr) auto;
      gap: 8px; align-items: center;
    }}
    .filter-row.first {{ grid-template-columns: var(--w-col, 150px) var(--w-op, 110px) minmax(var(--min-field, 110px), 1fr) auto; }}
    .filter-row.first > .logic {{ display: none; }}
    .filter-row.first .remove {{ display: none; }}
    .row-actions {{ display: flex; gap: 6px; align-items: center; }}
    .row-actions .icon {{ width: 32px; }}
    .filter-group {{
      border: 1px solid var(--border); border-radius: 12px; padding: 8px;
      background: color-mix(in srgb, var(--surface-solid) 55%, transparent);
      min-width: 0;
    }}
    .filter-group.first > .group-head > .logic {{ display: none; }}
    .filter-group.first > .group-head .remove {{ display: none; }}
    .group-head {{ display: flex; align-items: center; gap: 8px; min-width: 0; }}
    .group-head .logic {{ width: var(--w-logic, 60px); flex: none; }}
    .group-label {{ font-size: 11px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }}
    .group-head .row-actions {{ margin-left: auto; }}
    .filter-group > .filters-list {{
      margin-top: 8px; padding-left: 12px; border-left: 2px solid var(--border);
    }}
    .actions {{ display: flex; align-items: center; gap: 10px; }}
    #status {{ display: none; }}
    .toolbar.filters-collapsed {{ gap: 0; }}
    .filters-tab {{
      position: absolute;
      right: 26px;
      top: 100%;
      margin-top: -1px;
      width: 52px;
      height: 32px;
      min-height: 0;
      padding: 0;
      display: flex; align-items: center; justify-content: center;
      background: var(--surface);
      backdrop-filter: blur(22px) saturate(160%);
      -webkit-backdrop-filter: blur(22px) saturate(160%);
      border: none;
      border-radius: 0 0 16px 16px;
      box-shadow: var(--shadow);
      color: var(--muted);
      cursor: pointer;
      z-index: 4;
      transition: color .15s ease, filter .15s ease;
    }}
    .filters-tab:hover {{ color: var(--text); filter: brightness(1.18); transform: none; }}
    .filters-tab svg {{ width: 14px; height: 14px; transition: transform .28s ease; transform: rotate(180deg); }}
    .filters-tab.collapsed svg {{ transform: none; }}
    iframe {{ border: 0; position: fixed; inset: 0; width: 100%; height: 100%; background: var(--bg-grad); z-index: 1; }}
    .map-type-label {{ margin-right: 4px; }}
    #filters-menu-btn {{ display: none; }}
    #filter-menu-footer {{ display: none; }}
    .action-label {{ display: none; }}
    /* Mobile mode: the desktop filter builder (always-open grid rows + collapse tab, with
       Apply/Reset text buttons in the top bar) is replaced by a single "Add Filter" /
       "Filters (N)" button that opens the *entire* #filters tree fullscreen - every
       condition and group, grouped and ungrouped, in one scrollable list - with Reset/Apply
       fixed at the bottom of that list instead of the crowded top bar. Reuses the exact same
       DOM nodes/listeners (insert/group/remove/column/operator/value all still work
       untouched), so none of the filtering logic itself is duplicated. Kept entirely inside
       this query so desktop's inline panel + top-bar buttons are untouched. */
    @media (max-width: 760px) {{
      .toolbar {{ top: 8px; left: 8px; right: 8px; max-height: calc(100vh - 16px); border-radius: 16px; padding: 12px; gap: 8px; }}
      .toolbar-head {{ gap: 8px; flex-wrap: nowrap; }}
      .logo {{ position: static !important; height: 24px; margin: 0; flex-shrink: 0; }}
      #apply, #reset, #save-search-btn {{ display: none !important; }}
      button, select, input {{ min-height: 40px; }}
      button.icon, #theme-toggle, #saved-toggle, #settings-toggle {{ width: 34px; height: 34px; flex-shrink: 0; }}
      .row-actions .icon {{ width: 38px; }}
      #filters-wrap, .filters-tab {{ display: none !important; }}
      #filters-menu-btn {{
        display: flex; align-items: center; gap: 5px; flex: 1 1 auto; min-width: 0;
        padding: 0 8px; background: var(--surface-solid); color: var(--text);
        border: 1px solid var(--border); box-shadow: none; font-size: 11.5px;
      }}
      #filters-menu-btn svg {{ width: 13px; height: 13px; flex-shrink: 0; }}
      #filters-menu-btn span {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
      #filters-menu-btn.has-filters {{ border-color: var(--accent); color: var(--accent); }}
      #filters.mobile-editing {{
        position: fixed; inset: 0; z-index: 60; background: var(--surface-solid);
        padding: max(16px, env(safe-area-inset-top, 0px)) 16px 96px;
        overflow-y: auto; display: block;
      }}
      #filters.mobile-editing::before {{ content: 'Filters'; display: block; font-size: 15px; font-weight: 700; margin-bottom: 14px; }}
      #filters.mobile-editing .filter-row,
      #filters.mobile-editing .filter-group {{
        background: var(--surface-solid); border: 1px solid var(--border); border-radius: 12px; padding: 12px;
      }}
      #filters.mobile-editing .filter-row {{ grid-template-columns: 1fr !important; gap: 10px; }}
      #filters.mobile-editing .group-head {{ flex-wrap: wrap; gap: 8px; }}
      #filters.mobile-editing .group-head .row-actions {{ margin-left: 0; flex-basis: 100%; }}
      #filters.mobile-editing .filters-list {{ margin-top: 12px; padding-left: 14px; }}
      #filters.mobile-editing .row-actions {{ justify-content: flex-start; gap: 10px; margin-top: 4px; }}
      #filters.mobile-editing .row-actions .icon {{
        width: auto; height: 40px; padding: 0 14px; display: inline-flex; align-items: center; gap: 6px; font-size: 13px;
      }}
      /* The higher-specificity #filters.mobile-editing .icon rule above would otherwise beat
         the base rule hiding "remove" on the only/first row (so the tree can never end up
         with zero conditions) - restore that here. */
      #filters.mobile-editing .first > .row-actions .remove,
      #filters.mobile-editing .first > .group-head .row-actions .remove {{ display: none !important; }}
      #filters.mobile-editing .action-label {{ display: inline; }}
      body.filter-editing #filter-menu-footer {{
        display: flex; gap: 10px; position: fixed; left: 0; right: 0; bottom: 0; z-index: 61;
        padding: 12px 16px calc(12px + env(safe-area-inset-bottom, 0px));
        background: var(--surface-solid); border-top: 1px solid var(--border);
      }}
      #filter-menu-footer button {{ flex: 1 1 0; }}
      /* The fullscreen filter editor already covers the viewport itself, but the toolbar's
         translucent glass background otherwise still shows faintly through/around it - hide
         it outright so nothing but the map + airport dots is visible behind the editor. */
      body.filter-editing .toolbar {{ display: none; }}
    }}
  </style>
</head>

<body>
  <section class="toolbar">
    <div class="toolbar-head">
      <div class="brand">
        <img class="logo" src="/images/logo.png" alt="FSAtlas" width="1377" height="768">
      </div>
      <label class="map-type"><span class="map-type-label">Map Type</span><select id="map-type"></select></label>
      <button id="apply">Apply Filters</button>
      <button id="reset" class="danger" type="button" title="Reset filters" aria-label="Reset filters">Reset Filters</button>
      <button id="filters-menu-btn" type="button">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="4 4 20 4 14 12.5 14 19 10 21 10 12.5 4 4"></polygon></svg>
        <span id="filters-menu-label">Add Filter</span>
      </button>
      <div id="map-type-fab-wrap">
        <button id="map-type-fab" type="button" title="Map style" aria-label="Map style">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline></svg>
        </button>
        <div id="map-type-menu" class="map-type-menu"></div>
      </div>
      <button id="save-search-btn" type="button" title="Save the current filters as a search" aria-label="Save the current filters as a search">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"></path><polyline points="17 21 17 13 7 13 7 21"></polyline><polyline points="7 3 7 8 15 8"></polyline></svg>
      </button>
      <button id="theme-toggle" type="button" title="Toggle light / dark mode" aria-label="Toggle light / dark mode"></button>
      <button id="saved-toggle" type="button" title="Saved Items" aria-label="Saved Items">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>
      </button>
      <button id="settings-toggle" type="button" title="Settings" aria-label="Settings">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path></svg>
      </button>
      <span id="status"></span>
    </div>
    <div id="filters-wrap">
      <div id="filters"></div>
    </div>
    <button id="filters-tab" class="filters-tab" type="button" title="Toggle filter list" aria-label="Toggle filter list">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
    </button>
  </section>
  <div id="filter-menu-footer">
    <button id="filter-menu-reset" class="secondary" type="button">Reset</button>
    <button id="filter-menu-save-search" class="secondary" type="button">Save Search</button>
    <button id="filter-menu-apply" type="button">Apply Filters</button>
  </div>
  <div id="flights-toast">
    <span id="flights-toast-count"></span>
    <button id="flights-toast-close" type="button" aria-label="Dismiss">&times;</button>
    <div class="flights-toast-bar"></div>
  </div>
  <div id="saved-modal" class="modal-overlay">
    <div class="modal">
      <div class="modal-header">
        <h2>Saved Items</h2>
        <button id="saved-close" class="icon" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body">
        <div class="saved-section">
          <button class="saved-section-toggle" type="button" aria-expanded="true">
            <svg class="saved-section-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
            <span>Saved Routes</span>
            <span id="saved-routes-count" class="saved-section-count"></span>
          </button>
          <div class="saved-section-wrap">
            <div class="saved-section-inner">
              <div class="saved-toolbar">
                <label class="saved-sort-label">Sort by
                  <select id="saved-sort">
                    <option value="saved-desc">Saved (Newest First)</option>
                    <option value="saved-asc">Saved (Oldest First)</option>
                    <option value="distance-asc">Distance (Shortest First)</option>
                    <option value="distance-desc">Distance (Longest First)</option>
                    <option value="tag">Tag (A-Z)</option>
                  </select>
                </label>
              </div>
              <div id="saved-list" class="saved-list"></div>
            </div>
          </div>
        </div>
        <div class="saved-section">
          <button class="saved-section-toggle" type="button" aria-expanded="true">
            <svg class="saved-section-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
            <span>Saved Searches</span>
            <span id="saved-searches-count" class="saved-section-count"></span>
          </button>
          <div class="saved-section-wrap">
            <div class="saved-section-inner">
              <div id="saved-searches-list" class="saved-list"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <div id="settings-modal" class="modal-overlay">
    <div class="modal">
      <div class="modal-header">
        <h2>Settings</h2>
        <button id="settings-close" class="icon" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body">
        <label for="simbrief-pilot-id">SimBrief Pilot ID or Username</label>
        <input id="simbrief-pilot-id" type="text" placeholder="e.g. 123456 or jdoe">
        <p class="modal-hint">
          Used to pre-fill the Pilot ID when exporting a flight to SimBrief. Find yours on
          SimBrief's <a href="https://www.simbrief.com/system/profile.php#settings" target="_blank" rel="noopener">Account Settings</a> page.
        </p>
        <div class="modal-actions">
          <button id="simbrief-verify" class="secondary" type="button">Verify</button>
          <span id="simbrief-status"></span>
        </div>
      </div>
      <div class="modal-footer">
        <button id="settings-cancel" class="secondary" type="button">Cancel</button>
        <button id="settings-save" type="button">Save</button>
      </div>
    </div>
  </div>
  <div id="save-search-modal" class="modal-overlay">
    <div class="modal">
      <div class="modal-header">
        <h2>Save Search</h2>
        <button id="save-search-close" class="icon" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body">
        <label for="save-search-description">Brief Description</label>
        <input id="save-search-description" type="text" placeholder="e.g. Long-haul United flights" maxlength="120">
      </div>
      <div class="modal-footer">
        <button id="save-search-cancel" class="secondary" type="button">Cancel</button>
        <button id="save-search-confirm" type="button">Save</button>
      </div>
    </div>
  </div>
  <div id="connection-modal" class="modal-overlay">
    <div class="modal connection-modal">
      <div class="modal-header">
        <h2>Connection Lost</h2>
        <button id="connection-close" class="icon" type="button" aria-label="Close">&times;</button>
      </div>
      <div class="modal-body">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01M10.29 3.86l-8.18 14.18A2 2 0 0 0 3.82 21h16.36a2 2 0 0 0 1.71-2.96L13.71 3.86a2 2 0 0 0-3.42 0z"></path></svg>
        <p class="modal-message">Lost connection to the FSAtlas server. Make sure it is still running, then try again.</p>
      </div>
    </div>
  </div>
  <iframe id="map" title="Flight map"></iframe>
  <script>
    const columns = {columns_json};
    const mapTypes = {map_types_json};
    const filters = document.getElementById('filters');
    const mapType = document.getElementById('map-type');
    const THEME_MAP_TYPES = {{ dark: 'Dark Mode', light: 'Light Mode' }};
    let theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    // Sentinel (empty string can't collide with a real config.TILES key) for the default
    // "Use Theme" option - resolved to the real tile name on demand via effectiveMapType(),
    // so the server/tile layer never sees "Use Theme" itself.
    const USE_THEME_VALUE = '';
    mapType.add(new Option('Use Theme', USE_THEME_VALUE, true, true));
    mapTypes.forEach(name => mapType.add(new Option(name, name, false, false)));
    function effectiveMapType() {{ return mapType.value === USE_THEME_VALUE ? THEME_MAP_TYPES[theme] : mapType.value; }}

    // --- Mobile-mode detection: width-based (matches the .toolbar breakpoint above) so the
    // layout adapts the same way for a real phone, a resized window, or dev-tools device
    // emulation - deliberately not user-agent sniffing, which is unreliable and easy to spoof.
    function isMobileViewport() {{ return window.matchMedia('(max-width: 760px)').matches; }}
    let isMobile = isMobileViewport();
    document.body.classList.toggle('is-mobile', isMobile);

    // --- Map-type button: a single icon button (matches theme/saved/settings' shape) that
    // opens a tiny popover instead of a permanent "Map Type: X" label + <select> - same on
    // desktop and mobile. Reuses the exact same #map-type <select>/mapTypes array either way -
    // it's just a different UI to set the same underlying value, so applyFilters()/
    // effectiveMapType() stay the single source of truth regardless of which UI changed them.
    const mapTypeFabWrap = document.getElementById('map-type-fab-wrap');
    const mapTypeFab = document.getElementById('map-type-fab');
    const mapTypeMenu = document.getElementById('map-type-menu');

    function closeMapTypeMenu() {{ mapTypeMenu.classList.remove('open'); }}

    function buildMapTypeMenu() {{
      mapTypeMenu.replaceChildren();
      [['Use Theme', USE_THEME_VALUE], ...mapTypes.map(name => [name, name])].forEach(([label, value]) => {{
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'map-type-menu-item' + (value === mapType.value ? ' active' : '');
        item.textContent = label;
        item.addEventListener('click', () => {{
          mapType.value = value;
          applyFilters();
          closeMapTypeMenu();
        }});
        mapTypeMenu.append(item);
      }});
    }}

    mapTypeFab.addEventListener('click', e => {{
      e.stopPropagation();
      const opening = !mapTypeMenu.classList.contains('open');
      if (opening) buildMapTypeMenu();
      mapTypeMenu.classList.toggle('open', opening);
    }});
    document.addEventListener('click', e => {{ if (!mapTypeFabWrap.contains(e.target)) closeMapTypeMenu(); }});

    // --- Flight-count toast: hidden while no filter is applied, then a translucent bubble
    // bottom-left for 3s (with a shrinking timer bar) instead of a permanent inline count -
    // same on desktop and mobile. Dismissable early via its own close button.
    const flightsToast = document.getElementById('flights-toast');
    const flightsToastCount = document.getElementById('flights-toast-count');
    const flightsToastBar = document.querySelector('.flights-toast-bar');
    let flightsToastTimer = null;

    function hideFlightsToast() {{
      flightsToast.classList.remove('open');
      clearTimeout(flightsToastTimer);
    }}

    function showFlightsToast(count) {{
      flightsToastCount.textContent = count.toLocaleString() + ' Flights';
      flightsToast.classList.add('open');
      // Force the shrinking bar to restart from full width even if a toast is already showing.
      flightsToastBar.style.animation = 'none';
      void flightsToastBar.offsetWidth;
      flightsToastBar.style.animation = '';
      clearTimeout(flightsToastTimer);
      flightsToastTimer = setTimeout(hideFlightsToast, 3000);
    }}

    document.getElementById('flights-toast-close').addEventListener('click', hideFlightsToast);

    // --- Theme toggle (persists choice, re-themes the map iframe) ---
    const SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.5"></circle><path d="M12 2.5v3M12 18.5v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2.5 12h3M18.5 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"></path></svg>';
    const MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"></path></svg>';
    const themeToggle = document.getElementById('theme-toggle');
    let baseMapUrl = '';

    // --- Connection-lost detection ---
    const connectionModal = document.getElementById('connection-modal');

    function showConnectionLost() {{ connectionModal.classList.add('open'); }}
    function hideConnectionLost() {{ connectionModal.classList.remove('open'); }}

    document.getElementById('connection-close').addEventListener('click', hideConnectionLost);
    connectionModal.addEventListener('click', e => {{ if (e.target === connectionModal) hideConnectionLost(); }});

    // Also surfaces connection loss hit by the map iframe's own fetches (same-origin, so the
    // origin check just guards against unrelated postMessage senders, not a trust boundary).
    window.addEventListener('message', e => {{
      if (e.origin === window.location.origin && e.data && e.data.type === 'fsatlas:connection-lost') {{
        showConnectionLost();
      }}
    }});

    // Wraps fetch() to this server: a network-level failure (server unreachable/crashed) pops
    // the modal instead of failing silently or leaving stuck "Loading..." text; HTTP error
    // statuses still resolve normally so callers keep handling those themselves.
    async function fsatlasFetch(url, options) {{
      try {{
        const response = await fetch(url, options);
        hideConnectionLost();
        return response;
      }} catch (err) {{
        showConnectionLost();
        throw err;
      }}
    }}

    // Heartbeat - catches the server going away even when the user isn't actively doing anything.
    setInterval(() => {{ fsatlasFetch('/api/settings').catch(() => {{}}); }}, 5000);

    function withTheme(url) {{
      if (!url) return url;
      // The map iframe now fills the whole viewport behind the floating toolbar, so the
      // server needs to know how much of it is covered to keep the map's usual view in place.
      const offset = Math.round(document.querySelector('.toolbar').getBoundingClientRect().bottom);
      return url + (url.includes('?') ? '&' : '?') + 'theme=' + theme + '&offset=' + offset;
    }}

    function applyTheme(nextTheme, persist) {{
      theme = nextTheme;
      document.documentElement.dataset.theme = theme;
      themeToggle.innerHTML = theme === 'dark' ? MOON_ICON : SUN_ICON;
      if (persist) {{
        fsatlasFetch('/api/settings', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{theme}})
        }}).catch(() => {{}});
      }}
      if (mapType.value === USE_THEME_VALUE) {{
        applyFilters();
      }} else if (baseMapUrl) {{
        document.getElementById('map').src = withTheme(baseMapUrl);
      }}
    }}

    themeToggle.addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark', true));
    applyTheme(theme, false);

    // --- Saved Flights tab ---
    const savedModal = document.getElementById('saved-modal');
    const savedList = document.getElementById('saved-list');
    const savedSort = document.getElementById('saved-sort');
    let savedFlightsRaw = [];

    function escapeHtml(str) {{
      return String(str).replace(/[&<>"']/g, c => ({{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}})[c]);
    }}

    function sortedSavedFlights() {{
      const flights = [...savedFlightsRaw];
      const dist = f => (typeof f.distance === 'number' ? f.distance : null);
      const distCompare = (a, b, dir) => {{
        const da = dist(a), db = dist(b);
        if (da === null && db === null) return 0;
        if (da === null) return 1;
        if (db === null) return -1;
        return dir * (da - db);
      }};
      const savedAt = f => f.saved_at || '';
      const firstTag = f => (f.tags && f.tags.length ? f.tags[0].toLowerCase() : '\uffff');
      switch (savedSort.value) {{
        case 'distance-asc': flights.sort((a, b) => distCompare(a, b, 1)); break;
        case 'distance-desc': flights.sort((a, b) => distCompare(a, b, -1)); break;
        case 'saved-asc': flights.sort((a, b) => savedAt(a).localeCompare(savedAt(b))); break;
        case 'tag': flights.sort((a, b) => firstTag(a).localeCompare(firstTag(b))); break;
        case 'saved-desc':
        default: flights.sort((a, b) => savedAt(b).localeCompare(savedAt(a))); break;
      }}
      return flights;
    }}

    async function saveFlightTags(flight, tags) {{
      const uniqueTags = [...new Set(tags.map(t => t.trim()).filter(Boolean))];
      try {{
        await fsatlasFetch('/api/saved-flights', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{...flight, tags: uniqueTags}})
        }});
      }} catch (err) {{ /* best effort */ }}
      loadSavedFlights();
    }}

    function renderSavedFlights() {{
      const flights = sortedSavedFlights();
      document.getElementById('saved-routes-count').textContent = flights.length ? String(flights.length) : '';
      if (!flights.length) {{
        savedList.innerHTML = '<div class="saved-empty">No saved flights yet. Use the bookmark button in a flight\\'s More Info panel to save one.</div>';
        return;
      }}
      const allTags = [...new Set(savedFlightsRaw.flatMap(f => f.tags || []))].sort((a, b) => a.localeCompare(b));

      savedList.innerHTML = flights.map((f, i) => {{
        const depLabel = (f.dep_city ? escapeHtml(f.dep_city) + ' ' : '') + '(' + escapeHtml(f.dep_icao || f.dep || '?') + ')';
        const arrLabel = (f.arr_city ? escapeHtml(f.arr_city) + ' ' : '') + '(' + escapeHtml(f.arr_icao || f.arr || '?') + ')';
        const tags = f.tags || [];
        return `
          <div class="saved-row" data-idx="${{i}}">
            <div class="saved-row-top">
              <div>
                <div class="saved-route">${{depLabel}} &rarr; ${{arrLabel}}</div>
                <div class="saved-type">${{escapeHtml(f.type_icao || '')}}</div>
              </div>
              <button class="saved-remove" type="button" title="Remove" aria-label="Remove">&times;</button>
            </div>
            <div class="saved-tags">
              ${{tags.map(t => `<span class="tag-chip">${{escapeHtml(t)}}<button type="button" class="tag-remove" data-tag="${{escapeHtml(t)}}" title="Remove tag" aria-label="Remove tag ${{escapeHtml(t)}}">&times;</button></span>`).join('')}}
              <button type="button" class="tag-add-btn">+ Tag</button>
            </div>
            <div class="tag-editor">
              <input type="text" class="tag-input" placeholder="Search or create a tag...">
              <div class="tag-suggestions"></div>
            </div>
          </div>
        `;
      }}).join('');

      [...savedList.querySelectorAll('.saved-row')].forEach((row, i) => {{
        const flight = flights[i];
        row.addEventListener('click', () => viewSavedFlight(flight));
        row.querySelector('.saved-remove').addEventListener('click', async e => {{
          e.stopPropagation();
          try {{
            await fsatlasFetch('/api/saved-flights', {{
              method: 'DELETE', headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify(flight)
            }});
          }} catch (err) {{ /* best effort */ }}
          loadSavedFlights();
        }});

        [...row.querySelectorAll('.tag-remove')].forEach(btn => {{
          btn.addEventListener('click', e => {{
            e.stopPropagation();
            saveFlightTags(flight, (flight.tags || []).filter(t => t !== btn.dataset.tag));
          }});
        }});

        const addBtn = row.querySelector('.tag-add-btn');
        const editor = row.querySelector('.tag-editor');
        const input = editor.querySelector('.tag-input');
        const suggestionsEl = editor.querySelector('.tag-suggestions');

        function renderSuggestions() {{
          const query = input.value.trim().toLowerCase();
          const existing = new Set((flight.tags || []).map(t => t.toLowerCase()));
          const matches = allTags.filter(t => !existing.has(t.toLowerCase()) && (!query || t.toLowerCase().includes(query)));
          let html = matches.map(t => `<button type="button" class="tag-suggestion" data-tag="${{escapeHtml(t)}}">${{escapeHtml(t)}}</button>`).join('');
          if (query && !allTags.some(t => t.toLowerCase() === query)) {{
            html += `<button type="button" class="tag-suggestion create" data-tag="${{escapeHtml(input.value.trim())}}">+ Create "${{escapeHtml(input.value.trim())}}"</button>`;
          }}
          suggestionsEl.innerHTML = html || '<span class="tag-suggestion-empty">No matches</span>';
          [...suggestionsEl.querySelectorAll('.tag-suggestion')].forEach(sBtn => {{
            sBtn.addEventListener('click', e => {{
              e.stopPropagation();
              saveFlightTags(flight, [...(flight.tags || []), sBtn.dataset.tag]);
            }});
          }});
        }}

        addBtn.addEventListener('click', e => {{
          e.stopPropagation();
          const wasOpen = editor.classList.contains('open');
          savedList.querySelectorAll('.tag-editor').forEach(el => el.classList.remove('open'));
          if (!wasOpen) {{
            editor.classList.add('open');
            renderSuggestions();
            input.focus();
          }}
        }});
        input.addEventListener('click', e => e.stopPropagation());
        input.addEventListener('input', renderSuggestions);
        input.addEventListener('keydown', e => {{
          if (e.key === 'Enter') {{
            e.preventDefault();
            const value = input.value.trim();
            if (value) saveFlightTags(flight, [...(flight.tags || []), value]);
          }} else if (e.key === 'Escape') {{
            editor.classList.remove('open');
            input.value = '';
          }}
        }});
      }});
    }}

    async function loadSavedFlights() {{
      try {{
        const response = await fsatlasFetch('/api/saved-flights');
        savedFlightsRaw = await response.json();
        renderSavedFlights();
      }} catch (err) {{
        savedList.innerHTML = '<div class="saved-empty">Could not load saved flights.</div>';
      }}
    }}

    savedSort.addEventListener('change', renderSavedFlights);

    function viewSavedFlight(flight) {{
      savedModal.classList.remove('open');
      try {{
        document.getElementById('map').contentWindow.showSavedFlight(flight);
      }} catch (err) {{ /* map iframe isn't ready yet - ignore */ }}
    }}

    // --- Saved Searches (named filter sets, saved via the "Save Search" button below) ---
    const savedSearchesList = document.getElementById('saved-searches-list');
    let savedSearchesRaw = [];

    function renderSavedSearches() {{
      document.getElementById('saved-searches-count').textContent = savedSearchesRaw.length ? String(savedSearchesRaw.length) : '';
      if (!savedSearchesRaw.length) {{
        savedSearchesList.innerHTML = '<div class="saved-empty">No saved searches yet. Use "Save Search" in the filter bar to save one.</div>';
        return;
      }}
      savedSearchesList.innerHTML = savedSearchesRaw.map((s, i) => `
        <div class="saved-row" data-idx="${{i}}">
          <div class="saved-row-top">
            <div class="saved-route">${{escapeHtml(s.description)}}</div>
            <button class="saved-remove" type="button" title="Remove" aria-label="Remove">&times;</button>
          </div>
        </div>
      `).join('');
      [...savedSearchesList.querySelectorAll('.saved-row')].forEach((row, i) => {{
        const search = savedSearchesRaw[i];
        row.addEventListener('click', () => applySavedSearch(search));
        row.querySelector('.saved-remove').addEventListener('click', async e => {{
          e.stopPropagation();
          try {{
            await fsatlasFetch('/api/saved-searches', {{
              method: 'DELETE', headers: {{'Content-Type': 'application/json'}},
              body: JSON.stringify({{id: search.id}})
            }});
          }} catch (err) {{ /* best effort */ }}
          loadSavedSearches();
        }});
      }});
    }}

    async function loadSavedSearches() {{
      try {{
        const response = await fsatlasFetch('/api/saved-searches');
        savedSearchesRaw = await response.json();
        renderSavedSearches();
      }} catch (err) {{
        savedSearchesList.innerHTML = '<div class="saved-empty">Could not load saved searches.</div>';
      }}
    }}

    // Rebuilds the #filters DOM tree (rows/groups) from a saved {{kind, logic, children}}
    // tree - the inverse of serializeList()/serializeNode() below - then applies it.
    function buildFilterTreeNode(nodeData) {{
      if (nodeData && nodeData.kind === 'group') {{
        const group = createGroup();
        group.querySelector(':scope > .group-head > .logic').value = nodeData.logic || 'AND';
        const list = group.querySelector(':scope > .filters-list');
        (nodeData.children || []).forEach(child => list.append(buildFilterTreeNode(child)));
        return group;
      }}
      const row = createConditionRow();
      row.querySelector('.column').value = nodeData.column || '';
      updateOperators(row);
      row.querySelector('.operator').value = nodeData.operator || '';
      row.querySelector('.value').value = nodeData.value ?? '';
      row.querySelector(':scope > .logic').value = nodeData.logic || 'AND';
      return row;
    }}

    function applySavedSearch(search) {{
      savedModal.classList.remove('open');
      filters.replaceChildren();
      const children = (search.filters && search.filters.children) || [];
      if (children.length) {{
        children.forEach(child => filters.append(buildFilterTreeNode(child)));
      }} else {{
        addRow();
      }}
      refreshRows();
      applyFilters();
    }}

    // --- Save Search modal (own small modal rather than window.prompt(), which isn't
    // reliably supported/available in every browser context) ---
    const saveSearchModal = document.getElementById('save-search-modal');
    const saveSearchDescInput = document.getElementById('save-search-description');

    function openSaveSearchModal() {{
      saveSearchDescInput.value = '';
      saveSearchModal.classList.add('open');
      saveSearchDescInput.focus();
    }}
    function closeSaveSearchModal() {{ saveSearchModal.classList.remove('open'); }}

    async function confirmSaveSearch() {{
      const trimmed = saveSearchDescInput.value.trim();
      if (!trimmed) {{ saveSearchDescInput.focus(); return; }}
      const filterTree = {{kind: 'group', logic: 'AND', children: serializeList(filters)}};
      try {{
        await fsatlasFetch('/api/saved-searches', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{description: trimmed, filters: filterTree}})
        }});
      }} catch (err) {{ /* best effort */ }}
      closeSaveSearchModal();
      if (savedModal.classList.contains('open')) loadSavedSearches();
    }}

    document.getElementById('save-search-btn').addEventListener('click', openSaveSearchModal);
    document.getElementById('filter-menu-save-search').addEventListener('click', openSaveSearchModal);
    document.getElementById('save-search-close').addEventListener('click', closeSaveSearchModal);
    document.getElementById('save-search-cancel').addEventListener('click', closeSaveSearchModal);
    document.getElementById('save-search-confirm').addEventListener('click', confirmSaveSearch);
    saveSearchModal.addEventListener('click', e => {{ if (e.target === saveSearchModal) closeSaveSearchModal(); }});
    saveSearchDescInput.addEventListener('keydown', e => {{ if (e.key === 'Enter') {{ e.preventDefault(); confirmSaveSearch(); }} }});

    // Each section (Saved Routes / Saved Searches) expands/collapses independently.
    [...document.querySelectorAll('.saved-section-toggle')].forEach(btn => {{
      btn.addEventListener('click', () => {{
        const wrap = btn.nextElementSibling;
        const collapsed = wrap.classList.toggle('collapsed');
        btn.classList.toggle('collapsed', collapsed);
        btn.setAttribute('aria-expanded', String(!collapsed));
      }});
    }});

    document.getElementById('saved-toggle').addEventListener('click', () => {{
      savedModal.classList.add('open');
      loadSavedFlights();
      loadSavedSearches();
    }});
    document.getElementById('saved-close').addEventListener('click', () => savedModal.classList.remove('open'));
    savedModal.addEventListener('click', e => {{ if (e.target === savedModal) savedModal.classList.remove('open'); }});

    // --- Settings modal (SimBrief Pilot ID) ---
    let savedPilotId = {pilot_id_json};
    const settingsModal = document.getElementById('settings-modal');
    const pilotIdInput = document.getElementById('simbrief-pilot-id');
    const simbriefStatus = document.getElementById('simbrief-status');

    function openSettings() {{
      pilotIdInput.value = savedPilotId;
      simbriefStatus.textContent = '';
      settingsModal.classList.add('open');
    }}
    function closeSettings() {{ settingsModal.classList.remove('open'); }}

    document.getElementById('settings-toggle').addEventListener('click', openSettings);
    document.getElementById('settings-close').addEventListener('click', closeSettings);
    document.getElementById('settings-cancel').addEventListener('click', closeSettings);
    settingsModal.addEventListener('click', e => {{ if (e.target === settingsModal) closeSettings(); }});

    document.getElementById('simbrief-verify').addEventListener('click', async () => {{
      const pilotId = pilotIdInput.value.trim();
      simbriefStatus.style.color = 'var(--muted)';
      simbriefStatus.textContent = 'Checking...';
      try {{
        const response = await fsatlasFetch('/api/simbrief/verify', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{pilot_id: pilotId}})
        }});
        const result = await response.json();
        simbriefStatus.textContent = result.message;
        simbriefStatus.style.color = result.ok ? 'var(--accent)' : 'var(--danger)';
      }} catch (err) {{
        simbriefStatus.textContent = 'Could not reach SimBrief';
        simbriefStatus.style.color = 'var(--danger)';
      }}
    }});

    document.getElementById('settings-save').addEventListener('click', async () => {{
      savedPilotId = pilotIdInput.value.trim();
      try {{
        await fsatlasFetch('/api/settings', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{simbrief_pilot_id: savedPilotId}})
        }});
      }} catch (err) {{ /* best effort - the setting still applies for this session */ }}
      closeSettings();
    }});

    // --- Filter list collapse tab (protrudes from the toolbar, stays visible when hidden) ---
    const filtersTab = document.getElementById('filters-tab');
    const filtersWrap = document.getElementById('filters-wrap');
    const toolbar = document.querySelector('.toolbar');

    filtersTab.addEventListener('click', () => {{
      filtersWrap.classList.toggle('collapsed');
      filtersTab.classList.toggle('collapsed');
      toolbar.classList.toggle('filters-collapsed');
    }});

    // --- Mobile filter menu - replaces desktop's always-open inline panel entirely on
    // mobile with a single button (labelled "Add Filter" until anything's configured, then
    // "Filters (N)") that opens the *entire* #filters tree fullscreen, Reset/Apply fixed at
    // the bottom. Deliberately reuses the exact same #filters DOM/listeners (insert/group/
    // remove/column/operator/value) instead of duplicating any filter-tree logic - opening
    // the menu just repositions the whole tree, it doesn't rebuild or hide any of it.
    const filtersMenuBtn = document.getElementById('filters-menu-btn');
    const filtersMenuLabel = document.getElementById('filters-menu-label');

    function countConfiguredFilters() {{
      return [...filters.querySelectorAll('.column')].filter(col => col.value).length;
    }}

    function updateFiltersMenuLabel() {{
      const configured = countConfiguredFilters();
      const label = configured ? 'Filters (' + configured + ')' : 'Add Filter';
      filtersMenuLabel.textContent = label;
      filtersMenuBtn.classList.toggle('has-filters', configured > 0);
    }}

    // .toolbar's backdrop-filter makes it the containing block for position:fixed descendants
    // (same reason the logo gets moved out - see applyLogoPlacement below), so #filters has to
    // move to <body> too or "fullscreen" would mean "the size of the toolbar".
    function openFilterMenu() {{
      document.body.appendChild(filters);
      filters.classList.add('mobile-editing');
      document.body.classList.add('filter-editing');
    }}

    function closeFilterMenu() {{
      filters.classList.remove('mobile-editing');
      document.body.classList.remove('filter-editing');
      filtersWrap.appendChild(filters);
      updateFiltersMenuLabel();
    }}

    filtersMenuBtn.addEventListener('click', openFilterMenu);

    // Delegated so it keeps working across the column/operator/value swap in updateOperators()
    // below (the .value element gets replaced with a fresh input/select on column change).
    filters.addEventListener('change', e => {{ if (e.target.matches('.column')) updateFiltersMenuLabel(); }});

    function operatorsFor(column) {{
      // Dropdown-backed columns only ever match a single picked value, so there's no
      // meaningful choice of operator - lock it to "Is" instead of letting the user pick
      // Contains/Starts With/etc, which wouldn't make sense against a fixed value list.
      if (Array.isArray(column.options)) return [['equals', 'Is']];
      return column.numeric
        ? [['equals', 'Equals (=)'], ['>', 'Greater (>)'], ['<', 'Less (<)'], ['>=', 'Greater/Eq (>=)'], ['<=', 'Less/Eq (<=)']]
        : [['contains', 'Contains'], ['equals', 'Equals'], ['starts_with', 'Starts With'], ['ends_with', 'Ends With']];
    }}

    function updateOperators(row) {{
      const column = columns.find(item => item.id === row.querySelector('.column').value);
      const operator = row.querySelector('.operator');
      operator.replaceChildren();
      if (!column) return;
      operatorsFor(column).forEach(([value, label]) => operator.add(new Option(label, value)));

      // Low-cardinality columns (e.g. Departure Region) get a dropdown of real values
      // instead of a free-text field, so swap the element type when that changes.
      const oldValue = row.querySelector('.value');
      const wantsSelect = Array.isArray(column.options);
      operator.disabled = wantsSelect;
      let value = oldValue;
      if (wantsSelect !== (oldValue.tagName === 'SELECT')) {{
        value = document.createElement(wantsSelect ? 'select' : 'input');
        value.className = 'value';
        oldValue.replaceWith(value);
      }}

      if (wantsSelect) {{
        value.replaceChildren(new Option('Select value...', ''));
        column.options.forEach(opt => value.add(new Option(opt, opt)));
      }} else {{
        value.type = column.numeric ? 'number' : 'text';
        value.step = column.numeric ? 'any' : '';
        value.placeholder = column.example ? 'e.g. ' + column.example : 'Value';
      }}
      layoutFilterFields();
    }}

    // --- Auto-sizing filter fields: size the logic/column/operator fields to fit their
    // own text, sharing one width per column across every row so they always line up.
    const measureCtx = document.createElement('canvas').getContext('2d');
    function textWidth(text, font) {{
      measureCtx.font = font;
      return measureCtx.measureText(text || '').width;
    }}
    function fieldFont(el) {{
      const cs = getComputedStyle(el);
      return cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    }}
    function selectTextWidth(select) {{
      const opt = select.options[select.selectedIndex];
      return textWidth(opt ? opt.text : '', fieldFont(select));
    }}
    const FIELD_PADDING = 46; // horizontal padding/border plus the native dropdown-arrow allowance

    function layoutFilterFields() {{
      positionLogo();
      const rows = [...filters.querySelectorAll('.filter-row')];
      const logicEls = [...filters.querySelectorAll('.filter-row > .logic, .group-head > .logic')];
      if (!rows.length || !logicEls.length) return;
      const minField = textWidth('0123456789', fieldFont(rows[0].querySelector('.column'))) + FIELD_PADDING;
      const logicWidth = Math.max(...logicEls.map(el => Math.max(textWidth('AND', fieldFont(el)), textWidth('OR', fieldFont(el))))) + FIELD_PADDING;
      let colWidth = minField;
      let opWidth = minField;
      rows.forEach(row => {{
        colWidth = Math.max(colWidth, selectTextWidth(row.querySelector('.column')) + FIELD_PADDING);
        opWidth = Math.max(opWidth, selectTextWidth(row.querySelector('.operator')) + FIELD_PADDING);
      }});

      // AND/OR always keeps its full width unless that would leave the value field with
      // less than ~10 characters of room, in which case it gives space back.
      let finalLogic = logicWidth;
      const available = filters.clientWidth;
      if (available) {{
        const fixedOverhead = 96 + 8 * 4; // row-actions width and the gaps between columns
        const spareForLogicAndValue = available - fixedOverhead - colWidth - opWidth;
        finalLogic = Math.min(logicWidth, Math.max(32, spareForLogicAndValue - minField));
      }}

      filters.style.setProperty('--w-logic', Math.round(finalLogic) + 'px');
      filters.style.setProperty('--w-col', Math.round(colWidth) + 'px');
      filters.style.setProperty('--w-op', Math.round(opWidth) + 'px');
      filters.style.setProperty('--min-field', Math.round(minField) + 'px');
    }}

    // Keeps the logo horizontally centered on the first filter row's column select, and
    // vertically in line with the other toolbar-head buttons (Apply Filters, Map Type, etc).
    const logoEl = document.querySelector('.logo');
    const brandEl = document.querySelector('.brand');
    // .toolbar uses backdrop-filter, which (per spec) makes it the containing block for any
    // position:fixed descendant - so a fixed logo left inside it would be positioned relative
    // to .toolbar, not the viewport. Move it out to <body> so "fixed" means the viewport.
    // Mobile skips all of this and keeps the logo inline in .brand instead (small, static,
    // no floating tracker) - there's no per-row filter select to center it over once the
    // filter builder starts collapsed behind the pull-tab.
    function applyLogoPlacement() {{
      if (isMobile) {{
        if (logoEl.parentElement !== brandEl) brandEl.appendChild(logoEl);
      }} else {{
        if (logoEl.parentElement !== document.body) document.body.appendChild(logoEl);
        positionLogo();
      }}
    }}
    // The <img> has width/height attributes so its box is sized correctly from the first
    // layout pass, but re-run once the real pixels are in as a safety net regardless.
    logoEl.addEventListener('load', () => {{ if (!isMobile) positionLogo(); }});
    function positionLogo() {{
      if (isMobile) return;
      const targetSelect = filters.querySelector('.filter-row .column');
      const toolbarHead = document.querySelector('.toolbar-head');
      if (!targetSelect || !toolbarHead) return;
      const rect = targetSelect.getBoundingClientRect();
      if (!rect.width) return; // filters panel collapsed - keep the logo at its last position
      const headRect = toolbarHead.getBoundingClientRect();
      const logoRect = logoEl.getBoundingClientRect();
      logoEl.style.left = Math.round(rect.left + rect.width / 2 - logoRect.width / 2) + 'px';
      logoEl.style.top = Math.round(headRect.top + headRect.height / 2 - logoRect.height / 2) + 'px';
    }}
    applyLogoPlacement();

    let filterLayoutRaf;
    function scheduleFilterLayout() {{
      cancelAnimationFrame(filterLayoutRaf);
      filterLayoutRaf = requestAnimationFrame(() => {{
        // Re-checked on every resize (not just once at load) so rotating a device, resizing
        // a window, or toggling dev-tools emulation across the breakpoint takes effect live.
        const nowMobile = isMobileViewport();
        if (nowMobile !== isMobile) {{
          isMobile = nowMobile;
          document.body.classList.toggle('is-mobile', isMobile);
          applyLogoPlacement();
        }}
        layoutFilterFields();
      }});
    }}
    window.addEventListener('resize', scheduleFilterLayout);
    new ResizeObserver(scheduleFilterLayout).observe(toolbar);

    // --- Filter tree: each level (the root #filters, or a group's inner .filters-list) holds
    // a mix of condition rows and nested groups. A row/group's own "logic" select says how it
    // combines with the *previous sibling in its own list*, so wrapping a run of rows in a
    // group (and giving the group its own logic) is what lets AND/OR precedence be set
    // explicitly instead of always evaluating strictly left-to-right.
    function markFirst(list) {{
      [...list.children].forEach((node, index) => node.classList.toggle('first', index === 0));
    }}

    function refreshRows() {{
      markFirst(filters);
      filters.querySelectorAll('.filters-list').forEach(markFirst);
      layoutFilterFields();
      updateFiltersMenuLabel();
    }}

    function populateColumnSelect(columnSelect) {{
      const optgroups = {{}};
      columns.forEach(column => {{
        let parent = columnSelect;
        if (column.group) {{
          if (!optgroups[column.group]) {{
            optgroups[column.group] = document.createElement('optgroup');
            optgroups[column.group].label = column.group;
            columnSelect.append(optgroups[column.group]);
          }}
          parent = optgroups[column.group];
        }}
        parent.append(new Option(column.name, column.id));
      }});
    }}

    function removeNode(node) {{
      const parentList = node.parentElement;
      const isRoot = parentList === filters;
      node.remove();
      // Dissolve a group automatically once its last child is removed.
      if (!isRoot && parentList.classList.contains('filters-list') && !parentList.children.length) {{
        removeNode(parentList.closest('.filter-group'));
        return;
      }}
      refreshRows();
    }}

    function createConditionRow() {{
      const row = document.createElement('div');
      row.className = 'filter-row';
      row.innerHTML = '<select class="logic"><option>AND</option><option>OR</option></select>' +
        '<select class="column"><option value="">Select Filter...</option></select>' +
        '<select class="operator"></select>' +
        '<input class="value" placeholder="Value">' +
        '<div class="row-actions">' +
        '<button class="icon insert" title="Insert filter below" aria-label="Insert filter below">+<span class="action-label">Insert</span></button>' +
        '<button class="icon group" title="Wrap in a group" aria-label="Wrap in a group">⧉<span class="action-label">Group</span></button>' +
        '<button class="icon remove" title="Remove filter" aria-label="Remove filter">×<span class="action-label">Remove</span></button>' +
        '</div>';
      const columnSelect = row.querySelector('.column');
      populateColumnSelect(columnSelect);
      columnSelect.addEventListener('change', () => updateOperators(row));
      row.querySelector('.operator').addEventListener('change', layoutFilterFields);
      row.querySelector('.logic').addEventListener('change', layoutFilterFields);
      row.querySelector('.insert').addEventListener('click', () => {{
        row.after(createConditionRow());
        refreshRows();
      }});
      row.querySelector('.group').addEventListener('click', () => wrapInGroup(row));
      row.querySelector('.remove').addEventListener('click', () => removeNode(row));
      return row;
    }}

    function createGroup() {{
      const group = document.createElement('div');
      group.className = 'filter-group';
      group.innerHTML = '<div class="group-head">' +
        '<select class="logic"><option>AND</option><option>OR</option></select>' +
        '<span class="group-label">Group</span>' +
        '<div class="row-actions">' +
        '<button class="icon insert" title="Insert filter below group" aria-label="Insert filter below group">+<span class="action-label">Insert</span></button>' +
        '<button class="icon ungroup" title="Ungroup" aria-label="Ungroup">⧈<span class="action-label">Ungroup</span></button>' +
        '<button class="icon remove" title="Remove group" aria-label="Remove group">×<span class="action-label">Remove</span></button>' +
        '</div></div><div class="filters-list"></div>';
      group.querySelector('.logic').addEventListener('change', layoutFilterFields);
      group.querySelector('.insert').addEventListener('click', () => {{
        group.after(createConditionRow());
        refreshRows();
      }});
      group.querySelector('.ungroup').addEventListener('click', () => ungroup(group));
      group.querySelector('.remove').addEventListener('click', () => removeNode(group));
      return group;
    }}

    function wrapInGroup(node) {{
      const group = createGroup();
      const logic = node.querySelector(':scope > .logic').value;
      group.querySelector('.logic').value = logic;
      node.before(group);
      group.querySelector('.filters-list').append(node);
      refreshRows();
    }}

    function ungroup(group) {{
      const list = group.parentElement;
      const children = [...group.querySelector('.filters-list').children];
      if (children.length) {{
        children[0].querySelector(':scope > .logic, .group-head > .logic').value = group.querySelector('.logic').value;
      }}
      children.forEach(child => group.before(child));
      group.remove();
      refreshRows();
    }}

    function addRow(afterRow) {{
      const row = createConditionRow();
      if (afterRow) afterRow.after(row); else filters.append(row);
      refreshRows();
      return row;
    }}

    // --- Serialize the filter tree in the DOM into the nested group structure the backend
    // expects: {{kind: 'group', logic, children: [...]}} with leaf conditions as children too.
    function serializeList(listEl) {{
      return [...listEl.children].map(serializeNode).filter(Boolean);
    }}

    function serializeNode(node) {{
      if (node.classList.contains('filter-group')) {{
        const logic = node.querySelector(':scope > .group-head > .logic').value;
        const children = serializeList(node.querySelector(':scope > .filters-list'));
        return children.length ? {{kind: 'group', logic, children}} : null;
      }}
      const column = columns.find(item => item.id === node.querySelector('.column').value);
      const value = node.querySelector('.value').value.trim();
      if (!column || !value) return null;
      return {{
        column: column.id,
        operator: node.querySelector('.operator').value,
        value: column.numeric ? Number(value) : value,
        logic: node.querySelector(':scope > .logic').value,
        type: column.numeric ? 'number' : 'text'
      }};
    }}

    async function applyFilters() {{
      const filterTree = {{kind: 'group', logic: 'AND', children: serializeList(filters)}};
      document.getElementById('status').textContent = 'Rendering...';
      try {{
        const response = await fsatlasFetch('/api/maps', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{filters: filterTree, map_type: effectiveMapType()}})
        }});
        const result = await response.json();
        baseMapUrl = result.url;
        document.getElementById('map').src = withTheme(baseMapUrl);
        document.getElementById('status').textContent = result.count.toLocaleString() + ' Flights';
        // The permanent count stays hidden (see #status CSS) until a real filter is applied,
        // then shows as a transient toast instead - same on desktop and mobile.
        if (countConfiguredFilters() > 0) showFlightsToast(result.count); else hideFlightsToast();
      }} catch (err) {{
        document.getElementById('status').textContent = '';
      }}
    }}

    function resetFilters() {{
      filters.replaceChildren();
      addRow();
      applyFilters();
    }}

    document.getElementById('apply').addEventListener('click', applyFilters);
    document.getElementById('reset').addEventListener('click', resetFilters);
    document.getElementById('filter-menu-apply').addEventListener('click', () => {{ applyFilters(); closeFilterMenu(); }});
    document.getElementById('filter-menu-reset').addEventListener('click', () => {{ resetFilters(); closeFilterMenu(); }});
    mapType.addEventListener('change', () => {{
      applyFilters();
    }});
    addRow();
    applyFilters();
  </script>
</body>
</html>"""


class AtlasRequestHandler(BaseHTTPRequestHandler):
    state = None

    def log_message(self, format, *args):
        return

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=HTTPStatus.OK):
        body = html.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Always fetch fresh HTML/JS - browsers must never reuse a stale cached page.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def send_image(self, path):
        try:
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            self.send_html("Not found", HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            columns = build_columns(self.state.df)
            self.send_html(index_html(columns, load_settings()))
            return

        if parsed.path == "/images/logo.png":
            self.send_image(LOGO_FILE)
            return

        if parsed.path.startswith("/map/"):
            view_id = parsed.path.removeprefix("/map/")
            view = self.state.views.get(view_id)
            if not view:
                self.send_html("View expired", HTTPStatus.NOT_FOUND)
                return
            dataframe, map_type = view
            theme_mode = parse_qs(parsed.query).get("theme", ["dark"])[0]
            if theme_mode not in ("dark", "light"):
                theme_mode = "dark"
            try:
                pan_offset = int(float(parse_qs(parsed.query).get("offset", ["0"])[0]))
            except ValueError:
                pan_offset = 0
            self.send_html(mapping.create_map_html(
                dataframe, map_type, theme_mode, self.state.airport_counts,
                route_request_url=f"/api/routes/{view_id}",
                pan_offset=pan_offset,
            ))
            return

        if parsed.path.startswith("/api/routes/"):
            view_id = parsed.path.removeprefix("/api/routes/")
            view = self.state.views.get(view_id)
            source = parse_qs(parsed.query).get("source", [""])[0]
            self.send_json(route_records(view[0], source) if view else [],
                            HTTPStatus.OK if view else HTTPStatus.NOT_FOUND)
            return

        if parsed.path == "/api/saved-flights":
            self.send_json(load_saved_items()["saved_flights"])
            return

        if parsed.path == "/api/saved-searches":
            self.send_json(load_saved_items()["saved_searches"])
            return

        self.send_html("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/settings":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid settings payload"}, HTTPStatus.BAD_REQUEST)
                return
            settings = load_settings()
            if "theme" in payload:
                if payload["theme"] not in ("dark", "light"):
                    self.send_json({"error": "Invalid settings payload"}, HTTPStatus.BAD_REQUEST)
                    return
                settings["theme"] = payload["theme"]
            if "simbrief_pilot_id" in payload:
                pilot_id = payload["simbrief_pilot_id"]
                if not isinstance(pilot_id, str):
                    self.send_json({"error": "Invalid settings payload"}, HTTPStatus.BAD_REQUEST)
                    return
                settings["simbrief_pilot_id"] = pilot_id.strip()
            save_settings(settings)
            self.send_json({"ok": True})
            return

        if parsed.path == "/api/simbrief/verify":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                pilot_id = str(payload.get("pilot_id", ""))
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid request"}, HTTPStatus.BAD_REQUEST)
                return
            ok, message = simbrief_api.verify_pilot_id(pilot_id)
            self.send_json({"ok": ok, "message": message})
            return

        if parsed.path == "/api/simbrief/export":
            try:
                length = int(self.headers.get("Content-Length", 0))
                flight = json.loads(self.rfile.read(length))
                if not isinstance(flight, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid flight payload"}, HTTPStatus.BAD_REQUEST)
                return
            pilot_id = load_settings().get("simbrief_pilot_id", "")
            self.send_json({"url": simbrief_api.build_export_url(flight, pilot_id)})
            return

        if parsed.path == "/api/saved-flights":
            try:
                length = int(self.headers.get("Content-Length", 0))
                flight = json.loads(self.rfile.read(length))
                if not isinstance(flight, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid flight payload"}, HTTPStatus.BAD_REQUEST)
                return
            items = load_saved_items()
            key = flight_key(flight)
            existing = next((f for f in items["saved_flights"] if flight_key(f) == key), None)
            if existing:
                flight.setdefault("saved_at", existing.get("saved_at"))
            flight = normalize_saved_flight(flight)
            saved = [f for f in items["saved_flights"] if flight_key(f) != key]
            saved.append(flight)
            items["saved_flights"] = saved
            save_saved_items(items)
            self.send_json({"ok": True, "saved_flights": saved})
            return

        if parsed.path == "/api/saved-searches":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError
                description = payload.get("description", "")
                filter_tree = payload.get("filters")
                if not isinstance(description, str) or not description.strip():
                    raise ValueError
                if not isinstance(filter_tree, (dict, list)):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid saved search payload"}, HTTPStatus.BAD_REQUEST)
                return
            items = load_saved_items()
            search = normalize_saved_search({"description": description, "filters": filter_tree})
            items["saved_searches"].append(search)
            save_saved_items(items)
            self.send_json({"ok": True, "saved_searches": items["saved_searches"]})
            return

        if parsed.path != "/api/maps":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            filters = payload.get("filters", [])
            map_type = payload.get("map_type", "Dark Mode")
            if not isinstance(filters, (list, dict)) or map_type not in config.TILES:
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid filter request"}, HTTPStatus.BAD_REQUEST)
            return
        view_id, count = self.state.create_view(filters, map_type)
        self.send_json({"url": f"/map/{view_id}", "count": count})

    def do_DELETE(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/saved-flights":
            try:
                length = int(self.headers.get("Content-Length", 0))
                flight = json.loads(self.rfile.read(length))
                if not isinstance(flight, dict):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid flight payload"}, HTTPStatus.BAD_REQUEST)
                return
            items = load_saved_items()
            key = flight_key(flight)
            items["saved_flights"] = [f for f in items["saved_flights"] if flight_key(f) != key]
            save_saved_items(items)
            self.send_json({"ok": True, "saved_flights": items["saved_flights"]})
            return

        if parsed.path == "/api/saved-searches":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                search_id = payload.get("id") if isinstance(payload, dict) else None
                if not isinstance(search_id, str):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid saved search payload"}, HTTPStatus.BAD_REQUEST)
                return
            items = load_saved_items()
            items["saved_searches"] = [s for s in items["saved_searches"] if s["id"] != search_id]
            save_saved_items(items)
            self.send_json({"ok": True, "saved_searches": items["saved_searches"]})
            return

        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)


def main():
    parser = argparse.ArgumentParser(
        prog="fsatlas",
        description="FSAtlas - browse real-world flight data on an interactive world map.",
    )
    parser.add_argument(
        "--host", default=os.environ.get("FSATLAS_HOST", "127.0.0.1"),
        help="Interface to bind to (default: 127.0.0.1; use 0.0.0.0 for containers).",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FSATLAS_PORT", "0")),
        help="Port to bind to (default: 0, i.e. pick a free port).",
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        default=os.environ.get("FSATLAS_NO_BROWSER", "") not in ("", "0"),
        help="Don't try to open a browser window (implied when there's no display to open one on).",
    )
    parser.add_argument(
        "-i", "--import", dest="import_file", metavar="JSONFILE",
        help="Import flight records from JSONFILE into flights.csv, then exit without starting the server.",
    )
    args = parser.parse_args()

    if args.import_file:
        from run.import_flights import import_flights
        sys.exit(import_flights(args.import_file))

    ensure_data_files()

    instance = None
    if sys.platform == "win32" and getattr(sys, "frozen", False):
      instance = SingleInstance.acquire()
      if instance is None:
        url = running_url()
        if url:
          try:
            webbrowser.open(url)
          except webbrowser.Error:
            pass
        return

    AtlasRequestHandler.state = AtlasState()
    server = ThreadingHTTPServer((args.host, args.port), AtlasRequestHandler)
    url = f"http://{args.host}:{server.server_port}"
    if instance is not None:
      instance.publish(url)
    print(f"Flightsim Atlas web UI: {url}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass

    if sys.platform == "win32" and getattr(sys, "frozen", False):
        from run.windows_tray import run_with_tray

    try:
      if sys.platform == "win32" and getattr(sys, "frozen", False):
        run_with_tray(server, LOGO_FILE)
      else:
        server.serve_forever()
    except KeyboardInterrupt:
      pass
    finally:
      server.server_close()
      if instance is not None:
        instance.close()


if __name__ == "__main__":
    main()