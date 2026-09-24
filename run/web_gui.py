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
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from run import config, data_loader, filtering, mapping
from run.single_instance import SingleInstance, running_url

LOGO_FILE = os.path.join(os.path.dirname(__file__), 'images', 'FSAtlas Logo.png')

# Persisted UI settings (currently just the light/dark preference) - tracked in git with a
# default value, but local writes are excluded via `git update-index --skip-worktree` so a
# user's runtime preference never shows up as an uncommitted change.
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'settings.json')
DEFAULT_SETTINGS = {"theme": "dark"}


def load_settings():
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    if settings.get("theme") not in ("dark", "light"):
        settings["theme"] = DEFAULT_SETTINGS["theme"]
    return settings


def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f)


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
        "airline": matches["owner"].astype(str),
        "date": matches["timestamp_read"].astype(str).str.slice(0, 10),
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


def index_html(columns, theme='dark'):
    columns_json = json.dumps(columns)
    map_types_json = json.dumps(list(config.TILES.keys()))
    theme_attr = ' data-theme="light"' if theme == 'light' else ''
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
      margin: 14px 20px 12px;
      padding: 14px 20px; display: grid; gap: 12px; min-width: 0;
      border-radius: 20px;
      box-shadow: var(--shadow);
      position: relative; z-index: 5;
    }}
    .toolbar-head {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap; row-gap: 8px; }}
    .brand {{ display: flex; align-items: center; gap: 10px; }}
    h1 {{ font-size: 17px; margin: 0; font-weight: 600; letter-spacing: -0.01em; }}
    .logo {{ height: 44px; width: auto; display: block; }}
    .map-type {{ margin-left: auto; display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--muted); white-space: nowrap; flex-shrink: 0; }}
    select, input, button {{
      font: inherit; color: inherit; background: var(--surface-solid);
      border: 1px solid var(--border); border-radius: 10px; min-height: 34px; box-sizing: border-box;
      transition: border-color .15s ease, background .15s ease, transform .1s ease, box-shadow .15s ease;
    }}
    select, input {{ padding: 5px 10px; width: 100%; }}
    select:focus, input:focus, button:focus-visible {{
      outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent);
    }}
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
    #theme-toggle {{
      width: 34px; height: 34px; padding: 0; background: var(--surface-solid); border: 1px solid var(--border);
      box-shadow: none; display: flex; align-items: center; justify-content: center; color: var(--text);
    }}
    #theme-toggle:hover {{ background: var(--border); transform: none; filter: none; }}
    #theme-toggle svg {{ width: 17px; height: 17px; transition: transform .3s ease; }}
    #filters-wrap {{ display: grid; grid-template-rows: 1fr; min-width: 0; transition: grid-template-rows .28s ease; }}
    #filters-wrap.collapsed {{ grid-template-rows: 0fr; }}
    #filters-wrap > #filters {{ overflow: hidden; min-height: 0; }}
    #filters {{ display: grid; gap: 8px; min-width: 0; }}
    .filter-row {{
      display: grid; min-width: 0; overflow-x: auto; overscroll-behavior-x: contain;
      grid-template-columns: var(--w-logic, 60px) var(--w-col, 150px) var(--w-op, 110px) minmax(var(--min-field, 90px), 1fr) 32px 32px;
      gap: 8px; align-items: center;
    }}
    .filter-row.first {{ grid-template-columns: var(--w-col, 150px) var(--w-op, 110px) minmax(var(--min-field, 110px), 1fr) 32px; }}
    .filter-row.first .logic {{ display: none; }}
    .filter-row.first .remove {{ display: none; }}
    .actions {{ display: flex; align-items: center; gap: 10px; }}
    #status {{ color: var(--muted); font-size: 13px; }}
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
    @media (max-width: 760px) {{
      .toolbar {{ margin: 8px 8px 10px; border-radius: 16px; }}
    }}
  </style>
