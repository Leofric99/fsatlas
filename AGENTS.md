# AGENTS.md

Guidance for AI coding agents working in this repository. Read this before making changes.

## Project overview

FSAtlas is a self-hosted, browser-based tool for visualising real-world flight data (loaded
from `run/database/flights.csv`) on an interactive world map, similar to FlightConnections.
Airports render as canvas-based markers colour-coded by destination count; clicking one shows
its routes, and clicking a route shows the individual flights on it. Users can build nested
AND/OR filter trees (airline, aircraft type, country, distance, etc.) and save flights/searches.

It's a small personal project maintained by one person with AI-agent assistance (see the
README's note). There is **no CI, no automated test suite, and no linter config** — changes
are verified manually via a running dev server (see "Verifying changes" below).

## Repository layout

```
run/
  __main__.py         # `python -m run` entry point -> delegates to run.webapp.__main__.main()
  web_gui.py           # OLD frontend: hand-rolled http.server app, still `uv run fsatlas`'s
                       #   entry point (pyproject.toml [project.scripts]). Renders a toolbar/
                       #   filter-builder page (index_html(), one big f-string) with the map
                       #   in an <iframe> at /map/<view_id> (run/html/map.html, Jinja2).
  webapp/              # NEW frontend: Flask app, the default for `python -m run` and Docker.
    __init__.py        # App factory (create_app()), calls data.load()/storage.ensure_data_files()
    __main__.py        # CLI: --host/--port/--debug/--no-browser/-i/--import
    data.py            # Loads/caches the flight dataset, wraps data_loader/filtering/config
    storage.py         # settings.json / saved_items.json persistence (ported from web_gui.py)
    routes.py          # Flask blueprint: GET /, /api/meta, /api/airports, /api/flights, etc.
    templates/index.html
    static/app.js       # All client JS - single IIFE, vanilla, no build step
    static/app.css      # Full design-system stylesheet (CSS custom properties, dark/light)
  data_loader.py, filtering.py, config.py, mapping.py  # Shared data/filtering logic
  simbrief_api.py       # SimBrief prefill-link export + Pilot ID verification (no API key)
  import_flights.py     # `-i/--import` CSV import helper
  single_instance.py, windows_tray.py  # Desktop-launcher niceties (old app only)
  database/flights.csv  # User-supplied flight data (see README "Flight Data" for the schema)
  settings.json, saved_items.json      # Runtime data, git-tracked with `skip-worktree`
.dev/
  docker-release.sh    # Build/push/cleanup script for the Docker Hub image
Dockerfile, docker-compose.yml   # Container build/run (webapp is the image's CMD)
```

### Two parallel frontends — know which one you're editing

- **`run/webapp/`** is the active, actively-developed frontend. `python -m run` and the
  Docker image both launch it. Do your UI/feature work here unless told otherwise.
- **`run/web_gui.py`** + **`run/html/map.html`** is the original implementation, deliberately
  left untouched as a fallback/comparison baseline. It's still what `uv run fsatlas` launches
  (via the `[project.scripts]` entry in `pyproject.toml`) since that hasn't been cut over yet.
  Don't "fix" it opportunistically while working on `webapp/` — they're intentionally kept
  independent during the migration.

If a task doesn't specify which frontend, assume `run/webapp/`.

## Running the app locally

```bash
# New Flask app (default; add --debug for autoreload, --no-browser for headless dev servers)
FSATLAS_PORT=5050 python3 -m run.webapp --debug --no-browser
# or, equivalently, via the top-level entry point:
FSATLAS_PORT=5050 python3 -m run --debug --no-browser

# Old http.server app (only if explicitly asked to touch it)
FSATLAS_PORT=5050 python3 -m run.web_gui --no-browser
```

A `run/database/flights.csv` populated per the README's documented schema is required for
the app to show data — an empty/missing file will still start the server but the map will
be empty.

`--debug` on the Flask app enables the autoreloader for templates/static/Python. The old
`web_gui.py` app does **not** hot-reload — kill and restart the process after editing it.

## Verifying changes

There's no automated test suite. Changes to `run/webapp/` should be verified against a real
running dev server:

1. Start the server as above (background/async, since it's long-running).
2. Load `http://127.0.0.1:<port>/` and exercise the affected feature.
3. Check for console/page errors.
4. For canvas-rendered elements (Leaflet `circleMarker`s have no DOM node — regular
   coordinate-based clicks are unreliable), prefer driving state directly (e.g. a temporary
   `window.__fsatlasDebug` hook exposing `map`/`airports`/etc., or firing Leaflet events
   directly on marker objects) over trusting screenshot-derived pixel coordinates. Remove
   any temporary debug hooks before finishing.
5. Sanity-check both dark/light themes and the mobile breakpoint (`max-width: 760px`) for
   any layout change, since `run/webapp/` uses one shared responsive layout, not separate
   mobile/desktop code paths.

## Conventions & gotchas

- **No JS build step.** `static/app.js` is one plain IIFE; `static/app.css` uses CSS custom
  properties for theming (`:root` = dark, `:root[data-theme="light"]` = light, same token
  names in both). Don't introduce a bundler/framework without being asked.
- **Settings/saved-items files** (`run/settings.json`, `run/saved_items.json`) are git-tracked
  but marked `skip-worktree`, so `git status`/`git checkout` won't show or revert local edits.
  Storage location is overridable via `FSATLAS_DATA_DIR` (Docker sets it to `/data`).
- **f-string HTML in `web_gui.py`**: if you ever touch it, every literal `{`/`}` in the huge
  template string must be doubled — including inside comments (`/* ... */`, `//`). This fails
  at *render* time (`NameError`), not at import time, so a syntax check alone won't catch it.
- **CSS layout pitfall**: giving a `display:grid`/`flex` container a non-`visible` `overflow`
  floors its children's automatic minimum size to 0 — this squashes content instead of
  scrolling it. If something needs to scroll, prefer a plain block wrapper with
  `overflow-y: auto` around the grid/flex content, not `overflow` on the grid/flex container
  itself.
- **Toggling visibility**: use a dedicated class (`.foo.open { display: ... }`, base rule
  `display:none`) rather than the `hidden` attribute — an unconditional author `display:flex`
  rule elsewhere silently beats the `[hidden]` UA rule.
- **Don't bound a Leaflet map view to just two route endpoints** (`fitBounds([[src],[dest]])`)
  — a geodesic curve between distant airports can bulge far outside that simple bounding box.
  Build bounds from the actual computed curve points instead.

## Data & schema notes

- Flight CSV schema is documented in the README's "Flight Data" section — keep any loader
  changes (`run/data_loader.py`) and the README's documented column list in sync.
- `run/filtering.py`'s `apply_filters` operates on a nested filter tree:
  `{'kind': 'group', 'logic': 'AND'|'OR', 'children': [...]}`, leaves being condition dicts
  (`column`/`operator`/`value`/`type`/`logic`). A node's `logic` means "how do I combine with
  the previous sibling in my parent's children list." Legacy flat-list/dict shapes are still
  accepted and auto-converted.

## Packaging

`pyproject.toml` uses `hatchling`, with an explicit `include` list (dataset/HTML/templates/
static assets don't otherwise ship under hatchling's default VCS-based file selection) plus a
`force-include` for `run/settings.json` (which would otherwise be skipped as a gitignored
pattern). If you add new non-`.py` runtime assets under `run/`, add them to `include` too.
