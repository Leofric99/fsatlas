"""Entry point for the Flask frontend: python -m run.webapp (also what `python -m run`
and the Docker image now launch)."""
import argparse
import os
import sys
import webbrowser

from run.webapp import create_app


def main():
    parser = argparse.ArgumentParser(
        prog="fsatlas",
        description="FSAtlas - browse real-world flight data on an interactive world map.",
    )
    parser.add_argument(
        "--host", default=os.environ.get("FSATLAS_HOST", "127.0.0.1"),
        help="Interface to bind to (default: 127.0.0.1; use 0.0.0.0 for containers).",
    )
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("FSATLAS_PORT", "5050")),
        help="Port to bind to (default: 5050).",
    )
    parser.add_argument("--debug", action="store_true", default=os.environ.get("FSATLAS_DEBUG", "") not in ("", "0"))
    parser.add_argument(
        "--no-browser", action="store_true",
        default=os.environ.get("FSATLAS_NO_BROWSER", "") not in ("", "0"),
        help="Don't try to open a browser window (implied when there's no display to open one on).",
    )
    parser.add_argument(
        "-i", "--import", dest="import_file", metavar="JSONFILE",
        help="Import flight records from JSONFILE into flights.csv, then exit without starting the server.",
    )
    args = parser.parse_args()

    if args.import_file:
        from run.import_flights import import_flights
        sys.exit(import_flights(args.import_file))

    app = create_app()
    url = f"http://{args.host}:{args.port}"
    print(f"Flightsim Atlas web UI: {url}")

    if not args.no_browser and not args.debug:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
