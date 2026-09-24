import pandas as pd
import os
import sys
import country_converter as coco

# Get the path to the current script's directory (run/)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Data file path
DATA_FILE = os.path.join(CURRENT_DIR, 'database', 'flights.csv')

# Maps the UN geoscheme subregion (from country_converter) to the coarser set of
# regions we expose as a filter: the usual continents, plus Middle East, North
# America, South America, Central America (incl. Caribbean) and Antarctica.
UNREGION_TO_REGION = {
    'Northern Africa': 'Africa',
    'Eastern Africa': 'Africa',
    'Middle Africa': 'Africa',
    'Southern Africa': 'Africa',
    'Western Africa': 'Africa',
    'Caribbean': 'Central America',
    'Central America': 'Central America',
    'Northern America': 'North America',
    'South America': 'South America',
    'Central Asia': 'Asia',
    'Eastern Asia': 'Asia',
    'South-eastern Asia': 'Asia',
    'Southern Asia': 'Asia',
    'Western Asia': 'Middle East',
    'Eastern Europe': 'Europe',
    'Northern Europe': 'Europe',
    'Southern Europe': 'Europe',
    'Western Europe': 'Europe',
    'Australia and New Zealand': 'Oceania',
    'Melanesia': 'Oceania',
    'Micronesia': 'Oceania',
    'Polynesia': 'Oceania',
    'Antarctica': 'Antarctica',
}

def add_region_columns(df):
    """Derive dep/arr region columns in-memory from the country columns.

    Uses the country_converter library to look up each country's UN geoscheme
    subregion, then folds that into the broader region set above; this is
    never written back to the CSV, only added to the in-memory frame.
    """
    cc = coco.CountryConverter()
    for prefix in ('dep', 'arr'):
        country_col = f'{prefix}_airport_country'
        region_col = f'{prefix}_airport_region'
        if country_col not in df.columns:
            continue
        countries = df[country_col].unique().tolist()
        un_regions = cc.convert(names=countries, to='UNregion', not_found='')
        lookup = {
            country: UNREGION_TO_REGION.get(un_region, '')
            for country, un_region in zip(countries, un_regions)
        }
        df[region_col] = df[country_col].map(lookup)

        # Keep the region column right next to its country column in the filter list
        # instead of letting it fall to the end (where newly-added columns land).
        cols = list(df.columns)
        cols.remove(region_col)
        cols.insert(cols.index(country_col) + 1, region_col)
        df = df[cols].copy()
    return df

def normalize_airline_names(df):
    """Strip livery/sticker/anniversary suffixes (e.g. "Saudia (SkyTeam Livery)" ->
    "Saudia") from the owner column in-memory, so filters group flights by the base
    airline instead of splintering across every special-livery variant. Truncating from
    the first '(' (rather than requiring a matching ')') also cleans up the handful of
    rows in the source data with an unclosed trailing parenthesis. Never written back
    to the CSV.
    """
    if 'owner' in df.columns:
        df['owner'] = df['owner'].str.replace(r'\s*\(.*$', '', regex=True).str.strip()
    return df

def load_data():
    """
    Loads the flight data from the CSV file.
    Performs cleaning and type conversion.
    """
    if not os.path.exists(DATA_FILE):
        print(f"Error: Data file not found at {DATA_FILE}")
        return pd.DataFrame()

    try:
        df = pd.read_csv(DATA_FILE)
        
        # Ensure numerical columns are actually numeric
        numeric_cols = [
            'dep_airport_lat', 'dep_airport_lon', 'dep_airport_elevation',
            'arr_airport_lat', 'arr_airport_lon', 'arr_airport_elevation',
            'distance', 'rough_flight_time'
        ]
        
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Drop rows with critical missing location data
        df.dropna(subset=['dep_airport_lat', 'dep_airport_lon', 'arr_airport_lat', 'arr_airport_lon'], inplace=True)
        
        # Fill NaN for text columns with empty string
        text_cols = df.select_dtypes(include=['object']).columns
        df[text_cols] = df[text_cols].fillna("")

        df = normalize_airline_names(df)
        df = add_region_columns(df)

        return df
    
    except Exception as e:
        print(f"Error loading CSV data: {e}")
        return pd.DataFrame()


def get_airport_destination_counts(df):
    """Return each airport's number of unique directly connected airports."""
    dep = df["dep_airport_iata"].astype(str).str.strip()
    arr = df["arr_airport_iata"].astype(str).str.strip()
    valid = (dep != "") & (dep != "nan") & (arr != "") & (arr != "nan")
    dep, arr = dep[valid], arr[valid]

    # Each flight links its two airports both ways; stacking both directions and
    # deduping lets a single vectorized groupby count each airport's unique neighbours,
    # instead of a Python loop building up a dict of sets per row.
    pairs = pd.concat([
        pd.DataFrame({"airport": dep, "other": arr}),
        pd.DataFrame({"airport": arr, "other": dep}),
    ], ignore_index=True).drop_duplicates()
    return pairs.groupby("airport")["other"].nunique()

if __name__ == "__main__":
    # Test loading
    df = load_data()
    print(df.head())
    print(df.info())
