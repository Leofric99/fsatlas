"""Owns loading/parsing/caching the flight dataset (module-level cache, loaded once)
and exposes plain functions for column metadata, filtering and aggregation. No new
data pipeline - this wraps the existing run.data_loader/run.filtering/run.config.
"""
from math import asin, cos, isfinite, radians, sin, sqrt

import pandas as pd

from run import config, data_loader, filtering

# Columns hidden entirely from the filter dropdown.
HIDDEN_COLUMNS = {"timestamp_read"}

# Non-directional columns get bucketed into a couple of small logical groups.
COMPANY_COLUMNS = {"owner", "calsign", "flight_number"}
EQUIPMENT_COLUMNS = {"reg", "type", "type_icao"}
OTHER_COLUMNS = {"distance", "rough_flight_time"}

# Closed/categorical columns that get the chip multi-select input (type-ahead -> inline
# token chips, "is any of") instead of a free-text field - unlike _column_options' default
# 15-value cap (meant for a plain single-choice dropdown), these get the FULL distinct
# value list up to _MULTISELECT_OPTIONS_CAP, since the chip combobox does its own
# client-side prefix/substring filtering (same pattern already used for the scenery
# airport picker's full unfiltered directory).
MULTISELECT_COLUMNS = {
    "owner", "type", "type_icao",
    "dep_airport", "dep_airport_iata", "dep_airport_icao", "dep_airport_city",
    "dep_airport_country", "dep_airport_region",
    "arr_airport", "arr_airport_iata", "arr_airport_icao", "arr_airport_city",
    "arr_airport_country", "arr_airport_region",
}
_MULTISELECT_OPTIONS_CAP = 6000

# Columns pulled from the dep_/arr_ side of each flight row to build the unique airport
# list, keyed by the name they're exposed under in the resulting airport record.
_AIRPORT_FIELDS = {
    'airport_iata': 'iata',
    'airport_icao': 'icao',
    'airport': 'name',
    'airport_city': 'city',
    'airport_country': 'country',
    'airport_lat': 'lat',
    'airport_lon': 'lon',
}

# Match radius used to cross-reference an imported scenery location's (rough) coordinates
# against a known airport's official lat/lon (see match_scenery below).
_SCENERY_MATCH_RADIUS_MILES = 1.0
_EARTH_RADIUS_MILES = 3958.8

_DEFAULT_TILE_URL = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
_DEFAULT_TILE_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

_state = {}


def load():
    """Load the dataset and derived aggregations into the module-level cache. Safe to
    call more than once (e.g. from tests) - only loads on first call.
    """
    if not _state:
        df = data_loader.load_data()
        _state['df'] = df
        _state['airport_counts'] = data_loader.get_airport_destination_counts(df)
        _state['columns'] = _build_columns(df)
    return _state


def _column_example(series):
    """Pick a real sample value from a column to preview the expected filter format."""
    sample = series.dropna()
    if sample.empty:
        return ""
    value = sample.iloc[0]
    if pd.api.types.is_float_dtype(series):
        value = float(value)
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    if pd.api.types.is_integer_dtype(series):
        return str(int(value))
    return str(value).strip()[:24]


def _format_value(value, series):
    """Format a single value the same way _column_example formats its sample."""
    if pd.api.types.is_float_dtype(series):
        value = float(value)
        return str(int(value)) if value.is_integer() else f"{value:.2f}"
    if pd.api.types.is_integer_dtype(series):
        return str(int(value))
    return str(value).strip()


def _column_options(series, limit=15):
    """Return sorted distinct values for low-cardinality columns, so the filter UI can use
    a dropdown instead of a free-text field. Returns None when there are no or too many.
    """
    uniques = series.dropna().unique().tolist()
    if not pd.api.types.is_numeric_dtype(series):
        uniques = [v for v in uniques if str(v).strip() != ""]
    if not (0 < len(uniques) < limit):
        return None
    uniques.sort()
    return [_format_value(v, series) for v in uniques]


