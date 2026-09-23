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


def import_flights(json_path, dry_run=False):
    if not os.path.exists(DATA_FILE):
        print(f"Error: {DATA_FILE} not found", file=sys.stderr)
        return 1

    existing = pd.read_csv(DATA_FILE, dtype=str, keep_default_na=False)
    new_records = load_new_records(json_path)

    combined = pd.concat([existing, new_records], ignore_index=True)
    deduped = combined.drop_duplicates(subset=DEDUP_KEY_COLUMNS, keep='first')

    added = len(deduped) - len(existing)
    duplicates = len(new_records) - added

    print(f"Read {len(new_records)} record(s) from {json_path}")
    print(f"  {added} new flight(s) {'would be added' if dry_run else 'added'}")
    print(f"  {duplicates} duplicate(s) skipped")

    if not dry_run:
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
