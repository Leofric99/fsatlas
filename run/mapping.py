import json
import os
import pandas as pd
import math
from jinja2 import Environment, FileSystemLoader
from run import config

def create_map_html(df, tile_provider='Standard', theme_mode='light', airport_counts=None):
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

    # 2. Process Data: Extract Unique Airports Only
    airports = {}
    
    # Calculate frequency thresholds for coloring
    q33 = 0
    q66 = 0
    if airport_counts is not None and not airport_counts.empty:
        q33 = airport_counts.quantile(0.40)
        q66 = airport_counts.quantile(0.80)
    
    if not df.empty:
        # Helper to extract airport data
        def extract_airport(row, prefix):
            iata = str(row.get(f'{prefix}_airport_iata', '')).strip()
            if not iata or iata == 'nan': return
            
            if iata in airports: return
            
            try:
                lat = float(row.get(f'{prefix}_airport_lat', 0))
                lon = float(row.get(f'{prefix}_airport_lon', 0))
                
                if math.isnan(lat) or math.isnan(lon): return
                if lat == 0 and lon == 0: return # Null island check
                
                # Ranking
                freq = airport_counts.get(iata, 0) if airport_counts is not None else 0
                rank = 0
                if freq > q66: rank = 2
                elif freq > q33: rank = 1
                
                airports[iata] = {
                    "iata": iata,
                    "name": str(row.get(f'{prefix}_airport', '')),
                    "city": str(row.get(f'{prefix}_airport_city', '')),
                    "country": str(row.get(f'{prefix}_airport_country', '')),
                    "lat": lat,
                    "lon": lon,
                    "rank": rank
                }
            except (ValueError, TypeError):
                pass
        
        # We iterate to find unique airports. 
        for _, row in df.iterrows():
            extract_airport(row, 'dep')
            extract_airport(row, 'arr')
            
    # Sort ascending: rank 0 (red/small) first so they render at the bottom,
    # rank 2 (green/large) last so they appear on top and are clickable.
    sorted_airports = sorted(airports.values(), key=lambda x: x['rank'])
    airports_json = json.dumps(sorted_airports, default=str)
    
    # Colors
    bg_color = '#1e1e1e' if theme_mode == 'dark' else '#ffffff'
    text_color = '#ffffff' if theme_mode == 'dark' else '#000000'
    panel_bg = 'rgba(30, 30, 30, 0.95)' if theme_mode == 'dark' else 'rgba(255, 255, 255, 0.95)'
    line_color = '#00ccff' if theme_mode == 'dark' else '#0078d4'

    env = Environment(
        loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'html')),
        variable_start_string='<<',
        variable_end_string='>>',
        block_start_string='<%',
        block_end_string='%>',
        autoescape=False,
    )
    return env.get_template('map.html').render(
        bg_color=bg_color,
        text_color=text_color,
        panel_bg=panel_bg,
        line_color=line_color,
        airports_json=airports_json,
        tile_url=tile_url,
        attr=attr,
    )

