// FSAtlas frontend - vanilla JS, no framework/build step. Fetches JSON from the Flask API
// and renders everything client-side: Leaflet map (canvas circleMarkers for the ~3-4k
// airports, not one DOM node per marker), filter toolbar, legend, saved items, SimBrief
// export, and the dark/light theme toggle.
(function () {
  'use strict';

  // --- Mobile-mode detection: width-based, matching the CSS breakpoint, so it stays in
  // sync whether it's a real phone, a resized window, or dev-tools device emulation -
  // deliberately not user-agent sniffing. ---
  function isMobileViewport() { return window.matchMedia('(max-width: 760px)').matches; }
  let isMobile = isMobileViewport();
  document.body.classList.toggle('is-mobile', isMobile);

  // --- Connection-lost detection: wraps fetch() so a network-level failure (server
  // unreachable/crashed) pops a modal instead of failing silently. ---
  const connectionModal = document.getElementById('connection-modal');
  function showConnectionLost() { connectionModal.classList.add('open'); }
  function hideConnectionLost() { connectionModal.classList.remove('open'); }
  document.getElementById('connection-close').addEventListener('click', hideConnectionLost);
  connectionModal.addEventListener('click', e => { if (e.target === connectionModal) hideConnectionLost(); });

  async function fsatlasFetch(url, options) {
    try {
      const response = await fetch(url, options);
      hideConnectionLost();
      return response;
    } catch (err) {
      showConnectionLost();
      throw err;
    }
  }
  // Heartbeat - catches the server going away even when the user isn't doing anything.
  setInterval(() => { fsatlasFetch('/api/settings').catch(() => {}); }, 5000);

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // --- App state populated from /api/meta at startup ---
  let columns = [];
  let mapTypes = {}; // name -> {url, attr}
  const THEME_MAP_TYPES = { dark: 'Dark Mode', light: 'Light Mode' };
  const USE_THEME_VALUE = ''; // sentinel: real tile names come from mapTypes keys, never ''
  let theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  let savedPilotId = '';

  const filters = document.getElementById('filters');
  const mapTypeSelect = document.getElementById('map-type');

  function effectiveMapType() {
    return mapTypeSelect.value === USE_THEME_VALUE ? THEME_MAP_TYPES[theme] : mapTypeSelect.value;
  }

  // ===================================================================================
  // MAP SETUP
  // ===================================================================================
  const map = L.map('map', { preferCanvas: true, minZoom: 2, zoomControl: false }).setView([20, 0], 2);

  map.createPane('routesPane');
  map.getPane('routesPane').style.zIndex = 390;
  map.createPane('sceneryPane');
  map.getPane('sceneryPane').style.zIndex = 395;
  map.getPane('sceneryPane').style.pointerEvents = 'none';
  map.createPane('airportsPane');
  map.getPane('airportsPane').style.zIndex = 400;

  let tileLayer = null;
  function setTileLayer(name) {
    const info = mapTypes[name] || mapTypes[Object.keys(mapTypes)[0]];
    if (!info) return;
    if (tileLayer) map.removeLayer(tileLayer);
    tileLayer = L.tileLayer(info.url, { attribution: info.attr, maxZoom: 19 }).addTo(map);
  }

  // Destination-count bands (red, gold, blue, dark blue) - theme-independent, defined once
  // as CSS custom properties so this single function is the only place both the marker
  // fill and the legend swatches read the color from.
  const RANK_VARS = ['--rank-0', '--rank-1', '--rank-2', '--rank-3'];
  function colorForRank(rank) {
    return getComputedStyle(document.documentElement).getPropertyValue(RANK_VARS[rank] || RANK_VARS[0]).trim();
  }

  // Bottom-right, collapsible into a slim pull tab that stays docked to the edge.
  const legendTemplate = document.getElementById('legend-template');
  const legend = L.control({ position: 'bottomright' });
  let legendEl = null;
  legend.onAdd = () => {
    const container = L.DomUtil.create('div', 'legend-root');
    container.appendChild(legendTemplate.content.cloneNode(true));
    container.querySelectorAll('.legend-dot').forEach(dot => {
      dot.style.background = colorForRank(Number(dot.dataset.rank));
    });
    // Starts collapsed on mobile - the small pull tab stays reachable but doesn't eat into
    // the limited map area the way the full legend card would.
    if (isMobile) container.classList.add('collapsed');
    container.querySelector('.legend-tab').addEventListener('click', () => {
      container.classList.toggle('collapsed');
    });
    L.DomEvent.disableClickPropagation(container);
    legendEl = container;
    return container;
  };
  legend.addTo(map);

  // Auto-collapses the legend the moment the flight-details panel would visually overlap
  // it, then leaves it alone - never force-expands it back, even if the panel later
  // closes/shrinks, so a manual re-open (the pull tab) or a page refresh are the only ways
  // to see it again. Edge-triggered (only acts the instant overlap begins) so re-opening
  // the legend while the panel still happens to overlap doesn't immediately re-collapse it.
  let legendWasOverlapping = false;
  function checkLegendOverlap() {
    if (!legendEl || !infoPanel.classList.contains('open')) { legendWasOverlapping = false; return; }
    const a = infoPanel.getBoundingClientRect();
    const b = legendEl.getBoundingClientRect();
    const overlapping = a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    if (overlapping && !legendWasOverlapping) legendEl.classList.add('collapsed');
    legendWasOverlapping = overlapping;
  }

  // Layers
  const routeLayer = L.layerGroup().addTo(map);
  const airportLayer = L.layerGroup().addTo(map);
  let airportsOverlayEnabled = true;

  function setAirportsOverlayEnabled(enabled) {
    airportsOverlayEnabled = enabled;
    if (enabled) {
      routeLayer.addTo(map);
      airportLayer.addTo(map);
    } else {
      map.removeLayer(routeLayer);
      map.removeLayer(airportLayer);
    }
  }

  // --- Scenery overlay: star markers for the user's imported flight-sim scenery
  // locations (see /api/scenery), drawn in a pane below the airport dots and never
  // interactive/selectable (interactive:false + a pointer-events:none pane so clicks
  // always fall through to whatever's underneath). Only added to the map while enabled. ---
  let sceneryOverlayEnabled = false;
  let sceneryData = []; // [{iata, icao, name, city, lat, lon}, ...]
  let sceneryImportErrors = [];
  const sceneryLayer = L.layerGroup();
  const sceneryOffsetsBuilt = new Set();
  const STAR_ICON_SVG = '<svg viewBox="0 0 24 24" fill="#ffd447" stroke="#8a6d00" stroke-width="1" stroke-linejoin="round"><polygon points="12 2 14.9 8.6 22 9.3 16.5 14 18.2 21 12 17.3 5.8 21 7.5 14 2 9.3 9.1 8.6"></polygon></svg>';
  const sceneryIcon = L.divIcon({ className: 'scenery-star-icon', html: STAR_ICON_SVG, iconSize: [14, 14], iconAnchor: [7, 7] });

  function createSceneryForOffset(offset) {
    if (sceneryOffsetsBuilt.has(offset)) return;
    sceneryOffsetsBuilt.add(offset);
    sceneryData.forEach(s => {
      L.marker([s.lat, s.lon + offset], { icon: sceneryIcon, interactive: false, keyboard: false, pane: 'sceneryPane' })
        .bindTooltip((s.icao || s.iata) + ' - ' + s.name, { direction: 'top', offset: [0, -5], className: 'atlas-tooltip' })
        .addTo(sceneryLayer);
    });
  }

  function rebuildSceneryMarkers() {
    sceneryLayer.clearLayers();
    sceneryOffsetsBuilt.clear();
    [...renderedOffsets].forEach(offset => createSceneryForOffset(offset));
  }

  function setSceneryOverlayEnabled(enabled) {
    sceneryOverlayEnabled = enabled;
    if (enabled) { rebuildSceneryMarkers(); sceneryLayer.addTo(map); } else { map.removeLayer(sceneryLayer); }
  }

  function showImportedScenery() {
    if (!sceneryData.length) return;
    setSceneryOverlayEnabled(true);
    fsatlasFetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenery_overlay: true })
    }).catch(() => {});
  }

  async function loadSceneryData() {
    try {
      const response = await fsatlasFetch('/api/scenery');
      const result = await response.json();
      sceneryData = result.sceneries || [];
      sceneryImportErrors = result.errors || [];
      updateSceneryErrors(sceneryImportErrors);
    } catch (err) { sceneryData = []; }
    if (sceneryOverlayEnabled) rebuildSceneryMarkers();
  }

  // Render everything at whichever 360deg-wide "world copies" the viewport currently
  // covers. Offsets are created lazily as the user pans, so the map scrolls horizontally
  // without limit while every airport stays populated (matches flightconnections.com).
  const renderedOffsets = new Set();
  let airports = {}; // iata -> airport record
  let airportMarkers = {}; // iata -> {offset -> marker}
  let airportsData = [];

  // State
  let selectedSource = null;
  let selectedSourceOffset = 0;
  let selectedDest = null;
  let selectedDestOffset = 0;
  let currentRoutes = [];
  let deselectMarker = null;
  let deselectDestMarker = null;
  let _isDragging = false;
  let _dragTimer = null;
  let visibleFilter = null; // Set of visible IATA codes, or null to show all
  let pendingSavedFlight = null;
  let highlightedFlightKey = null;
  let lastRenderedRoutes = [];

  function nearestOffset(canonicalLon) {
    const centerLon = map.getCenter().lng;
    return Math.round((centerLon - canonicalLon) / 360) * 360;
  }

  function showAirport(iata) {
    Object.values(airportMarkers[iata] || {}).forEach(m => { if (!airportLayer.hasLayer(m)) airportLayer.addLayer(m); });
  }
  function hideAirport(iata) {
    Object.values(airportMarkers[iata] || {}).forEach(m => { if (airportLayer.hasLayer(m)) airportLayer.removeLayer(m); });
  }

  // Bigger radius on mobile - a 3-6px circle is nearly impossible to tap accurately with
  // a finger, even though it's fine for a mouse pointer on desktop.
  function markerRadius(rank) {
    return isMobile ? 7 + rank * 2 : 3 + rank * 1.15;
  }

  function createAirportsForOffset(offset) {
    if (renderedOffsets.has(offset)) return;
    renderedOffsets.add(offset);
    if (sceneryOverlayEnabled) createSceneryForOffset(offset);

    airportsData.forEach(ap => {
      const color = colorForRank(ap.rank);
      const marker = L.circleMarker([ap.lat, ap.lon + offset], {
        radius: markerRadius(ap.rank),
        fillColor: color,
        color: getComputedStyle(document.documentElement).getPropertyValue('--marker-border').trim(),
        weight: isMobile ? 1 : 0.6, stroke: true, fillOpacity: 0.75, pane: 'airportsPane'
      });
      marker.bindTooltip(ap.iata + " - " + ap.city, { direction: 'top', offset: [0, -5], className: 'atlas-tooltip' });
      marker.on('click', (e) => { L.DomEvent.stopPropagation(e); handleAirportClick(ap.iata, offset); });

      if (!airportMarkers[ap.iata]) airportMarkers[ap.iata] = {};
      airportMarkers[ap.iata][offset] = marker;

      if (!visibleFilter || visibleFilter.has(ap.iata)) airportLayer.addLayer(marker);
    });
  }

  function ensureWorldCoverage() {
    const bounds = map.getBounds();
    const minOffset = Math.floor(bounds.getWest() / 360) * 360 - 360;
    const maxOffset = Math.ceil(bounds.getEast() / 360) * 360 + 360;
    for (let offset = minOffset; offset <= maxOffset; offset += 360) createAirportsForOffset(offset);
  }

  map.on('move', ensureWorldCoverage);
  map.on('drag', () => { _isDragging = true; if (_dragTimer) { clearTimeout(_dragTimer); _dragTimer = null; } });
  map.on('dragend', () => { _dragTimer = setTimeout(() => { _isDragging = false; }, 200); });
  map.on('moveend', () => { if (selectedSource) renderMapState(); });
  map.on('click', () => { if (!_isDragging) deselect(); });

  const infoPanel = document.getElementById('info');
  document.getElementById('info-close').addEventListener('click', deselect);

  function handleAirportClick(code, offset) {
    if (selectedSource === null) { selectSource(code, offset); return; }

    if (selectedSource === code) {
      if (selectedDest !== null) deslectDestOnly(); else deselect();
      return;
    }

    const isConnected = currentRoutes.some(r => r.dep === code || r.arr === code);
    if (isConnected) {
      if (selectedDest === code) { deslectDestOnly(); return; }
      selectedDest = code;
      selectedDestOffset = offset;
      infoPanel.classList.add('open');

      if (deselectDestMarker) airportLayer.removeLayer(deselectDestMarker);
      const destAp = airports[code];
      if (destAp) {
        deselectDestMarker = L.marker([destAp.lat, destAp.lon + offset], {
          icon: L.divIcon({
            className: 'deselect-icon',
            html: '<div style="background:rgba(30,30,30,0.85); color:white; border-radius:4px; width:16px; height:16px; text-align:center; line-height:14px; font-weight:bold; font-size:12px; border: 1px solid #555; cursor: pointer;">\u00d7</div>',
            iconSize: [16, 16], iconAnchor: [-10, 10]
          })
        }).addTo(airportLayer);
        deselectDestMarker.on('click', (e) => { L.DomEvent.stopPropagation(e); deslectDestOnly(); });
      }
      renderMapState();
    } else {
      selectSource(code, offset);
    }
  }

  function selectSource(code, offset) {
    selectedSource = code;
    const ap = airports[code];
    selectedSourceOffset = offset !== undefined ? offset : nearestOffset(ap ? ap.lon : 0);
    selectedDest = null;
    currentRoutes = [];
    routeLayer.clearLayers();

    if (deselectDestMarker) { airportLayer.removeLayer(deselectDestMarker); deselectDestMarker = null; }

    if (deselectMarker) airportLayer.removeLayer(deselectMarker);
    if (ap) {
      deselectMarker = L.marker([ap.lat, ap.lon + selectedSourceOffset], {
        icon: L.divIcon({
          className: 'deselect-icon',
          html: '<div style="background:rgba(30,30,30,0.85); color:white; border-radius:4px; width:16px; height:16px; text-align:center; line-height:14px; font-weight:bold; font-size:12px; border: 1px solid #555; cursor: pointer;">\u00d7</div>',
          iconSize: [16, 16], iconAnchor: [-10, 10]
        })
      }).addTo(airportLayer);
      deselectMarker.on('click', (e) => {
        L.DomEvent.stopPropagation(e);
        if (selectedDest !== null && selectedDest !== selectedSource) {
          const destAp = airports[selectedDest];
          if (destAp) map.panTo([destAp.lat, destAp.lon + selectedDestOffset], { animate: true, duration: 0.5 });
          selectSource(selectedDest, selectedDestOffset);
        } else {
          deselect();
        }
      });
    }

    infoPanel.classList.remove('open');

    fsatlasFetch('/api/flights?iata=' + encodeURIComponent(code) + '&filters=' + encodeURIComponent(JSON.stringify(currentFilterTree())))
      .then(response => response.ok ? response.json() : [])
      .then(loadRoutes)
      .catch(() => loadRoutes([]));
  }

  function deselect() {
    selectedSource = null;
    selectedDest = null;
    currentRoutes = [];
    highlightedFlightKey = null;
    routeLayer.clearLayers();
    infoPanel.classList.remove('open');
    checkLegendOverlap();

    if (deselectMarker) { airportLayer.removeLayer(deselectMarker); deselectMarker = null; }
    if (deselectDestMarker) { airportLayer.removeLayer(deselectDestMarker); deselectDestMarker = null; }

    visibleFilter = null;
    Object.keys(airports).forEach(iata => showAirport(iata));
  }

  function deslectDestOnly() {
    selectedDest = null;
    infoPanel.classList.remove('open');
    if (deselectDestMarker) { airportLayer.removeLayer(deselectDestMarker); deselectDestMarker = null; }
    renderMapState();
  }

  function loadRoutes(routes) {
    if (!routes) routes = [];
    currentRoutes = routes;

    if (selectedSource) {
      const connectedIatas = new Set();
      connectedIatas.add(selectedSource);
      routes.forEach(r => { connectedIatas.add(r.dep); connectedIatas.add(r.arr); });
      visibleFilter = connectedIatas;

      Object.keys(airports).forEach(iata => { connectedIatas.has(iata) ? showAirport(iata) : hideAirport(iata); });

      if (pendingSavedFlight && pendingSavedFlight.dep === selectedSource) {
        const wantedArr = pendingSavedFlight.arr;
        if (wantedArr && connectedIatas.has(wantedArr)) {
          selectedDest = wantedArr;
          selectedDestOffset = selectedSourceOffset;
          infoPanel.classList.add('open');
        } else if (routes.length > 0 && connectedIatas.size === 1) {
          selectedDest = selectedSource;
          selectedDestOffset = selectedSourceOffset;
          infoPanel.classList.add('open');
        }
      } else if (routes.length > 0 && connectedIatas.size === 1) {
        selectedDest = selectedSource;
        selectedDestOffset = selectedSourceOffset;
        infoPanel.classList.add('open');
      }

      renderMapState();

      if (pendingSavedFlight) {
        if (selectedDest) { frameSavedFlightRoute(pendingSavedFlight); scrollToHighlightedCard(pendingSavedFlight); }
        pendingSavedFlight = null;
      }
    }
  }

  // --- SAVED FLIGHTS (bookmarking) ---
  const BOOKMARK_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>';
  const BOOKMARK_ICON_FILLED = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"></path></svg>';
  const savedFlightKeys = new Set();

  // Shared identity for a route record - must match `flight_key` in run/webapp/storage.py.
  function flightKey(r) { return [r.reg, r.flight, r.dep, r.arr, r.date].join('|'); }

  function formatFlightTime(hours) {
    if (hours === null || hours === undefined) return 'Unknown';
    const totalMinutes = Math.round(hours * 60);
    return `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;
  }
  const FLIGHT_TIME_INFO = "Estimated as (Distance x 1.07 / Cruise Speed) + Ascent/Descent Buffer, "
    + "where Buffer = 83.33 / Cruise Speed (time lost climbing/descending ~250nm at 75% of cruise "
    + "speed vs. covering it at full cruise speed). Cruise speed comes from aircraft_crz_speeds.json; "
    + "shown as Unknown when the aircraft's ICAO type isn't in that file.";

  function setSaveButtonState(btn, saved) {
    btn.classList.toggle('saved', saved);
    btn.innerHTML = (saved ? BOOKMARK_ICON_FILLED : BOOKMARK_ICON) + (saved ? ' Saved' : ' Save Flight');
  }

  async function toggleSaveFlight(route, btn) {
    const key = flightKey(route);
    const wasSaved = savedFlightKeys.has(key);
    btn.disabled = true;
    try {
      await fsatlasFetch('/api/saved-flights', {
        method: wasSaved ? 'DELETE' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(route)
      });
      if (wasSaved) savedFlightKeys.delete(key); else savedFlightKeys.add(key);
      setSaveButtonState(btn, !wasSaved);
    } catch (err) { /* connection-lost modal already shown */ }
    btn.disabled = false;
  }

  function showSavedFlight(record) {
    document.getElementById('saved-modal').classList.remove('open');
    pendingSavedFlight = record;
    highlightedFlightKey = flightKey(record);
    const depAp = airports[record.dep];
    const offset = depAp ? nearestOffset(depAp.lon) : 0;
    selectSource(record.dep, offset);
  }

  function frameSavedFlightRoute(record) {
    const depAp = airports[record.dep];
    const arrAp = airports[record.arr];
    if (!depAp) return;

    const offset = nearestOffset(depAp.lon);
    let bounds;
    if (arrAp && record.arr !== record.dep) {
      const curvePoints = computeGeodesicPoints(depAp.lat, depAp.lon, arrAp.lat, arrAp.lon).map(p => [p[0], p[1] + offset]);
      bounds = L.latLngBounds(curvePoints);
    } else {
      bounds = L.latLngBounds([[depAp.lat, depAp.lon + offset], [depAp.lat, depAp.lon + offset]]);
    }

    const panelRect = infoPanel.getBoundingClientRect();
    map.fitBounds(bounds, isMobile ? {
      paddingTopLeft: [50, 50], paddingBottomRight: [50, panelRect.height + 20], maxZoom: 7, animate: true
    } : {
      paddingTopLeft: [panelRect.right + 20, 50], paddingBottomRight: [50, 50], maxZoom: 7, animate: true
    });
  }

  function scrollToHighlightedCard(record) {
    const idx = lastRenderedRoutes.findIndex(r => flightKey(r) === flightKey(record));
    if (idx === -1) return;
    const card = document.getElementById('flight-card-' + idx);
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // --- SIMBRIEF EXPORT ---
  async function exportToSimbrief(route, btn) {
    const originalLabel = btn.innerHTML;
    btn.disabled = true;
    btn.innerText = 'Opening SimBrief...';
    try {
      const response = await fsatlasFetch('/api/simbrief/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(route)
      });
      const result = await response.json();
      if (!response.ok || !result.url) throw new Error(result.error || 'Export failed');
      window.open(result.url, '_blank', 'noopener');
    } catch (err) {
      btn.innerText = 'Export failed';
      setTimeout(() => { btn.innerHTML = originalLabel; btn.disabled = false; }, 2500);
      return;
    }
    btn.innerHTML = originalLabel;
    btn.disabled = false;
  }

  // --- VISUALIZATION (great-circle routes) ---
  function computeGeodesicPoints(srcLat, srcLon, destLat, destLon) {
    let dLon = destLon - srcLon;
    if (dLon > 180) destLon -= 360; else if (dLon < -180) destLon += 360;

    const lat1 = srcLat * Math.PI / 180, lon1 = srcLon * Math.PI / 180;
    const lat2 = destLat * Math.PI / 180, lon2 = destLon * Math.PI / 180;

    const d = 2 * Math.asin(Math.sqrt(
      Math.pow(Math.sin((lat1 - lat2) / 2), 2) + Math.cos(lat1) * Math.cos(lat2) * Math.pow(Math.sin((lon1 - lon2) / 2), 2)
    ));

    if (d < 1e-9) return [[srcLat, srcLon], [destLat, destLon]];

    const numPoints = 50;
    const raw = [];
    for (let i = 0; i <= numPoints; i++) {
      const f = i / numPoints;
      const A = Math.sin((1 - f) * d) / Math.sin(d);
      const B = Math.sin(f * d) / Math.sin(d);
      const x = A * Math.cos(lat1) * Math.cos(lon1) + B * Math.cos(lat2) * Math.cos(lon2);
      const y = A * Math.cos(lat1) * Math.sin(lon1) + B * Math.cos(lat2) * Math.sin(lon2);
      const z = A * Math.sin(lat1) + B * Math.sin(lat2);
      raw.push([Math.atan2(z, Math.sqrt(x * x + y * y)) * 180 / Math.PI, Math.atan2(y, x) * 180 / Math.PI]);
    }

    const pts = [raw[0].slice()];
    for (let i = 1; i < raw.length; i++) {
      const prev = pts[i - 1];
      const curr = raw[i].slice();
      let diff = curr[1] - prev[1];
      while (diff > 180) { curr[1] -= 360; diff -= 360; }
      while (diff < -180) { curr[1] += 360; diff += 360; }
      pts.push(curr);
    }
    return pts;
  }

  function drawRouteAtCenter(src, dest, options, layer, offset) {
    const pts = computeGeodesicPoints(src.lat, src.lon, dest.lat, dest.lon);
    const shifted = pts.map(p => [p[0], p[1] + offset]);
    L.polyline(shifted, options).addTo(layer);
  }

  // Small filled plane silhouette (matches the reference image) - colored with the same
  // --line-color as the route itself rather than a separate icon color, and rotated to
  // face from source toward destination.
  const PLANE_ICON = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="#000" stroke-width="1" stroke-linejoin="round"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-4.5l8 2.5z"></path></svg>';

  // Marks the midpoint of a complete (both-ends-selected) route with a plane icon,
  // oriented along the direction of travel. Direction is computed in screen-pixel space
  // (not raw lat/lon bearing) so it stays visually correct regardless of map projection
  // or which world copy the route is drawn on.
  function drawRouteMidpointPlane(src, dest, offset, layer) {
    const pts = computeGeodesicPoints(src.lat, src.lon, dest.lat, dest.lon);
    const mid = Math.floor(pts.length / 2);
    const before = pts[Math.max(0, mid - 1)];
    const after = pts[Math.min(pts.length - 1, mid + 1)];
    const midPoint = pts[mid];
    const p1 = map.latLngToLayerPoint([before[0], before[1] + offset]);
    const p2 = map.latLngToLayerPoint([after[0], after[1] + offset]);
    const angle = Math.atan2(p2.y - p1.y, p2.x - p1.x) * 180 / Math.PI + 90;

    L.marker([midPoint[0], midPoint[1] + offset], {
      icon: L.divIcon({
        className: 'route-plane-icon',
        html: `<div class="route-plane-badge" style="transform: rotate(${angle}deg)">${PLANE_ICON}</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11]
      }),
      interactive: false,
      pane: 'routesPane'
    }).addTo(layer);
  }

  function renderMapState() {
    checkLegendOverlap();
    routeLayer.clearLayers();
    if (!currentRoutes || currentRoutes.length === 0) return;

    const lineColor = getComputedStyle(document.documentElement).getPropertyValue('--line-color').trim();

    // CASE A: Source Selected, No Dest (Show All Connections)
    if (selectedSource && !selectedDest) {
      currentRoutes.forEach(r => {
        const otherCode = (r.dep === selectedSource) ? r.arr : r.dep;
        const otherAp = airports[otherCode];
        if (otherAp) {
          const srcAp = airports[selectedSource];
          drawRouteAtCenter(srcAp, otherAp, { color: lineColor, weight: 1.5, opacity: 0.6, pane: 'routesPane' }, routeLayer, selectedSourceOffset);
        }
      });
      return;
    }

    // CASE B: Source AND Dest Selected (Show Pair Details)
    if (selectedSource && selectedDest) {
      const destAp = airports[selectedDest];
      const destCity = destAp ? destAp.city : '';
      const isSelfLoop = selectedDest === selectedSource;
      document.getElementById('title-loc').innerText = isSelfLoop
        ? selectedSource + " (" + destCity + ") \u2014 Self Flights"
        : selectedSource + " \u21c4 " + selectedDest + " (" + destCity + ")";

      const pairRoutes = currentRoutes.filter(r => r.dep === selectedDest || r.arr === selectedDest);
      lastRenderedRoutes = pairRoutes;

      const srcAp = airports[selectedSource];
      if (srcAp && destAp && !isSelfLoop) {
        drawRouteAtCenter(srcAp, destAp, { color: lineColor, weight: 2, opacity: 1, pane: 'routesPane' }, routeLayer, selectedSourceOffset);
        drawRouteMidpointPlane(srcAp, destAp, selectedSourceOffset, routeLayer);
      }

      let html = '<div><strong>' + pairRoutes.length + ' Flights</strong></div><br>';
      pairRoutes.forEach((r, idx) => {
        const callsign = r.callsign || '-';
        const reg = r.reg || '-';
        const type = r.type || '-';
        const type_icao = r.type_icao || '-';
        const dep_icao = r.dep_icao || '-';
        const arr_icao = r.arr_icao || '-';
        const airline = r.airline || '-';
        const date = r.date || '-';
        const flightTime = formatFlightTime(r.flight_time_hours);
        const dep = r.dep, arr = r.arr;
        const uniqueId = uniqueIdFor(idx);
        const isHighlighted = highlightedFlightKey !== null && flightKey(r) === highlightedFlightKey;

        html += `
          <div class="flight-card${isHighlighted ? ' highlighted' : ''}" id="flight-card-${idx}">
            <div class="flight-header"><span>${escapeHtml(r.flight)}</span><span style="opacity:0.7">${escapeHtml(type_icao)}</span></div>
            <div class="flight-sub">${escapeHtml(dep)} (${escapeHtml(dep_icao)}) &rarr; ${escapeHtml(arr)} (${escapeHtml(arr_icao)})</div>
            <div id="${uniqueId}" style="display:${isHighlighted ? 'block' : 'none'}; margin-top:8px; padding-top:8px; border-top:1px solid rgba(255,255,255,0.1);">
              <div style="display:grid; grid-template-columns: 1fr 1fr; gap:5px; font-size:11px;">
                <div class="detail-kv" style="grid-column: span 2"><span class="label">Airline</span><span>${escapeHtml(airline)}</span></div>
                <div class="detail-kv"><span class="label">Callsign</span><span>${escapeHtml(callsign)}</span></div>
                <div class="detail-kv"><span class="label">Registration</span><span>${escapeHtml(reg)}</span></div>
                <div class="detail-kv"><span class="label">Aircraft</span><span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${escapeHtml(type)}</span></div>
                <div class="detail-kv"><span class="label">Type Code</span><span>${escapeHtml(type_icao)}</span></div>
                <div class="detail-kv"><span class="label">From</span><span>${escapeHtml(dep)} / ${escapeHtml(dep_icao)}</span></div>
                <div class="detail-kv"><span class="label">To</span><span>${escapeHtml(arr)} / ${escapeHtml(arr_icao)}</span></div>
                <div class="detail-kv"><span class="label">Date Recorded</span><span>${escapeHtml(date)}</span></div>
                <div class="detail-kv"><span class="label">Flight Time<span class="info-icon" title="${escapeHtml(FLIGHT_TIME_INFO)}">i</span></span><span>${escapeHtml(flightTime)}</span></div>
              </div>
              <button class="simbrief-btn" id="simbrief-${idx}" type="button">&#9992; Export to SimBrief</button>
              <button class="save-btn" id="save-${idx}" type="button"></button>
            </div>
            <div class="expand-btn" id="expand-btn-${idx}">${isHighlighted ? '\u25b2 Less Info' : '\u25bc More Info'}</div>
          </div>`;
      });
      html += '<br><div style="text-align:center; font-size:11px; opacity:0.6; cursor:pointer;" id="return-to-all">Return to All Connections</div>';

      document.getElementById('panel-content').innerHTML = html;

      pairRoutes.forEach((r, idx) => {
        const btn = document.getElementById('simbrief-' + idx);
        if (btn) btn.addEventListener('click', () => exportToSimbrief(r, btn));
        const saveBtn = document.getElementById('save-' + idx);
        if (saveBtn) {
          setSaveButtonState(saveBtn, savedFlightKeys.has(flightKey(r)));
          saveBtn.addEventListener('click', () => toggleSaveFlight(r, saveBtn));
        }
        const expandBtn = document.getElementById('expand-btn-' + idx);
        expandBtn.addEventListener('click', () => {
          highlightedFlightKey = null;
          const el = document.getElementById(uniqueIdFor(idx));
          const open = el.style.display === 'block';
          el.style.display = open ? 'none' : 'block';
          expandBtn.innerText = open ? '\u25bc More Info' : '\u25b2 Less Info';
        });
      });
      const returnLink = document.getElementById('return-to-all');
      if (returnLink) returnLink.addEventListener('click', deslectDestOnly);
    }
  }
  function uniqueIdFor(idx) { return 'flight-detail-' + idx; }

  // ===================================================================================
  // SIDEBAR / FILTER PANEL
  // ===================================================================================
  const flightsCountEl = document.getElementById('flights-count');

  // --- Theme toggle ---
  const SUN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.5"></circle><path d="M12 2.5v3M12 18.5v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2.5 12h3M18.5 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"></path></svg>';
  const MOON_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"></path></svg>';
  const themeToggle = document.getElementById('theme-toggle');

  function applyTheme(nextTheme, persist) {
    theme = nextTheme;
    document.documentElement.dataset.theme = theme;
    themeToggle.innerHTML = theme === 'dark' ? MOON_ICON : SUN_ICON;
    if (persist) {
      fsatlasFetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ theme })
      }).catch(() => {});
    }
    if (mapTypeSelect.value === USE_THEME_VALUE) setTileLayer(effectiveMapType());
    // Marker/route colors read CSS vars live via getComputedStyle, but existing markers'
    // fillColor was set at creation time - re-render them so an in-session toggle updates
    // colors immediately instead of only on the next filter apply.
    rebuildAirportMarkers();
    if (selectedSource) renderMapState();
  }
  themeToggle.addEventListener('click', () => applyTheme(theme === 'dark' ? 'light' : 'dark', true));

  // --- Saved Flights / Saved Searches modal ---
  const savedModal = document.getElementById('saved-modal');
  const savedList = document.getElementById('saved-list');
  const savedSort = document.getElementById('saved-sort');
  let savedFlightsRaw = [];

  function sortedSavedFlights() {
    const flights = [...savedFlightsRaw];
    const dist = f => (typeof f.distance === 'number' ? f.distance : null);
    const distCompare = (a, b, dir) => {
      const da = dist(a), db = dist(b);
      if (da === null && db === null) return 0;
      if (da === null) return 1;
      if (db === null) return -1;
      return dir * (da - db);
    };
    const savedAt = f => f.saved_at || '';
    const firstTag = f => (f.tags && f.tags.length ? f.tags[0].toLowerCase() : '\uffff');
    switch (savedSort.value) {
      case 'distance-asc': flights.sort((a, b) => distCompare(a, b, 1)); break;
      case 'distance-desc': flights.sort((a, b) => distCompare(a, b, -1)); break;
      case 'saved-asc': flights.sort((a, b) => savedAt(a).localeCompare(savedAt(b))); break;
      case 'tag': flights.sort((a, b) => firstTag(a).localeCompare(firstTag(b))); break;
      case 'saved-desc':
      default: flights.sort((a, b) => savedAt(b).localeCompare(savedAt(a))); break;
    }
    return flights;
  }

  async function saveFlightTags(flight, tags) {
    const uniqueTags = [...new Set(tags.map(t => t.trim()).filter(Boolean))];
    try {
      await fsatlasFetch('/api/saved-flights', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...flight, tags: uniqueTags })
      });
    } catch (err) { /* best effort */ }
    loadSavedFlights();
  }

  function renderSavedFlights() {
    const flights = sortedSavedFlights();
    document.getElementById('saved-routes-count').textContent = flights.length ? String(flights.length) : '';
    if (!flights.length) {
      savedList.innerHTML = '<div class="saved-empty">No saved flights yet. Use the bookmark button in a flight\'s More Info panel to save one.</div>';
      return;
    }
    const allTags = [...new Set(savedFlightsRaw.flatMap(f => f.tags || []))].sort((a, b) => a.localeCompare(b));

    savedList.innerHTML = flights.map((f, i) => {
      const depLabel = (f.dep_city ? escapeHtml(f.dep_city) + ' ' : '') + '(' + escapeHtml(f.dep_icao || f.dep || '?') + ')';
      const arrLabel = (f.arr_city ? escapeHtml(f.arr_city) + ' ' : '') + '(' + escapeHtml(f.arr_icao || f.arr || '?') + ')';
      const tags = f.tags || [];
      return `
        <div class="saved-row" data-idx="${i}">
          <div class="saved-row-top">
            <div>
              <div class="saved-route">${depLabel} &rarr; ${arrLabel}</div>
              <div class="saved-type">${escapeHtml(f.type_icao || '')}</div>
            </div>
            <button class="saved-remove" type="button" title="Remove" aria-label="Remove">&times;</button>
          </div>
          <div class="saved-tags">
            ${tags.map(t => `<span class="tag-chip">${escapeHtml(t)}<button type="button" class="tag-remove" data-tag="${escapeHtml(t)}" title="Remove tag" aria-label="Remove tag ${escapeHtml(t)}">&times;</button></span>`).join('')}
            <button type="button" class="tag-add-btn">+ Tag</button>
          </div>
          <div class="tag-editor">
            <input type="text" class="tag-input" placeholder="Search or create a tag...">
            <div class="tag-suggestions"></div>
          </div>
        </div>
      `;
    }).join('');

    [...savedList.querySelectorAll('.saved-row')].forEach((row, i) => {
      const flight = flights[i];
      row.addEventListener('click', () => { savedModal.classList.remove('open'); showSavedFlight(flight); });
      row.querySelector('.saved-remove').addEventListener('click', async e => {
        e.stopPropagation();
        try {
          await fsatlasFetch('/api/saved-flights', {
            method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(flight)
          });
        } catch (err) { /* best effort */ }
        loadSavedFlights();
      });

      [...row.querySelectorAll('.tag-remove')].forEach(btn => {
        btn.addEventListener('click', e => {
          e.stopPropagation();
          saveFlightTags(flight, (flight.tags || []).filter(t => t !== btn.dataset.tag));
        });
      });

      const addBtn = row.querySelector('.tag-add-btn');
      const editor = row.querySelector('.tag-editor');
      const input = editor.querySelector('.tag-input');
      const suggestionsEl = editor.querySelector('.tag-suggestions');

      function renderSuggestions() {
        const query = input.value.trim().toLowerCase();
        const existing = new Set((flight.tags || []).map(t => t.toLowerCase()));
        const matches = allTags.filter(t => !existing.has(t.toLowerCase()) && (!query || t.toLowerCase().includes(query)));
        let html = matches.map(t => `<button type="button" class="tag-suggestion" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`).join('');
        if (query && !allTags.some(t => t.toLowerCase() === query)) {
          html += `<button type="button" class="tag-suggestion create" data-tag="${escapeHtml(input.value.trim())}">+ Create "${escapeHtml(input.value.trim())}"</button>`;
        }
        suggestionsEl.innerHTML = html || '<span class="tag-suggestion-empty">No matches</span>';
        [...suggestionsEl.querySelectorAll('.tag-suggestion')].forEach(sBtn => {
          sBtn.addEventListener('click', e => { e.stopPropagation(); saveFlightTags(flight, [...(flight.tags || []), sBtn.dataset.tag]); });
        });
      }

      addBtn.addEventListener('click', e => {
        e.stopPropagation();
        const wasOpen = editor.classList.contains('open');
        savedList.querySelectorAll('.tag-editor').forEach(el => el.classList.remove('open'));
        if (!wasOpen) { editor.classList.add('open'); renderSuggestions(); input.focus(); }
      });
      input.addEventListener('click', e => e.stopPropagation());
      input.addEventListener('input', renderSuggestions);
      input.addEventListener('keydown', e => {
        if (e.key === 'Enter') {
          e.preventDefault();
          const value = input.value.trim();
          if (value) saveFlightTags(flight, [...(flight.tags || []), value]);
        } else if (e.key === 'Escape') { editor.classList.remove('open'); input.value = ''; }
      });
    });
  }

  async function loadSavedFlights() {
    try {
      const response = await fsatlasFetch('/api/saved-flights');
      savedFlightsRaw = await response.json();
      renderSavedFlights();
    } catch (err) {
      savedList.innerHTML = '<div class="saved-empty">Could not load saved flights.</div>';
    }
  }
  savedSort.addEventListener('change', renderSavedFlights);

  // --- Saved Searches ---
  const savedSearchesList = document.getElementById('saved-searches-list');
  let savedSearchesRaw = [];

  function renderSavedSearches() {
    document.getElementById('saved-searches-count').textContent = savedSearchesRaw.length ? String(savedSearchesRaw.length) : '';
    if (!savedSearchesRaw.length) {
      savedSearchesList.innerHTML = '<div class="saved-empty">No saved searches yet. Use "Save Search" in the filter bar to save one.</div>';
      return;
    }
    savedSearchesList.innerHTML = savedSearchesRaw.map((s, i) => `
      <div class="saved-row" data-idx="${i}">
        <div class="saved-row-top">
          <div class="saved-route">${escapeHtml(s.description)}</div>
          <button class="saved-remove" type="button" title="Remove" aria-label="Remove">&times;</button>
        </div>
      </div>
    `).join('');
    [...savedSearchesList.querySelectorAll('.saved-row')].forEach((row, i) => {
      const search = savedSearchesRaw[i];
      row.addEventListener('click', () => applySavedSearch(search));
      row.querySelector('.saved-remove').addEventListener('click', async e => {
        e.stopPropagation();
        try {
          await fsatlasFetch('/api/saved-searches', {
            method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: search.id })
          });
        } catch (err) { /* best effort */ }
        loadSavedSearches();
      });
    });
  }

  async function loadSavedSearches() {
    try {
      const response = await fsatlasFetch('/api/saved-searches');
      savedSearchesRaw = await response.json();
      renderSavedSearches();
    } catch (err) {
      savedSearchesList.innerHTML = '<div class="saved-empty">Could not load saved searches.</div>';
    }
  }

  function buildFilterTreeNode(nodeData) {
    if (nodeData && nodeData.kind === 'group') {
      const group = createGroup();
      group.querySelector(':scope > .group-head > .logic').value = nodeData.logic || 'AND';
      const list = group.querySelector(':scope > .filters-list');
      (nodeData.children || []).forEach(child => list.append(buildFilterTreeNode(child)));
      return group;
    }
    const row = createConditionRow();
    row.querySelector('.column').value = nodeData.column || '';
    updateOperators(row);
    row.querySelector('.operator').value = nodeData.operator || '';
    row.querySelector('.value').value = nodeData.value ?? '';
    row.querySelector(':scope > .logic').value = nodeData.logic || 'AND';
    return row;
  }

  function applySavedSearch(search) {
    savedModal.classList.remove('open');
    filters.replaceChildren();
    const children = (search.filters && search.filters.children) || [];
    if (children.length) children.forEach(child => filters.append(buildFilterTreeNode(child)));
    else addRow();
    refreshRows();
    applyFilters();
  }

  // --- Save Search modal (own small modal rather than window.prompt(), which isn't
  // reliably supported in every browser/automation context) ---
  const saveSearchModal = document.getElementById('save-search-modal');
  const saveSearchDescInput = document.getElementById('save-search-description');

  function openSaveSearchModal() {
    saveSearchDescInput.value = '';
    saveSearchModal.classList.add('open');
    saveSearchDescInput.focus();
  }
  function closeSaveSearchModal() { saveSearchModal.classList.remove('open'); }

  async function confirmSaveSearch() {
    const trimmed = saveSearchDescInput.value.trim();
    if (!trimmed) { saveSearchDescInput.focus(); return; }
    const filterTree = { kind: 'group', logic: 'AND', children: serializeList(filters) };
    try {
      await fsatlasFetch('/api/saved-searches', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: trimmed, filters: filterTree })
      });
    } catch (err) { /* best effort */ }
    closeSaveSearchModal();
    if (savedModal.classList.contains('open')) loadSavedSearches();
  }

  document.getElementById('save-search-btn').addEventListener('click', openSaveSearchModal);
  document.getElementById('save-search-close').addEventListener('click', closeSaveSearchModal);
  document.getElementById('save-search-cancel').addEventListener('click', closeSaveSearchModal);
  document.getElementById('save-search-confirm').addEventListener('click', confirmSaveSearch);
  saveSearchModal.addEventListener('click', e => { if (e.target === saveSearchModal) closeSaveSearchModal(); });
  saveSearchDescInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); confirmSaveSearch(); } });

  [...document.querySelectorAll('.saved-section-toggle')].forEach(btn => {
    btn.addEventListener('click', () => {
      const wrap = btn.nextElementSibling;
      const collapsed = wrap.classList.toggle('collapsed');
      btn.classList.toggle('collapsed', collapsed);
      btn.setAttribute('aria-expanded', String(!collapsed));
    });
  });

  document.getElementById('saved-toggle').addEventListener('click', () => {
    savedModal.classList.add('open');
    loadSavedFlights();
    loadSavedSearches();
  });
  document.getElementById('saved-close').addEventListener('click', () => savedModal.classList.remove('open'));
  savedModal.addEventListener('click', e => { if (e.target === savedModal) savedModal.classList.remove('open'); });

  // --- Settings modal (SimBrief Pilot ID) ---
  const settingsModal = document.getElementById('settings-modal');
  const pilotIdInput = document.getElementById('simbrief-pilot-id');
  const simbriefStatus = document.getElementById('simbrief-status');

  function openSettings() {
    pilotIdInput.value = savedPilotId;
    simbriefStatus.textContent = '';
    sceneryStatusEl.textContent = '';
    settingsModal.classList.add('open');
  }
  function closeSettings() { settingsModal.classList.remove('open'); }

  document.getElementById('settings-toggle').addEventListener('click', openSettings);
  document.getElementById('settings-close').addEventListener('click', closeSettings);
  document.getElementById('settings-cancel').addEventListener('click', closeSettings);
  settingsModal.addEventListener('click', e => { if (e.target === settingsModal) closeSettings(); });

  document.getElementById('simbrief-verify').addEventListener('click', async () => {
    const pilotId = pilotIdInput.value.trim();
    simbriefStatus.style.color = 'var(--muted)';
    simbriefStatus.textContent = 'Checking...';
    try {
      const response = await fsatlasFetch('/api/simbrief/verify', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pilot_id: pilotId })
      });
      const result = await response.json();
      simbriefStatus.textContent = result.message;
      simbriefStatus.style.color = result.ok ? 'var(--accent)' : 'var(--danger)';
    } catch (err) {
      simbriefStatus.textContent = 'Could not reach SimBrief';
      simbriefStatus.style.color = 'var(--danger)';
    }
  });

  document.getElementById('settings-save').addEventListener('click', async () => {
    savedPilotId = pilotIdInput.value.trim();
    try {
      await fsatlasFetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ simbrief_pilot_id: savedPilotId })
      });
    } catch (err) { /* best effort - the setting still applies for this session */ }
    await loadSceneryData();
    showImportedScenery();
    closeSettings();
  });

  // --- Scenery import: scan package directories client-side and read only manifests. ---
  const sceneryImportBtn = document.getElementById('scenery-import-btn');
  const sceneryFolderInput = document.getElementById('scenery-folder-input');
  const sceneryStatusEl = document.getElementById('scenery-status');
  const sceneryProgressEl = document.getElementById('scenery-progress');
  const sceneryProgressText = document.getElementById('scenery-progress-text');
  const sceneryErrorsEl = document.getElementById('scenery-errors');
  const sceneryErrorsSummary = document.getElementById('scenery-errors-summary');
  const sceneryErrorsCode = document.getElementById('scenery-errors-code');
  const sceneryErrorResolutions = document.getElementById('scenery-error-resolutions');
  const sceneryWarningModal = document.getElementById('scenery-warning-modal');
  let sceneryAirportOptions = null;
  let sceneryErrorsRenderId = 0;

  function updateSceneryErrors(errors) {
    sceneryImportErrors = Array.isArray(errors) ? errors : [];
    sceneryErrorsEl.classList.toggle('visible', sceneryImportErrors.length > 0);
    sceneryErrorsEl.removeAttribute('open');
    sceneryErrorsSummary.textContent = `Import details (${sceneryImportErrors.length})`;
    sceneryErrorsCode.textContent = JSON.stringify(sceneryImportErrors, null, 2);
    renderSceneryErrorResolutions(sceneryImportErrors);
  }

  async function renderSceneryErrorResolutions(errors) {
    const renderId = ++sceneryErrorsRenderId;
    const unresolved = errors.filter(error => error.stage === 'airport_match' && error.source);
    sceneryErrorResolutions.replaceChildren();
    if (!unresolved.length) return;

    if (!sceneryAirportOptions) {
      try {
        const response = await fsatlasFetch('/api/scenery/airports');
        if (!response.ok) throw new Error('Could not load known airports');
        sceneryAirportOptions = await response.json();
      } catch (err) {
        if (renderId === sceneryErrorsRenderId) {
          const message = document.createElement('p');
          message.className = 'modal-hint';
          message.textContent = 'Airport choices could not be loaded. Reload Settings to try again.';
          sceneryErrorResolutions.append(message);
        }
        return;
      }
    }
    if (renderId !== sceneryErrorsRenderId) return;

    unresolved.forEach(error => {
      const row = document.createElement('div');
      row.className = 'scenery-error-resolution';
      const packageName = document.createElement('span');
      packageName.className = 'scenery-error-package';
      packageName.textContent = error.source;

      const search = document.createElement('input');
      search.type = 'search';
      search.maxLength = 4;
      search.autocomplete = 'off';
      search.spellcheck = false;
      search.placeholder = 'Type ICAO prefix (e.g. EG)';
      search.setAttribute('aria-label', `Filter airports by ICAO prefix for ${error.source}`);

      const select = document.createElement('select');
      select.setAttribute('aria-label', `Airport for scenery package ${error.source}`);
      select.disabled = true;

      const updateAirportOptions = () => {
        const prefix = search.value.trim().toUpperCase();
        select.replaceChildren();
        select.add(new Option(
          prefix ? `Airports starting with ${prefix}...` : 'Type an ICAO prefix first...', ''
        ));
        select.options[0].disabled = true;
        select.options[0].selected = true;
        const matches = prefix
          ? sceneryAirportOptions.filter(airport => String(airport.icao || '').toUpperCase().startsWith(prefix))
          : [];
        matches.forEach(airport => {
          const code = airport.icao || airport.iata;
          const codes = [airport.icao, airport.iata].filter(Boolean).join(' / ');
          select.add(new Option(`${codes} - ${airport.name} (${airport.city})`, code));
        });
        if (prefix && !matches.length) {
          select.options[0].textContent = `No ICAO codes start with ${prefix}`;
        }
        select.disabled = matches.length === 0;
        button.disabled = true;
      };

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary';
      button.textContent = 'Assign';
      button.disabled = true;
      select.addEventListener('change', () => { button.disabled = !select.value; });
      button.addEventListener('click', () => resolveSceneryAirport(error.source, select.value, button));
      search.addEventListener('input', updateAirportOptions);

      row.append(packageName, search, select, button);
      sceneryErrorResolutions.append(row);
    });
  }

  async function resolveSceneryAirport(source, airportId, button) {
    button.disabled = true;
    button.textContent = 'Saving...';
    try {
      const response = await fsatlasFetch('/api/scenery/resolve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, airport_id: airportId }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Airport assignment failed');
      sceneryData = result.sceneries || [];
      updateSceneryErrors(result.errors || []);
      showImportedScenery();
      sceneryStatusEl.style.color = 'var(--accent)';
      sceneryStatusEl.textContent = `Assigned ${source} to ${airportId}.`;
    } catch (err) {
      button.disabled = false;
      button.textContent = 'Retry';
      sceneryStatusEl.style.color = 'var(--danger)';
      sceneryStatusEl.textContent = err.message || 'Airport assignment failed.';
    }
  }

  function setSceneryProgress(active, message = '') {
    sceneryProgressEl.classList.toggle('active', active);
    sceneryProgressEl.setAttribute('aria-hidden', String(!active));
    sceneryProgressText.textContent = message;
    sceneryImportBtn.disabled = active;
  }

  sceneryImportBtn.addEventListener('click', () => {
    if (sceneryData.length > 0) sceneryWarningModal.classList.add('open');
    else chooseSceneryFolder();
  });
  function closeSceneryWarning() { sceneryWarningModal.classList.remove('open'); }
  document.getElementById('scenery-warning-cancel').addEventListener('click', closeSceneryWarning);
  document.getElementById('scenery-warning-close').addEventListener('click', closeSceneryWarning);
  sceneryWarningModal.addEventListener('click', e => { if (e.target === sceneryWarningModal) closeSceneryWarning(); });
  document.getElementById('scenery-warning-continue').addEventListener('click', () => {
    closeSceneryWarning();
    chooseSceneryFolder();
  });

  function extractIcao(text) {
    const match = String(text || '').toUpperCase().match(/\b([A-Z]{4})\b/);
    return match ? match[1] : null;
  }

  // Recognizes MSFS/P3D/FSX package manifests (title usually embeds the ICAO) and
  // X-Plane's "Earth nav data/<grid>/" folder (a rough lat/lon good enough for the
  // server's within-a-mile cross-reference against the real airport). Deliberately does
  // NOT attempt to parse compiled .bgl scenery binaries - there's no reliably documented
  // public format for that, so a folder-name/manifest ICAO guess is used there instead.
  async function scanSceneryDirectory(directoryHandle, scanErrors) {
    const candidates = [];
    for await (const [folderName, packageHandle] of directoryHandle.entries()) {
      if (packageHandle.kind !== 'directory') continue;
      let icao = null, lat = null, lon = null;

      try {
        const manifestHandle = await packageHandle.getFileHandle('manifest.json');
        const manifestFile = await manifestHandle.getFile();
        const manifest = JSON.parse(await manifestFile.text());
        icao = extractIcao(manifest.title);
      } catch (err) {
        if (err.name !== 'NotFoundError') {
          scanErrors.push({
            severity: 'warning', stage: 'manifest_read', source: folderName,
            message: 'Could not read or parse manifest.json.', details: `${err.name}: ${err.message}`,
          });
        }
      }

      try {
        const earthNavHandle = await packageHandle.getDirectoryHandle('Earth nav data');
        for await (const [gridName, gridHandle] of earthNavHandle.entries()) {
          if (gridHandle.kind !== 'directory') continue;
          const gridMatch = gridName.match(/^([+-]\d+)([+-]\d+)$/);
          if (!gridMatch) continue;
          const gLat = parseInt(gridMatch[1], 10), gLon = parseInt(gridMatch[2], 10);
          lat = gLat + (gLat >= 0 ? 0.5 : -0.5);
          lon = gLon + (gLon >= 0 ? 0.5 : -0.5);
          break;
        }
      } catch (err) { /* no X-Plane Earth nav data directory */ }

      if (!icao) icao = extractIcao(folderName);
      candidates.push({ icao, lat, lon, source: folderName });
    }
    return candidates;
  }

  async function scanSceneryFiles(fileList, scanErrors) {
    const packages = new Map();
    let scannedFiles = 0;
    for (const file of fileList) {
      scannedFiles++;
      if (scannedFiles % 1000 === 0) {
        setSceneryProgress(true, `Scanning filenames (${scannedFiles.toLocaleString()} of ${fileList.length.toLocaleString()})...`);
        await new Promise(resolve => setTimeout(resolve, 0));
      }
      const pathParts = (file.webkitRelativePath || file.name).split('/');
      if (pathParts.length < 3) continue;
      const folderName = pathParts[1];
      let candidate = packages.get(folderName);
      if (!candidate) {
        candidate = { icao: extractIcao(folderName), lat: null, lon: null, source: folderName };
        packages.set(folderName, candidate);
      }

      if (pathParts[pathParts.length - 1].toLowerCase() === 'manifest.json') {
        try {
          const manifest = JSON.parse(await file.text());
          candidate.icao = extractIcao(manifest.title) || candidate.icao;
        } catch (err) {
          scanErrors.push({
            severity: 'warning', stage: 'manifest_read', source: folderName,
            message: 'Could not parse manifest.json.', details: `${err.name}: ${err.message}`,
          });
        }
      }

      const earthNavIndex = pathParts.findIndex((part, index) => index > 1 && /^Earth nav data$/i.test(part));
      if (earthNavIndex >= 0 && pathParts[earthNavIndex + 1]) {
        const gridMatch = pathParts[earthNavIndex + 1].match(/^([+-]\d+)([+-]\d+)$/);
        if (gridMatch && candidate.lat === null) {
          const gLat = parseInt(gridMatch[1], 10), gLon = parseInt(gridMatch[2], 10);
          candidate.lat = gLat + (gLat >= 0 ? 0.5 : -0.5);
          candidate.lon = gLon + (gLon >= 0 ? 0.5 : -0.5);
        }
      }
    }
    return [...packages.values()];
  }

  async function importSceneryCandidates(candidates, scanErrors) {
    setSceneryProgress(true, 'Matching scenery against known airports...');
    sceneryStatusEl.textContent = 'Matching against known airports...';
    const response = await fsatlasFetch('/api/scenery/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ candidates, scan_errors: scanErrors })
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Import failed');
    sceneryData = result.sceneries || [];
    updateSceneryErrors(result.errors || []);
    showImportedScenery();
    sceneryStatusEl.style.color = 'var(--accent)';
    sceneryStatusEl.textContent = `Imported ${result.matched} scenery location${result.matched === 1 ? '' : 's'}`
      + (result.unmatched ? ` (${result.unmatched} not recognized)` : '') + '.';
  }

  async function chooseSceneryFolder() {
    setSceneryProgress(true, 'Choose the scenery folder...');
    if (typeof window.showDirectoryPicker !== 'function') {
      sceneryFolderInput.click();
      return;
    }

    let directoryHandle;
    try {
      directoryHandle = await window.showDirectoryPicker({ mode: 'read' });
    } catch (err) {
      if (err.name === 'AbortError') { setSceneryProgress(false); return; }
      sceneryFolderInput.click();
      return;
    }

    sceneryStatusEl.style.color = 'var(--muted)';
    sceneryStatusEl.textContent = 'Scanning scenery packages...';
    setSceneryProgress(true, 'Scanning scenery packages...');
    const scanErrors = [];
    try {
      const candidates = await scanSceneryDirectory(directoryHandle, scanErrors);
      await importSceneryCandidates(candidates, scanErrors);
    } catch (err) {
      sceneryStatusEl.style.color = 'var(--danger)';
      sceneryStatusEl.textContent = 'Import failed.';
      updateSceneryErrors(scanErrors.concat({
        severity: 'error', stage: 'import', source: directoryHandle.name,
        message: 'The scenery folder could not be scanned or imported.', details: `${err.name}: ${err.message}`,
      }));
    } finally {
      setSceneryProgress(false);
    }
  }

  sceneryFolderInput.addEventListener('cancel', () => setSceneryProgress(false));
  sceneryFolderInput.addEventListener('change', async () => {
    if (!sceneryFolderInput.files || !sceneryFolderInput.files.length) {
      setSceneryProgress(false);
      return;
    }
    sceneryStatusEl.style.color = 'var(--muted)';
    sceneryStatusEl.textContent = 'Scanning scenery package names and manifests...';
    setSceneryProgress(true, 'Scanning package names and manifests...');
    const scanErrors = [];
    try {
      const candidates = await scanSceneryFiles(sceneryFolderInput.files, scanErrors);
      await importSceneryCandidates(candidates, scanErrors);
    } catch (err) {
      sceneryStatusEl.style.color = 'var(--danger)';
      sceneryStatusEl.textContent = 'Import failed.';
      updateSceneryErrors(scanErrors.concat({
        severity: 'error', stage: 'import', source: 'Selected folder',
        message: 'The scenery folder could not be scanned or imported.', details: `${err.name}: ${err.message}`,
      }));
    } finally {
      setSceneryProgress(false);
      sceneryFolderInput.value = '';
    }
  });

  // --- Map type popover (FAB button + small menu, drives the hidden native <select>) ---
  const mapTypeFabWrap = document.getElementById('map-type-fab-wrap');
  const mapTypeFab = document.getElementById('map-type-fab');
  const mapTypeMenu = document.getElementById('map-type-menu');

  function closeMapTypeMenu() { mapTypeMenu.classList.remove('open'); }
  function buildMapTypeMenu() {
    mapTypeMenu.replaceChildren();
    [['Use Theme', USE_THEME_VALUE], ...Object.keys(mapTypes).map(name => [name, name])].forEach(([label, value]) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'map-type-menu-item' + (value === mapTypeSelect.value ? ' active' : '');
      item.textContent = label;
      item.addEventListener('click', () => {
        mapTypeSelect.value = value;
        setTileLayer(effectiveMapType());
        closeMapTypeMenu();
      });
      mapTypeMenu.append(item);
    });

    // Keep overlays together beneath the map-style choices.
    mapTypeMenu.append(Object.assign(document.createElement('hr'), { className: 'popover-divider' }));
    const airportsToggle = document.createElement('button');
    airportsToggle.type = 'button';
    airportsToggle.className = 'map-type-menu-toggle' + (airportsOverlayEnabled ? ' on' : '');
    airportsToggle.setAttribute('aria-pressed', String(airportsOverlayEnabled));
    airportsToggle.innerHTML = '<span>Airports Overlay</span><span class="switch"></span>';
    airportsToggle.addEventListener('click', () => {
      setAirportsOverlayEnabled(!airportsOverlayEnabled);
      airportsToggle.classList.toggle('on', airportsOverlayEnabled);
      airportsToggle.setAttribute('aria-pressed', String(airportsOverlayEnabled));
      fsatlasFetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ airports_overlay: airportsOverlayEnabled })
      }).catch(() => {});
    });
    mapTypeMenu.append(airportsToggle);

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'map-type-menu-toggle' + (sceneryOverlayEnabled ? ' on' : '');
    toggle.setAttribute('aria-pressed', String(sceneryOverlayEnabled));
    toggle.innerHTML = '<span>Scenery Overlay</span><span class="switch"></span>';
    toggle.addEventListener('click', () => {
      setSceneryOverlayEnabled(!sceneryOverlayEnabled);
      toggle.classList.toggle('on', sceneryOverlayEnabled);
      toggle.setAttribute('aria-pressed', String(sceneryOverlayEnabled));
      fsatlasFetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ scenery_overlay: sceneryOverlayEnabled })
      }).catch(() => {});
    });
    mapTypeMenu.append(toggle);
  }
  mapTypeFab.addEventListener('click', e => {
    e.stopPropagation();
    const opening = !mapTypeMenu.classList.contains('open');
    if (opening) buildMapTypeMenu();
    mapTypeMenu.classList.toggle('open', opening);
  });
  document.addEventListener('click', e => { if (!mapTypeFabWrap.contains(e.target)) closeMapTypeMenu(); });

  // --- Sidebar open/close: one slide-over drawer shared by desktop and mobile (desktop
  // just defaults to open) - no more separate "move #filters to <body> for a fullscreen
  // mobile editor" trick, since the sidebar IS the same DOM at every breakpoint now. ---
  const sidebar = document.getElementById('sidebar');
  const sidebarScrim = document.getElementById('sidebar-scrim');
  const filtersCountBadge = document.getElementById('filters-count-badge');

  function openSidebar() { sidebar.classList.add('open'); document.body.classList.add('sidebar-open'); }
  function closeSidebar() { sidebar.classList.remove('open'); document.body.classList.remove('sidebar-open'); }
  function toggleSidebar() { sidebar.classList.contains('open') ? closeSidebar() : openSidebar(); }

  document.getElementById('sidebar-toggle').addEventListener('click', toggleSidebar);
  document.getElementById('sidebar-close').addEventListener('click', closeSidebar);
  sidebarScrim.addEventListener('click', closeSidebar);

  function countConfiguredFilters() { return [...filters.querySelectorAll('.column')].filter(col => col.value).length; }
  function updateFilterCountBadge() {
    const configured = countConfiguredFilters();
    filtersCountBadge.textContent = configured ? configured + ' active' : 'none active';
    filtersCountBadge.classList.toggle('zero', configured === 0);
  }
  filters.addEventListener('change', e => { if (e.target.matches('.column')) updateFilterCountBadge(); });

  function operatorsFor(column) {
    if (Array.isArray(column.options)) return [['equals', 'Is']];
    return column.numeric
      ? [['equals', 'Equals (=)'], ['>', 'Greater (>)'], ['<', 'Less (<)'], ['>=', 'Greater/Eq (>=)'], ['<=', 'Less/Eq (<=)']]
      : [['contains', 'Contains'], ['equals', 'Equals'], ['starts_with', 'Starts With'], ['ends_with', 'Ends With']];
  }

  function updateOperators(row) {
    const column = columns.find(item => item.id === row.querySelector('.column').value);
    const operator = row.querySelector('.operator');
    operator.replaceChildren();
    if (!column) return;
    operatorsFor(column).forEach(([value, label]) => operator.add(new Option(label, value)));

    const oldValue = row.querySelector('.value');
    const wantsSelect = Array.isArray(column.options);
    operator.disabled = wantsSelect;
    let value = oldValue;
    if (wantsSelect !== (oldValue.tagName === 'SELECT')) {
      value = document.createElement(wantsSelect ? 'select' : 'input');
      value.className = 'value';
      oldValue.replaceWith(value);
    }

    if (wantsSelect) {
      value.replaceChildren(new Option('Select value...', ''));
      column.options.forEach(opt => value.add(new Option(opt, opt)));
    } else {
      value.type = column.numeric ? 'number' : 'text';
      value.step = column.numeric ? 'any' : '';
      value.placeholder = column.example ? 'e.g. ' + column.example : 'Value';
    }
  }

  // Sidebar default state follows the breakpoint: open on desktop, closed (drawer) on
  // mobile - re-applied live if the window is resized across it.
  let resizeRaf;
  function scheduleResizeCheck() {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(() => {
      const nowMobile = isMobileViewport();
      if (nowMobile !== isMobile) {
        isMobile = nowMobile;
        document.body.classList.toggle('is-mobile', isMobile);
        isMobile ? closeSidebar() : openSidebar();
        rebuildAirportMarkers(); // marker radius depends on isMobile
      }
      checkLegendOverlap();
    });
  }
  window.addEventListener('resize', scheduleResizeCheck);
  // The info panel is user-resizable (CSS `resize: both`) - dragging it larger can newly
  // overlap the legend without any of the other triggers above firing.
  new ResizeObserver(checkLegendOverlap).observe(infoPanel);

  // --- Filter tree (nested AND/OR groups) ---
  function markFirst(list) { [...list.children].forEach((node, index) => node.classList.toggle('first', index === 0)); }
  function refreshRows() {
    markFirst(filters);
    filters.querySelectorAll('.filters-list').forEach(markFirst);
    updateFilterCountBadge();
  }

  function populateColumnSelect(columnSelect) {
    const optgroups = {};
    columns.forEach(column => {
      let parent = columnSelect;
      if (column.group) {
        if (!optgroups[column.group]) {
          optgroups[column.group] = document.createElement('optgroup');
          optgroups[column.group].label = column.group;
          columnSelect.append(optgroups[column.group]);
        }
        parent = optgroups[column.group];
      }
      parent.append(new Option(column.name, column.id));
    });
  }

  function removeNode(node) {
    const parentList = node.parentElement;
    const isRoot = parentList === filters;
    node.remove();
    if (!isRoot && parentList.classList.contains('filters-list') && !parentList.children.length) {
      removeNode(parentList.closest('.filter-group'));
      return;
    }
    refreshRows();
  }

  // Small stroke-style SVG icons for the filter-row action cluster, matching the app's
  // existing icon language (the old "+"/"⧉"/"⧈" unicode glyphs weren't legible at a glance).
  const ICON_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>';
  const ICON_GROUP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2"></path><path d="M16 4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2"></path></svg>';
  const ICON_UNGROUP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 4H6a2 2 0 0 0-2 2v3"></path><path d="M4 15v3a2 2 0 0 0 2 2h3"></path><path d="M15 4h3a2 2 0 0 1 2 2v3"></path><path d="M20 15v3a2 2 0 0 1-2 2h-3"></path></svg>';
  const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';

  function createConditionRow() {
    const row = document.createElement('div');
    row.className = 'filter-row';
    row.innerHTML = '<select class="logic"><option>AND</option><option>OR</option></select>' +
      '<select class="column"><option value="">Select Filter...</option></select>' +
      '<select class="operator"></select>' +
      '<input class="value" placeholder="Value">' +
      '<div class="row-actions">' +
      '<button class="icon insert" title="Add a filter below" aria-label="Add a filter below">' + ICON_PLUS + '<span class="action-label">Add</span></button>' +
      '<button class="icon group" title="Wrap in a group" aria-label="Wrap in a group">' + ICON_GROUP + '<span class="action-label">Group</span></button>' +
      '<button class="icon remove" title="Remove this filter" aria-label="Remove this filter">' + ICON_X + '<span class="action-label">Remove Filter</span></button>' +
      '</div>';
    const columnSelect = row.querySelector('.column');
    populateColumnSelect(columnSelect);
    columnSelect.addEventListener('change', () => updateOperators(row));
    row.querySelector('.insert').addEventListener('click', () => { row.after(createConditionRow()); refreshRows(); });
    row.querySelector('.group').addEventListener('click', () => wrapInGroup(row));
    row.querySelector('.remove').addEventListener('click', () => removeNode(row));
    return row;
  }

  function createGroup() {
    const group = document.createElement('div');
    group.className = 'filter-group';
    group.innerHTML = '<div class="group-head">' +
      '<select class="logic"><option>AND</option><option>OR</option></select>' +
      '<span class="group-label">Group</span>' +
      '<div class="row-actions">' +
      '<button class="icon ungroup" title="Ungroup" aria-label="Ungroup">' + ICON_UNGROUP + '<span class="action-label">Ungroup</span></button>' +
      '<button class="icon remove" title="Remove this group" aria-label="Remove this group">' + ICON_X + '<span class="action-label">Remove Group</span></button>' +
      '</div></div><div class="filters-list"></div>' +
      // Deliberately styled/placed OUTSIDE the group's tinted box (see .group-append in
      // app.css) and labelled accordingly - this button does NOT add to this group's own
      // conditions, it adds a new sibling filter after the group ends, so it must not look
      // like it belongs to the group's contents the way the Ungroup/Remove buttons above do.
      '<div class="group-append">' +
      '<button class="icon insert" title="Add a new filter after this group (not inside it)" aria-label="Add a new filter after this group (not inside it)">' + ICON_PLUS + '<span class="action-label">Add After Group</span></button>' +
      '</div>';
    group.querySelector('.insert').addEventListener('click', () => { group.after(createConditionRow()); refreshRows(); });
    group.querySelector('.ungroup').addEventListener('click', () => ungroup(group));
    group.querySelector('.remove').addEventListener('click', () => removeNode(group));
    return group;
  }

  function wrapInGroup(node) {
    const group = createGroup();
    const logic = node.querySelector(':scope > .logic').value;
    group.querySelector('.logic').value = logic;
    node.before(group);
    group.querySelector('.filters-list').append(node);
    refreshRows();
  }

  function ungroup(group) {
    const children = [...group.querySelector('.filters-list').children];
    if (children.length) {
      children[0].querySelector(':scope > .logic, .group-head > .logic').value = group.querySelector('.logic').value;
    }
    children.forEach(child => group.before(child));
    group.remove();
    refreshRows();
  }

  function addRow(afterRow) {
    const row = createConditionRow();
    if (afterRow) afterRow.after(row); else filters.append(row);
    refreshRows();
    return row;
  }

  function serializeList(listEl) { return [...listEl.children].map(serializeNode).filter(Boolean); }
  function serializeNode(node) {
    if (node.classList.contains('filter-group')) {
      const logic = node.querySelector(':scope > .group-head > .logic').value;
      const children = serializeList(node.querySelector(':scope > .filters-list'));
      return children.length ? { kind: 'group', logic, children } : null;
    }
    const column = columns.find(item => item.id === node.querySelector('.column').value);
    const value = node.querySelector('.value').value.trim();
    if (!column || !value) return null;
    return {
      column: column.id,
      operator: node.querySelector('.operator').value,
      value: column.numeric ? Number(value) : value,
      logic: node.querySelector(':scope > .logic').value,
      type: column.numeric ? 'number' : 'text'
    };
  }

  function currentFilterTree() { return { kind: 'group', logic: 'AND', children: serializeList(filters) }; }

  // --- Airport marker rebuild: called on filter apply and on theme change (colors read
  // from CSS vars at creation time) - clears and recreates every marker for the world
  // copies already rendered, keeping pan/zoom state untouched. ---
  function rebuildAirportMarkers() {
    Object.values(airportMarkers).forEach(byOffset => Object.values(byOffset).forEach(m => airportLayer.removeLayer(m)));
    airportMarkers = {};
    const offsets = [...renderedOffsets];
    renderedOffsets.clear();
    offsets.forEach(offset => createAirportsForOffset(offset));
  }

  async function applyFilters() {
    flightsCountEl.textContent = 'Loading...';
    try {
      const response = await fsatlasFetch('/api/airports?filters=' + encodeURIComponent(JSON.stringify(currentFilterTree())));
      const result = await response.json();

      deselect(); // fresh dataset - drop any stale route/marker selection state
      airports = {};
      (result.airports || []).forEach(ap => { airports[ap.iata] = ap; });
      airportsData = result.airports || [];
      rebuildAirportMarkers();

      flightsCountEl.textContent = result.count.toLocaleString() + ' flights match';
    } catch (err) {
      flightsCountEl.textContent = '';
    }
    if (isMobile) closeSidebar();
  }

  function resetFilters() {
    filters.replaceChildren();
    addRow();
    applyFilters();
  }

  document.getElementById('apply').addEventListener('click', applyFilters);
  document.getElementById('reset').addEventListener('click', resetFilters);

  // ===================================================================================
  // INIT
  // ===================================================================================
  async function init() {
    const response = await fsatlasFetch('/api/meta');
    const meta = await response.json();
    columns = meta.columns;
    mapTypes = meta.map_types;
    savedPilotId = meta.simbrief_pilot_id || '';
    airportsOverlayEnabled = meta.airports_overlay !== false;
    sceneryOverlayEnabled = !!meta.scenery_overlay;
    setAirportsOverlayEnabled(airportsOverlayEnabled);

    mapTypeSelect.add(new Option('Use Theme', USE_THEME_VALUE, true, true));
    Object.keys(mapTypes).forEach(name => mapTypeSelect.add(new Option(name, name, false, false)));

    applyTheme(meta.theme === 'light' ? 'light' : 'dark', false);
    setTileLayer(effectiveMapType());

    isMobile ? closeSidebar() : openSidebar();

    // The map fills the whole viewport behind the fixed top bar and (on desktop) the
    // open sidebar, so pan once at load to keep the initial view centered in what's
    // actually visible, rather than in the full occluded viewport.
    const topbarH = document.querySelector('.topbar').getBoundingClientRect().height;
    const sidebarW = sidebar.classList.contains('open') ? sidebar.getBoundingClientRect().width : 0;
    if (topbarH || sidebarW) map.panBy([-sidebarW / 2, -topbarH / 2], { animate: false });

    ensureWorldCoverage();
    addRow();
    await applyFilters();

    fsatlasFetch('/api/saved-flights')
      .then(response => response.ok ? response.json() : [])
      .then(flights => (flights || []).forEach(f => savedFlightKeys.add(flightKey(f))))
      .catch(() => {});

    loadSceneryData();
  }

  init();
})();
