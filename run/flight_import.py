"""Import flights from an old-style flights.csv, a JSON file or another FSAtlas flight
database (the Export button's output) into the flight database.

Used by the GUI drop zone (POST /api/import) and by the command line
(`python -m run.import_flights FILE [--dry-run]`).

Accepted input:
  * CSV  - the old 25-column flights.csv layout (`reg` is accepted and ignored).
  * JSON - a bare list of flight objects, or a dict holding the list under `flights`, `data`
           or `results`. Objects use the same field names as the CSV columns.
  * DB   - an FSAtlas flight database (.db/.sqlite/.sqlite3), e.g. from the Export button. It
           is merged table by table; flights are matched on their exact identity (airline,
           flight number, route, aircraft type, year and season), so every season survives
           the round trip and anything already present is ignored.

Behaviour (all reported in the returned dict):
  * `timestamp_read` is converted to year + season; the exact date/time is not stored.
  * Rows with no callsign, an estimated duration over 25 hours, or unusable data are skipped.
  * A flight is a duplicate - and skipped - if the database (or an earlier row of the same
    file) already has the same flight number, departure, arrival and aircraft ICAO type,
    regardless of airline name, date or season.
  * Airports, airlines and aircraft types that the database doesn't have are created from the
    file's own columns.
  * JSON imports always recompute `rough_flight_time` from distance and the aircraft's cruise
    speed (as the original JSON importer did); CSV imports keep the supplied time and only
    estimate blanks.
"""
import json
import math
import os
import re
import sqlite3
from datetime import date, datetime

import pandas as pd

from run import flightdb
from run.data_loader import MAX_FLIGHT_TIME_HOURS, UNREGION_TO_REGION

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CRZ_SPEEDS_FILE = os.path.join(CURRENT_DIR, "database", "aircraft_crz_speeds.json")

# nm -> hours conversion factor applied on top of raw cruise TAS to approximate
# routing overhead (indirect routing, holding, etc).
FLIGHT_TIME_DISTANCE_FACTOR = 1.07

# Ascent/descent buffer coefficient, in hours*knots.
#
# Climbing and descending happen at roughly 75% of cruise TAS, so covering a nominal
# 250nm of climb+descent at 0.75x cruise speed takes longer than covering that same
# 250nm at full cruise speed - the difference is time "lost" to the climb/descent that
# a pure distance/cruise_speed calculation wouldn't otherwise account for:
#
#   buffer (hours) = 250 / (0.75 * cruise_tas_knots) - 250 / cruise_tas_knots
#                   = (250 / 0.75 - 250) / cruise_tas_knots
#                   = 83.33 / cruise_tas_knots
#
# BUFFER_COEFFICIENT is that constant 83.33 (= 250/0.75 - 250), so the buffer for a
# given aircraft simplifies to BUFFER_COEFFICIENT / cruise_tas_knots.
BUFFER_COEFFICIENT = 83.33

# Column order of the old flights.csv - JSON records use these same field names.
REQUIRED_COLUMNS = [
    "owner", "reg", "type", "type_icao", "flight_number", "calsign",
    "dep_airport", "dep_airport_iata", "dep_airport_icao", "dep_airport_city", "dep_airport_country",
    "dep_airport_lat", "dep_airport_lon", "dep_airport_elevation",
    "arr_airport", "arr_airport_iata", "arr_airport_icao", "arr_airport_city", "arr_airport_country",
    "arr_airport_lat", "arr_airport_lon", "arr_airport_elevation",
    "distance", "rough_flight_time", "timestamp_read",
]

# Another FSAtlas flight database (e.g. from the Export button) - merged table by table.
DATABASE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}

# A file must contain at least these to be recognised as flight data.
RECOGNISED_COLUMNS = {"flight_number", "dep_airport_icao", "arr_airport_icao", "timestamp_read"}

