from PyQt6.QtGui import QColor

# --- Display Names ---
# Map column names to "appealing" display names
COLUMN_DISPLAY_NAMES = {
    'owner': 'Airline',
    'reg': 'Registration',
    'type': 'Aircraft Type',
    'type_icao': 'Aircraft ICAO',
    'flight_number': 'Flight Number',
    'calsign': 'Callsign',
    'dep_airport': 'Departure Airport',
    'dep_airport_iata': 'Dep. IATA',
    'dep_airport_icao': 'Dep. ICAO',
    'dep_airport_city': 'Departure City',
    'dep_airport_country': 'Departure Country',
    'dep_airport_elevation': 'Dep. Elevation (ft)',
    'arr_airport': 'Arrival Airport',
    'arr_airport_iata': 'Arr. IATA',
    'arr_airport_icao': 'Arr. ICAO',
    'arr_airport_city': 'Arrival City',
    'arr_airport_country': 'Arrival Country',
    'arr_airport_elevation': 'Arr. Elevation (ft)',
    'distance': 'Distance (km)',
    'rough_flight_time': 'Flight Time (hours)',
    'timestamp_read': 'Timestamp'
}

# Default columns to show in filter dropdowns (or all if empty)
# Use the internal names, not display names
FILTER_COLUMNS = [
    'owner', 
    'type_icao', 
    'dep_airport_country', 
    'arr_airport_country', 
    'distance',
    'rough_flight_time'
]

# --- Themes ---
class Theme:
    def __init__(self, name, bg_color, fg_color, accent_color, map_tile_url, map_attr):
        self.name = name
        self.bg_color = bg_color
        self.fg_color = fg_color
        self.accent_color = accent_color
        self.map_tile_url = map_tile_url
        self.map_attr = map_attr

# Tile providers for folium
TILES = {
    'Standard': 'OpenStreetMap',
    'Satellite': (
        'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community'
    ),
    'Hybrid': (
        'http://mt0.google.com/vt/lyrs=y&hl=en&x={x}&y={y}&z={z}',
        'Google Hybrid'
    )
}

# Dark Mode Style Sheet (CSS-like for Qt)
DARK_STYLE = """
QWidget {
    background-color: #2b2b2b;
    color: #ffffff;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
}
QGroupBox {
    border: 1px solid #555;
    border-radius: 5px;
    margin-top: 10px;
    font-weight: bold; 
    color: #e0e0e0;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 3px;
    color: #aaaaff;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #3b3b3b;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 4px;
    color: #fff;
    selection-background-color: #5555ff;
}
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4444ff, stop:1 #2222dd);
    color: white;
    border-radius: 6px;
    padding: 6px 12px;
    border: none;
    font-weight: bold;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5555ff, stop:1 #3333ee);
}
QPushButton:pressed {
    background-color: #1111cc;
}
QCheckBox {
    spacing: 5px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
"""

LIGHT_STYLE = """
QWidget {
    background-color: #f0f0f0;
    color: #333333;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px;
}
QGroupBox {
    border: 1px solid #ccc;
    border-radius: 5px;
    margin-top: 10px;
    font-weight: bold;
    color: #333;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 3px;
    color: #0000aa;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #ffffff;
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 4px;
    color: #333;
    selection-background-color: #aaaaff;
}
QPushButton {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #66aaff, stop:1 #4488dd);
    color: white;
    border-radius: 6px;
    padding: 6px 12px;
    border: none;
    font-weight: bold;
}
QPushButton:hover {
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #77bbff, stop:1 #5599ee);
}
QPushButton:pressed {
    background-color: #3377cc;
}
QScrollArea {
    border: none; 
    background-color: transparent;
}
"""
