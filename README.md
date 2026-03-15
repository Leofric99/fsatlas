# FSAtlas

Real-world is a desktop application for visualising live flight data on an interactive world map inspired by flightconnections. Built with Python, PyQt6, and Leaflet.js.

---

## What Is It?

FSDispatch reads the included CSV file containing flight data (e.g. captured from live flight tracking services) and displays every flight's departure and arrival airport as a coloured dot on an interactive world map.

**Key features:**

- **Instant world map** — all airports from your CSV are shown immediately on launch, no filtering required.
- **Airport colour coding by traffic volume:**
  - 🟢 **Green** — Major / high-frequency airports
  - 🟡 **Yellow** — Medium-frequency airports
  - 🔴 **Red** — Low-frequency / small airports
- **Click-to-explore interaction:**
  - Click an airport to load all flights connected to it (departures *and* arrivals).
  - Click a second connected airport to drill down and see only the flights between those two airports.
  - Click the source airport again to return to all connections.
  - Click anywhere on the map to deselect.
- **Detailed flight cards** — for any airport pair, each flight is displayed as a card showing:
  - Flight Number
  - Airline
  - Callsign
  - Aircraft Registration
  - Aircraft Type (full name) & ICAO type code
  - Departure & Arrival IATA codes
  - Departure & Arrival ICAO codes
- **Filters** — narrow the data shown on the map by any combination of filter criteria.
- **Multiple map tile styles** — Standard (OpenStreetMap), Satellite (Esri), and Hybrid (Google).
- **Dark / Light mode** toggle.
- **Performance-first design** — only airport locations are loaded into the map on initial render. Flight route data is fetched on demand when you click an airport, so the map remains responsive even with tens of thousands of flights in the dataset.

---

## Requirements

- Python 3.10+
- Pip
- The packages listed in `requirements.txt`

---

## Installation & Running

### 1. Clone the repository

```bash
git clone https://github.com/Leofric99/fsdispatch.git
cd fsdispatch
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> It is recommended to use a virtual environment:
> ```bash
> python3 -m venv .venv
> source .venv/bin/activate
> pip install -r requirements.txt
> ```

#### 3. Run the application

```bash
python3 -m run
```

## Filtering

The filter bar at the top of the window lets you narrow the data shown on the map. Filters can be applied on their own or combined.

| Filter | Type | Description |
|--------|------|-------------|
| **Airline** | Text (contains / equals / starts with / ends with) | Filter by airline operator name |
| **Aircraft Type** | Text | Filter by aircraft type, e.g. `A320`, `B737` |
| **Departure Country** | Dropdown (multi-select) | Show only flights departing from selected countries |
| **Arrival Country** | Dropdown (multi-select) | Show only flights arriving into selected countries |
| **Distance (km)** | Numeric (`>`, `<`, `=`, `>=`, `<=`) | Filter by route distance |
| **Flight Time (hours)** | Numeric | Filter by approximate flight duration |

Click **Apply Filters & Show Map** to update the map. Click **Reset Filters** to return to the full unfiltered dataset.

---

## Dependencies

These dependancies are installed from the requirements.txt file as per the above instructions.

| Package | Purpose |
|---------|---------|
| `PyQt6` | Desktop GUI framework |
| `PyQt6-WebEngine` | Embedded browser for the Leaflet map |
| `pandas` | CSV loading and data filtering |
| `folium` | Map utility (retained as dependency) |
| `jinja2` | Templating (transitive dependency) |
