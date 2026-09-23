import json
import os
import pandas as pd
import math
from jinja2 import Environment, FileSystemLoader
from run import config

def create_map_html(df, tile_provider='Dark Mode', theme_mode='light', airport_counts=None, route_request_url=None):
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
                
                # Rank by number of unique non-stop destinations.
                destination_count = airport_counts.get(iata, 0) if airport_counts is not None else 0
                rank = 0
                if destination_count > 100:
                    rank = 3
                elif destination_count > 30:
                    rank = 2
                elif destination_count > 7:
                    rank = 1
                
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
            
    # Draw larger, better-connected airports on top of smaller airports.
    sorted_airports = sorted(airports.values(), key=lambda x: x['rank'])
    airports_json = json.dumps(sorted_airports, default=str)
    
    # Colors - glassy dark/light palettes for the redesigned map chrome
    if theme_mode == 'dark':
        bg_color = '#0b0d10'
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
        bg_color = '#eef1f5'
        text_color = '#14181d'
        panel_bg = 'rgba(255, 255, 255, 0.78)'
        panel_border = 'rgba(15, 23, 42, 0.08)'
        muted_color = 'rgba(20, 24, 29, 0.62)'
        card_bg = 'rgba(15, 23, 42, 0.045)'
        card_border = 'rgba(15, 23, 42, 0.08)'
        shadow_color = 'rgba(15, 23, 42, 0.16)'
        line_color = '#0a84ff'
        marker_border = '#888888'

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
    )