def _chip_options(series, limit=_MULTISELECT_OPTIONS_CAP):
    """Like _column_options but for the chip multi-select input - no low-cardinality
    cap (a few thousand airports/airlines is still a perfectly fine one-shot JSON list,
    same as the existing scenery airport directory), just a generous safety ceiling.
    """
    uniques = [v for v in series.dropna().unique().tolist() if str(v).strip() != ""]
    if not (0 < len(uniques) < limit):
        return None
    uniques.sort()
    return [_format_value(v, series) for v in uniques]


def _build_columns(df):
    """Build the filter column list, grouped into "Company", "Equipment", "Departure",
    "Arrival", a synthetic "Departure or Arrival X" per dep_/arr_ column pair (matches rows
    where either side matches), and "Other" - in that order. Timestamp is hidden entirely.
    """
    ungrouped, company, equipment, departure, arrival, combined, other = [], [], [], [], [], [], []
    seen_dep = {}
    for column in df.columns:
        if column in HIDDEN_COLUMNS:
            continue

        display_name = config.COLUMN_DISPLAY_NAMES.get(column, column)
        entry = {
            "id": column, "name": display_name,
            "numeric": bool(pd.api.types.is_numeric_dtype(df[column])),
            "example": _column_example(df[column]),
            "options": _chip_options(df[column]) if column in MULTISELECT_COLUMNS else _column_options(df[column]),
            "multi": column in MULTISELECT_COLUMNS,
        }

        if column.startswith("dep_"):
            entry["group"] = "Departure"
            entry["name"] = display_name.removeprefix("Departure ")
            departure.append(entry)
            seen_dep[column.removeprefix("dep_")] = column
            continue

        if column.startswith("arr_"):
            entry["group"] = "Arrival"
            entry["name"] = display_name.removeprefix("Arrival ")
            arrival.append(entry)
            dep_col = seen_dep.get(column.removeprefix("arr_"))
            if dep_col:
                dep_name = config.COLUMN_DISPLAY_NAMES.get(dep_col, dep_col)
                base_name = dep_name.removeprefix("Departure ")
                combined_series = pd.concat([df[dep_col], df[column]], ignore_index=True)
                combined.append({
                    "id": f"combined:{dep_col}:{column}",
                    "name": base_name,
                    "numeric": bool(pd.api.types.is_numeric_dtype(df[dep_col])),
                    "example": _column_example(df[dep_col]),
                    "options": _chip_options(combined_series) if dep_col in MULTISELECT_COLUMNS else _column_options(combined_series),
                    "multi": dep_col in MULTISELECT_COLUMNS,
                    "group": "Departure or Arrival",
                })
            continue

        if column in COMPANY_COLUMNS:
            entry["group"] = "Company"
            company.append(entry)
        elif column in EQUIPMENT_COLUMNS:
            entry["group"] = "Equipment"
            equipment.append(entry)
        elif column in OTHER_COLUMNS:
            entry["group"] = "Other"
            other.append(entry)
        else:
            entry["group"] = None
            ungrouped.append(entry)

    return ungrouped + company + equipment + departure + arrival + combined + other


def get_columns():
    return load()['columns']


def get_map_types():
    """Tile provider name -> {url, attr}, resolved the same way mapping.create_map_html
    resolves run.config.TILES (a tuple, the 'OpenStreetMap' sentinel, or a bare URL).
    """
    result = {}
    for name, info in config.TILES.items():
        if isinstance(info, tuple):
            url, attr = info
        elif isinstance(info, str) and info != 'OpenStreetMap':
            url, attr = info, _DEFAULT_TILE_ATTR
        else:
            url, attr = _DEFAULT_TILE_URL, _DEFAULT_TILE_ATTR
        result[name] = {"url": url, "attr": attr}
    return result


def _filtered_df(filters):
    df = load()['df']
    if not filters:
        return df
    return filtering.apply_filters(df, filters)


