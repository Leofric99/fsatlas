"""Build run/database/flights.db from the flat FlightSim Dispatch extract.

Usage (from the repo root):
    .venv/bin/python .dev/build_flights_db.py /path/to/flightsim_dispatch_flights.db [--out run/database/flights.db]

The source is a single flat `flights` table with the 24 columns of the old flights.csv minus
`reg`. The result is the normalised schema in run/flightdb.py. Rows that are identical apart
from fields the new schema doesn't store (e.g. registration) collapse into one.
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from run import flightdb  # noqa: E402
from run.data_loader import UNREGION_TO_REGION  # noqa: E402


def region_lookup(countries):
    import country_converter as coco

    countries = sorted(c for c in countries if c)
    un_regions = coco.CountryConverter().convert(names=countries, to="UNregion", not_found="")
    return {c: UNREGION_TO_REGION.get(r, "") for c, r in zip(countries, un_regions)}


def build(source, out):
    if os.path.exists(out):
        os.remove(out)
    con = flightdb.connect(out)
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    flightdb.ensure_schema(con)
    con.execute("ATTACH DATABASE ? AS src", (source,))

    # Airports: one row per ICAO code, taken from either side of any flight.
    con.execute("""
        INSERT INTO airports (icao, iata, name, city, country, lat, lon, elevation_ft)
        SELECT icao, MAX(iata), MAX(name), MAX(city), MAX(country), MAX(lat), MAX(lon), MAX(elev) FROM (
            SELECT dep_airport_icao AS icao, ifnull(dep_airport_iata, '') AS iata, dep_airport AS name,
                   dep_airport_city AS city, dep_airport_country AS country,
                   dep_airport_lat AS lat, dep_airport_lon AS lon, dep_airport_elevation AS elev
            FROM src.flights GROUP BY dep_airport_icao
            UNION ALL
            SELECT arr_airport_icao, ifnull(arr_airport_iata, ''), arr_airport, arr_airport_city,
                   arr_airport_country, arr_airport_lat, arr_airport_lon, arr_airport_elevation
            FROM src.flights GROUP BY arr_airport_icao
        ) GROUP BY icao
    """)
    regions = region_lookup({r[0] for r in con.execute("SELECT DISTINCT country FROM airports")})
    con.executemany("UPDATE airports SET region = ? WHERE country = ?", [(v, k) for k, v in regions.items()])

    con.execute("INSERT INTO airlines (name) SELECT DISTINCT owner FROM src.flights ORDER BY owner")
    con.execute("""INSERT INTO aircraft_types (type_icao, name)
                   SELECT DISTINCT type_icao, type FROM src.flights ORDER BY type_icao, type""")
    con.commit()

    # Flights: collapse to one row per (airline, flight number, route, type, season). A flight
    # usually has several source legs (one per weekly departure) whose durations can differ a
    # little, so the row kept is the one with the median duration (the lower median, so it is a
    # real source value) rather than an arbitrary one.
    con.execute("""
        INSERT INTO flights (airline_id, flight_number, callsign, type_id, dep_icao, arr_icao,
                             distance_nm, duration_h, year, season)
        SELECT al.airline_id, s.flight_number, s.callsign, ty.type_id,
               s.dep_airport_icao, s.arr_airport_icao, s.distance, s.rough_flight_time,
               CAST(substr(s.timestamp_read, 8) AS INTEGER), substr(s.timestamp_read, 1, 6)
        FROM (
            SELECT owner, type, type_icao, flight_number, callsign, dep_airport_icao, arr_airport_icao,
                   distance, rough_flight_time, timestamp_read
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER w AS rn,
                       COUNT(rough_flight_time) OVER (PARTITION BY owner, type, type_icao, flight_number,
                           dep_airport_icao, arr_airport_icao, timestamp_read) AS known
                FROM src.flights
                WINDOW w AS (PARTITION BY owner, type, type_icao, flight_number, dep_airport_icao,
                                          arr_airport_icao, timestamp_read
                             ORDER BY rough_flight_time IS NULL, rough_flight_time, rowid)
            )
            WHERE rn = CASE WHEN known = 0 THEN 1 ELSE (known + 1) / 2 END
        ) s
        JOIN airlines al ON al.name = s.owner
        JOIN aircraft_types ty ON ty.type_icao = s.type_icao AND ty.name = s.type
    """)
    con.commit()
    con.execute("DETACH DATABASE src")
    con.execute("ANALYZE")
    con.execute("VACUUM")
    con.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Path to the flat flightsim_dispatch_flights.db")
    parser.add_argument("--out", default=flightdb.DB_PATH, help="Output database (default: run/database/flights.db)")
    args = parser.parse_args()
    build(os.path.abspath(args.source), os.path.abspath(args.out))
    con = sqlite3.connect(args.out)
    for table in ("airports", "airlines", "aircraft_types", "flights"):
        print(f"{table:15s} {con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>10,}")
    print(f"{os.path.getsize(args.out) / 1e6:,.0f} MB written to {args.out}")


if __name__ == "__main__":
    main()
