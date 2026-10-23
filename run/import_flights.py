"""CLI tool to import flight records from a JSON, old-style CSV or FSAtlas database (.db) file into the
flight database.

Usage:
    python -m run.import_flights path/to/new_flights.json
    python -m run.import_flights path/to/old_flights.csv --dry-run
    python -m run.import_flights path/to/fsatlas-flights-20261023.db

The import rules live in run/flight_import.py (shared with the GUI drop zone).
"""
import argparse
import sys

from run import flight_import


def print_report(report):
    verb = "would be added" if report["dry_run"] else "added"
    print(f"Read {report['read']} record(s) from {report['file']}")
    print(f"  {report['skipped_invalid']} flight(s) skipped (unusable: missing flight number, airport or date)")
    print(f"  {report['skipped_no_callsign']} flight(s) skipped (missing callsign)")
    print(f"  {report['skipped_overlong']} flight(s) skipped (estimated duration over 25 hours)")
    print(f"  {report['duplicates']} duplicate(s) skipped")
    print(f"  {report['added']} new flight(s) {verb}")
    created = [f"{report[k]} {label}" for k, label in
               (("new_airports", "airport(s)"), ("new_airlines", "airline(s)"), ("new_types", "aircraft type(s)"))
               if report[k]]
    if created:
        print("  created: " + ", ".join(created))


def import_flights(path, dry_run=False):
    try:
        report = flight_import.import_file(path, dry_run=dry_run)
    except (OSError, ValueError) as exc:
        print(f"Import failed: {exc}")
        return 1
    print_report(report)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Import flight records from a JSON, old-style CSV or FSAtlas database file into the flight database, skipping duplicates."
    )
    parser.add_argument("file", help="Path to a .json, .csv or .db file of flight records")
    parser.add_argument("--dry-run", action="store_true", help="Preview the import without writing to the database")
    args = parser.parse_args()
    sys.exit(import_flights(args.file, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