def _unique_airports_df(df):
    """Dedup dep_/arr_ airport columns down to one row per IATA code (iata/icao/name/
    city/country/lat/lon) - the shared basis for both get_airports (ranked, filtered) and
    get_airport_directory (unfiltered, used for scenery cross-referencing).
    """
    frames = []
    for prefix in ('dep', 'arr'):
        rename = {f'{prefix}_{suffix}': target for suffix, target in _AIRPORT_FIELDS.items()}
        if not set(rename).issubset(df.columns):
            continue
        frames.append(df[list(rename)].rename(columns=rename))

    if not frames:
        return pd.DataFrame(columns=list(_AIRPORT_FIELDS.values()))

    combined = pd.concat(frames, ignore_index=True)
    combined['iata'] = combined['iata'].astype(str).str.strip()
    combined = combined[(combined['iata'] != '') & (combined['iata'] != 'nan')]
    combined['icao'] = combined['icao'].astype(str).str.strip().str.upper()
    combined['lat'] = pd.to_numeric(combined['lat'], errors='coerce')
    combined['lon'] = pd.to_numeric(combined['lon'], errors='coerce')
    combined.dropna(subset=['lat', 'lon'], inplace=True)
    combined = combined[~((combined['lat'] == 0) & (combined['lon'] == 0))]  # Null island check
    return combined.drop_duplicates(subset='iata', keep='first')


def get_airports(filters):
    """Unique airports appearing in the filtered dataset, ranked by *global* (unfiltered)
    destination count so colors reflect overall network connectivity regardless of the
    active filter - matching the original mapping.create_map_html behaviour. Returns
    (airports, matching_flight_count).
    """
    df = _filtered_df(filters)
    airport_counts = load()['airport_counts']
    airports = {}

    if not df.empty:
        combined = _unique_airports_df(df)

        if not combined.empty:
            counts = combined['iata'].map(airport_counts).fillna(0)
            rank = pd.Series(0, index=combined.index)
            rank[counts > 7] = 1
            rank[counts > 30] = 2
            rank[counts > 100] = 3

            for iata, icao, name, city, country, lat, lon, rk in zip(
                combined['iata'], combined['icao'], combined['name'], combined['city'],
                combined['country'], combined['lat'], combined['lon'], rank,
            ):
                airports[iata] = {
                    "iata": iata,
                    "icao": icao,
                    "name": str(name),
                    "city": str(city),
                    "country": str(country),
                    "lat": lat,
                    "lon": lon,
                    "rank": int(rk),
                }

    # Draw larger, better-connected airports on top of smaller airports.
    sorted_airports = sorted(airports.values(), key=lambda x: x['rank'])
    return sorted_airports, len(df)


def get_airport_directory():
    """Every known airport (iata/icao/name/city/lat/lon), unfiltered - cached once, used
    to cross-reference imported flight-sim scenery locations against real airports.
    """
    if 'airport_directory' not in _state:
        combined = _unique_airports_df(load()['df'])
        _state['airport_directory'] = [
            {
                "iata": row.iata, "icao": row.icao, "name": str(row.name_),
                "city": str(row.city), "lat": float(row.lat), "lon": float(row.lon),
            }
            for row in combined.rename(columns={'name': 'name_'}).itertuples()
        ]
    return _state['airport_directory']


def _haversine_miles(lat1, lon1, lat2, lon2):
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_MILES * asin(sqrt(a))


