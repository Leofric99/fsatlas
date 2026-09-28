"""Owns loading/parsing/caching the flight dataset (module-level cache, loaded once)
and exposes plain functions for column metadata, filtering and aggregation. No new
data pipeline - this wraps the existing run.data_loader/run.filtering/run.config.
"""
import pandas as pd

from run import config, data_loader, filtering

# Columns hidden entirely from the filter dropdown.
HIDDEN_COLUMNS = {"timestamp_read"}

# Non-directional columns get bucketed into a couple of small logical groups.
COMPANY_COLUMNS = {"owner", "calsign", "flight_number"}
EQUIPMENT_COLUMNS = {"reg", "type", "type_icao"}
OTHER_COLUMNS = {"distance", "rough_flight_time"}

# Columns pulled from the dep_/arr_ side of each flight row to build the unique airport
# list, keyed by the name they're exposed under in the resulting airport record.
_AIRPORT_FIELDS = {
    'airport_iata': 'iata',
    'airport': 'name',
    'airport_city': 'city',
    'airport_country': 'country',
    'airport_lat': 'lat',
    'airport_lon': 'lon',
}

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
            "options": _column_options(df[column]),
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
                    "options": _column_options(combined_series),
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
        frames = []
        for prefix in ('dep', 'arr'):
            rename = {f'{prefix}_{suffix}': target for suffix, target in _AIRPORT_FIELDS.items()}
            if not set(rename).issubset(df.columns):
                continue
            frames.append(df[list(rename)].rename(columns=rename))

        if frames:
            combined = pd.concat(frames, ignore_index=True)
            combined['iata'] = combined['iata'].astype(str).str.strip()
            combined = combined[(combined['iata'] != '') & (combined['iata'] != 'nan')]
            combined['lat'] = pd.to_numeric(combined['lat'], errors='coerce')
            combined['lon'] = pd.to_numeric(combined['lon'], errors='coerce')
            combined.dropna(subset=['lat', 'lon'], inplace=True)
            combined = combined[~((combined['lat'] == 0) & (combined['lon'] == 0))]  # Null island check
            combined = combined.drop_duplicates(subset='iata', keep='first')

            counts = combined['iata'].map(airport_counts).fillna(0)
            rank = pd.Series(0, index=combined.index)
            rank[counts > 7] = 1
            rank[counts > 30] = 2
            rank[counts > 100] = 3

            for iata, name, city, country, lat, lon, rk in zip(
                combined['iata'], combined['name'], combined['city'], combined['country'],
                combined['lat'], combined['lon'], rank,
            ):
                airports[iata] = {
                    "iata": iata,
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