# Spelling variants/typos seen in flight sources -> the standard name used in the database.
COUNTRY_FIXES = {
    "united states of america": "United States", "united states f america": "United States",
    "united states of ameirca": "United States", "unites states of america": "United States",
    "the bahamas": "Bahamas", "the netherlands": "Netherlands", "the philippines": "Philippines",
    "algeira": "Algeria", "findland": "Finland", "laus": "Laos", "malawai": "Malawi",
    "marshallislands": "Marshall Islands", "rusia": "Russia", "taiwain": "Taiwan",
    "india": "India", "iran": "Iran", "guayana": "Guyana", "south south sudan": "South Sudan",
    "republic of kiribati": "Kiribati", "republic of nauru": "Nauru",
}

_LIVERY_SUFFIX = re.compile(r"\s*\(.*$")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


# ---------------------------------------------------------------- reading

def _extract_records(data):
    """Accept either a bare list of flight objects, or a dict wrapping them under a common key."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("flights", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("JSON file must contain a list of flight records (optionally wrapped in a 'flights' key).")


def read_records(path):
    """Read a CSV or JSON file into a string DataFrame with exactly REQUIRED_COLUMNS.
    Returns (frame, kind) where kind is 'csv' or 'json'.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        df, kind = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig"), "csv"
    elif ext == ".json":
        with open(path, "r", encoding="utf-8-sig") as f:
            records = _extract_records(json.load(f))
        df, kind = (pd.DataFrame.from_records(records) if records else pd.DataFrame(columns=REQUIRED_COLUMNS)), "json"
    else:
        raise ValueError(f"Unsupported file type '{ext or path}': use a .csv or .json file.")

    if "callsign" in df.columns:
        if "calsign" in df.columns:
            has_calsign = df["calsign"].notna() & df["calsign"].astype(str).str.strip().ne("")
            df["calsign"] = df["calsign"].where(has_calsign, df["callsign"])
            df = df.drop(columns="callsign")
        else:
            df = df.rename(columns={"callsign": "calsign"})

    if (len(df) or kind == "csv") and not RECOGNISED_COLUMNS.issubset(df.columns):
        missing = sorted(RECOGNISED_COLUMNS - set(df.columns))
        raise ValueError("Not a flights file - missing required column(s): " + ", ".join(missing))

    df = df[[c for c in REQUIRED_COLUMNS if c in df.columns]].copy()
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[REQUIRED_COLUMNS].where(pd.notnull(df), "").astype(str)
    return df.apply(lambda s: s.str.strip()), kind


# ---------------------------------------------------------------- cleaning helpers

def parse_period(text):
    """Convert a timestamp to (year, season), or None if it can't be understood. Understands
    `dd/mm/yyyy hh:mm`, `03th Nov 2024 at 11:24`, ISO `yyyy-mm-dd...` and `Summer 2025`.
    """
    text = (text or "").strip()
    m = re.fullmatch(r"(summer|winter)\s+(\d{4})", text, re.I)
    if m:
        return int(m.group(2)), m.group(1).capitalize()
    d = None
    try:
        if (m := re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)):
            d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        elif (m := re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})", text)):
            month = _MONTHS.get(m.group(2)[:3].lower())
            d = date(int(m.group(3)), month, int(m.group(1))) if month else None
        elif (m := re.match(r"(\d{4})-(\d{2})-(\d{2})", text)):
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return flightdb.season_for_date(d) if d else None


def strip_livery(name):
    return _LIVERY_SUFFIX.sub("", name).strip()


def haversine_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 3440.065 * math.asin(math.sqrt(a))


