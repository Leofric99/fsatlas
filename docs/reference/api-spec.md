# API Spec

FSAtlas's browser frontend talks to its Flask backend over a small JSON HTTP API. This
page documents it for anyone who wants to script against a self-hosted instance (e.g. to
pull your own filtered dataset) or just understand what's happening over the wire.

> **This is an internal API, not a versioned public contract.** It's documented here for
> transparency and convenience, but endpoints/shapes can change between releases without
> a deprecation period — there's no authentication, and it's designed to be called by the
> same browser session as the page itself, not as a general-purpose integration API.

All endpoints are relative to wherever you're running FSAtlas, e.g.
`http://127.0.0.1:5050`. Responses are JSON unless noted otherwise.

## Filter tree format

`GET /api/airports` and `GET /api/flights` both take a `filters` query parameter: a
JSON-encoded, arbitrarily-nested AND/OR tree.

```json
{
  "kind": "group",
  "logic": "AND",
  "children": [
    { "column": "owner", "operator": "equals", "value": ["British Airways"], "type": "select", "logic": "AND" },
    { "column": "distance", "operator": ">=", "value": 1000, "type": "number", "logic": "AND" }
  ]
}
```

- A **group** node (`kind: "group"`) combines its `children` left-to-right, where each
  child's own `logic` ("AND" or "OR") says how it combines with the *previous* sibling.
- A **leaf** condition has `column` (a real column id, or `combined:<dep_col>:<arr_col>`
  to match either side of a departure/arrival pair), `operator`, `value`, and `type`
  (`"text"`, `"number"`, or `"select"`).
- `type: "select"` accepts either a single value or a list (list = "is any of"); add
  `"operator": "not_equals"` to invert it ("is none of").
- `type: "number"` operators: `equals`, `>`, `<`, `>=`, `<=`.
- `type: "text"` operators: `equals`, `contains`, `starts_with`, `ends_with`.
- Omit `filters` entirely (or pass an empty object) for "no filter" / match everything.

## Endpoints

### `GET /api/meta`

Everything the frontend needs at startup: filterable columns, available map tile styles,
and the current persisted settings.

```json
{
  "columns": [ { "id": "owner", "name": "Airline", "numeric": false, "options": ["..."], "multi": true, "group": "Company" } ],
  "map_types": { "Dark Mode": { "url": "...", "attr": "..." } },
  "theme": "dark",
  "simbrief_pilot_id": "",
  "scenery_overlay": false,
  "airports_overlay": true
}
```

### `GET /api/airports?filters=<json>`

The airports matching the given filters, keyed by IATA code, plus a total flight count.

```json
{
  "airports": {
    "LHR": { "iata": "LHR", "icao": "EGLL", "name": "London Heathrow Airport", "city": "London", "country": "United Kingdom", "lat": 51.4706, "lon": -0.461941, "rank": 3 }
  },
  "count": 175191
}
```

### `GET /api/flights?iata=<IATA>&filters=<json>`

Every flight touching the given airport (as departure or arrival) under the given
filters — a list of flight records:

```json
[
  {
    "dep": "LHR", "arr": "JFK", "flight": "BA117", "type": "Boeing 777-236", "callsign": "BAW117",
    "type_icao": "B772", "reg": "G-VIIO", "dep_icao": "EGLL", "arr_icao": "KJFK",
    "dep_name": "London Heathrow Airport", "arr_name": "John F Kennedy International Airport",
    "dep_city": "London", "arr_city": "New York", "airline": "British Airways",
    "date": "2024-11-08", "flight_time_hours": 7.8, "distance": 2979
  }
]
```

`flight_time_hours`/`distance` are `null` when the source data didn't have them.

### `GET` / `POST` `/api/settings`

`GET` returns the full current settings object (see
[Configuration](configuration.md)). `POST` does a **partial** merge — send
only the keys you want to change:

```json
{ "theme": "light" }
```

Accepted keys: `theme` (`"dark"`/`"light"`), `simbrief_pilot_id` (string),
`scenery_overlay` / `airports_overlay` (booleans). Returns `{"ok": true}`.

### `GET` / `POST` / `DELETE` `/api/saved-flights`

- `GET` — list every saved flight.
- `POST` — body is a flight record dict (upserted by `reg`+`flight`+`dep`+`arr`+`date`).
  Returns `{"ok": true, "saved_flights": [...]}`.
- `DELETE` — body is `{reg, flight, dep, arr, date}` identifying which saved flight to
  remove. Returns `{"ok": true, "saved_flights": [...]}`.

### `GET` / `POST` / `DELETE` `/api/saved-searches`

- `GET` — list every saved search.
- `POST` — body `{"description": "...", "filters": {...filter tree...}}`. Returns
  `{"ok": true, "saved_searches": [...]}` (the new search includes a server-generated
  `id`).
- `DELETE` — body `{"id": "..."}`. Returns `{"ok": true, "saved_searches": [...]}`.

### `POST /api/simbrief/verify`

Body `{"pilot_id": "..."}`. Checks the ID/username resolves to a real SimBrief account
via SimBrief's public, key-free lookup.

```json
{ "ok": true, "message": "..." }
```

### `POST /api/simbrief/export`

Body is a flight record dict (same shape as a saved flight). Returns a pre-filled
SimBrief dispatch URL built from the stored Pilot ID + the flight's origin/destination/
type/registration/callsign:

```json
{ "url": "https://www.simbrief.com/system/dispatch.php?..." }
```

### `GET /api/scenery`

The current contents of `installed_scenery.json` (see
[Configuration](configuration.md)).

### `GET /api/scenery/airports`

The full, unfiltered airport directory (every airport in your dataset, regardless of
current filters), sorted by city then name — used to populate the manual
package-to-airport resolver.

### `POST /api/scenery/import`

Body: `{"candidates": [{"icao"?, "lat"?, "lon"?, "source"}], "scan_errors"?: [...]}` — the
raw scan results produced client-side (see
[Advanced Features](../tutorials/advanced-features.md#how-the-scan-works)). The server
matches each candidate against the real airport directory (exact ICAO, then nearest
airport within 1 mile) and **replaces** `installed_scenery.json` wholesale.

```json
{ "ok": true, "matched": 42, "unmatched": 3, "sceneries": [ "..." ], "errors": [ "..." ] }
```

### `POST /api/scenery/resolve`

Body `{"source": "<package name>", "airport_id": "<ICAO or IATA>"}` — manually assigns an
unmatched scenery package (from a previous import) to a specific airport. Returns the
updated scenery object (same shape as `GET /api/scenery`).
