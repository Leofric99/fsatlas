import pandas as pd
import os
import sys

# Get the path to the current script's directory (run/)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# Data file path
DATA_FILE = os.path.join(CURRENT_DIR, 'database', 'flights.csv')

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

        return df
    
    except Exception as e:
        print(f"Error loading CSV data: {e}")
        return pd.DataFrame()

if __name__ == "__main__":
    # Test loading
    df = load_data()
    print(df.head())
    print(df.info())
