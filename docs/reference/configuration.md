# Configuration

## Command-line flags

All of these apply to `python -m run` / `python -m run.webapp` (the interface this wiki
documents) and the Docker image, which just sets the environment variable equivalents
below instead of passing flags directly.

| Flag                    | Default                  | Description                                                              |
|-------------------------|---------------------------|---------------------------------------------------------------------------|
| `--host`                | `127.0.0.1`               | Interface to bind to. Use `0.0.0.0` to accept connections from other devices/containers. |
| `--port`                | `5050`                    | Port to bind to.                                                           |
| `--debug`               | off                       | Enables Flask's debug mode and autoreloader (template/static/code changes apply without restarting). |
| `--no-browser`          | off                       | Don't try to open a browser window automatically.                         |
| `-i`, `--import FILE`   | —                         | Import flight records from a JSON file into `flights.csv`, then exit without starting the server — see [Flight Data Schema](flight-data-schema.md#adding-new-flights-with-the-import-tool). |

```bash
python -m run --host 0.0.0.0 --port 8080 --no-browser
```

## Environment variables

Each flag above has an environment-variable equivalent (used instead of a flag, e.g. in
Docker), plus one extra for where data files live:

| Variable             | Default                    | Equivalent to      |
|----------------------|------------------------------|---------------------|
| `FSATLAS_HOST`       | `127.0.0.1`                 | `--host`            |
| `FSATLAS_PORT`       | `5050`                      | `--port`            |
| `FSATLAS_DEBUG`      | unset (falsy)               | `--debug`           |
| `FSATLAS_NO_BROWSER` | unset (falsy)               | `--no-browser`      |
| `FSATLAS_DATA_DIR`   | `run/` (next to the app code) | *(no flag — see below)* |

`FSATLAS_DATA_DIR` controls where `settings.json`, `saved_items.json`, and
`installed_scenery.json` are read from/written to. The Docker image sets this to `/data`
so those files can be bind-mounted outside the container — see
[Installation → Data persistence](../getting-started/installation.md#data-persistence).

## Persisted settings files

These live inside `FSATLAS_DATA_DIR` and are created automatically with sensible
defaults on first run if they don't exist. You generally don't need to hand-edit them —
everything in them is managed from the UI — but they're documented here in case you want
to back them up, migrate them, or inspect them directly.

### `settings.json`

```json
{
  "theme": "dark",
  "simbrief_pilot_id": "",
  "scenery_overlay": false,
  "airports_overlay": true
}
```

| Key                  | Values                | Set from                                   |
|----------------------|------------------------|---------------------------------------------|
| `theme`              | `"dark"` \| `"light"` | Theme toggle (top bar)                      |
| `simbrief_pilot_id`  | string                | Settings panel                              |
| `scenery_overlay`    | boolean               | Map-style popover's Scenery Overlay toggle  |
| `airports_overlay`   | boolean               | Whether airport dots render at all (used internally by the scenery feature) |

### `saved_items.json`

```json
{
  "saved_flights": [
    {
      "reg": "G-XLEC", "flight": "BA106", "dep": "DXB", "arr": "LHR",
      "date": "08/11/2024", "airline": "British Airways", "type": "Airbus A380-841",
      "type_icao": "A388", "distance": 2969, "flight_time_hours": 6.65,
      "saved_at": "2026-10-09T11:20:01.706329+00:00"
    }
  ],
  "saved_searches": [
    {
      "id": "ncinHOr2SGs",
      "description": "British Airways A380s from the Middle East",
      "filters": { "kind": "group", "logic": "AND", "children": [ "..." ] },
      "saved_at": "2026-10-09T11:16:35.458428+00:00"
    }
  ]
}
```

A saved flight's identity (used to detect "already saved"/de-duplicate) is the
combination of `reg` + `flight` + `dep` + `arr` + `date`.

### `installed_scenery.json`

Written wholesale by the [scenery overlay](../tutorials/advanced-features.md#scenery-overlay)
import — not meant to be hand-edited.

```json
{
  "sceneries": [ { "icao": "EGLL", "iata": "LHR", "name": "London Heathrow Airport" } ],
  "imported_at": "2026-10-09T11:00:00+00:00",
  "unmatched_count": 0,
  "errors": []
}
```

## Customizing columns and map tiles (source edits)

A few things are only configurable by editing `run/config.py` directly in your checkout
(there's no UI for these, since they affect every user of a given deployment):

- **`COLUMN_DISPLAY_NAMES`** — the human-readable label shown for each CSV column
  throughout the filter UI.
- **`FILTER_COLUMNS`** — reserved for a future flat filter-column allow-list; the current
  UI's five categories are defined in the frontend instead (see `run/webapp/data.py`'s
  `MULTISELECT_COLUMNS` if you want to add a new chip-pickable column).
- **`TILES`** — the five map tile providers (Dark Mode, Light Mode, Standard, Satellite,
  Hybrid) and their URL templates/attribution text.

Restart the app after editing `run/config.py` (or rebuild the Docker image) for changes
to take effect.