def match_scenery(candidates):
    """Cross-reference client-scanned scenery candidates (`{icao, lat, lon, source}`,
    lat/lon and icao both optional) against the real airport directory: an exact ICAO
    match wins outright (using the airport's official lat/lon instead of whatever rough
    coordinate the scan produced); failing that, a candidate with coordinates is matched
    to the nearest known airport within `_SCENERY_MATCH_RADIUS_MILES`. Returns
    (matched_airports, structured_import_errors).
    """
    directory = get_airport_directory()
    by_icao = {str(a['icao']).strip().upper(): a for a in directory if a['icao']}

    matched = {}
    errors = []
    for index, candidate in enumerate(candidates, start=1):
        if not isinstance(candidate, dict):
            errors.append({
                "severity": "error", "stage": "airport_match", "source": f"Candidate {index}",
                "message": "The browser returned an invalid scenery candidate.",
                "details": f"Expected an object, received {type(candidate).__name__}.",
            })
            continue
        icao = str(candidate.get('icao') or '').strip().upper()
        lat, lon = candidate.get('lat'), candidate.get('lon')
        has_coordinates = (
            isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and isfinite(lat) and isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180
        )

        airport = by_icao.get(icao) if icao else None
        nearest, nearest_dist = None, None
        if has_coordinates:
            for a in directory:
                dist = _haversine_miles(lat, lon, a['lat'], a['lon'])
                if nearest_dist is None or dist < nearest_dist:
                    nearest, nearest_dist = a, dist
            if airport is None and nearest_dist is not None and nearest_dist <= _SCENERY_MATCH_RADIUS_MILES:
                airport = nearest

        if airport:
            matched[airport['icao'] or airport['iata']] = airport
        else:
            details = []
            if icao and icao not in by_icao:
                details.append(f"ICAO {icao} is not present in the loaded airport directory.")
            if has_coordinates:
                if nearest:
                    details.append(
                        f"Nearest known airport is {nearest['icao'] or nearest['iata']} "
                        f"({nearest['name']}, {nearest['city']}), {nearest_dist:.2f} miles away; "
                        f"the allowed match radius is {_SCENERY_MATCH_RADIUS_MILES:.1f} mile."
                    )
                else:
                    details.append("No known airports are available for coordinate matching.")
            elif lat is not None or lon is not None:
                details.append("Detected coordinates are invalid or outside latitude/longitude bounds.")
            else:
                details.append("No ICAO code or usable coordinates were detected for this package.")
            errors.append({
                "severity": "error", "stage": "airport_match",
                "source": str(candidate.get('source') or icao or f"Candidate {index}"),
                "message": "No known airport could be matched to this scenery package.",
                "icao": icao or None,
                "coordinates": {"lat": lat, "lon": lon} if has_coordinates else None,
                "nearest_airport": {
                    "icao": nearest['icao'], "iata": nearest['iata'], "name": nearest['name'],
                    "city": nearest['city'], "distance_miles": round(nearest_dist, 2),
                } if nearest else None,
                "match_radius_miles": _SCENERY_MATCH_RADIUS_MILES,
                "details": " ".join(details),
            })

    return list(matched.values()), errors


def get_flights(iata, filters):
    """Route records for every flight touching `iata` under the given filters - the
    per-airport popup/info-panel dataset.
    """
    df = _filtered_df(filters)
    matches = df[(df["dep_airport_iata"] == iata) | (df["arr_airport_iata"] == iata)]
    if matches.empty:
        return []
    result = pd.DataFrame({
        "dep": matches["dep_airport_iata"].astype(str),
        "arr": matches["arr_airport_iata"].astype(str),
        "flight": matches["flight_number"].astype(str),
        "type": matches["type"].astype(str),
        "callsign": matches["calsign"].astype(str),
        "type_icao": matches["type_icao"].astype(str),
        "reg": matches["reg"].astype(str),
        "dep_icao": matches["dep_airport_icao"].astype(str),
        "arr_icao": matches["arr_airport_icao"].astype(str),
        "dep_city": matches["dep_airport_city"].astype(str),
        "arr_city": matches["arr_airport_city"].astype(str),
        "airline": matches["owner"].astype(str),
        "date": matches["timestamp_read"].astype(str).str.slice(0, 10),
        # NaN (unrecognised type_icao / no cruise speed on import) -> JSON null.
        "flight_time_hours": matches["rough_flight_time"].astype(object).where(matches["rough_flight_time"].notna(), None),
        "distance": matches["distance"].astype(object).where(matches["distance"].notna(), None),
    })
    return result.to_dict("records")
