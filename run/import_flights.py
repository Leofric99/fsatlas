"""CLI tool to import new flight records from a JSON file into flights.csv.

Usage:
    python -m run.import_flights path/to/new_flights.json
    python -m run.import_flights path/to/new_flights.json --dry-run
"""
import argparse
import json
import os
import sys

import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(CURRENT_DIR, 'database', 'flights.csv')
CRZ_SPEEDS_FILE = os.path.join(CURRENT_DIR, 'database', 'aircraft_crz_speeds.json')

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

# Column order the CSV is stored in - JSON records must use these same field names.
REQUIRED_COLUMNS = [
    'owner', 'reg', 'type', 'type_icao', 'flight_number', 'calsign',
    'dep_airport', 'dep_airport_iata', 'dep_airport_icao', 'dep_airport_city', 'dep_airport_country',
    'dep_airport_lat', 'dep_airport_lon', 'dep_airport_elevation',
    'arr_airport', 'arr_airport_iata', 'arr_airport_icao', 'arr_airport_city', 'arr_airport_country',
    'arr_airport_lat', 'arr_airport_lon', 'arr_airport_elevation',
    'distance', 'rough_flight_time', 'timestamp_read',
]

# A new record is treated as a duplicate of an existing one if all of these match.
DEDUP_KEY_COLUMNS = ['reg', 'flight_number', 'dep_airport_iata', 'arr_airport_iata', 'timestamp_read']


def _extract_records(data):
    """Accept either a bare list of flight objects, or a dict wrapping them under a common key."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('flights', 'data', 'results'):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("JSON file must contain a list of flight records (optionally wrapped in a 'flights' key).")


def load_new_records(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    records = _extract_records(data)
    if not records:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    df = pd.DataFrame.from_records(records)

    unknown_columns = [c for c in df.columns if c not in REQUIRED_COLUMNS]
    if unknown_columns:
        print(f"Ignoring unrecognised columns: {', '.join(unknown_columns)}")
        df = df.drop(columns=unknown_columns)

    missing_columns = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_columns:
        print(f"Filling missing columns with blank values: {', '.join(missing_columns)}")
        for col in missing_columns:
            df[col] = ""

    df = df[REQUIRED_COLUMNS]
    df = df.where(pd.notnull(df), "")
    return df.astype(str)


def _drop_missing_callsigns(df):
    has_callsign = df['calsign'].fillna('').astype(str).str.strip().ne('')
    return df.loc[has_callsign].copy(), int((~has_callsign).sum())


def load_cruise_speeds():
    """Return {type_icao: cruise_tas_knots}, or {} if the speeds file is missing."""
    if not os.path.exists(CRZ_SPEEDS_FILE):
        return {}
    with open(CRZ_SPEEDS_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return {icao: info['cruise_tas_knots'] for icao, info in data.items() if 'cruise_tas_knots' in info}


def recompute_flight_times(df, cruise_speeds):
    """Overwrite rough_flight_time from distance and the aircraft's cruise speed:

        flight_time (hours) = distance_nm * FLIGHT_TIME_DISTANCE_FACTOR / cruise_tas_knots
                              + BUFFER_COEFFICIENT / cruise_tas_knots

    (the base cruise-time term plus the ascent/descent buffer derived above). Rows
    whose type_icao isn't in the cruise speeds database have no way to compute either
    term, so rough_flight_time is cleared to blank for them (rendered as "Unknown" in
    the GUI) rather than keeping whatever estimate the source data provided.
    """
    if not cruise_speeds:
        return df
    distance = pd.to_numeric(df['distance'], errors='coerce')
    speed = df['type_icao'].map(cruise_speeds)
    computable = distance.notna() & speed.notna()

    base_hours = distance * FLIGHT_TIME_DISTANCE_FACTOR / speed
    buffer_hours = BUFFER_COEFFICIENT / speed
    flight_time = (base_hours + buffer_hours).round(2)

    df['rough_flight_time'] = ""
    df.loc[computable, 'rough_flight_time'] = flight_time[computable].astype(str)
    return df


def import_flights(json_path, dry_run=False):
    if os.path.exists(DATA_FILE) and os.path.getsize(DATA_FILE) > 0:
        existing = pd.read_csv(DATA_FILE, dtype=str, keep_default_na=False)
    else:
        print(f"{DATA_FILE} not found or empty; creating it with columns: {', '.join(REQUIRED_COLUMNS)}")
        existing = pd.DataFrame(columns=REQUIRED_COLUMNS)

    existing, removed_existing = _drop_missing_callsigns(existing)
    new_records = load_new_records(json_path)
    read_count = len(new_records)
    new_records, skipped_missing_callsign = _drop_missing_callsigns(new_records)

    cruise_speeds = load_cruise_speeds()
    if cruise_speeds:
        new_records = recompute_flight_times(new_records, cruise_speeds)
    else:
        print(f"Warning: {CRZ_SPEEDS_FILE} not found; keeping rough_flight_time as provided")

    combined = pd.concat([existing, new_records], ignore_index=True)
    deduped = combined.drop_duplicates(subset=DEDUP_KEY_COLUMNS, keep='first')

    added = len(deduped) - len(existing)
    duplicates = len(new_records) - added

    print(f"Read {read_count} record(s) from {json_path}")
    print(f"  {skipped_missing_callsign} flight(s) skipped (missing callsign)")
    print(f"  {removed_existing} existing flight(s) removed (missing callsign)")
    print(f"  {added} new flight(s) {'would be added' if dry_run else 'added'}")
    print(f"  {duplicates} duplicate(s) skipped")

    if not dry_run:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        deduped.to_csv(DATA_FILE, index=False)

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Import flight records from a JSON file into flights.csv, skipping duplicates."
    )
    parser.add_argument('json_file', help="Path to a JSON file containing a list of flight records")
    parser.add_argument('--dry-run', action='store_true', help="Preview the import without writing to flights.csv")
    args = parser.parse_args()
    sys.exit(import_flights(args.json_file, dry_run=args.dry_run))


if __name__ == '__main__':
    main()
