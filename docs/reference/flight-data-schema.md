# Flight Data Schema

FSAtlas reads its flights from a SQLite database at `run/database/flights.db`. Set
`FSATLAS_DB` to use a different file (the Docker image uses `/data/flights.db`). This page
documents the tables, the `flights_flat` view the app loads, and how files are imported.

## Tables

One `flights` row is one flight leg in one season. The exact date a flight was seen is
**not** stored - only its `year` and `season`.

| Table            | Columns |
|------------------|---------|
| `airports`       | `icao` (primary key), `iata`, `name`, `city`, `country`, `region`, `lat`, `lon`, `elevation_ft` |
| `airlines`       | `airline_id` (primary key), `name` (unique) |
| `aircraft_types` | `type_id` (primary key), `type_icao`, `name` - unique on (`type_icao`, `name`) |
| `flights`        | `flight_id`, `airline_id`, `flight_number`, `callsign`, `type_id`, `dep_icao`, `arr_icao`, `distance_nm`, `duration_h`, `year`, `season` (`Summer` or `Winter`) |

`flights` has a unique index on (`airline_id`, `flight_number`, `dep_icao`, `arr_icao`,
`type_id`, `year`, `season`), plus indexes on the route, year and each airport. The
schema version is stored in SQLite's `user_version`.

Airports are keyed by **ICAO** code. IATA codes are not unique (e.g. a defunct airport and
its replacement can share one); where the map needs a single airport per IATA code it uses
the one with the most recent flights.

## The `flights_flat` view

The app loads the `flights_flat` view, which joins the tables back into one row per flight
with the columns of the old `flights.csv` (minus `reg`):

| Column                   | Type   | Description |
|--------------------------|--------|-------------|
| `owner`                  | text   | Airline. Any `(...)` suffix (livery/anniversary stickers) is stripped when the app loads, so "Saudia (SkyTeam Livery)" is grouped under "Saudia". |
| `type`                   | text   | Full aircraft type name (e.g. `Airbus A350-900`). |
| `type_icao`              | text   | Aircraft ICAO type designator (e.g. `A359`). |
| `flight_number`          | text   | Flight number, including the airline's IATA prefix. |
| `callsign`               | text   | ATC callsign. The app exposes it as `calsign` (legacy spelling). |
| `dep_airport`            | text   | Departure airport full name. |
| `dep_airport_iata`       | text   | Departure airport IATA code. |
| `dep_airport_icao`       | text   | Departure airport ICAO code. |
| `dep_airport_city`       | text   | Departure city. |
| `dep_airport_country`    | text   | Departure country. |
| `dep_airport_region`     | text   | Region (Africa, Asia, Central America, Europe, Middle East, North America, Oceania, South America). |
| `dep_airport_lat` / `dep_airport_lon` | number | Departure airport coordinates. |
| `dep_airport_elevation`  | number | Departure airport elevation, in feet (may be empty). |
| `arr_airport` ... `arr_airport_elevation` | | The same set of columns for the arrival airport. |
| `distance`               | number | Great-circle distance, in **nautical miles**. |
| `rough_flight_time`      | number | Estimated flight time, in hours. |
| `timestamp_read`         | text   | Schedule period, e.g. `Summer 2025`. For display only. |
| `year`, `season`         | number, text | The two parts of `timestamp_read`; usable as filters. |

## What gets loaded

On startup the app loads every flight in the database, except any whose `rough_flight_time`
is greater than **25 hours** (treated as bad data). Repeated text columns are held as
pandas categoricals, so the full dataset (~1.9 million flights) uses a few hundred MB.

To load only part of the database, set `FSATLAS_MIN_YEAR` and/or `FSATLAS_MAX_YEAR` (inclusive).

## Importing flights

Drop `.csv`, `.json` or `.db` files onto the **Import & Export** panel (rail button "Import") in the GUI, or run:

```bash
python -m run.import_flights path/to/flights.csv
python -m run.import_flights path/to/new_flights.json --dry-run   # preview without writing
python -m run --import path/to/flights.csv                         # same, via the app entry point
python -m run.import_flights path/to/fsatlas-flights-20261023.db   # merge another database
```

### Accepted formats

