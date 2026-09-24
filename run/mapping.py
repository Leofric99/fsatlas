import json
import os
import pandas as pd
from jinja2 import Environment, FileSystemLoader
from run import config

# Built once and reused: constructing a fresh Environment per render (as before) forced
# Jinja2 to re-read and re-compile map.html from disk on every single map render (every
# filter apply, reset, and theme toggle), which added up given how often that happens.
_JINJA_ENV = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'html')),
    variable_start_string='<<',
    variable_end_string='>>',
    block_start_string='<%',
    block_end_string='%>',
    autoescape=False,
)

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

def create_map_html(df, tile_provider='Dark Mode', theme_mode='light', airport_counts=None, route_request_url=None, pan_offset=0):
    """
    Generates the HTML/JS for the map.
    Optimized for performance with Lazy Loading.
    Supports drilling down into specific A<->B connections.
    """
    
    # 1. Determine Tiles
    tile_url = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png'
    attr = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    
    if tile_provider in config.TILES:
        info = config.TILES[tile_provider]
        if isinstance(info, tuple):
            tile_url, max_attr = info
            attr = max_attr
        elif isinstance(info, str):
             if info != 'OpenStreetMap': 
                 tile_url = info

    # 2. Process Data: Extract Unique Airports Only (vectorized - iterating every flight
    # row in Python to pull out the handful of unique airports was the dominant cost when
    # switching map views or resetting filters on the full ~170k-row dataset).
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

            # Rank by number of unique non-stop destinations.
            counts = combined['iata'].map(airport_counts).fillna(0) if airport_counts is not None else 0
            rank = pd.Series(0, index=combined.index)
            if airport_counts is not None:
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
    airports_json = json.dumps(sorted_airports, default=str)
    
    # Colors - glassy dark/light palettes for the redesigned map chrome
    if theme_mode == 'dark':
        bg_color = '#232227'
        text_color = '#f5f6f7'
        panel_bg = 'rgba(22, 25, 29, 0.72)'
        panel_border = 'rgba(255, 255, 255, 0.09)'
        muted_color = 'rgba(245, 246, 247, 0.62)'
        card_bg = 'rgba(255, 255, 255, 0.05)'
        card_border = 'rgba(255, 255, 255, 0.08)'
        shadow_color = 'rgba(0, 0, 0, 0.45)'
        line_color = '#3fd0ff'
        marker_border = '#171b20'
    else:
        bg_color = '#D0CFD4'
        text_color = '#14181d'
        panel_bg = 'rgba(255, 255, 255, 0.78)'
        panel_border = 'rgba(15, 23, 42, 0.08)'
        muted_color = 'rgba(20, 24, 29, 0.62)'
        card_bg = 'rgba(15, 23, 42, 0.045)'
        card_border = 'rgba(15, 23, 42, 0.08)'
        shadow_color = 'rgba(15, 23, 42, 0.16)'
        line_color = '#0a84ff'
        marker_border = '#171b20'

    return _JINJA_ENV.get_template('map.html').render(
        bg_color=bg_color,
        text_color=text_color,
        panel_bg=panel_bg,
        panel_border=panel_border,
        muted_color=muted_color,
        card_bg=card_bg,
        card_border=card_border,
        shadow_color=shadow_color,
        line_color=line_color,
        marker_border=marker_border,
        airports_json=airports_json,
        tile_url=tile_url,
        attr=attr,
        route_request_url_json=json.dumps(route_request_url),
        pan_offset=pan_offset,
    )

