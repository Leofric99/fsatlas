"""SQLite flight database: location, schema and small shared helpers.

The database is normalised (airports / airlines / aircraft_types / flights) and exposes a
`flights_flat` view that has the shape of the old flights.csv (minus `reg`), so loaders and
importers can work with flat rows while storage stays compact.

One `flights` row is one scheduled/observed leg in one season (`year` + `season`). The exact
date a flight was seen is not stored - imports convert it to year + season.
"""
import os
import sqlite3
from datetime import date, timedelta

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("FSATLAS_DB") or os.path.join(CURRENT_DIR, "database", "flights.db")

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS airports (
    icao          TEXT PRIMARY KEY,
    iata          TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL,
    city          TEXT NOT NULL DEFAULT '',
    country       TEXT NOT NULL DEFAULT '',
    region        TEXT NOT NULL DEFAULT '',
    lat           REAL NOT NULL,
    lon           REAL NOT NULL,
    elevation_ft  REAL
);

CREATE TABLE IF NOT EXISTS airlines (
    airline_id  INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS aircraft_types (
    type_id    INTEGER PRIMARY KEY,
    type_icao  TEXT NOT NULL,
    name       TEXT NOT NULL,
    UNIQUE (type_icao, name)
);

CREATE TABLE IF NOT EXISTS flights (
    flight_id      INTEGER PRIMARY KEY,
    airline_id     INTEGER NOT NULL REFERENCES airlines (airline_id),
    flight_number  TEXT NOT NULL,
    callsign       TEXT NOT NULL DEFAULT '',
    type_id        INTEGER NOT NULL REFERENCES aircraft_types (type_id),
    dep_icao       TEXT NOT NULL REFERENCES airports (icao),
    arr_icao       TEXT NOT NULL REFERENCES airports (icao),
    distance_nm    REAL,
    duration_h     REAL,
    year           INTEGER NOT NULL,
    season         TEXT NOT NULL CHECK (season IN ('Summer', 'Winter'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_flights_identity
    ON flights (airline_id, flight_number, dep_icao, arr_icao, type_id, year, season);
CREATE INDEX IF NOT EXISTS ix_flights_route ON flights (flight_number, dep_icao, arr_icao);
CREATE INDEX IF NOT EXISTS ix_flights_year ON flights (year);
CREATE INDEX IF NOT EXISTS ix_flights_dep ON flights (dep_icao);
CREATE INDEX IF NOT EXISTS ix_flights_arr ON flights (arr_icao);
CREATE INDEX IF NOT EXISTS ix_airports_iata ON airports (iata);

DROP VIEW IF EXISTS flights_flat;
CREATE VIEW flights_flat AS
SELECT
    al.name              AS owner,
    ty.name              AS type,
    ty.type_icao         AS type_icao,
    f.flight_number      AS flight_number,
    f.callsign           AS callsign,
    d.name               AS dep_airport,
    d.iata               AS dep_airport_iata,
    d.icao               AS dep_airport_icao,
    d.city               AS dep_airport_city,
    d.country            AS dep_airport_country,
    d.region             AS dep_airport_region,
    d.lat                AS dep_airport_lat,
    d.lon                AS dep_airport_lon,
    d.elevation_ft       AS dep_airport_elevation,
    a.name               AS arr_airport,
    a.iata               AS arr_airport_iata,
    a.icao               AS arr_airport_icao,
    a.city               AS arr_airport_city,
    a.country            AS arr_airport_country,
    a.region             AS arr_airport_region,
    a.lat                AS arr_airport_lat,
    a.lon                AS arr_airport_lon,
    a.elevation_ft       AS arr_airport_elevation,
    f.distance_nm        AS distance,
    f.duration_h         AS rough_flight_time,
    f.season || ' ' || f.year AS timestamp_read,
    f.year               AS year,
    f.season             AS season
FROM flights f
JOIN airlines al       ON al.airline_id = f.airline_id
JOIN aircraft_types ty ON ty.type_id = f.type_id
JOIN airports d        ON d.icao = f.dep_icao
JOIN airports a        ON a.icao = f.arr_icao;
"""


def connect(path=None, readonly=False):
    """Open the flight database. With readonly=True the file must already exist."""
    path = path or DB_PATH
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        con = sqlite3.connect(path)
        con.execute("PRAGMA foreign_keys = ON")
    return con


def ensure_schema(con):
    con.executescript(SCHEMA_SQL)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    con.commit()


def export_copy(dest_path, src_path=None):
    """Write a consistent copy of the whole database to `dest_path` (SQLite online backup, so
    it is safe while the app is running). The copy is itself a valid flight database that
    can be imported back through the Import panel.
    """
    src = connect(src_path, readonly=True)
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()


def exists(path=None):
    path = path or DB_PATH
    return os.path.isfile(path) and os.path.getsize(path) > 0


def _last_sunday(year, month):
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def season_for_date(d):
    """Return (year, season) for a date using the airline schedule seasons: Summer runs
    from the last Sunday of March to the last Sunday of October, otherwise Winter. The year
    is the calendar year of the date.
    """
    start, end = _last_sunday(d.year, 3), _last_sunday(d.year, 10)
    return d.year, ("Summer" if start <= d < end else "Winter")