- **Old-style `flights.csv`** - the original 25 columns: `owner, reg, type, type_icao,
  flight_number, calsign, dep_airport, dep_airport_iata, dep_airport_icao, dep_airport_city,
  dep_airport_country, dep_airport_lat, dep_airport_lon, dep_airport_elevation, arr_airport, ...,
  arr_airport_elevation, distance, rough_flight_time, timestamp_read`. `reg` is accepted and ignored.
- **JSON** - either a bare list of flight objects, or a dict with the list under a
  `flights`/`data`/`results` key. Each object uses the same field names as the CSV columns.
  `callsign` is accepted as an alias for `calsign`; unknown fields are ignored and missing
  fields are treated as blank.

A file must contain at least the `flight_number`, `dep_airport_icao`, `arr_airport_icao` and
`timestamp_read` columns to be recognised.

- **Database (`.db`, `.sqlite`, `.sqlite3`)** - another FSAtlas flight database, such as the file the
  Export button produces. It is merged table by table: airports are matched on ICAO code, airlines on
  name and aircraft types on (ICAO type, name), and existing records are kept as they are. Unlike CSV
  and JSON files, flights are matched on their **exact identity** (airline, flight number, route,
  aircraft type, year and season), so every season is kept and only flights that are already present
  are ignored. Rows are copied as stored: none of the CSV/JSON cleaning or skip rules apply. A SQLite
  file without the FSAtlas tables is rejected.

### What the importer does

- **Dates:** `timestamp_read` is converted to year and season. It understands `dd/mm/yyyy hh:mm`,
  `03th Nov 2024 at 11:24`, ISO `yyyy-mm-dd...` and `Summer 2025`. Dates from the last Sunday in
  March to the last Sunday in October are `Summer`; all others are `Winter`; the year is the calendar
  year of the date. The exact date and time are discarded.
- **Duplicates:** a flight is skipped if the database - or an earlier row in the same file - already
  has the same flight number, departure, arrival and aircraft ICAO type, whatever the airline name,
  date or season.
- **New reference data:** airports, airlines (livery suffixes stripped) and aircraft types that the
  database doesn't have are created from the file's own columns. A new airport needs valid coordinates
  in the file, otherwise its rows are skipped as unusable.
- **Skipped rows:** no callsign, estimated duration over 25 hours, or missing flight number, airport
  or date.
- **Distance:** taken from the file; if blank, measured from the airport coordinates.
- **Flight time:** for JSON files `rough_flight_time` is always recomputed from distance and the
  aircraft's cruise speed (`run/database/aircraft_crz_speeds.json`):
  `distance x 1.07 / cruise_tas + 83.33 / cruise_tas`, leaving it blank if the type has no cruise speed.
  For CSV files the supplied value is kept and only blanks are estimated.
- **Countries** are normalised for known typos and variants (e.g. `United States of America` becomes
  `United States`).

Imports are one transaction: if anything fails, nothing from that file is written. Each import
returns counts of flights read, added, duplicates and skipped rows, and of new airports, airlines and
aircraft types.

Imported CSV and JSON files are kept in `run/import/processed/`; ones that failed go to
`run/import/failed/` (both under `FSATLAS_DATA_DIR` if it is set). Database files are deleted after the
import (the exported copy is the original), whether it succeeded or not.

## Exporting the database

The **Export database...** button in the Import & Export panel saves a snapshot of the whole database
(`fsatlas-flights-YYYYMMDD.db`, the same size as `flights.db`). It is a complete, valid flight database,
so it works as a backup and can be imported into another install by dropping it on the Import box.

- In browsers with the File System Access API (Chrome and Edge, on `localhost` or HTTPS) a save dialog
  lets you choose the folder and file name, and the file is streamed straight to disk.
- In other browsers (Firefox, Safari) or on plain `http://` addresses other than `localhost`, the
  export is a normal download into the browser's downloads folder.

The snapshot is taken with SQLite's online backup, so it is safe while the app is running, and imports
are held back until it finishes. The endpoint is `GET /api/export`.

## Building the database

`.dev/build_flights_db.py` builds a database from a flat table with the old CSV columns (minus `reg`):

```bash
.venv/bin/python .dev/build_flights_db.py /path/to/flat_flights.db
```
