# Flight Data Schema

FSAtlas reads a single CSV file at `run/database/flights.csv`. This page documents its
exact columns, how they're used, and a couple of small automatic transformations applied
when the file is loaded.

## Columns

| Column                   | Type   | Description                                                                 |
|--------------------------|--------|-------------------------------------------------------------------------------|
| `owner`                  | text   | Operating airline. Any `(...)` suffix (livery/anniversary stickers) is stripped automatically for filtering — "Saudia (SkyTeam Livery)" is grouped under "Saudia". |
| `reg`                    | text   | Aircraft registration (e.g. `N88888`). Not shown in filters.                   |
| `type`                   | text   | Full aircraft type name (e.g. `Acmebus A320`).                                 |
| `type_icao`              | text   | Aircraft ICAO type designator (e.g. `A320`).                                   |
| `flight_number`          | text   | Flight number.                                                                 |
| `calsign`                | text   | ATC callsign (note the column name's spelling — no double "l").               |
| `dep_airport`            | text   | Departure airport full name.                                                  |
| `dep_airport_iata`       | text   | Departure airport IATA code.                                                   |
| `dep_airport_icao`       | text   | Departure airport ICAO code.                                                   |
| `dep_airport_city`       | text   | Departure city.                                                               |
| `dep_airport_country`    | text   | Departure country.                                                            |
| `dep_airport_lat`        | number | Departure airport latitude.                                                   |
| `dep_airport_lon`        | number | Departure airport longitude.                                                  |
| `dep_airport_elevation`  | number | Departure airport elevation, in feet.                                        |
| `arr_airport`            | text   | Arrival airport full name.                                                    |
| `arr_airport_iata`       | text   | Arrival airport IATA code.                                                    |
| `arr_airport_icao`       | text   | Arrival airport ICAO code.                                                    |
| `arr_airport_city`       | text   | Arrival city.                                                                 |
| `arr_airport_country`    | text   | Arrival country.                                                              |
| `arr_airport_lat`        | number | Arrival airport latitude.                                                     |
| `arr_airport_lon`        | number | Arrival airport longitude.                                                    |
| `arr_airport_elevation`  | number | Arrival airport elevation, in feet.                                          |
| `distance`               | number | Great-circle distance, in **nautical miles** (not km).                        |
| `rough_flight_time`      | number | Estimated flight time, in hours.                                              |
| `timestamp_read`         | text   | When this flight record was discovered/read, e.g. `03th Nov 2024 at 11:24`.    |

### Example row

```csv
owner,reg,type,type_icao,flight_number,calsign,dep_airport,dep_airport_iata,dep_airport_icao,dep_airport_city,dep_airport_country,dep_airport_lat,dep_airport_lon,dep_airport_elevation,arr_airport,arr_airport_iata,arr_airport_icao,arr_airport_city,arr_airport_country,arr_airport_lat,arr_airport_lon,arr_airport_elevation,distance,rough_flight_time,timestamp_read
ACME Airlines,N88888,Acmebus A320,A320,AA88,AAL88,San Francisco International Airport,SFO,KSFO,San Francisco,United States,37.61881,-122.37542,13.1,Singapore Changi International Airport,SIN,WSSS,Singapore,Singapore,1.35019,103.994,22,7333,15.76,03th Nov 2024 at 11:24
```

## Derived columns (not in the CSV)

A couple of extra columns are computed in-memory every time FSAtlas loads the data, and
are never written back to the CSV file:

- **`dep_airport_region` / `arr_airport_region`** — a coarse region (Africa, Asia, Central
  America, Europe, Middle East, North America, Oceania, South America, Antarctica),
  looked up from the country column via the UN geoscheme. This powers the **Location**
  filter category's region option.
- Airline livery/sticker suffixes are stripped from `owner` as described in the table
  above.

## Rows that get discarded

Any row whose `rough_flight_time` is greater than **25 hours** is silently excluded when
the dataset loads (it's treated as an implausible single flight leg, e.g. bad source
data). You'll see a line like `Discarded 16 flight(s) exceeding 25 hours` printed to the
console on startup — that's expected, not an error.

## Where this data comes from

For now, you need to source your own flight data from tracking APIs or other freely
available downloads and shape it into the format above — FSAtlas doesn't collect it for
you (yet). If you have flight data that could be incorporated into the project more
broadly, see [Contributing](../community/contributing.md).

## Adding new flights with the import tool

Instead of hand-editing the CSV, you can append new records from a JSON file:

```bash
python -m run.import_flights path/to/new_flights.json
python -m run.import_flights path/to/new_flights.json --dry-run   # preview without writing
```

The JSON file is either a bare list of flight objects, or a dict with the list under a
`flights`/`data`/`results` key. Each object should use the same field names as the CSV
columns above. A few conveniences the importer handles for you:

- **De-duplication** — a record matching an existing row's `reg` + `flight_number` +
  `dep_airport_iata` + `arr_airport_iata` + `timestamp_read` is skipped.
- **Flight time estimation** — if you don't supply `rough_flight_time` yourself, it's
  estimated from `distance` and the aircraft's cruise speed (looked up by `type_icao` in
  `run/database/aircraft_crz_speeds.json`), plus a climb/descent buffer. Unknown ICAO
  types are left without an estimate rather than guessed.
- The same 25-hour sanity check described above is applied to anything the importer adds.
