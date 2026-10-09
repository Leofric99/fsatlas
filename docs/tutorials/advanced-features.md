# Advanced Features

Deeper dives into the parts of FSAtlas that go beyond browsing the map and filtering.

## SimBrief Export

FSAtlas can hand a selected flight straight to [SimBrief](https://www.simbrief.com) as a
pre-filled dispatch, without needing an API key — it opens SimBrief's own "New Flight"
page with the origin, destination, aircraft type, registration, and callsign already
filled in from the flight record, using your browser's existing logged-in SimBrief
session to do the rest.

### Setting your Pilot ID

1. Open **Settings** (gear icon on the rail).
2. Enter your SimBrief **Pilot ID or Username** (find it on SimBrief's
   [Account Settings](https://www.simbrief.com/system/profile.php#settings) page).
3. Click **Verify** to confirm it resolves to a real SimBrief account (this uses
   SimBrief's public, key-free "latest OFP" lookup — it doesn't change anything on your
   SimBrief account, it just checks the ID is valid).
4. Click **Save Changes**.

This is optional — the export link itself doesn't require a Pilot ID, verifying one just
lets FSAtlas confirm it's valid before you rely on it.

### Exporting a flight

Click the paper-plane icon next to any flight row (in a route's flight table, or in the
Saved panel's Routes list) to open SimBrief's dispatch page in a new tab, pre-filled with
that flight's details.

## Scenery Overlay

If you fly a flight simulator (MSFS or X-Plane), FSAtlas can scan your installed scenery
add-ons and mark which airports in your dataset you already have custom scenery for —
shown as a small star underneath that airport's dot.

### How the scan works

Everything happens in your browser — FSAtlas never reads an absolute file path or
uploads your scenery files themselves, only the folder names/manifests needed to guess
each package's airport:

- **MSFS/P3D/FSX**-style packages: FSAtlas reads each package's `manifest.json` and looks
  for a 4-letter ICAO code in the title.
- **X-Plane**: recognised via the `Earth nav data/<lat><lon>/` grid-folder naming
  convention for a rough coordinate.
- Anything else falls back to guessing from the folder name itself.

Whatever FSAtlas scans is then matched server-side against your actual flight data's
airport directory — first by exact ICAO code, then (if that fails) by nearest known
airport within 1 mile of the guessed coordinate. Either way, the *official* airport
location is what gets stored and plotted, not the rough guess.

> FSAtlas can't reliably parse compiled `.bgl` scenery files directly — there's no public
> format for that — so folder-name/manifest guessing is the ceiling of what's possible
> here. A handful of packages may come back unresolved; see below for fixing those up
> manually.

### Importing your scenery folder

1. Open **Hangar** (house icon on the rail).
2. Click **Import Scenery Folder…** and pick your simulator's Community folder (MSFS) or
   Custom Scenery folder (X-Plane) in the browser's folder picker.
3. Wait for the scan to finish — FSAtlas shows a progress bar while it works through
   potentially thousands of package folders.
4. The **Matched** / **Unresolved** counts at the top of the panel update, and any
   packages that couldn't be matched automatically are listed below with a search box to
   manually assign them to the correct airport.

Re-importing **replaces** the entire previous scenery list — FSAtlas will warn you before
overwriting if you already have scenery imported.

### Turning the overlay on/off

Open the map-style popover (layered-squares icon, top bar) and use the **Scenery
Overlay** toggle at the bottom. With it on, every airport you have scenery for shows a
star instead of (or underneath) its regular dot; with it off, your scenery data stays
imported but isn't drawn.

## Filtering in depth

### Chip multi-select vs. range filters

Airline/Airports/Aircraft/Location use the same **chip** input: type to narrow a long
list of distinct values (airlines, airport codes/names, countries...), click to add a
chip, and the category is satisfied if a flight matches *any* chip you've added (switch
to **is not** to instead exclude every chip you've added). **Length** is the one
exception — it's a plain min/max range over either Distance (nm) or Flight Time (hours),
not a value list.

### How categories combine

All five categories (and any active Length range) combine with **AND** — a flight has to
match every category you've configured, not just one. Within a single category, multiple
chips are combined with **OR** ("is any of these").

### Self-loop routes

If you filter down to an airport that only connects to itself in your dataset (e.g. local
sightseeing flights with the same departure and arrival airport), FSAtlas automatically
selects that self-loop as the "destination" so you still see its flight table, instead of
leaving you on an empty fan-out view.

## Mobile differences

FSAtlas is fully usable on a phone-sized screen, with a few deliberate differences from
desktop:

- The rail becomes a **bottom tab bar**, and panels slide up from the bottom instead of
  in from the left.
- Filter categories stack as full-width rows instead of a horizontal scroll strip, and
  each one's picker opens as a large floating card (with its own **×** close button) so
  there's room to actually use the search box and value list.
- Applying filters automatically collapses the Filters section (instead of fully closing
  the panel), so you can see your result count/preview immediately without losing your
  place.
