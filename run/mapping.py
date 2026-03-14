import json
import pandas as pd
import math
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
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        html, body {{ height: 100%; margin: 0; padding: 0; background-color: {bg_color}; color: {text_color}; font-family: sans-serif; overflow: hidden; }}
        #map {{ width: 100%; height: 100%; }}
        .info-panel {{
            position: absolute;
            bottom: 20px; left: 10px;
            width: 300px;
            max-height: 50%;
            display: flex; flex-direction: column;
            background: {panel_bg};
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0,0,0,0.3);
            z-index: 1000;
            display: none; /* Hidden by default */
            resize: both;
            overflow: auto;
            min-width: 200px;
            min-height: 100px;
        }}
        .panel-header {{ font-weight: bold; font-size: 16px; margin-bottom: 10px; border-bottom: 1px solid #444; padding-bottom: 5px; }}
        .panel-content {{ overflow-y: auto; flex: 1; font-size: 13px; }}
        .flight-row {{ padding: 4px 0; border-bottom: 1px solid rgba(128,128,128,0.2); }}
        .flight-row:hover {{ background: rgba(128,128,128,0.1); }}
        .loader {{ text-align: center; padding: 20px; font-style: italic; opacity: 0.7; }}
        .close-btn {{ float: right; cursor: pointer; opacity: 0.7; }}
        .close-btn:hover {{ opacity: 1; }}
        
        /* Flight Card Styles */
        .flight-card {{ 
            background: rgba(255,255,255,0.05); 
            border-left: 3px solid {line_color};
            margin-bottom: 8px;
            border-radius: 4px;
            padding: 10px;
        }}
        .dark-mode .flight-card {{ background: rgba(255,255,255,0.05); }}
        .flight-header {{ display: flex; justify-content: space-between; margin-bottom: 5px; font-weight: bold; }}
        .flight-sub {{ font-size: 11px; opacity: 0.7; margin-bottom: 5px; }}
        .flight-details {{ 
            display: none; 
            margin-top: 8px; 
            padding-top: 8px; 
            border-top: 1px solid rgba(255,255,255,0.1); 
            font-size: 12px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4px;
        }}
        .detail-kv {{ display: flex; flex-direction: column; margin-bottom: 4px; }}
        .label {{ opacity: 0.5; font-size: 10px; text-transform: uppercase; }}
        .expand-btn {{ 
            text-align: center; font-size: 10px; opacity: 0.5; cursor: pointer; margin-top: 5px; 
            background: rgba(0,0,0,0.2); padding: 2px; border-radius: 3px;
        }}
        .expand-btn:hover {{ opacity: 1; background: rgba(0,0,0,0.4); }}
    </style>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
</head>
<body>
    <div id="map"></div>
    <div id="info" class="info-panel">
        <div class="panel-header">Airport Info <span class="close-btn" onclick="deselect()">×</span></div>
        <div id="title-loc" style="margin-bottom:10px; opacity:0.8;"></div>
        <div id="panel-content" class="panel-content">Select an airport...</div>
    </div>

    <script>
        // --- DATA ---
        const airportsData = {airports_json};
        const airports = {{}}; // Lookup map
        const airportMarkers = {{}}; // Marker lookup
        
        // --- CONFIG ---
        // Green = Big/High, Yellow = Med, Red = Small/Low
        const COLOR_LOW = '#e74c3c';   // Red
        const COLOR_MED = '#f1c40f';   // Yellow
        const COLOR_HIGH = '#2ecc71';  // Green
        const LINE_COLOR = '{line_color}';

        // --- MAP INIT ---
        const map = L.map('map', {{
            preferCanvas: true,
            worldCopyJump: true,
            minZoom: 2
        }}).setView([20, 0], 2);

        // Custom Panes for Z-Index ordering
        map.createPane('routesPane');
        map.getPane('routesPane').style.zIndex = 390;
        
        map.createPane('airportsPane');
        map.getPane('airportsPane').style.zIndex = 400;

        L.tileLayer('{tile_url}', {{
            attribution: '{attr}',
            maxZoom: 19
        }}).addTo(map);

        // Layers
        const routeLayer = L.layerGroup().addTo(map);
        const airportLayer = L.layerGroup().addTo(map);
        const ghostLayer = L.layerGroup().addTo(map);

        // State
        let selectedSource = null;
        let selectedDest = null;
        let currentRoutes = []; // Store routes for client-side filtering
        let deselectMarker = null;
        let ghostMarkers = []; // Track ghost markers to clear easily

        // Helper for ghost markers
        function addGhost(ap, offsetLon) {{
             let color = COLOR_LOW;
             if (ap.rank === 1) color = COLOR_MED;
             if (ap.rank === 2) color = COLOR_HIGH;
            
             const marker = L.circleMarker([ap.lat, ap.lon + offsetLon], {{
                radius: 4 + (ap.rank * 1.5),
                fillColor: color,
                color: "#000000",
                weight: 0.5,
                stroke: true,
                fillOpacity: 0.8,
                pane: 'airportsPane'
            }});
            
            marker.bindTooltip(ap.iata + " - " + ap.city, {{ direction: 'top', offset: [0, -5] }});
            marker.on('click', (e) => {{
                L.DomEvent.stopPropagation(e);
                handleAirportClick(ap.iata);
            }});
            
            ghostLayer.addLayer(marker);
            ghostMarkers.push(marker);
        }}

        // --- RENDER AIRPORTS ---
        airportsData.forEach(ap => {{
            airports[ap.iata] = ap;
            
            let color = COLOR_LOW;
            if (ap.rank === 1) color = COLOR_MED;
            if (ap.rank === 2) color = COLOR_HIGH;
            
            const marker = L.circleMarker([ap.lat, ap.lon], {{
                radius: 4 + (ap.rank * 1.5),
                fillColor: color,
                color: "#000000",
                weight: 0.5,
                stroke: true,
                fillOpacity: 0.8,
                pane: 'airportsPane'
            }});
            
            marker.bindTooltip(ap.iata + " - " + ap.city, {{ direction: 'top', offset: [0, -5] }});
            
            marker.on('click', (e) => {{
                L.DomEvent.stopPropagation(e);
                handleAirportClick(ap.iata);
            }});
            
            airportMarkers[ap.iata] = marker;
            airportLayer.addLayer(marker);
        }});
        
        map.on('click', () => {{
            deselect();
        }});

        // --- INTERACTION ---
        function handleAirportClick(code) {{
            // Scenario 1: Nothing selected yet -> Select Source
            if (selectedSource === null) {{
                selectSource(code);
                return;
            }}

            // Scenario 2: Clicking the Source airport
            if (selectedSource === code) {{
                if (selectedDest !== null) {{
                    // If we were looking at a specific route, go back to showing all connections
                    deslectDestOnly();
                }} else {{
                    // Otherwise deselect all
                    deselect();
                }}
                return;
            }}

            // Scenario 3: Clicking a different airport
            // Check if it is connected to the current source
            const isConnected = currentRoutes.some(r => r.dep === code || r.arr === code);
            
            if (isConnected) {{
                if (selectedDest === code) {{
                    deslectDestOnly();
                }} else {{
                    selectedDest = code;
                    // SHOW panel here
                    document.getElementById('info').style.display = 'flex';
                    renderMapState(); // Filter view to this pair
                }}
            }} else {{
                // Not connected (or data not loaded), switch source
                selectSource(code);
            }}
        }}

        function selectSource(code) {{
            selectedSource = code;
            selectedDest = null;
            currentRoutes = []; // Clear previous data
            routeLayer.clearLayers();
            
            // Add Deselect Marker
            if (deselectMarker) map.removeLayer(deselectMarker);
            const ap = airports[code];
            if (ap) {{
                 deselectMarker = L.marker([ap.lat, ap.lon], {{
                    icon: L.divIcon({{
                        className: 'deselect-icon',
                        html: '<div style="background:rgba(30,30,30,0.85); color:white; border-radius:4px; width:16px; height:16px; text-align:center; line-height:14px; font-weight:bold; font-size:12px; border: 1px solid #555; cursor: pointer;">×</div>',
                        iconSize: [16, 16],
                        iconAnchor: [-10, 10] // Top-right offset
                    }})
                }}).addTo(map);
                
                deselectMarker.on('click', (e) => {{
                     L.DomEvent.stopPropagation(e);
                     deselect();
                }});
            }}
            
            // Do NOT remove this, as it handles the logic for hiding IF we were already showing something
            // But per request: "Don't show... until I click on a second airport"
            // So we hide it initially.
            document.getElementById('info').style.display = 'none';
            
            // Request Routes from Python
            console.log("REQUEST_ROUTES|" + code);
        }}
        
        function deselect() {{
            selectedSource = null;
            selectedDest = null;
            currentRoutes = [];
            routeLayer.clearLayers();
            document.getElementById('info').style.display = 'none';

            if (deselectMarker) {{
                map.removeLayer(deselectMarker);
                deselectMarker = null;
            }}
            
            // Clear ghosts
            ghostLayer.clearLayers();
            ghostMarkers = [];
            
            // Show all airports
            Object.values(airportMarkers).forEach(marker => {{
                if (!airportLayer.hasLayer(marker)) airportLayer.addLayer(marker);
            }});
        }}

        function deslectDestOnly() {{
            selectedDest = null;
            document.getElementById('info').style.display = 'none';
            renderMapState();
        }}
        
        // --- DATA RECEIVER ---
        function loadRoutes(routes) {{
            if (!routes) routes = [];
            currentRoutes = routes; // Store globally
            
            // Only render if we still have a source selected (user might have clicked away)
            if (selectedSource) {{
                renderMapState();
                
                // Clear old ghosts
                ghostLayer.clearLayers();
                ghostMarkers = [];

                // Filter Airports: Hide those not connected
                const connectedIatas = new Set();
                connectedIatas.add(selectedSource);
                routes.forEach(r => {{
                    connectedIatas.add(r.dep);
                    connectedIatas.add(r.arr);
                }});

                Object.keys(airportMarkers).forEach(iata => {{
                    const marker = airportMarkers[iata];
                    if (connectedIatas.has(iata)) {{
                        if (!airportLayer.hasLayer(marker)) airportLayer.addLayer(marker);
                        
                        // Add ghosts for wrapped worlds
                        const ap = airports[iata];
                        if (ap) {{
                             addGhost(ap, 360);
                             addGhost(ap, -360);
                        }}
                    }} else {{
                        if (airportLayer.hasLayer(marker)) airportLayer.removeLayer(marker);
                    }}
                }});
            }}
        }}

        // --- VISUALIZATION ---
        
        // Helper to draw geodesic line
        function drawGeodesic(src, dest, options, layer) {{
             let start = {{ lat: src.lat, lon: src.lon }};
             let end = {{ lat: dest.lat, lon: dest.lon }};
             
             // Simple Dateline Wrap Logic
             // If going the "long way" (diff > 180), shift one point by 360
             let dLon = end.lon - start.lon;
             let shift = 0;
             if (dLon > 180) {{
                 end.lon -= 360;
                 shift = -360;
             }} else if (dLon < -180) {{
                 end.lon += 360;
                 shift = 360;
             }}
             
             // Draw Primary Line
             const poly1 = _computeAndDrawPoly(start, end, options, layer);
             
             // If wrapped, Draw "Shadow" Line shifted by 360 (or -360) so it appears on the other side
             if (shift !== 0) {{
                 const start2 = {{ lat: start.lat, lon: start.lon - shift }}; // Inverse shift
                 const end2 = {{ lat: end.lat, lon: end.lon - shift }};
                 _computeAndDrawPoly(start2, end2, options, layer);
             }}
             return poly1;
        }}

        function _computeAndDrawPoly(start, end, options, layer) {{
             // Compute intermediate points for Great Circle
             const numPoints = 50;
             const points = [];
             
             const lat1 = start.lat * Math.PI / 180;
             const lon1 = start.lon * Math.PI / 180;
             const lat2 = end.lat * Math.PI / 180;
             const lon2 = end.lon * Math.PI / 180;
             
             const d = 2 * Math.asin(Math.sqrt(
                Math.pow(Math.sin((lat1 - lat2) / 2), 2) +
                Math.cos(lat1) * Math.cos(lat2) * Math.pow(Math.sin((lon1 - lon2) / 2), 2)
            ));

             for (let i = 0; i <= numPoints; i++) {{
                const f = i / numPoints;
                const A = Math.sin((1 - f) * d) / Math.sin(d);
                const B = Math.sin(f * d) / Math.sin(d);
                
                const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
                const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
                const z = A * Math.sin(lat1) + B * Math.sin(lat2);
                
                const phi = Math.atan2(z, Math.sqrt(x*x + y*y));
                const lam = Math.atan2(y, x);
                
                let pLat = phi * 180 / Math.PI;
                let pLon = lam * 180 / Math.PI;
                points.push([pLat, pLon]);
             }}
             
             // Unwrap for Leaflet Polyline continuity
             const finalPoints = [];
             if (points.length > 0) {{
                 finalPoints.push(points[0]);
                 for (let i = 1; i < points.length; i++) {{
                     let prev = finalPoints[i-1];
                     let curr = points[i];
                     let diff = curr[1] - prev[1];
                     
                     // If huge jump, adjust current to match previous direction
                     while (diff > 180) {{ curr[1] -= 360; diff -= 360; }}
                     while (diff < -180) {{ curr[1] += 360; diff += 360; }}
                     
                     finalPoints.push(curr);
                 }}
             }}

             const poly = L.polyline(finalPoints, options).addTo(layer);
             return poly;
        }}

        function renderMapState() {{
            routeLayer.clearLayers();
            
            if (!currentRoutes || currentRoutes.length === 0) {{
                 // Don't show panel content if empty, but panel logic handles visibility
                 return;
            }}

            // CASE A: Source Selected, No Destiny (Show All Connections)
            if (selectedSource && !selectedDest) {{
                // HIDE PANEL (Requested) - Logic already in selectSource/deselectDestOnly
                // But just in case, ensure map renders lines
                
                currentRoutes.forEach(r => {{
                    const otherCode = (r.dep === selectedSource) ? r.arr : r.dep;
                    const otherAp = airports[otherCode];
                    
                    if (otherAp) {{
                        const srcAp = airports[selectedSource];
                        drawGeodesic(srcAp, otherAp, {{
                            color: LINE_COLOR,
                            weight: 1.5,
                            opacity: 0.6,
                            pane: 'routesPane'
                        }}, routeLayer).bindPopup(r.flight + " (" + r.dep + "->" + r.arr + ")");
                    }}
                }});
                // Panel is hidden, so no need to update HTML content
            }}
            
            // CASE B: Source AND Dest Selected (Show Pair Details)
            else if (selectedSource && selectedDest) {{
                const destAp = airports[selectedDest];
                const destCity = destAp ? destAp.city : '';
                document.getElementById('title-loc').innerText = selectedSource + " ⇄ " + selectedDest + " (" + destCity + ")";
                
                // Filter routes
                const pairRoutes = currentRoutes.filter(r => r.dep === selectedDest || r.arr === selectedDest);
                
                // Draw ONE bold line
                const srcAp = airports[selectedSource];
                if (srcAp && destAp) {{
                    // Main Line
                    drawGeodesic(srcAp, destAp, {{
                         color: LINE_COLOR,
                         weight: 2,
                         opacity: 1,
                         pane: 'routesPane'
                    }}, routeLayer);
                }}
                
                // Detailed List with Collapsible Cards
                let html = '<div><strong>' + pairRoutes.length + ' Flights</strong></div><br>';
                
                pairRoutes.forEach((r, idx) => {{
                    // Ensure undefined values are handled
                    const callsign = r.callsign || '-';
                    const reg = r.reg || '-';
                    const type = r.type || '-';
                    const type_icao = r.type_icao || '-';
                    const dep_icao = r.dep_icao || '-';
                    const arr_icao = r.arr_icao || '-';
                    const airline = r.airline || '-';
                    const dep = r.dep;
                    const arr = r.arr;
                    
                    const uniqueId = 'flight-detail-' + idx;
                    
                    html += `
                    <div class="flight-card">
                        <div class="flight-header">
                            <span>${{r.flight}}</span>
                            <span style="opacity:0.7">${{type_icao}}</span>
                        </div>
                        <div class="flight-sub">
                             ${{dep}} (${{dep_icao}}) &rarr; ${{arr}} (${{arr_icao}})
                        </div>
                        
                        <!-- Details (Initially Hidden) -->
                        <div id="${{uniqueId}}" style="display:none; margin-top:8px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.1);">
                            <div style="display:grid; grid-template-columns: 1fr 1fr; gap:5px; font-size:11px;">
                                <div class="detail-kv" style="grid-column: span 2"><span class="label">Airline</span><span>${{airline}}</span></div>
                                <div class="detail-kv"><span class="label">Callsign</span><span>${{callsign}}</span></div>
                                <div class="detail-kv"><span class="label">Registration</span><span>${{reg}}</span></div>
                                <div class="detail-kv"><span class="label">Aircraft</span><span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${{type}}</span></div>
                                <div class="detail-kv"><span class="label">Type Code</span><span>${{type_icao}}</span></div>
                                <div class="detail-kv"><span class="label">From</span><span>${{dep}} / ${{dep_icao}}</span></div>
                                <div class="detail-kv"><span class="label">To</span><span>${{arr}} / ${{arr_icao}}</span></div>
                            </div>
                        </div>
                        
                        <div class="expand-btn" onclick="let el = document.getElementById('${{uniqueId}}'); el.style.display = (el.style.display === 'none' ? 'block' : 'none'); this.innerText = (el.style.display === 'block' ? '▲ Less Info' : '▼ More Info');">
                            ▼ More Info
                        </div>
                    </div>`;
                }});
                
                // Add "Back" button hint
                html += '<br><div style="text-align:center; font-size:11px; opacity:0.6; cursor:pointer;" onclick="deslectDestOnly()">Return to All Connections</div>';
                
                document.getElementById('panel-content').innerHTML = html;
            }}
        }}

    </script>
</body>
</html>
"""
    return html
