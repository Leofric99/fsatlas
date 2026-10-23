"""Load flight data from the SQLite flight database into the DataFrame shape the web app uses.

The frame has the columns of the old flights.csv minus `reg` (`calsign` keeps its legacy
spelling), plus `year` and `season`. Repeated text columns are pandas categoricals built
straight from the database's integer keys, which keeps the full dataset (~1.8M rows) at a few
hundred MB instead of several GB.

Optional environment variables restrict what is loaded:
    FSATLAS_MIN_YEAR / FSATLAS_MAX_YEAR   only load flights in this (inclusive) year range
"""
import os
import re

import numpy as np
import pandas as pd

from run import flightdb
from run.data_loader import MAX_FLIGHT_TIME_HOURS

AIRPORT_COLUMNS = ("airport", "airport_iata", "airport_icao", "airport_city", "airport_country",
                   "airport_region", "airport_lat", "airport_lon", "airport_elevation")

_LIVERY_SUFFIX = re.compile(r"\s*\(.*$")


def _env_year(name):
    value = os.environ.get(name, "").strip()
    return int(value) if value.isdigit() else None


def strip_livery(name):
    """"Saudia (SkyTeam Livery)" -> "Saudia" - groups special-livery variants under one airline."""
    return _LIVERY_SUFFIX.sub("", name).strip()


def load_airports(con):
    """One row per airport (indexed by ICAO), with `last_year` = latest year it has flights in
    so shared IATA codes can prefer the airport still in use.
    """
    airports = pd.read_sql_query(
        "SELECT icao, iata, name, city, country, region, lat, lon, elevation_ft FROM airports", con)
    last_year = pd.read_sql_query(
        "SELECT icao, MAX(y) AS last_year FROM ("
        " SELECT dep_icao AS icao, MAX(year) AS y FROM flights GROUP BY dep_icao"
        " UNION ALL SELECT arr_icao, MAX(year) FROM flights GROUP BY arr_icao) GROUP BY icao", con)
    airports = airports.merge(last_year, on="icao", how="left")
    airports["last_year"] = airports["last_year"].fillna(0).astype(int)
    return airports.set_index("icao", drop=False)


def _categorical_from_lookup(values, row_index):
    """Categorical column for rows whose lookup position is `row_index`, using the distinct
    values of `values` (one per lookup row) as categories.
    """
    categories, inverse = np.unique(np.asarray(values, dtype=object), return_inverse=True)
    return pd.Categorical.from_codes(inverse[row_index], categories=categories)


def load_data(con=None, year_from=None, year_to=None):
    """Return the flight DataFrame, or an empty frame if the database is missing."""
    own_con = con is None
    if own_con:
        if not flightdb.exists():
            print(f"Error: flight database not found at {flightdb.DB_PATH}")
            return pd.DataFrame()
        con = flightdb.connect(readonly=True)
    try:
        year_from = year_from if year_from is not None else _env_year("FSATLAS_MIN_YEAR")
        year_to = year_to if year_to is not None else _env_year("FSATLAS_MAX_YEAR")
        where, params = [], []
        if year_from is not None:
            where.append("year >= ?")
            params.append(year_from)
        if year_to is not None:
            where.append("year <= ?")
            params.append(year_to)
        in_range = (" AND ".join(where) + " AND ") if where else ""

        discarded = con.execute(
            f"SELECT COUNT(*) FROM flights WHERE {in_range}duration_h > ?", params + [MAX_FLIGHT_TIME_HOURS]).fetchone()[0]
        if discarded:
            print(f"Discarded {discarded} flight(s) exceeding {MAX_FLIGHT_TIME_HOURS} hours")

        flights = pd.read_sql_query(
            "SELECT airline_id, flight_number, callsign, type_id, dep_icao, arr_icao, distance_nm,"
            f" duration_h, year, season FROM flights WHERE {in_range}(duration_h IS NULL OR duration_h <= ?)"
            " ORDER BY flight_id",
            con, params=params + [MAX_FLIGHT_TIME_HOURS])
        if flights.empty:
            return pd.DataFrame()

        airports = load_airports(con)
        airlines = pd.read_sql_query("SELECT airline_id, name FROM airlines", con).set_index("airline_id")
        types = pd.read_sql_query("SELECT type_id, type_icao, name FROM aircraft_types", con).set_index("type_id")
    finally:
        if own_con:
            con.close()

    airline_pos = airlines.index.get_indexer(flights["airline_id"])
    type_pos = types.index.get_indexer(flights["type_id"])
    dep_pos = airports.index.get_indexer(flights["dep_icao"])
    arr_pos = airports.index.get_indexer(flights["arr_icao"])

    data = {
        "owner": _categorical_from_lookup([strip_livery(n) for n in airlines["name"]], airline_pos),
        "type": _categorical_from_lookup(types["name"], type_pos),
        "type_icao": _categorical_from_lookup(types["type_icao"], type_pos),
        "flight_number": pd.Categorical(flights["flight_number"]),
        "calsign": pd.Categorical(flights["callsign"]),
    }
    source = {"airport": "name", "airport_iata": "iata", "airport_icao": "icao", "airport_city": "city",
              "airport_country": "country", "airport_region": "region"}
    numeric = {"airport_lat": "lat", "airport_lon": "lon", "airport_elevation": "elevation_ft"}
    for prefix, pos in (("dep", dep_pos), ("arr", arr_pos)):
        for column, field in source.items():
            data[f"{prefix}_{column}"] = _categorical_from_lookup(airports[field], pos)
        for column, field in numeric.items():
            data[f"{prefix}_{column}"] = airports[field].to_numpy(dtype=float)[pos]
    data["distance"] = flights["distance_nm"].astype(float).to_numpy()
    data["rough_flight_time"] = flights["duration_h"].astype(float).to_numpy()
    label_keys = flights["year"].to_numpy() * 2 + (flights["season"] == "Summer").to_numpy()
    uniq, inverse = np.unique(label_keys, return_inverse=True)
    labels = [f"{'Summer' if k % 2 else 'Winter'} {k // 2}" for k in uniq]
    data["timestamp_read"] = pd.Categorical.from_codes(inverse, categories=labels)
    data["year"] = flights["year"].astype("int16").to_numpy()
    data["season"] = pd.Categorical(flights["season"])
    return pd.DataFrame(data)


def get_airport_destination_counts(df):
    """Each airport's number of unique directly connected airports, keyed by IATA code.
    De-duplicates on the (categorical) dep/arr columns first, so it stays fast on millions of rows.
    """
    pairs = df[["dep_airport_iata", "arr_airport_iata"]].drop_duplicates()
    dep = pairs["dep_airport_iata"].astype(str).str.strip()
    arr = pairs["arr_airport_iata"].astype(str).str.strip()
    valid = (dep != "") & (arr != "")
    dep, arr = dep[valid], arr[valid]
    both = pd.concat([
        pd.DataFrame({"airport": dep, "other": arr}),
        pd.DataFrame({"airport": arr, "other": dep}),
    ], ignore_index=True).drop_duplicates()
    return both.groupby("airport")["other"].nunique()