</head>
<body>
  <section class="toolbar">
    <div class="toolbar-head">
      <div class="brand">
        <img class="logo" src="/images/logo.png" alt="FSAtlas">
        <h1>FSAtlas</h1>
      </div>
      <label class="map-type">Map Type <select id="map-type"></select></label>
      <button id="apply">Apply Filters</button>
      <button id="reset" class="danger" type="button" title="Reset filters" aria-label="Reset filters">Reset Filters</button>
      <button id="theme-toggle" type="button" title="Toggle light / dark mode" aria-label="Toggle light / dark mode"></button>
      <span id="status"></span>
    </div>
    <div id="filters-wrap">
      <div id="filters"></div>
    </div>
    <button id="filters-tab" class="filters-tab" type="button" title="Toggle filter list" aria-label="Toggle filter list">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
    </button>
  </section>
  <iframe id="map" title="Flight map"></iframe>
  <script>
    const columns = {columns_json};
    const mapTypes = {map_types_json};
    const filters = document.getElementById('filters');
    const mapType = document.getElementById('map-type');
    const THEME_MAP_TYPES = {{ dark: 'Dark Mode', light: 'Light Mode' }};
    let theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    let autoMapType = true; // Tracks the theme-matched default until the user manually picks a style
    mapTypes.forEach(name => mapType.add(new Option(name, name, name === THEME_MAP_TYPES[theme], name === THEME_MAP_TYPES[theme])));

    // --- Theme toggle (persists choice, re-themes the map iframe) ---
    const SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.5"></circle><path d="M12 2.5v3M12 18.5v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2.5 12h3M18.5 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"></path></svg>';
    const MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"></path></svg>';
    const themeToggle = document.getElementById('theme-toggle');
    let baseMapUrl = '';

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
        fetch('/api/settings', {{
          method: 'POST', headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify({{theme}})
        }}).catch(() => {{}});
      }}
      if (autoMapType && mapType.value !== THEME_MAP_TYPES[theme]) {{
        mapType.value = THEME_MAP_TYPES[theme];
        applyFilters();
      }} else if (baseMapUrl) {{
        document.getElementById('map').src = withTheme(baseMapUrl);
      }}
    }}

    themeToggle.addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark', true));
    applyTheme(theme, false);

    // --- Filter list collapse tab (protrudes from the toolbar, stays visible when hidden) ---
    const filtersTab = document.getElementById('filters-tab');
    const filtersWrap = document.getElementById('filters-wrap');
    const toolbar = document.querySelector('.toolbar');

    filtersTab.addEventListener('click', () => {{
      filtersWrap.classList.toggle('collapsed');
      filtersTab.classList.toggle('collapsed');
      toolbar.classList.toggle('filters-collapsed');
    }});

    function operatorsFor(column) {{
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
      const rows = [...filters.children];
      if (!rows.length) return;
      const logicEl = rows[0].querySelector('.logic');
      const minField = textWidth('0123456789', fieldFont(rows[0].querySelector('.column'))) + FIELD_PADDING;
      const logicWidth = Math.max(textWidth('AND', fieldFont(logicEl)), textWidth('OR', fieldFont(logicEl))) + FIELD_PADDING;
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
        const fixedOverhead = 32 + 32 + 8 * 5; // insert + remove buttons and the gaps between 6 columns
        const spareForLogicAndValue = available - fixedOverhead - colWidth - opWidth;
        finalLogic = Math.min(logicWidth, Math.max(32, spareForLogicAndValue - minField));
      }}

      filters.style.setProperty('--w-logic', Math.round(finalLogic) + 'px');
      filters.style.setProperty('--w-col', Math.round(colWidth) + 'px');
      filters.style.setProperty('--w-op', Math.round(opWidth) + 'px');
      filters.style.setProperty('--min-field', Math.round(minField) + 'px');
    }}

    let filterLayoutRaf;
    function scheduleFilterLayout() {{
      cancelAnimationFrame(filterLayoutRaf);
      filterLayoutRaf = requestAnimationFrame(layoutFilterFields);
    }}
    window.addEventListener('resize', scheduleFilterLayout);
    new ResizeObserver(scheduleFilterLayout).observe(toolbar);

    function refreshRows() {{
      [...filters.children].forEach((row, index) => row.classList.toggle('first', index === 0));
      layoutFilterFields();
    }}

    function addRow(afterRow) {{
      const row = document.createElement('div');
      row.className = 'filter-row';
      row.innerHTML = '<select class="logic"><option>AND</option><option>OR</option></select><select class="column"><option value="">Select Filter...</option></select><select class="operator"></select><input class="value" placeholder="Value"><button class="icon insert" title="Insert filter below" aria-label="Insert filter below">+</button><button class="icon remove" title="Remove filter" aria-label="Remove filter">×</button>';
      const columnSelect = row.querySelector('.column');
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
      columnSelect.addEventListener('change', () => updateOperators(row));
      row.querySelector('.operator').addEventListener('change', layoutFilterFields);
      row.querySelector('.logic').addEventListener('change', layoutFilterFields);
      row.querySelector('.insert').addEventListener('click', () => addRow(row));
      row.querySelector('.remove').addEventListener('click', () => {{ row.remove(); refreshRows(); }});
      if (afterRow) afterRow.after(row); else filters.append(row);
      refreshRows();
    }}

    async function applyFilters() {{
      const activeFilters = [...filters.children].map(row => {{
        const column = columns.find(item => item.id === row.querySelector('.column').value);
        const value = row.querySelector('.value').value.trim();
        return column && value ? {{
          column: column.id,
          operator: row.querySelector('.operator').value,
          value: column.numeric ? Number(value) : value,
          logic: row.querySelector('.logic').value,
          type: column.numeric ? 'number' : 'text'
        }} : null;
      }}).filter(Boolean);
      document.getElementById('status').textContent = 'Rendering...';
      const response = await fetch('/api/maps', {{
        method: 'POST', headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{filters: activeFilters, map_type: mapType.value}})
      }});
      const result = await response.json();
      baseMapUrl = result.url;
      document.getElementById('map').src = withTheme(baseMapUrl);
      document.getElementById('status').textContent = result.count.toLocaleString() + ' Flights';
    }}

    document.getElementById('apply').addEventListener('click', applyFilters);
    document.getElementById('reset').addEventListener('click', () => {{
      filters.replaceChildren();
      addRow();
      applyFilters();
    }});
    mapType.addEventListener('change', () => {{
      autoMapType = false;
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
            self.send_html(index_html(columns, load_settings().get("theme", "dark")))
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

        self.send_html("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/settings":
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length))
                theme = payload.get("theme")
                if theme not in ("dark", "light"):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                self.send_json({"error": "Invalid settings payload"}, HTTPStatus.BAD_REQUEST)
                return
            save_settings({"theme": theme})
            self.send_json({"ok": True})
            return

        if parsed.path != "/api/maps":
            self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
            filters = payload.get("filters", [])
            map_type = payload.get("map_type", "Dark Mode")
            if not isinstance(filters, list) or map_type not in config.TILES:
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Invalid filter request"}, HTTPStatus.BAD_REQUEST)
            return
        view_id, count = self.state.create_view(filters, map_type)
        self.send_json({"url": f"/map/{view_id}", "count": count})


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
    args = parser.parse_args()

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