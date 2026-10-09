// FSAtlas frontend - vanilla JS, no framework/build step. Fetches JSON from the Flask API
// and renders everything client-side: Leaflet map (canvas circleMarkers for the ~3-4k
// airports, not one DOM node per marker), filter toolbar, saved items, SimBrief
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
  // Keep the vertical pan bounded to where real map tiles exist (~85.06deg is the usual
  // Web Mercator limit) so the user can never drag/scroll up into the empty gray void
  // above the north pole or below the south pole. Longitude is deliberately left
  // effectively unbounded (an absurdly large but finite range, since Leaflet's bounds
  // math needs finite numbers) - horizontal world-copy wrapping is intentional (routes/
  // airports render across repeated world copies as the user pans east/west forever).
  const VERTICAL_PAN_LIMIT = 85.06;
  map.setMaxBounds(L.latLngBounds([-VERTICAL_PAN_LIMIT, -1e9], [VERTICAL_PAN_LIMIT, 1e9]));

  map.createPane('routesPane');
  map.getPane('routesPane').style.zIndex = 390;
  map.createPane('airportsPane');
  map.getPane('airportsPane').style.zIndex = 400;
  // Above airportsPane, not below: airport dots are canvas-rendered (preferCanvas:true),
  // which means ONE <canvas> element covers the whole map surface regardless of where
  // dots are actually drawn - sitting below it, a star's own DOM element would never
  // receive real clicks at all (the canvas intercepts every pointer event in its
  // bounding box first, even over "empty" pixels), even though Leaflet's own internal
  // `.fire('click', ...)` calls bypass that and appear to work fine in isolation.
  map.createPane('sceneryPane');
  map.getPane('sceneryPane').style.zIndex = 405;

  let tileLayer = null;
  function setTileLayer(name) {
    const info = mapTypes[name] || mapTypes[Object.keys(mapTypes)[0]];
    if (!info) return;
    if (tileLayer) map.removeLayer(tileLayer);
    tileLayer = L.tileLayer(info.url, { attribution: info.attr, maxZoom: 19 }).addTo(map);
  }

  // Shared star shape (used for the scenery map markers),
  // parameterized on fill color so it can match whichever rank color it stands in for.
  function starSvgMarkup(color) {
    return '<svg viewBox="0 0 24 24" fill="' + color + '" stroke="var(--map-outline)" stroke-width="1" stroke-linejoin="round"><polygon points="12 2 14.9 8.6 22 9.3 16.5 14 18.2 21 12 17.3 5.8 21 7.5 14 2 9.3 9.1 8.6"></polygon></svg>';
  }
  // The star polygon above only fills ~148.3 sq units of its 24x24 (576 sq unit) viewBox
  // (shoelace area of its points) - a star fills far less of its own bounding box than a
  // circle does, so sizing its bounding box to the SAME diameter as a dot makes it read as
  // noticeably smaller. Scale the box up so the star's actual rendered AREA matches the
  // circle's area of the same rank instead, so they look the same size at a glance.
  // The 0.9 factor is a manual 10% trim on top of that - area-matching alone still read
  // as slightly too big in practice (area equivalence isn't quite the same as perceived
  // size equivalence for a spiky vs. round shape).
  const STAR_POLYGON_AREA = 148.28;
  const STAR_SIZE_SCALE = 24 * Math.sqrt(Math.PI / STAR_POLYGON_AREA) * 0.9;

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
    // Stars are only selectable while the airport overlay is on - rebuild so their
    // clickability (and cursor) picks up the change immediately.
    if (sceneryOverlayEnabled) rebuildSceneryMarkers();
  }

  // --- Scenery overlay: star markers for the user's imported flight-sim scenery
  // locations (see /api/scenery), drawn in a pane above the airport dots (see the pane
  // z-index note above for why). Two distinct modes depending on the airport overlay:
  // - Airport overlay ON: a star only exists for an airport that also has a dot (i.e. has
  //   flights matching the current filters) - same click behavior/size/rank-color as the
  //   dot it stands in for. An airport whose scenery is installed but has no matching
  //   flights right now shows no star at all (not a stray rank-0/red one).
  // - Airport overlay OFF: every installed-scenery airport shows regardless of filters,
  //   in a uniform accent-colored/sized star (no per-rank dots exist to match), and
  //   clicking just opens an identifying popup bubble instead of the dot's normal
  //   route-selection behavior (there's no dot/route layer to select into anyway). ---
  let sceneryOverlayEnabled = false;
  let sceneryData = []; // [{iata, icao, name, city, lat, lon}, ...]
  let sceneryIatas = new Set(); // iata codes with imported scenery - drives dot-hiding below
  let sceneryImportErrors = [];
  const sceneryLayer = L.layerGroup();
  const sceneryOffsetsBuilt = new Set();
  let sceneryMarkers = {}; // iata -> {offset -> marker} - mirrors airportMarkers, drives connectivity show/hide
  let sceneryUniformIconCached = null; // single accent-colored icon, reused for every scenery star

  function refreshSceneryIatas() {
    sceneryIatas = new Set(sceneryData.map(s => s.iata).filter(Boolean));
  }

  // True while the scenery overlay should take over an airport's dot entirely (only when
  // the overlay is switched on - otherwise scenery-covered airports render normally).
  function sceneryHidesAirportDot(iata) {
    return sceneryOverlayEnabled && sceneryIatas.has(iata);
  }

  // Single accent-colored star, one fixed size - airport dots no longer vary by rank, so
  // scenery stars don't either (every scenery airport looks the same regardless of the
  // airport overlay's on/off state).
  function sceneryUniformIcon() {
    const size = markerRadius() * STAR_SIZE_SCALE;
    if (!sceneryUniformIconCached || sceneryUniformIconCached.__size !== size) {
      sceneryUniformIconCached = L.divIcon({
        className: 'scenery-star-icon', html: starSvgMarkup(getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()),
        iconSize: [size, size], iconAnchor: [size / 2, size / 2]
      });
      sceneryUniformIconCached.__size = size;
    }
    return sceneryUniformIconCached;
  }

  function createSceneryForOffset(offset) {
    if (sceneryOffsetsBuilt.has(offset)) return;
    sceneryOffsetsBuilt.add(offset);
    sceneryData.forEach(s => {
      if (airportsOverlayEnabled) {
        // Matches the airport dots' own filtering: an airport with no flights in the
        // current filtered dataset has no dot to show either, so hide its star too.
        const ap = airports[s.iata];
        if (!ap) return;
        const marker = L.marker([s.lat, s.lon + offset], {
          icon: sceneryUniformIcon(), interactive: true, keyboard: true, pane: 'sceneryPane'
        })
          .bindTooltip((s.icao || s.iata) + ' - ' + s.name, { direction: 'top', offset: [0, -5], className: 'atlas-tooltip' })
          .on('click', (e) => { L.DomEvent.stopPropagation(e); handleAirportClick(s.iata, offset); });
        if (!sceneryMarkers[s.iata]) sceneryMarkers[s.iata] = {};
        sceneryMarkers[s.iata][offset] = marker;
        // Same connectivity filter as the dots (see loadRoutes/deselect) - a star built
        // lazily for a newly-panned-to world copy while a source is already selected
        // must respect that selection immediately, not just future show/hide calls. Each
        // airport has its OWN correct offset (see visibleFilterOffsets/routeOffsetFor) -
        // not necessarily the source's offset, since its route line may wrap the antimeridian.
        if (!visibleFilter || (visibleFilter.has(s.iata) && visibleFilterOffsets[s.iata] === offset)) marker.addTo(sceneryLayer);
      } else {
        // Airport overlay off: every installed-scenery airport shows regardless of
        // filters/flight matches (there's no dot layer to stay consistent with), and
        // hovering just identifies the airport via a small bubble - no dot/route layer
        // exists right now to select into.
        const label = (s.icao || s.iata) + ' - ' + s.name;
        const marker = L.marker([s.lat, s.lon + offset], {
          icon: sceneryUniformIcon(), interactive: true, keyboard: true, pane: 'sceneryPane'
        }).bindTooltip(label, { direction: 'top', offset: [0, -2], className: 'atlas-star-tooltip' });
        if (!sceneryMarkers[s.iata]) sceneryMarkers[s.iata] = {};
        sceneryMarkers[s.iata][offset] = marker;
        marker.addTo(sceneryLayer);
      }
    });
  }

  function showSceneryStar(iata, onlyOffset = null) {
    Object.entries(sceneryMarkers[iata] || {}).forEach(([offset, marker]) => {
      if (onlyOffset === null || Number(offset) === onlyOffset) {
        if (!sceneryLayer.hasLayer(marker)) sceneryLayer.addLayer(marker);
      } else if (sceneryLayer.hasLayer(marker)) {
        sceneryLayer.removeLayer(marker);
      }
    });
  }
  function hideSceneryStar(iata) {
    Object.values(sceneryMarkers[iata] || {}).forEach(m => { if (sceneryLayer.hasLayer(m)) sceneryLayer.removeLayer(m); });
  }

  function rebuildSceneryMarkers() {
    sceneryLayer.clearLayers();
    sceneryMarkers = {};
    sceneryOffsetsBuilt.clear();
    [...renderedOffsets].forEach(offset => createSceneryForOffset(offset));
  }

  function setSceneryOverlayEnabled(enabled) {
    sceneryOverlayEnabled = enabled;
    if (enabled) { rebuildSceneryMarkers(); sceneryLayer.addTo(map); } else { map.removeLayer(sceneryLayer); }
    rebuildAirportMarkers(); // scenery-covered dots need to hide/reappear immediately
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
    refreshSceneryIatas();
    if (sceneryOverlayEnabled) {
      rebuildSceneryMarkers();
      if (!map.hasLayer(sceneryLayer)) sceneryLayer.addTo(map);
      rebuildAirportMarkers();
    }
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
  // iata -> world-copy offset to show that airport at while a source is selected. Each
  // connected airport gets its OWN offset (not necessarily the source's), because a route
  // can cross the antimeridian in either direction - see routeOffsetFor().
  let visibleFilterOffsets = {};

  // Picks whichever world-copy offset makes `destLon` render on the SAME side of the map
  // as the great-circle route line actually draws on (computeGeodesicPoints picks the
  // shorter great-circle path by wrapping destLon by ±360 when the raw difference exceeds
  // 180° - this mirrors that same single-wrap decision so a connected airport's dot never
  // ends up on the opposite side of the map from where its own route line terminates).
  function routeOffsetFor(srcLon, destLon, baseOffset) {
    const dLon = destLon - srcLon;
    if (dLon > 180) return baseOffset - 360;
    if (dLon < -180) return baseOffset + 360;
    return baseOffset;
  }
  let pendingSavedFlight = null;
  let highlightedFlightKey = null;
  let lastRenderedRoutes = [];
  let lastPairRoutesUnsorted = [];
  let flightSortColumn = null; // 'flight' | 'from' | 'to' | 'aircraft' | 'time'
  let flightSortDir = 'asc'; // 'asc' | 'desc'

  function nearestOffset(canonicalLon) {
    const centerLon = map.getCenter().lng;
    return Math.round((centerLon - canonicalLon) / 360) * 360;
  }

  function showAirport(iata, onlyOffset = null) {
    if (sceneryHidesAirportDot(iata)) return;
    Object.entries(airportMarkers[iata] || {}).forEach(([offset, marker]) => {
      if (onlyOffset === null || Number(offset) === onlyOffset) {
        if (!airportLayer.hasLayer(marker)) airportLayer.addLayer(marker);
      } else if (airportLayer.hasLayer(marker)) {
        airportLayer.removeLayer(marker);
      }
    });
  }
  function hideAirport(iata) {
    Object.values(airportMarkers[iata] || {}).forEach(m => { if (airportLayer.hasLayer(m)) airportLayer.removeLayer(m); });
  }

  // Fixed radius for every airport, regardless of destination-count rank - bigger on
  // mobile since a small circle is nearly impossible to tap accurately with a finger.
  function markerRadius() {
    return isMobile ? 9 : 4;
  }

  function createAirportsForOffset(offset) {
    if (renderedOffsets.has(offset)) return;
    renderedOffsets.add(offset);
    if (sceneryOverlayEnabled) createSceneryForOffset(offset);

    airportsData.forEach(ap => {
      const marker = L.circleMarker([ap.lat, ap.lon + offset], {
        radius: markerRadius(),
        fillColor: getComputedStyle(document.documentElement).getPropertyValue('--marker-dot').trim(),
        color: getComputedStyle(document.documentElement).getPropertyValue('--marker-border').trim(),
        weight: isMobile ? 1 : 0.6, stroke: true, fillOpacity: 0.95, pane: 'airportsPane'
      });
      // Hover-only tooltip (no `permanent: true`) - the ICAO/city label only shows on
      // mouseover, never baked permanently onto the dot.
      marker.bindTooltip(ap.iata + " - " + ap.city, { direction: 'top', offset: [0, -5], className: 'atlas-tooltip' });
      marker.on('click', (e) => { L.DomEvent.stopPropagation(e); handleAirportClick(ap.iata, offset); });

      if (!airportMarkers[ap.iata]) airportMarkers[ap.iata] = {};
      airportMarkers[ap.iata][offset] = marker;

      if (!sceneryHidesAirportDot(ap.iata) && (!visibleFilter || (visibleFilter.has(ap.iata) && visibleFilterOffsets[ap.iata] === offset))) airportLayer.addLayer(marker);
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

  const routeFlightsSection = document.getElementById('route-flights');
  document.getElementById('route-flights-close').addEventListener('click', deslectDestOnly);

  const FLIGHT_SORT_KEYS = {
    flight: r => (r.flight || '').toLowerCase(),
    from: r => (r.dep_icao || r.dep || '').toLowerCase(),
    to: r => (r.arr_icao || r.arr || '').toLowerCase(),
    aircraft: r => (r.type_icao || r.type || '').toLowerCase(),
    time: r => (r.flight_time_hours === null || r.flight_time_hours === undefined) ? -Infinity : r.flight_time_hours
  };
  document.querySelectorAll('.sortable-col').forEach(btn => {
    btn.addEventListener('click', () => {
      const col = btn.dataset.sort;
      flightSortDir = (flightSortColumn === col && flightSortDir === 'asc') ? 'desc' : 'asc';
      flightSortColumn = col;
      renderFlightsTable();
    });
  });

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
      if (!panelEls.explore.classList.contains('open')) openPanel('explore');

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
    routeFlightsSection.classList.remove('visible');

    if (deselectMarker) { airportLayer.removeLayer(deselectMarker); deselectMarker = null; }
    if (deselectDestMarker) { airportLayer.removeLayer(deselectDestMarker); deselectDestMarker = null; }

    visibleFilter = null;
    visibleFilterOffsets = {};
    Object.keys(airports).forEach(iata => showAirport(iata));
    Object.keys(sceneryMarkers).forEach(iata => showSceneryStar(iata));
  }

  function deslectDestOnly() {
    selectedDest = null;
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

      const srcAp = airports[selectedSource];
      visibleFilterOffsets = {};
      connectedIatas.forEach(iata => {
        const ap = airports[iata];
        visibleFilterOffsets[iata] = (ap && srcAp) ? routeOffsetFor(srcAp.lon, ap.lon, selectedSourceOffset) : selectedSourceOffset;
      });

      Object.keys(airports).forEach(iata => { connectedIatas.has(iata) ? showAirport(iata, visibleFilterOffsets[iata]) : hideAirport(iata); });
      Object.keys(sceneryMarkers).forEach(iata => { connectedIatas.has(iata) ? showSceneryStar(iata, visibleFilterOffsets[iata]) : hideSceneryStar(iata); });

      if (pendingSavedFlight && pendingSavedFlight.dep === selectedSource) {
        const wantedArr = pendingSavedFlight.arr;
        if (wantedArr && connectedIatas.has(wantedArr)) {
          selectedDest = wantedArr;
          selectedDestOffset = selectedSourceOffset;
        } else if (routes.length > 0 && connectedIatas.size === 1) {
          selectedDest = selectedSource;
          selectedDestOffset = selectedSourceOffset;
        }
      } else if (routes.length > 0 && connectedIatas.size === 1) {
        selectedDest = selectedSource;
        selectedDestOffset = selectedSourceOffset;
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
  const SIMBRIEF_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
  const savedFlightKeys = new Set();

  // Shared identity for a route record - must match `flight_key` in run/webapp/storage.py.
  function flightKey(r) { return [r.reg, r.flight, r.dep, r.arr, r.date].join('|'); }

  function formatFlightTime(hours) {
    if (hours === null || hours === undefined) return 'Unknown';
    const totalMinutes = Math.round(hours * 60);
    return `${Math.floor(totalMinutes / 60)}h ${totalMinutes % 60}m`;
  }
  function formatDistance(distance) {
    return typeof distance === 'number' ? Math.round(distance).toLocaleString() + ' nm' : 'Unknown';
  }
  function compactAirportName(name) {
    const compact = String(name || '').replace(/\b(?:airport|airfield|aerodrome)\b/gi, '').replace(/\s{2,}/g, ' ').trim();
    return compact || 'Unknown';
  }
  function airportLabel(route, direction) {
    const code = route[direction];
    return compactAirportName(route[direction + '_name'] || (airports[code] && airports[code].name));
  }
  function fitFlightTableColumns(table, routes) {
    if (!table) return;
    const measureCanvas = document.createElement('canvas');
    const context = measureCanvas.getContext('2d');
    const fontFamily = getComputedStyle(table).fontFamily;
    context.font = `italic 9px ${fontFamily}`;
    const minimumWidth = values => Math.ceil(Math.max(0, ...values.flatMap(value => String(value || 'Unknown').split(/\s+/))
      .map(word => context.measureText(word).width))) + 2;
    table.style.setProperty('--from-min', minimumWidth(routes.map(route => airportLabel(route, 'dep'))) + 'px');
    table.style.setProperty('--to-min', minimumWidth(routes.map(route => airportLabel(route, 'arr'))) + 'px');
    table.style.setProperty('--aircraft-min', minimumWidth(routes.map(route => route.type || route.type_icao)) + 'px');
  }
  const FLIGHT_TIME_INFO = "Estimated as (Distance x 1.07 / Cruise Speed) + Ascent/Descent Buffer, "
    + "where Buffer = 83.33 / Cruise Speed (time lost climbing/descending ~250nm at 75% of cruise "
    + "speed vs. covering it at full cruise speed). Cruise speed comes from aircraft_crz_speeds.json; "
    + "shown as Unknown when the aircraft's ICAO type isn't in that file.";
  document.getElementById('flights-table-time-header').title = FLIGHT_TIME_INFO;
  document.getElementById('saved-flights-time-header').title = FLIGHT_TIME_INFO;

  function setSaveButtonState(btn, saved) {
    btn.classList.toggle('saved', saved);
    btn.innerHTML = saved ? BOOKMARK_ICON_FILLED : BOOKMARK_ICON;
    btn.title = saved ? 'Remove from saved flights' : 'Save flight';
    btn.setAttribute('aria-label', btn.title);
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
    openPanel('explore'); // show the restored route's table immediately, not a closed panel
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

    const panelRect = panelEls.explore.getBoundingClientRect();
    map.fitBounds(bounds, isMobile ? {
      paddingTopLeft: [50, 50], paddingBottomRight: [50, panelRect.height + 20], maxZoom: 7, animate: true
    } : {
      paddingTopLeft: [panelRect.right + 20, 50], paddingBottomRight: [50, 50], maxZoom: 7, animate: true
    });
  }

  function scrollToHighlightedCard(record) {
    const idx = lastRenderedRoutes.findIndex(r => flightKey(r) === flightKey(record));
    if (idx === -1) return;
    const card = document.getElementById('flight-row-' + idx);
    if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  // --- SIMBRIEF EXPORT ---
  async function exportToSimbrief(route, btn) {
    btn.disabled = true;
    btn.classList.remove('error');
    try {
      const response = await fsatlasFetch('/api/simbrief/export', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(route)
      });
      const result = await response.json();
      if (!response.ok || !result.url) throw new Error(result.error || 'Export failed');
      window.open(result.url, '_blank', 'noopener');
    } catch (err) {
      btn.classList.add('error');
      setTimeout(() => btn.classList.remove('error'), 2000);
    }
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
  const PLANE_ICON = '<svg viewBox="0 0 24 24" fill="currentColor" stroke="var(--map-outline)" stroke-width="1" stroke-linejoin="round"><path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-4.5l8 2.5z"></path></svg>';

  // Marks the midpoint of a complete (both-ends-selected) route with a plane icon,
  // oriented along the direction of travel. Walks CUMULATIVE ON-SCREEN PIXEL DISTANCE
  // along the already-projected curve (not the Nth-of-50 equal-angle sample point) to find
  // the true visual midpoint - a Mercator projection distorts long-haul/polar routes
  // enough that equal great-circle angular steps are very unevenly spaced on screen (e.g.
  // a route that bulges up near the pole bunches many samples together up there), so the
  // old "array index 25 of 50" pick could land well off-center of the rendered curve, or
  // pick two neighbouring points far enough apart in screen space to misjudge the angle.
  // Working in screen pixels guarantees the plane always sits exactly on the rendered
  // line, at its actual halfway point as drawn, regardless of projection distortion.
  // Returns the point at the given cumulative on-screen pixel distance along `screenPts`
  // (consecutive screen points, with `segLengths`/`totalLength` precomputed by the
  // caller), clamped to the curve's start/end.
  function pointAtCumulativeDistance(screenPts, segLengths, totalLength, distance) {
    const target = Math.min(Math.max(distance, 0), totalLength);
    let covered = 0;
    let idx = 0;
    while (idx < segLengths.length - 1 && covered + segLengths[idx] < target) {
      covered += segLengths[idx];
      idx++;
    }
    const segStart = screenPts[idx];
    const segEnd = screenPts[idx + 1];
    const t = segLengths[idx] ? Math.min(1, Math.max(0, (target - covered) / segLengths[idx])) : 0;
    return L.point(segStart.x + (segEnd.x - segStart.x) * t, segStart.y + (segEnd.y - segStart.y) * t);
  }

  function drawRouteMidpointPlane(src, dest, offset, layer) {
    const pts = computeGeodesicPoints(src.lat, src.lon, dest.lat, dest.lon).map(p => [p[0], p[1] + offset]);
    const screenPts = pts.map(p => map.latLngToLayerPoint(p));

    const segLengths = [];
    let totalLength = 0;
    for (let i = 1; i < screenPts.length; i++) {
      const d = screenPts[i].distanceTo(screenPts[i - 1]);
      segLengths.push(d);
      totalLength += d;
    }
    if (totalLength === 0) return; // degenerate curve (identical points) - nothing to orient along

    const midScreenPoint = pointAtCumulativeDistance(screenPts, segLengths, totalLength, totalLength / 2);

    // Direction is taken between two points straddling the midpoint by a fixed minimum
    // on-screen distance, not the immediate bracketing sample segment - when zoomed far
    // out, a long route's ~50 equal-angle samples can compress to well under a pixel
    // apart on screen, and subtracting two nearly-identical pixel coordinates amplifies
    // floating-point rounding into visible noise (the plane appearing to point off at an
    // unrelated/jittery angle instead of along the route). A wider window still follows
    // real curvature when the route is large on screen, but degrades gracefully to the
    // overall start->end direction (both ends clamp to the curve's endpoints) once the
    // whole route is too small on screen for a local window to be numerically meaningful.
    const halfWindow = Math.min(totalLength / 2, 24);
    const before = pointAtCumulativeDistance(screenPts, segLengths, totalLength, totalLength / 2 - halfWindow);
    const after = pointAtCumulativeDistance(screenPts, segLengths, totalLength, totalLength / 2 + halfWindow);
    const angle = Math.atan2(after.y - before.y, after.x - before.x) * 180 / Math.PI + 90;

    L.marker(map.layerPointToLatLng(midScreenPoint), {
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
    routeLayer.clearLayers();
    if (!currentRoutes || currentRoutes.length === 0) { routeFlightsSection.classList.remove('visible'); return; }

    const lineColor = getComputedStyle(document.documentElement).getPropertyValue('--line-color').trim();

    // CASE A: Source Selected, No Dest (Show All Connections)
    if (selectedSource && !selectedDest) {
      routeFlightsSection.classList.remove('visible');
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

    // CASE B: Source AND Dest Selected - list the pair's flights as a table in the Explore
    // panel (underneath the filter tree), instead of a separate floating card panel.
    if (selectedSource && selectedDest) {
      const destAp = airports[selectedDest];
      const srcAp = airports[selectedSource];
      const isSelfLoop = selectedDest === selectedSource;
      const srcIcao = srcAp ? (srcAp.icao || selectedSource) : selectedSource;
      const destIcao = destAp ? (destAp.icao || selectedDest) : selectedDest;
      document.getElementById('route-flights-title').innerText = isSelfLoop
        ? srcIcao + " \u2014 Self Flights"
        : srcIcao + " \u21c4 " + destIcao;
      document.getElementById('route-flights-airports').textContent = isSelfLoop
        ? (srcAp && srcAp.city) || 'Unknown'
        : ((srcAp && srcAp.city) || 'Unknown') + " \u21c4 " + ((destAp && destAp.city) || 'Unknown');

      const pairRoutes = currentRoutes.filter(r => r.dep === selectedDest || r.arr === selectedDest);
      lastPairRoutesUnsorted = pairRoutes;
      document.getElementById('route-flights-count').textContent = pairRoutes.length + (pairRoutes.length === 1 ? ' Flight' : ' Flights');

      if (srcAp && destAp && !isSelfLoop) {
        drawRouteAtCenter(srcAp, destAp, { color: lineColor, weight: 2, opacity: 1, pane: 'routesPane' }, routeLayer, selectedSourceOffset);
        drawRouteMidpointPlane(srcAp, destAp, selectedSourceOffset, routeLayer);
      }

      renderFlightsTable();
      routeFlightsSection.classList.add('visible');
    }
  }

  // Builds the flight rows from `lastPairRoutesUnsorted`, applying the current column sort
  // (set by clicking a `.sortable-col` header) - kept separate from renderMapState() so a
  // header click can just re-sort/re-render the table without touching the map/route layer.
  function renderFlightsTable() {
    let pairRoutes = lastPairRoutesUnsorted;
    if (flightSortColumn) {
      const keyFn = FLIGHT_SORT_KEYS[flightSortColumn];
      const dir = flightSortDir === 'asc' ? 1 : -1;
      pairRoutes = [...pairRoutes].sort((a, b) => {
        const av = keyFn(a), bv = keyFn(b);
        if (av < bv) return -1 * dir;
        if (av > bv) return 1 * dir;
        return 0;
      });
    }
    lastRenderedRoutes = pairRoutes;

    document.querySelectorAll('.sortable-col').forEach(btn => {
      btn.classList.toggle('sort-asc', btn.dataset.sort === flightSortColumn && flightSortDir === 'asc');
      btn.classList.toggle('sort-desc', btn.dataset.sort === flightSortColumn && flightSortDir === 'desc');
    });

    const tableBody = document.getElementById('flights-table-body');
    tableBody.innerHTML = pairRoutes.map((r, idx) => {
      const type_icao = r.type_icao || r.type || '-';
      const flightTime = formatFlightTime(r.flight_time_hours);
      const isHighlighted = highlightedFlightKey !== null && flightKey(r) === highlightedFlightKey;
      return `
        <div class="flight-table-entry">
          <div class="flight-row${isHighlighted ? ' highlighted' : ''}" id="flight-row-${idx}">
            <button class="row-bookmark" id="row-bookmark-${idx}" type="button"></button>
            <div class="row-flight"><span class="row-primary">${escapeHtml(r.flight || '-')}</span><span class="row-secondary">${escapeHtml(r.airline || 'Unknown')}</span></div>
            <div class="row-from"><span class="row-primary">${escapeHtml(r.dep_icao || r.dep || '-')}</span><span class="row-secondary">${escapeHtml(airportLabel(r, 'dep'))}</span></div>
            <div class="row-to"><span class="row-primary">${escapeHtml(r.arr_icao || r.arr || '-')}</span><span class="row-secondary">${escapeHtml(airportLabel(r, 'arr'))}</span></div>
            <div class="row-aircraft"><span class="row-primary">${escapeHtml(type_icao)}</span><span class="row-secondary">${escapeHtml(r.type || 'Unknown')}</span></div>
            <div class="row-time"><span class="row-primary">${escapeHtml(flightTime)}</span><span class="row-secondary">${escapeHtml(formatDistance(r.distance))}</span></div>
            <button class="row-simbrief" id="row-simbrief-${idx}" type="button" title="Export to SimBrief" aria-label="Export to SimBrief">${SIMBRIEF_ICON}</button>
          </div>
        </div>`;
    }).join('');

    pairRoutes.forEach((r, idx) => {
      const bookmarkBtn = document.getElementById('row-bookmark-' + idx);
      setSaveButtonState(bookmarkBtn, savedFlightKeys.has(flightKey(r)));
      bookmarkBtn.addEventListener('click', () => toggleSaveFlight(r, bookmarkBtn).then(loadSavedFlights));
      const simbriefBtn = document.getElementById('row-simbrief-' + idx);
      simbriefBtn.addEventListener('click', () => exportToSimbrief(r, simbriefBtn));
    });
  }

  // ===================================================================================
  // RAIL / FLYOUT PANELS (Explore, Saved, Hangar, Settings)
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

  // --- Saved Flights / Saved Searches panel ---
  const savedPanel = document.getElementById('panel-saved');
  const savedList = document.getElementById('saved-list');
  const savedSort = document.getElementById('saved-sort');
  const savedTableHead = savedList.closest('.flights-table').querySelector('.flights-table-head');
  let savedFlightsRaw = [];
  let savedSortColumn = null; // set by clicking a column header - takes priority over the "Sort by" dropdown
  let savedSortDir = 'asc';

  function sortedSavedFlights() {
    const flights = [...savedFlightsRaw];
    if (savedSortColumn) {
      const keyFn = FLIGHT_SORT_KEYS[savedSortColumn];
      const dir = savedSortDir === 'asc' ? 1 : -1;
      flights.sort((a, b) => (keyFn(a) < keyFn(b) ? -1 : keyFn(a) > keyFn(b) ? 1 : 0) * dir);
      return flights;
    }
    const dist = f => (typeof f.distance === 'number' ? f.distance : null);
    const distCompare = (a, b, dir) => {
      const da = dist(a), db = dist(b);
      if (da === null && db === null) return 0;
      if (da === null) return 1;
      if (db === null) return -1;
      return dir * (da - db);
    };
    const savedAt = f => f.saved_at || '';
    switch (savedSort.value) {
      case 'distance-asc': flights.sort((a, b) => distCompare(a, b, 1)); break;
      case 'distance-desc': flights.sort((a, b) => distCompare(a, b, -1)); break;
      case 'saved-asc': flights.sort((a, b) => savedAt(a).localeCompare(savedAt(b))); break;
      case 'saved-desc':
      default: flights.sort((a, b) => savedAt(b).localeCompare(savedAt(a))); break;
    }
    return flights;
  }

  [...savedTableHead.querySelectorAll('.sortable-col')].forEach(btn => {
    btn.addEventListener('click', () => {
      const col = btn.dataset.sort;
      savedSortDir = (savedSortColumn === col && savedSortDir === 'asc') ? 'desc' : 'asc';
      savedSortColumn = col;
      renderSavedFlights();
    });
  });

  function renderSavedFlights() {
    const flights = sortedSavedFlights();
    document.getElementById('saved-routes-count').textContent = flights.length ? String(flights.length) : '';
    [...savedTableHead.querySelectorAll('.sortable-col')].forEach(btn => {
      btn.classList.toggle('sort-asc', btn.dataset.sort === savedSortColumn && savedSortDir === 'asc');
      btn.classList.toggle('sort-desc', btn.dataset.sort === savedSortColumn && savedSortDir === 'desc');
    });
    if (!flights.length) {
      savedList.innerHTML = '<div class="saved-empty">No saved flights yet. Use the bookmark button in a flight\'s More Info panel to save one.</div>';
      return;
    }

    savedList.innerHTML = flights.map((f, i) => `
      <div class="saved-flight-entry" data-idx="${i}">
        <div class="flight-row saved-flight-row">
          <button class="row-bookmark saved-bookmark" type="button"></button>
          <div class="row-flight"><span class="row-primary">${escapeHtml(f.flight || '-')}</span><span class="row-secondary">${escapeHtml(f.airline || 'Unknown')}</span></div>
          <div class="row-from"><span class="row-primary">${escapeHtml(f.dep_icao || f.dep || '-')}</span><span class="row-secondary">${escapeHtml(airportLabel(f, 'dep'))}</span></div>
          <div class="row-to"><span class="row-primary">${escapeHtml(f.arr_icao || f.arr || '-')}</span><span class="row-secondary">${escapeHtml(airportLabel(f, 'arr'))}</span></div>
          <div class="row-aircraft"><span class="row-primary">${escapeHtml(f.type_icao || f.type || '-')}</span><span class="row-secondary">${escapeHtml(f.type || 'Unknown')}</span></div>
          <div class="row-time"><span class="row-primary">${escapeHtml(formatFlightTime(f.flight_time_hours))}</span><span class="row-secondary">${escapeHtml(formatDistance(f.distance))}</span></div>
          <button class="row-simbrief" type="button" title="Export to SimBrief" aria-label="Export to SimBrief">${SIMBRIEF_ICON}</button>
        </div>
      </div>
    `).join('');
    fitFlightTableColumns(savedList.closest('.flights-table'), flights);

    [...savedList.querySelectorAll('.saved-flight-entry')].forEach((row, i) => {
      const flight = flights[i];
      row.addEventListener('click', () => { closePanel(); showSavedFlight(flight); });
      const bookmarkBtn = row.querySelector('.saved-bookmark');
      setSaveButtonState(bookmarkBtn, true);
      bookmarkBtn.addEventListener('click', async e => {
        e.stopPropagation();
        await toggleSaveFlight(flight, bookmarkBtn);
        loadSavedFlights();
      });
      const simbriefBtn = row.querySelector('.row-simbrief');
      simbriefBtn.addEventListener('click', e => { e.stopPropagation(); exportToSimbrief(flight, simbriefBtn); });
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
  savedSort.addEventListener('change', () => { savedSortColumn = null; renderSavedFlights(); });

  // --- Saved Searches ---
  const savedSearchesList = document.getElementById('saved-searches-list');
  const exploreSavedSearchesSelect = document.getElementById('explore-saved-searches');
  let savedSearchesRaw = [];

  // Lets the Explore panel apply a saved search without opening the Saved panel - always
  // reflects the same list as the Saved panel's own list.
  function renderExploreSavedSearchesDropdown() {
    exploreSavedSearchesSelect.innerHTML = '<option value="" selected>Apply a saved search\u2026</option>' +
      savedSearchesRaw.map((s, i) => `<option value="${i}">${escapeHtml(s.description || 'Untitled search')}</option>`).join('');
  }
  exploreSavedSearchesSelect.addEventListener('change', () => {
    const idx = exploreSavedSearchesSelect.value;
    exploreSavedSearchesSelect.value = '';
    if (idx === '') return;
    const search = savedSearchesRaw[Number(idx)];
    if (search) applySavedSearch(search);
  });

  function renderSavedSearches() {
    document.getElementById('saved-searches-count').textContent = savedSearchesRaw.length ? String(savedSearchesRaw.length) : '';
    renderExploreSavedSearchesDropdown();
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
      const children = nodeData.children || [];
      group.dataset.logic = majorityLogic(children);
      setScopeUI(group.querySelector(':scope > .scope-header'), group.dataset.logic);
      const list = group.querySelector(':scope > .filters-list');
      children.forEach(child => list.append(buildFilterTreeNode(child)));
      return group;
    }
    const row = createConditionRow();
    row.querySelector('.column').value = nodeData.column || '';
    renderRowBody(row);
    const column = columns.find(c => c.id === nodeData.column);
    const opWord = row.querySelector('.op-word');
    if (column && Array.isArray(column.options)) {
      if (opWord) opWord.value = nodeData.operator === 'not_equals' ? 'not_equals' : 'equals';
      const values = Array.isArray(nodeData.value) ? nodeData.value : (nodeData.value != null && nodeData.value !== '' ? [nodeData.value] : []);
      if (row._chipCombo) row._chipCombo.setValues(values);
    } else {
      if (opWord) opWord.value = nodeData.operator || '';
      const valueInput = row.querySelector('.value');
      if (valueInput) valueInput.value = nodeData.value ?? '';
    }
    return row;
  }

  function applySavedSearch(search) {
    openPanel('explore'); // show the restored tree immediately, not an empty map with nothing open
    SIMPLE_FILTER_SPECS.forEach(spec => { if (simpleFilterState[spec.key]) simpleFilterState[spec.key].values = []; });
    const length = simpleFilterState.length;
    if (length) length.active = false;
    const leaves = [];
    const collectLeaves = node => {
      if (!node) return;
      if (node.kind === 'group') (node.children || []).forEach(collectLeaves);
      else leaves.push(node);
    };
    collectLeaves(search.filters);
    leaves.forEach(leaf => {
      const spec = SIMPLE_FILTER_SPECS.find(candidate => candidate.fields.some(field => field.id === leaf.column));
      if (spec && Array.isArray(leaf.value) && simpleFilterState[spec.key]) {
        simpleFilterState[spec.key].column = leaf.column;
        simpleFilterState[spec.key].values = leaf.value.slice();
      } else if (length && (leaf.column === 'distance' || leaf.column === 'rough_flight_time')) {
        length.column = leaf.column;
        if (leaf.operator === '>=') { length.min = Number(leaf.value); length.active = true; }
        if (leaf.operator === '<=') { length.max = Number(leaf.value); length.active = true; }
      }
    });
    SIMPLE_FILTER_SPECS.forEach(spec => {
      const menu = simpleFiltersEl.querySelector(`.simple-filter-menu[data-filter="${spec.key}"]`);
      if (menu) renderSimpleCategory(spec, menu);
    });
    const lengthMenu = simpleFiltersEl.querySelector('.simple-length-menu');
    if (lengthMenu && length) {
      const fieldSelect = lengthMenu.querySelector('.simple-filter-field');
      const minInput = lengthMenu.querySelector('.simple-length-min');
      const maxInput = lengthMenu.querySelector('.simple-length-max');
      const outputs = lengthMenu.querySelectorAll('.simple-range-label output');
      fieldSelect.value = length.column;
      minInput.value = length.min; maxInput.value = length.max;
      outputs[0].textContent = length.min + (length.column === 'distance' ? ' nm' : ' hr');
      outputs[1].textContent = length.max + (length.column === 'distance' ? ' nm' : ' hr');
      lengthMenu.querySelector('.simple-filter-trigger').textContent = length.active ? `Length (${length.column === 'distance' ? 'distance' : 'time'})` : 'Length';
    }
    updateFilterCountBadge();
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
    const filterTree = currentFilterTree();
    try {
      await fsatlasFetch('/api/saved-searches', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: trimmed, filters: filterTree })
      });
    } catch (err) { /* best effort */ }
    closeSaveSearchModal();
    if (savedPanel.classList.contains('open')) loadSavedSearches();
  }

  document.getElementById('save-search-btn').addEventListener('click', openSaveSearchModal);
  document.getElementById('save-search-close').addEventListener('click', closeSaveSearchModal);
  document.getElementById('save-search-cancel').addEventListener('click', closeSaveSearchModal);
  document.getElementById('save-search-confirm').addEventListener('click', confirmSaveSearch);
  saveSearchModal.addEventListener('click', e => { if (e.target === saveSearchModal) closeSaveSearchModal(); });
  saveSearchDescInput.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); confirmSaveSearch(); } });

  // --- Settings panel (SimBrief Pilot ID) ---
  const pilotIdInput = document.getElementById('simbrief-pilot-id');
  const simbriefStatus = document.getElementById('simbrief-status');

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
    closePanel();
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
    updateHangarStats();
  }

  function updateHangarStats() {
    const unresolvedCount = sceneryImportErrors.filter(e => e.stage === 'airport_match' && e.source).length;
    document.getElementById('hangar-matched-count').textContent = String(sceneryData.length);
    document.getElementById('hangar-unresolved-count').textContent = String(unresolvedCount);
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

      const controls = document.createElement('div');
      controls.className = 'scenery-error-resolution-controls';
      controls.append(search, select, button);
      row.append(packageName, controls);
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
      refreshSceneryIatas();
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
    refreshSceneryIatas();
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

  // --- Icon rail + flyout panels: Explore/Saved/Hangar/Settings each slide over the map
  // from the same spot (right of the rail on desktop, up from the bottom rail on mobile -
  // see app.css); only one is open at a time, and clicking the active rail icon again
  // closes it. Desktop defaults to Explore open; mobile defaults to all closed. ---
  const PANEL_NAMES = ['explore', 'saved', 'hangar', 'settings'];
  const panelEls = {};
  const railBtns = {};
  PANEL_NAMES.forEach(name => { panelEls[name] = document.getElementById('panel-' + name); });
  document.querySelectorAll('.rail-btn').forEach(btn => { railBtns[btn.dataset.panel] = btn; });
  const panelScrim = document.getElementById('panel-scrim');
  const filtersCountBadge = document.getElementById('filters-count-badge');

  function closeAllPanels() {
    PANEL_NAMES.forEach(name => { panelEls[name].classList.remove('open'); railBtns[name].classList.remove('active'); });
    document.body.classList.remove('panel-open');
  }
  function closePanel() { closeAllPanels(); }
  function openPanel(name) {
    const alreadyOpen = panelEls[name].classList.contains('open');
    closeAllPanels();
    if (alreadyOpen) return; // clicking the active rail icon again just closes it
    panelEls[name].classList.add('open');
    railBtns[name].classList.add('active');
    document.body.classList.add('panel-open');
    if (name === 'saved') { loadSavedFlights(); loadSavedSearches(); }
    if (name === 'settings') { pilotIdInput.value = savedPilotId; simbriefStatus.textContent = ''; }
  }
  PANEL_NAMES.forEach(name => railBtns[name].addEventListener('click', () => openPanel(name)));
  document.querySelectorAll('.panel-close').forEach(btn => btn.addEventListener('click', closeAllPanels));
  panelScrim.addEventListener('click', closeAllPanels);

  function countConfiguredFilters() { return [...filters.querySelectorAll('.column')].filter(col => col.value).length; }
  function updateFilterCountBadge() {
    const configured = simpleFilterChildren().length;
    filtersCountBadge.textContent = configured ? configured + ' active' : 'none active';
    filtersCountBadge.classList.toggle('zero', configured === 0);
  }
  filters.addEventListener('change', e => { if (e.target.matches('.column')) updateFilterCountBadge(); });

  // Default panel state follows the breakpoint: Explore open on desktop, everything
  // closed on mobile - re-applied live if the window is resized across it.
  let resizeRaf;
  function scheduleResizeCheck() {
    cancelAnimationFrame(resizeRaf);
    resizeRaf = requestAnimationFrame(() => {
      const nowMobile = isMobileViewport();
      if (nowMobile !== isMobile) {
        isMobile = nowMobile;
        document.body.classList.toggle('is-mobile', isMobile);
        isMobile ? closeAllPanels() : openPanel('explore');
        rebuildAirportMarkers(); // marker radius depends on isMobile
        if (sceneryOverlayEnabled) rebuildSceneryMarkers(); // star size depends on isMobile too
      }
    });
  }
  window.addEventListener('resize', scheduleResizeCheck);

  // --- Filter tree (nested AND/OR groups) ---
  // Scope Header pattern: a container (the root #filters list, or a .filter-group) owns
  // ONE AND/OR toggle anchored at its top, instead of a per-row floating logic pill - every
  // row inherits whichever operator its nearest container declares. Mixing AND/OR requires
  // nesting a sub-group (its own Scope Header), never alternating row-level pills.
  let rootLogic = 'AND';

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
    if (isRoot && !filters.children.length) addRow(); // never leave the root list empty
    updateFilterCountBadge();
    schedulePreview();
  }

  const ICON_PLUS = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>';
  const ICON_GROUP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h2"></path><path d="M16 4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2"></path></svg>';
  const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>';
  const ICON_KEBAB = '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="1.7"></circle><circle cx="12" cy="12" r="1.7"></circle><circle cx="12" cy="19" r="1.7"></circle></svg>';

  // --- Contextual kebab menu: shared by every row and every group's Scope Header. The
  // button fades in on hover/focus (see app.css) instead of a persistent button row, and
  // only ONE menu is ever open at a time (closed by the delegated document click below). ---
  function attachKebab(wrap, items) {
    const btn = wrap.querySelector('.kebab');
    const menu = wrap.querySelector('.kebab-menu');
    items.forEach(({ label, action, danger }) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'kebab-item' + (danger ? ' danger' : '');
      item.textContent = label;
      item.addEventListener('click', e => { e.stopPropagation(); closeAllKebabMenus(); action(); });
      menu.append(item);
    });
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const opening = !menu.classList.contains('open');
      closeAllKebabMenus();
      menu.classList.toggle('open', opening);
      btn.classList.toggle('menu-open', opening);
    });
  }
  function closeAllKebabMenus() {
    document.querySelectorAll('.kebab-menu.open').forEach(m => m.classList.remove('open'));
    document.querySelectorAll('.kebab.menu-open').forEach(b => b.classList.remove('menu-open'));
  }
  document.addEventListener('click', closeAllKebabMenus);

  function wireScopeToggle(scopeHeader, onChange) {
    const buttons = [...scopeHeader.querySelectorAll('.scope-opt')];
    buttons.forEach(btn => btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.toggle('active', b === btn));
      onChange(btn.dataset.logic);
    }));
  }
  function setScopeUI(scopeHeader, logic) {
    [...scopeHeader.querySelectorAll('.scope-opt')].forEach(
      b => b.classList.toggle('active', b.dataset.logic === logic)
    );
  }
  // Old saved searches may predate the Scope Header simplification and carry genuinely
  // mixed per-child logic - pick whichever operator the majority of (non-first) children
  // used, so restoring one degrades gracefully instead of crashing or losing data.
  function majorityLogic(children) {
    if (!children || !children.length) return 'AND';
    if (children.length === 1) return (children[0].logic || 'AND').toUpperCase() === 'OR' ? 'OR' : 'AND';
    const counts = { AND: 0, OR: 0 };
    children.slice(1).forEach(c => { counts[(c.logic || 'AND').toUpperCase() === 'OR' ? 'OR' : 'AND']++; });
    return counts.OR > counts.AND ? 'OR' : 'AND';
  }

  // --- Multi-select chip combobox: categorical columns (airline, airport, aircraft type,
  // country, region, ...) get type-ahead -> inline chip tokens in the SAME row instead of
  // a bare dropdown/text field - "is any of A, B, C" needs no nested OR sub-group. ---
  function createChipCombo(options, onChange) {
    const el = document.createElement('div');
    el.className = 'chip-combo';
    const chipsWrap = document.createElement('div');
    chipsWrap.className = 'chip-combo-chips';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'chip-combo-input';
    input.placeholder = 'Search\u2026';
    chipsWrap.append(input);
    const suggestions = document.createElement('div');
    suggestions.className = 'chip-combo-suggestions';
    el.append(chipsWrap, suggestions);

    let values = [];

    function renderChips() {
      [...chipsWrap.querySelectorAll('.chip')].forEach(c => c.remove());
      values.forEach(v => {
        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.textContent = v;
        const x = document.createElement('button');
        x.type = 'button'; x.className = 'chip-x'; x.setAttribute('aria-label', 'Remove ' + v);
        x.innerHTML = ICON_X;
        x.addEventListener('click', e => { e.stopPropagation(); values = values.filter(v2 => v2 !== v); renderChips(); onChange(); });
        chip.append(x);
        chipsWrap.insertBefore(chip, input);
      });
    }

    function currentMatches() {
      const q = input.value.trim().toLowerCase();
      const pool = options.filter(o => !values.includes(o));
      return (q ? pool.filter(o => o.toLowerCase().includes(q)) : pool).slice(0, 40);
    }

    function renderSuggestions() {
      const matches = currentMatches();
      suggestions.replaceChildren();
      if (!matches.length) { suggestions.classList.remove('open'); return; }
      matches.forEach((m, i) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'chip-combo-suggestion' + (i === 0 ? ' active' : '');
        item.textContent = m;
        item.addEventListener('mousedown', e => { e.preventDefault(); addValue(m); });
        suggestions.append(item);
      });
      suggestions.classList.add('open');
    }

    function addValue(v) {
      if (!values.includes(v)) { values.push(v); renderChips(); onChange(); }
      input.value = '';
      renderSuggestions();
      input.focus();
    }

    input.addEventListener('focus', renderSuggestions);
    input.addEventListener('input', renderSuggestions);
    input.addEventListener('keydown', e => {
      const items = [...suggestions.querySelectorAll('.chip-combo-suggestion')];
      const activeIdx = items.findIndex(i => i.classList.contains('active'));
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        if (!items.length) return;
        const next = e.key === 'ArrowDown' ? (activeIdx + 1) % items.length : (activeIdx - 1 + items.length) % items.length;
        items.forEach((item, i) => item.classList.toggle('active', i === next));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const active = items[activeIdx] || items[0];
        if (active) addValue(active.textContent);
      } else if (e.key === 'Backspace' && !input.value && values.length) {
        values.pop(); renderChips(); onChange();
      } else if (e.key === 'Escape') {
        suggestions.classList.remove('open'); input.blur();
      }
    });
    input.addEventListener('blur', () => { setTimeout(() => suggestions.classList.remove('open'), 120); });

    return { el, getValues: () => values.slice(), setValues: v => { values = (v || []).slice(); renderChips(); } };
  }

  // Static unit suffixes for numeric columns, shown as plain text after the value input
  // (part of the "sentence" - never something the user has to type themselves).
  const UNITS = {
    distance: 'nm', rough_flight_time: 'hrs',
    dep_airport_elevation: 'ft', arr_airport_elevation: 'ft',
    dep_airport_lat: '\u00b0', dep_airport_lon: '\u00b0', arr_airport_lat: '\u00b0', arr_airport_lon: '\u00b0',
  };

  // Builds (or rebuilds) a row's content based on whichever column is currently selected -
  // categorical columns get the chip combo, numeric/text columns get an inline operator
  // word + value input, collapsing the old 3-separate-boxes grid into one flowing line.
  function renderRowBody(row) {
    const column = columns.find(c => c.id === row.querySelector('.column').value);
    const body = row.querySelector('.row-body');
    body.replaceChildren();
    delete row._chipCombo;
    if (!column) return;

    if (Array.isArray(column.options)) {
      const opWord = document.createElement('select');
      opWord.className = 'op-word';
      opWord.add(new Option('is', 'equals'));
      opWord.add(new Option('is not', 'not_equals'));
      opWord.addEventListener('change', schedulePreview);
      const combo = createChipCombo(column.options, schedulePreview);
      row._chipCombo = combo;
      body.append(opWord, combo.el);
      return;
    }

    const opWord = document.createElement('select');
    opWord.className = 'op-word';
    const ops = column.numeric
      ? [['equals', 'is'], ['>', 'is greater than'], ['<', 'is less than'], ['>=', 'is at least'], ['<=', 'is at most']]
      : [['contains', 'contains'], ['equals', 'is exactly'], ['starts_with', 'starts with'], ['ends_with', 'ends with']];
    ops.forEach(([value, label]) => opWord.add(new Option(label, value)));
    opWord.addEventListener('change', schedulePreview);

    const value = document.createElement('input');
    value.className = 'value';
    value.type = column.numeric ? 'number' : 'text';
    value.step = column.numeric ? 'any' : '';
    value.placeholder = column.example ? 'e.g. ' + column.example : 'Value';
    value.addEventListener('input', schedulePreview);

    body.append(opWord, value);
    const unit = UNITS[column.id];
    if (column.numeric && unit) body.append(Object.assign(document.createElement('span'), { className: 'row-unit', textContent: unit }));
  }

  function createConditionRow() {
    const row = document.createElement('div');
    row.className = 'filter-row';
    row.innerHTML =
      '<div class="row-sentence">' +
        '<select class="column"><option value="">Choose a field\u2026</option></select>' +
        '<div class="row-body"></div>' +
      '</div>' +
      '<div class="kebab-wrap">' +
        '<button class="kebab" type="button" aria-label="Row options">' + ICON_KEBAB + '</button>' +
        '<div class="kebab-menu"></div>' +
      '</div>';
    const columnSelect = row.querySelector('.column');
    populateColumnSelect(columnSelect);
    columnSelect.addEventListener('change', () => { renderRowBody(row); updateFilterCountBadge(); schedulePreview(); });
    attachKebab(row.querySelector('.kebab-wrap'), [
      { label: 'Duplicate', action: () => duplicateRow(row) },
      { label: 'Wrap in group', action: () => wrapInGroup(row) },
      { label: 'Delete', action: () => removeNode(row), danger: true },
    ]);
    renderRowBody(row);
    return row;
  }

  function duplicateRow(row) {
    const clone = createConditionRow();
    const colId = row.querySelector('.column').value;
    clone.querySelector('.column').value = colId;
    renderRowBody(clone);
    const column = columns.find(c => c.id === colId);
    if (column) {
      const srcOpWord = row.querySelector('.op-word');
      const cloneOpWord = clone.querySelector('.op-word');
      if (srcOpWord && cloneOpWord) cloneOpWord.value = srcOpWord.value;
      if (Array.isArray(column.options)) {
        if (row._chipCombo && clone._chipCombo) clone._chipCombo.setValues(row._chipCombo.getValues());
      } else {
        const srcValue = row.querySelector('.value');
        const cloneValue = clone.querySelector('.value');
        if (srcValue && cloneValue) cloneValue.value = srcValue.value;
      }
    }
    row.after(clone);
    schedulePreview();
  }

  function createGroup() {
    const group = document.createElement('div');
    group.className = 'filter-group';
    group.dataset.logic = 'AND';
    group.innerHTML =
      '<div class="scope-header">' +
        '<div class="scope-toggle" role="group" aria-label="How conditions in this group combine">' +
          '<button type="button" class="scope-opt active" data-logic="AND">Match ALL</button>' +
          '<button type="button" class="scope-opt" data-logic="OR">Match ANY</button>' +
        '</div>' +
        '<div class="kebab-wrap">' +
          '<button class="kebab" type="button" aria-label="Group options">' + ICON_KEBAB + '</button>' +
          '<div class="kebab-menu"></div>' +
        '</div>' +
      '</div>' +
      '<div class="filters-list"></div>' +
      '<div class="tree-add-row">' +
        '<button type="button" class="ghost-add add-condition">' + ICON_PLUS + 'Add condition</button>' +
        '<button type="button" class="ghost-add add-group">' + ICON_GROUP + 'Add nested group</button>' +
      '</div>';
    wireScopeToggle(group.querySelector(':scope > .scope-header'), logic => { group.dataset.logic = logic; schedulePreview(); });
    attachKebab(group.querySelector(':scope > .scope-header > .kebab-wrap'), [
      { label: 'Ungroup', action: () => ungroup(group) },
      { label: 'Delete group', action: () => removeNode(group), danger: true },
    ]);
    const list = group.querySelector(':scope > .filters-list');
    group.querySelector(':scope > .tree-add-row > .add-condition').addEventListener('click', () => { list.append(createConditionRow()); schedulePreview(); });
    group.querySelector(':scope > .tree-add-row > .add-group').addEventListener('click', () => { list.append(createGroup()); schedulePreview(); });
    return group;
  }

  function wrapInGroup(node) {
    const group = createGroup();
    node.before(group);
    group.querySelector(':scope > .filters-list').append(node);
    schedulePreview();
  }

  function ungroup(group) {
    const children = [...group.querySelector(':scope > .filters-list').children];
    children.forEach(child => group.before(child));
    group.remove();
    schedulePreview();
  }

  function addRow(afterRow) {
    const row = createConditionRow();
    if (afterRow) afterRow.after(row); else filters.append(row);
    updateFilterCountBadge();
    return row;
  }

  // Each container's OWN children all share that container's single Scope Header
  // operator (parentLogic), stamped onto every child dict here - `logic` on a child still
  // means "how I combine with my previous sibling" per the existing backend schema, it's
  // just uniformly authored by the container now instead of per-row.
  function serializeList(listEl, parentLogic) {
    return [...listEl.children].map(serializeNode).filter(Boolean).map(item => ({ ...item, logic: parentLogic }));
  }
  function serializeNode(node) {
    if (node.classList.contains('filter-group')) {
      const children = serializeList(node.querySelector(':scope > .filters-list'), node.dataset.logic || 'AND');
      return children.length ? { kind: 'group', logic: 'AND', children } : null;
    }
    const column = columns.find(item => item.id === node.querySelector('.column').value);
    if (!column) return null;
    const opWord = node.querySelector('.op-word');
    if (!opWord) return null;
    if (Array.isArray(column.options)) {
      const values = node._chipCombo ? node._chipCombo.getValues() : [];
      if (!values.length) return null;
      return { column: column.id, operator: opWord.value, value: values, logic: 'AND', type: 'select' };
    }
    const raw = node.querySelector('.value').value.trim();
    if (!raw) return null;
    return {
      column: column.id, operator: opWord.value,
      value: column.numeric ? Number(raw) : raw,
      logic: 'AND', type: column.numeric ? 'number' : 'text',
    };
  }

  const simpleFiltersEl = document.getElementById('simple-filters');
  const SIMPLE_FILTER_SPECS = [
    { key: 'airline', label: 'Airline', fields: [{ label: 'Airline', id: 'owner' }] },
    { key: 'airport', label: 'Airports', fields: [
      { label: 'ICAO', id: 'combined:dep_airport_icao:arr_airport_icao' },
      { label: 'IATA', id: 'combined:dep_airport_iata:arr_airport_iata' },
      { label: 'Name', id: 'combined:dep_airport:arr_airport' },
    ] },
    { key: 'aircraft', label: 'Aircraft', fields: [{ label: 'ICAO', id: 'type_icao' }, { label: 'Full type', id: 'type' }] },
    { key: 'location', label: 'Location', fields: [
      { label: 'City', id: 'combined:dep_airport_city:arr_airport_city' },
      { label: 'Country', id: 'combined:dep_airport_country:arr_airport_country' },
      { label: 'Region', id: 'combined:dep_airport_region:arr_airport_region' },
    ] },
  ];
  const simpleFilterState = {};

  function simpleColumn(id) { return columns.find(column => column.id === id); }
  function simpleCategoryLabel(spec) {
    const state = simpleFilterState[spec.key];
    return spec.label;
  }
  function closeSimpleMenus(except) {
    simpleFiltersEl.querySelectorAll('.simple-filter-menu.open').forEach(menu => { if (menu !== except) menu.classList.remove('open'); });
  }
  function renderSimpleCategory(spec, menu) {
    const state = simpleFilterState[spec.key];
    const field = simpleColumn(state.column);
    const button = menu.querySelector('.simple-filter-trigger');
    button.querySelector('.simple-filter-label').textContent = simpleCategoryLabel(spec);
    const count = button.querySelector('.simple-filter-count');
    count.textContent = state.values.length;
    count.hidden = state.values.length === 0;
    const fieldSelect = menu.querySelector('.simple-filter-field');
    const search = menu.querySelector('.simple-filter-search');
    const options = menu.querySelector('.simple-filter-options');
    const query = search.value.trim().toLowerCase();
    const values = Array.isArray(field && field.options) ? field.options : [];
    options.replaceChildren();
    values.filter(value => !query || value.toLowerCase().includes(query)).slice(0, 160).forEach(value => {
      const label = document.createElement('label');
      label.className = 'simple-filter-option';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox'; checkbox.checked = state.values.includes(value);
      checkbox.addEventListener('change', () => {
        state.values = checkbox.checked ? [...state.values, value] : state.values.filter(item => item !== value);
        renderSimpleCategory(spec, menu); schedulePreview();
      });
      label.append(checkbox, document.createTextNode(value));
      options.append(label);
    });
    if (!values.length) options.textContent = 'No values available for this field.';
    fieldSelect.value = state.column;
  }
  function buildSimpleCategory(spec) {
    const availableFields = spec.fields.filter(field => simpleColumn(field.id));
    if (!availableFields.length) return null;
    const state = simpleFilterState[spec.key] = { column: availableFields[0].id, values: [] };
    const menu = document.createElement('div');
    menu.className = 'simple-filter-menu';
    menu.dataset.filter = spec.key;
    menu.innerHTML = '<button type="button" class="simple-filter-trigger"><span class="simple-filter-label"></span><span class="simple-filter-count" hidden></span></button><div class="simple-filter-popover"><select class="simple-filter-field"></select><input class="simple-filter-search" type="search" placeholder="Search..."><div class="simple-filter-options"></div></div>';
    const fieldSelect = menu.querySelector('.simple-filter-field');
    availableFields.forEach(field => fieldSelect.add(new Option(field.label, field.id)));
    menu.querySelector('.simple-filter-trigger').addEventListener('click', () => {
      const opening = !menu.classList.contains('open'); closeSimpleMenus(menu); menu.classList.toggle('open', opening);
      if (opening) menu.querySelector('.simple-filter-search').focus();
    });
    fieldSelect.addEventListener('change', () => { state.column = fieldSelect.value; state.values = []; renderSimpleCategory(spec, menu); schedulePreview(); });
    menu.querySelector('.simple-filter-search').addEventListener('input', () => renderSimpleCategory(spec, menu));
    renderSimpleCategory(spec, menu);
    return menu;
  }
  function buildLengthFilter() {
    const menu = document.createElement('div');
    menu.className = 'simple-filter-menu simple-length-menu';
    menu.innerHTML = '<button type="button" class="simple-filter-trigger"><span class="simple-filter-label">Length</span></button><div class="simple-filter-popover"><select class="simple-filter-field"><option value="distance">Distance</option><option value="rough_flight_time">Flight time</option></select><label class="simple-range-label"><span>Minimum</span><output></output><input class="simple-length-min" type="range"></label><label class="simple-range-label"><span>Maximum</span><output></output><input class="simple-length-max" type="range"></label></div>';
    const state = simpleFilterState.length = { column: 'distance', min: null, max: null, active: false };
    const trigger = menu.querySelector('.simple-filter-trigger');
    const fieldSelect = menu.querySelector('.simple-filter-field');
    const minInput = menu.querySelector('.simple-length-min');
    const maxInput = menu.querySelector('.simple-length-max');
    const update = (reset) => {
      const column = simpleColumn(state.column);
      const minimum = Math.floor((column && column.minimum) || 0);
      const maximum = Math.ceil((column && column.maximum) || 1);
      if (reset) { state.min = minimum; state.max = maximum; state.active = false; }
      [minInput, maxInput].forEach(input => { input.min = minimum; input.max = maximum; input.step = state.column === 'rough_flight_time' ? '0.25' : '10'; });
      minInput.value = state.min; maxInput.value = state.max;
      menu.querySelector('.simple-range-label output').textContent = state.min + (state.column === 'distance' ? ' nm' : ' hr');
      menu.querySelectorAll('.simple-range-label output')[1].textContent = state.max + (state.column === 'distance' ? ' nm' : ' hr');
      trigger.querySelector('.simple-filter-label').textContent = state.active ? `Length (${state.column === 'distance' ? 'distance' : 'time'})` : 'Length';
    };
    menu.querySelector('.simple-filter-trigger').addEventListener('click', () => { const opening = !menu.classList.contains('open'); closeSimpleMenus(menu); menu.classList.toggle('open', opening); });
    fieldSelect.addEventListener('change', () => { state.column = fieldSelect.value; update(true); schedulePreview(); });
    minInput.addEventListener('input', () => { state.min = Number(minInput.value); if (state.min > state.max) state.max = state.min; state.active = true; update(false); schedulePreview(); });
    maxInput.addEventListener('input', () => { state.max = Number(maxInput.value); if (state.max < state.min) state.min = state.max; state.active = true; update(false); schedulePreview(); });
    update(true);
    return menu;
  }
  function initSimpleFilters() {
    simpleFiltersEl.replaceChildren();
    SIMPLE_FILTER_SPECS.forEach(spec => { const menu = buildSimpleCategory(spec); if (menu) simpleFiltersEl.append(menu); });
    simpleFiltersEl.append(buildLengthFilter());
    const clear = document.createElement('button');
    clear.type = 'button'; clear.className = 'simple-filter-clear'; clear.textContent = 'Clear';
    clear.addEventListener('click', resetFilters);
    simpleFiltersEl.append(clear);
    document.addEventListener('click', event => { if (!simpleFiltersEl.contains(event.target)) closeSimpleMenus(); });
  }
  function simpleFilterChildren() {
    const children = [];
    SIMPLE_FILTER_SPECS.forEach(spec => {
      const state = simpleFilterState[spec.key];
      if (state && state.values.length) children.push({ column: state.column, operator: 'equals', value: state.values, type: 'select', logic: 'AND' });
    });
    const length = simpleFilterState.length;
    if (length && length.active) {
      children.push({ column: length.column, operator: '>=', value: length.min, type: 'number', logic: 'AND' });
      children.push({ column: length.column, operator: '<=', value: length.max, type: 'number', logic: 'AND' });
    }
    return children;
  }
  function currentFilterTree() { return { kind: 'group', logic: 'AND', children: simpleFilterChildren() }; }

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
      // Stars' clickability/rank depend on `airports`, just refreshed above.
      if (sceneryOverlayEnabled) rebuildSceneryMarkers();

      lastAppliedCount = result.count;
      flightsCountEl.textContent = result.count.toLocaleString() + ' flights match';
      previewEl.innerHTML = ''; // the preview now matches what's actually applied
    } catch (err) {
      flightsCountEl.textContent = '';
    }
    if (isMobile) closeAllPanels();
  }

  function resetFilters() {
    SIMPLE_FILTER_SPECS.forEach(spec => {
      const state = simpleFilterState[spec.key];
      if (state) state.values = [];
    });
    const length = simpleFilterState.length;
    if (length) {
      const column = simpleColumn(length.column);
      length.min = Math.floor((column && column.minimum) || 0);
      length.max = Math.ceil((column && column.maximum) || 1);
      length.active = false;
    }
    SIMPLE_FILTER_SPECS.forEach(spec => {
      const menu = simpleFiltersEl.querySelector(`.simple-filter-menu[data-filter="${spec.key}"]`);
      if (menu) renderSimpleCategory(spec, menu);
    });
    const lengthMenu = simpleFiltersEl.querySelector('.simple-length-menu');
    if (lengthMenu) {
      const minInput = lengthMenu.querySelector('.simple-length-min');
      const maxInput = lengthMenu.querySelector('.simple-length-max');
      const outputs = lengthMenu.querySelectorAll('.simple-range-label output');
      minInput.value = length.min; maxInput.value = length.max;
      outputs[0].textContent = length.min + (length.column === 'distance' ? ' nm' : ' hr');
      outputs[1].textContent = length.max + (length.column === 'distance' ? ' nm' : ' hr');
      lengthMenu.querySelector('.simple-filter-trigger').textContent = 'Length';
    }
    closeSimpleMenus();
    updateFilterCountBadge();
    applyFilters();
  }

  document.getElementById('apply').addEventListener('click', applyFilters);
  document.getElementById('reset').addEventListener('click', resetFilters);

  // --- Root Scope Header + consolidated "+ Add" footer (one of each for the whole tree,
  // mirroring what every nested .filter-group gets via createGroup()). ---
  const filtersScopeHeader = document.getElementById('filters-scope');
  wireScopeToggle(filtersScopeHeader, logic => { rootLogic = logic; schedulePreview(); });
  document.getElementById('filters-add-condition').addEventListener('click', () => { addRow(); schedulePreview(); });
  document.getElementById('filters-add-group').addEventListener('click', () => { filters.append(createGroup()); schedulePreview(); });

  // --- Collapse the whole filter tree (Scope Header + rows + add-buttons) down to just
  // the toggle button itself, next to the Match ALL/ANY control. ---
  const filtersCollapseToggle = document.getElementById('filters-collapse-toggle');
  filtersCollapseToggle.addEventListener('click', () => {
    const collapsed = panelEls.explore.classList.toggle('filters-collapsed');
    filtersCollapseToggle.title = collapsed ? 'Expand filters' : 'Collapse filters';
    filtersCollapseToggle.setAttribute('aria-label', filtersCollapseToggle.title);
    filtersCollapseToggle.setAttribute('aria-expanded', String(!collapsed));
  });

  // --- Live density preview: a debounced "what would Apply do right now" estimate shown
  // above the bold last-applied count, so editing a rule gives immediate feedback before
  // the (comparatively expensive) marker-rebuilding Apply click. Stale responses from a
  // superseded request are dropped via a monotonically increasing request id. ---
  const previewEl = document.getElementById('filters-preview');
  let lastAppliedCount = null;
  let previewTimer = null;
  let previewRequestId = 0;

  function schedulePreview() {
    updateFilterCountBadge();
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreviewCount, 350);
  }

  async function runPreviewCount() {
    const myId = ++previewRequestId;
    try {
      const response = await fsatlasFetch('/api/airports?filters=' + encodeURIComponent(JSON.stringify(currentFilterTree())));
      if (myId !== previewRequestId) return; // a newer edit already superseded this request
      const result = await response.json();
      if (myId !== previewRequestId) return;
      const count = result.count || 0;
      const baseline = lastAppliedCount || count || 1;
      const pct = Math.max(2, Math.min(100, Math.round((count / baseline) * 100)));
      previewEl.innerHTML = '~' + count.toLocaleString() + ' flights <span class="preview-label">(preview)</span>' +
        '<div class="preview-bar"><span style="width:' + pct + '%"></span></div>';
    } catch (err) { /* best effort - preview is non-critical */ }
  }

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

    isMobile ? closeAllPanels() : openPanel('explore');

    // The map fills the whole viewport behind the fixed top bar, the rail, and (on
    // desktop) the open panel, so pan once at load to keep the initial view centered in
    // what's actually visible, rather than in the full occluded viewport.
    const topbarH = document.querySelector('.topbar').getBoundingClientRect().height;
    const railW = isMobile ? 0 : document.getElementById('rail').getBoundingClientRect().width;
    const explorePanel = panelEls.explore;
    const panelW = (!isMobile && explorePanel.classList.contains('open')) ? explorePanel.getBoundingClientRect().width : 0;
    const occludedLeft = railW + panelW;
    if (topbarH || occludedLeft) map.panBy([-occludedLeft / 2, -topbarH / 2], { animate: false });

    ensureWorldCoverage();
    initSimpleFilters();
    await applyFilters();

    // Also populates the Explore panel's "jump to saved route"/"apply a saved search"
    // dropdowns, not just the Saved panel's own lists - no need to open it first.
    await loadSavedFlights();
    savedFlightsRaw.forEach(f => savedFlightKeys.add(flightKey(f)));
    await loadSavedSearches();

    loadSceneryData();
  }

  init();
})();