def load_cruise_speeds():
    """Return {type_icao: cruise_tas_knots}, or {} if the speeds file is missing."""
    if not os.path.exists(CRZ_SPEEDS_FILE):
        return {}
    with open(CRZ_SPEEDS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {icao: info["cruise_tas_knots"] for icao, info in data.items() if "cruise_tas_knots" in info}


def estimate_hours(distance_nm, speed_knots):
    """Flight time = distance * routing factor / cruise speed + climb/descent buffer."""
    return round(distance_nm * FLIGHT_TIME_DISTANCE_FACTOR / speed_knots + BUFFER_COEFFICIENT / speed_knots, 2)


def _region_for(countries):
    """UN-geoscheme region per country name, folded into the app's regions."""
    countries = sorted({c for c in countries if c})
    if not countries:
        return {}
    import country_converter as coco

    regions = coco.CountryConverter().convert(names=countries, to="UNregion", not_found="")
    return {c: UNREGION_TO_REGION.get(r, "") for c, r in zip(countries, regions)}


# ---------------------------------------------------------------- the import

def _prepare(df, kind, existing_icaos, speeds, report):
    """Validate and clean the raw rows, returning one candidate frame (index = original row)."""
    df = df.copy()
    df["owner"] = df["owner"].map(strip_livery).replace("", "Unknown")
    for side in ("dep", "arr"):
        df[f"{side}_airport_country"] = df[f"{side}_airport_country"].map(
            lambda c: COUNTRY_FIXES.get(c.lower(), c))
        df[f"{side}_airport_icao"] = df[f"{side}_airport_icao"].str.upper()
        for col in ("lat", "lon", "elevation"):
            df[f"{side}_airport_{col}_n"] = pd.to_numeric(df[f"{side}_airport_{col}"], errors="coerce")
    df["distance_n"] = pd.to_numeric(df["distance"], errors="coerce")
    df["hours_n"] = pd.to_numeric(df["rough_flight_time"], errors="coerce")

    periods = {t: parse_period(t) for t in df["timestamp_read"].unique()}
    df["year"] = df["timestamp_read"].map(lambda t: periods[t][0] if periods[t] else 0)
    df["season"] = df["timestamp_read"].map(lambda t: periods[t][1] if periods[t] else "")

    def airport_ok(side):
        icao = df[f"{side}_airport_icao"]
        has_coords = df[f"{side}_airport_lat_n"].notna() & df[f"{side}_airport_lon_n"].notna()
        return icao.ne("") & (icao.isin(existing_icaos) | has_coords)

    valid = (df["flight_number"].ne("") & airport_ok("dep") & airport_ok("arr") & df["year"].gt(0))
    report["skipped_invalid"] = int((~valid).sum())
    df = df[valid].copy()

    has_callsign = df["calsign"].ne("")
    report["skipped_no_callsign"] = int((~has_callsign).sum())
    df = df[has_callsign].copy()

    # Distance: use the file's value, otherwise measure it from the airport coordinates.
    missing_dist = df["distance_n"].isna()
    if missing_dist.any():
        known = {}
        for side in ("dep", "arr"):
            for icao, lat, lon in zip(df[f"{side}_airport_icao"], df[f"{side}_airport_lat_n"], df[f"{side}_airport_lon_n"]):
                if pd.notna(lat) and pd.notna(lon):
                    known.setdefault(icao, (lat, lon))
        for idx in df.index[missing_dist]:
            a, b = known.get(df.at[idx, "dep_airport_icao"]), known.get(df.at[idx, "arr_airport_icao"])
            if a and b:
                df.at[idx, "distance_n"] = round(haversine_nm(*a, *b))

    # Duration: JSON always recomputed from cruise speed; CSV keeps supplied values, fills blanks.
    if speeds:
        speed = df["type_icao"].map(speeds)
        computable = df["distance_n"].notna() & speed.notna()
        estimate = pd.Series(
            [estimate_hours(d, s) if ok else float("nan") for d, s, ok in zip(df["distance_n"], speed, computable)],
            index=df.index)
        if kind == "json":
            df["hours_n"] = estimate
        else:
            df["hours_n"] = df["hours_n"].where(df["hours_n"].notna(), estimate)
    overlong = df["hours_n"] > MAX_FLIGHT_TIME_HOURS
    report["skipped_overlong"] = int(overlong.sum())
    return df[~overlong].copy()


def import_file(path, dry_run=False, db_path=None):
    """Import one CSV/JSON/database file. Returns a dict of counts; raises ValueError for
    unusable files.
    """
    if os.path.splitext(path)[1].lower() in DATABASE_EXTENSIONS:
        return import_database(path, dry_run=dry_run, db_path=db_path)
    raw, kind = read_records(path)
    report = {
        "file": os.path.basename(path), "kind": kind, "dry_run": dry_run, "read": len(raw), "added": 0,
        "duplicates": 0, "skipped_invalid": 0, "skipped_no_callsign": 0, "skipped_overlong": 0,
        "new_airports": 0, "new_airlines": 0, "new_types": 0,
    }
    if raw.empty:
        return report

    con = flightdb.connect(db_path)
    try:
        flightdb.ensure_schema(con)
        existing_icaos = {r[0] for r in con.execute("SELECT icao FROM airports")}
        df = _prepare(raw, kind, existing_icaos, load_cruise_speeds(), report)

        # Duplicates: same flight number + route + aircraft type, within the file or already stored.
        keys = ["flight_number", "dep_airport_icao", "arr_airport_icao", "type_icao"]
        before = len(df)
        df = df.drop_duplicates(subset=keys, keep="first")
        in_db = _existing_flight_rows(con, df, keys)
        df = df[~df.index.isin(in_db)]
        report["duplicates"] = before - len(df)

        if len(df):
            report["added"] = len(df)
            report.update(_store(con, df))
        if dry_run:
            con.rollback()
        else:
            con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return report


def _existing_flight_rows(con, df, keys):
    """Index labels of `df` rows whose key already exists in the flights table."""
    con.execute("CREATE TEMP TABLE IF NOT EXISTS cand (n INTEGER PRIMARY KEY, fn TEXT, dep TEXT, arr TEXT, ticao TEXT)")
    con.execute("DELETE FROM cand")
    con.executemany("INSERT INTO cand VALUES (?,?,?,?,?)",
                    zip(df.index.tolist(), *(df[k].tolist() for k in keys)))
    rows = con.execute(
        "SELECT c.n FROM cand c WHERE EXISTS ("
        " SELECT 1 FROM flights f JOIN aircraft_types ty ON ty.type_id = f.type_id"
        " WHERE f.flight_number = c.fn AND f.dep_icao = c.dep AND f.arr_icao = c.arr AND ty.type_icao = c.ticao)"
    ).fetchall()
    con.execute("DROP TABLE cand")
    return {r[0] for r in rows}


def _store(con, df):
    """Create any missing airports/airlines/types for `df` and insert its flights."""
    created = {"new_airports": 0, "new_airlines": 0, "new_types": 0}

    existing_icaos = {r[0] for r in con.execute("SELECT icao FROM airports")}
    frames = []
    for side in ("dep", "arr"):
        frames.append(pd.DataFrame({
            "icao": df[f"{side}_airport_icao"], "iata": df[f"{side}_airport_iata"].str.upper(),
            "name": df[f"{side}_airport"], "city": df[f"{side}_airport_city"],
            "country": df[f"{side}_airport_country"], "lat": df[f"{side}_airport_lat_n"],
            "lon": df[f"{side}_airport_lon_n"], "elev": df[f"{side}_airport_elevation_n"]}))
    new_airports = pd.concat(frames)
    new_airports = new_airports[~new_airports["icao"].isin(existing_icaos)]
    new_airports = new_airports[new_airports["lat"].notna() & new_airports["lon"].notna()]
    new_airports = new_airports.drop_duplicates(subset="icao")
    if len(new_airports):
        regions = _region_for(new_airports["country"])
        con.executemany(
            "INSERT OR IGNORE INTO airports (icao, iata, name, city, country, region, lat, lon, elevation_ft)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [(r.icao, r.iata, r.name or r.icao, r.city, r.country, regions.get(r.country, ""), r.lat, r.lon,
              None if pd.isna(r.elev) else r.elev) for r in new_airports.itertuples()])
        created["new_airports"] = len(new_airports)

    airline_ids = {name.casefold(): i for i, name in con.execute("SELECT airline_id, name FROM airlines")}
    for name in df["owner"].unique():
        if name.casefold() not in airline_ids:
            airline_ids[name.casefold()] = con.execute("INSERT INTO airlines (name) VALUES (?)", (name,)).lastrowid
            created["new_airlines"] += 1

    type_ids = {(icao, name): i for i, icao, name in con.execute("SELECT type_id, type_icao, name FROM aircraft_types")}
    for icao, name in df[["type_icao", "type"]].drop_duplicates().itertuples(index=False):
        if (icao, name) not in type_ids:
            type_ids[(icao, name)] = con.execute(
                "INSERT INTO aircraft_types (type_icao, name) VALUES (?, ?)", (icao, name)).lastrowid
            created["new_types"] += 1

    rows = [
        (airline_ids[r.owner.casefold()], r.flight_number, r.calsign, type_ids[(r.type_icao, r.type)],
         r.dep_airport_icao, r.arr_airport_icao,
         None if pd.isna(r.distance_n) else float(r.distance_n),
         None if pd.isna(r.hours_n) else float(r.hours_n), int(r.year), r.season)
        for r in df.itertuples()
    ]
    con.executemany(
        "INSERT OR IGNORE INTO flights (airline_id, flight_number, callsign, type_id, dep_icao, arr_icao,"
        " distance_nm, duration_h, year, season) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return created


# ---------------------------------------------------------------- database files

_EXPECTED_COLUMNS = {
    "airports": {"icao", "iata", "name", "city", "country", "region", "lat", "lon", "elevation_ft"},
    "airlines": {"airline_id", "name"},
    "aircraft_types": {"type_id", "type_icao", "name"},
    "flights": {"airline_id", "flight_number", "callsign", "type_id", "dep_icao", "arr_icao",
                "distance_nm", "duration_h", "year", "season"},
}


def _check_flight_database(path):
    """Raise ValueError unless `path` is a SQLite file with the FSAtlas flight tables."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for table, wanted in _EXPECTED_COLUMNS.items():
                have = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
                if not wanted <= have:
                    missing = ", ".join(sorted(wanted - have)) if have else "the whole table"
                    raise ValueError(f"Not an FSAtlas flight database - table '{table}' is missing {missing}.")
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Not a valid SQLite database file ({exc}).") from exc


def import_database(path, dry_run=False, db_path=None):
    """Merge another FSAtlas flight database into this one. Airports are matched on ICAO code,
    airlines on name and aircraft types on (ICAO type, name); existing records are kept as they
    are. A flight is skipped if the same airline, flight number, route, type, year and season is
    already present.
    """
    _check_flight_database(path)
    report = {
        "file": os.path.basename(path), "kind": "database", "dry_run": dry_run, "read": 0, "added": 0,
        "duplicates": 0, "skipped_invalid": 0, "skipped_no_callsign": 0, "skipped_overlong": 0,
        "new_airports": 0, "new_airlines": 0, "new_types": 0,
    }
    con = flightdb.connect(db_path)
    try:
        flightdb.ensure_schema(con)
        con.execute("ATTACH DATABASE ? AS src", (path,))
        count = lambda table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        report["read"] = count("src.flights")
        before = {t: count(t) for t in ("airports", "airlines", "aircraft_types", "flights")}

        con.execute("INSERT OR IGNORE INTO airports (icao, iata, name, city, country, region, lat, lon, elevation_ft)"
                    " SELECT icao, iata, name, city, country, region, lat, lon, elevation_ft FROM src.airports")
        con.execute("INSERT OR IGNORE INTO airlines (name) SELECT name FROM src.airlines")
        con.execute("INSERT OR IGNORE INTO aircraft_types (type_icao, name) SELECT type_icao, name FROM src.aircraft_types")
        con.execute(
            "INSERT OR IGNORE INTO flights (airline_id, flight_number, callsign, type_id, dep_icao, arr_icao,"
            " distance_nm, duration_h, year, season)"
            " SELECT al.airline_id, f.flight_number, f.callsign, ty.type_id, f.dep_icao, f.arr_icao,"
            " f.distance_nm, f.duration_h, f.year, f.season"
            " FROM src.flights f"
            " JOIN src.airlines sal ON sal.airline_id = f.airline_id"
            " JOIN src.aircraft_types sty ON sty.type_id = f.type_id"
            " JOIN airlines al ON al.name = sal.name"
            " JOIN aircraft_types ty ON ty.type_icao = sty.type_icao AND ty.name = sty.name")

        report["added"] = count("flights") - before["flights"]
        report["duplicates"] = report["read"] - report["added"]
        report["new_airports"] = count("airports") - before["airports"]
        report["new_airlines"] = count("airlines") - before["airlines"]
        report["new_types"] = count("aircraft_types") - before["aircraft_types"]
        if dry_run:
            con.rollback()
        else:
            con.commit()
        con.execute("DETACH DATABASE src")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return report
