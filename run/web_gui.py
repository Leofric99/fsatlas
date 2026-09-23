"""Optional browser-based UI for Flightsim Atlas.

Run with ``python -m run.web_gui``. The existing Qt desktop UI remains the
default entry point at ``python -m run``.
"""

import json
import secrets
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

from run import config, data_loader, filtering, mapping


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


def route_records(df, source):
    matches = df[(df["dep_airport_iata"] == source) | (df["arr_airport_iata"] == source)]
    return [
        {
            "dep": str(row.get("dep_airport_iata", "")),
            "arr": str(row.get("arr_airport_iata", "")),
            "flight": str(row.get("flight_number", "")),
            "type": str(row.get("type", "")),
            "callsign": str(row.get("calsign", "")),
            "type_icao": str(row.get("type_icao", "")),
            "reg": str(row.get("reg", "")),
            "dep_icao": str(row.get("dep_airport_icao", "")),
            "arr_icao": str(row.get("arr_airport_icao", "")),
            "airline": str(row.get("owner", "")),
            "date": str(row.get("timestamp_read", ""))[:10],
        }
        for _, row in matches.iterrows()
    ]


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


def index_html(columns):
    columns_json = json.dumps(columns)
    map_types_json = json.dumps(list(config.TILES.keys()))
    return f"""<!doctype html>
<html lang="en">
<head>
  <script>
    (function() {{
      try {{
        if (localStorage.getItem('atlas-theme') === 'light') {{
          document.documentElement.dataset.theme = 'light';
        }}
      }} catch (e) {{}}
    }})();
  </script>
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
      margin: 0; min-height: 100vh; display: grid; grid-template-rows: auto minmax(0, 1fr);
      background: var(--bg-grad); background-attachment: fixed; color: var(--text);
      transition: background .3s ease, color .3s ease;
    }}
    .toolbar {{
      background: var(--surface);
      backdrop-filter: blur(22px) saturate(160%);
      -webkit-backdrop-filter: blur(22px) saturate(160%);
      border-bottom: 1px solid var(--border);
      padding: 14px 20px; display: grid; gap: 12px;
      box-shadow: var(--shadow);
      position: relative; z-index: 5;
    }}
    .toolbar-head {{ display: flex; align-items: center; gap: 14px; }}
    h1 {{ font-size: 17px; margin: 0; font-weight: 600; letter-spacing: -0.01em; }}
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
      cursor: pointer; padding: 5px 14px; font-weight: 600;
      background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #fff; border: none;
      box-shadow: 0 4px 14px color-mix(in srgb, var(--accent) 35%, transparent);
    }}
    button:hover {{ transform: translateY(-1px); filter: brightness(1.08); }}
    button:active {{ transform: translateY(0); }}
    button.icon {{
      width: 34px; padding: 0; background: var(--surface-solid); color: var(--text); font-size: 16px;
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
    #filters-wrap {{ display: grid; grid-template-rows: 1fr; transition: grid-template-rows .28s ease; }}
    #filters-wrap.collapsed {{ grid-template-rows: 0fr; }}
    #filters-wrap > #filters {{ overflow: hidden; min-height: 0; }}
    #filters {{ display: grid; gap: 8px; }}
    .filter-row {{ display: grid; grid-template-columns: 64px minmax(120px, 1.2fr) 110px minmax(100px, 1fr) 34px 34px; gap: 8px; align-items: center; }}
    .filter-row.first {{ grid-template-columns: minmax(120px, 1.2fr) 110px minmax(100px, 1fr) 34px; }}
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
      border: 1px solid var(--border);
      border-top: none;
      border-radius: 0 0 14px 14px;
      box-shadow: none;
      color: var(--muted);
      cursor: pointer;
      z-index: 4;
      transition: color .15s ease, filter .15s ease;
    }}
    .filters-tab:hover {{ color: var(--text); filter: brightness(1.18); transform: none; }}
    .filters-tab svg {{ width: 14px; height: 14px; transition: transform .28s ease; }}
    .filters-tab.collapsed svg {{ transform: rotate(180deg); }}
    iframe {{ border: 0; width: 100%; height: 100%; min-height: 450px; background: var(--bg-grad); }}
    @media (max-width: 760px) {{
      .filter-row {{ grid-template-columns: 50px minmax(90px, 1.2fr) 90px minmax(90px, 1fr) 30px 30px; }}
      .filter-row.first {{ grid-template-columns: minmax(90px, 1.2fr) 90px minmax(90px, 1fr) 30px; }}
    }}
  </style>
</head>
<body>
  <section class="toolbar">
    <div class="toolbar-head">
      <h1>FSAtlas</h1>
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
      return url + (url.includes('?') ? '&' : '?') + 'theme=' + theme;
    }}

    function applyTheme(nextTheme) {{
      theme = nextTheme;
      document.documentElement.dataset.theme = theme;
      themeToggle.innerHTML = theme === 'dark' ? MOON_ICON : SUN_ICON;
      try {{ localStorage.setItem('atlas-theme', theme); }} catch (e) {{}}
      if (autoMapType && mapType.value !== THEME_MAP_TYPES[theme]) {{
        mapType.value = THEME_MAP_TYPES[theme];
        applyFilters();
      }} else if (baseMapUrl) {{
        document.getElementById('map').src = withTheme(baseMapUrl);
      }}
    }}

    themeToggle.addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark'));
    applyTheme(theme);

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
      const value = row.querySelector('.value');
      value.type = column.numeric ? 'number' : 'text';
      value.step = column.numeric ? 'any' : '';
      value.placeholder = column.example ? 'e.g. ' + column.example : 'Value';
    }}

    function refreshRows() {{
      [...filters.children].forEach((row, index) => row.classList.toggle('first', index === 0));
    }}

    function addRow(afterRow) {{
      const row = document.createElement('div');
      row.className = 'filter-row';
      row.innerHTML = '<select class="logic"><option>AND</option><option>OR</option></select><select class="column"><option value="">Select Filter...</option></select><select class="operator"></select><input class="value" placeholder="Value"><button class="icon insert" title="Insert filter below" aria-label="Insert filter below">+</button><button class="icon remove" title="Remove filter" aria-label="Remove filter">×</button>';
      const columnSelect = row.querySelector('.column');
      columns.forEach(column => columnSelect.add(new Option(column.name, column.id)));
      columnSelect.addEventListener('change', () => updateOperators(row));
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
      document.getElementById('status').textContent = result.count.toLocaleString() + ' flights';
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

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            columns = [
                {"id": column, "name": config.COLUMN_DISPLAY_NAMES.get(column, column),
                 "numeric": bool(pd.api.types.is_numeric_dtype(self.state.df[column])),
                 "example": column_example(self.state.df[column])}
                for column in self.state.df.columns
            ]
            self.send_html(index_html(columns))
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
            self.send_html(mapping.create_map_html(
                dataframe, map_type, theme_mode, self.state.airport_counts,
                route_request_url=f"/api/routes/{view_id}",
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
        if self.path != "/api/maps":
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
    AtlasRequestHandler.state = AtlasState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), AtlasRequestHandler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"Flightsim Atlas web UI: {url}")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()