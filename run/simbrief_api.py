"""Helpers for exporting flights to SimBrief and validating a stored Pilot ID.

SimBrief's write API (https://developers.navigraph.com/docs/simbrief/using-the-api)
requires an API key that must be requested from and approved by SimBrief/Navigraph
support, so it isn't usable here. Instead, "export" opens SimBrief's own "New Flight"
dispatch page in the user's browser with the flight's details pre-filled via the same
query-string parameters that API integrations use - no key required, since the user's
own logged-in SimBrief session handles the rest.

Pilot ID validation uses SimBrief's public read-only "latest OFP" fetcher
(https://developers.navigraph.com/docs/simbrief/fetching-ofp-data), which is also
key-free and simply confirms the ID/username resolves to a real account.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

DISPATCH_URL = "https://www.simbrief.com/system/dispatch.php"
FETCHER_URL = "https://www.simbrief.com/api/xml.fetcher.php"
REQUEST_TIMEOUT = 8  # seconds

# Values that mean "no data" once a pandas column full of NaN gets stringified.
_BLANK_VALUES = {"", "-", "nan", "none", "null"}

# ATC callsigns are an ICAO airline code followed by the flight number, e.g. "UAL29".
_CALLSIGN_RE = re.compile(r"^([A-Z]{2,3})(\d[\w]*)$")


def _clean(value):
    """Return a stripped string, or None if the value is missing/blank/NaN-like."""
    text = str(value).strip() if value is not None else ""
    return text if text.lower() not in _BLANK_VALUES else None


def build_export_url(flight, pilot_id=""):
    """Build a SimBrief "New Flight" URL pre-filled with a route record's details.

    `flight` is a dict shaped like the route records `web_gui.route_records` sends to
    the browser (dep_icao/arr_icao/type_icao/reg/callsign/date, etc.).
    """
    params = {}

    orig = _clean(flight.get("dep_icao"))
    dest = _clean(flight.get("arr_icao"))
    aircraft = _clean(flight.get("type_icao"))
    reg = _clean(flight.get("reg"))
    callsign = _clean(flight.get("callsign"))

    if orig:
        params["orig"] = orig
    if dest:
        params["dest"] = dest
    if aircraft:
        params["type"] = aircraft
    if reg:
        params["reg"] = reg
    if callsign:
        params["callsign"] = callsign
        match = _CALLSIGN_RE.match(callsign.upper())
        if match:
            params["airline"], params["fltnum"] = match.groups()

    pilot_id = _clean(pilot_id)
    if pilot_id:
        params["pid"] = pilot_id

    return f"{DISPATCH_URL}?{urllib.parse.urlencode(params)}"


def verify_pilot_id(pilot_id):
    """Check that a SimBrief Pilot ID / username resolves to a real profile.

    Returns (ok, message) - `message` is a short, user-facing status string either way.
    """
    pilot_id = _clean(pilot_id)
    if not pilot_id:
        return False, "Enter a Pilot ID or username first"

    param = "userid" if pilot_id.isdigit() else "username"
    url = f"{FETCHER_URL}?{urllib.parse.urlencode({param: pilot_id, 'json': 1})}"

    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as response:
            json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            return False, "No SimBrief account found with that ID/username"
        return False, f"SimBrief returned an error (HTTP {exc.code})"
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False, "Could not reach SimBrief - check your connection and try again"

    return True, "Looks good! This Pilot ID has a SimBrief flight plan on file."
