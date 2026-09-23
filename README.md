# FSAtlas

A lightweight, browser-based tool for visualising real-world flight data on an interactive world map, inspired by [flightconnections](www.flightconnections.com).

---

## Demo

<video controls src="videos/FSAtlas%20Demo.mp4">
	<a href="videos/FSAtlas%20Demo.mp4">Watch the FSAtlas demo</a>
</video>

---

## What Is It?

FSAtlas reads over 170,000 real-world flight records (built from live tracking data) and renders every departure/arrival airport as a colour-coded dot on an interactive map just like [flightconnections](www.flightconnections.com). 

Click an airport to see every flight connected to it, then click a specific destination to drill into the individual flights on that route.

**Key features:**

- **Interactive world map** — every airport in the dataset is plotted on launch, colour-coded by how many non-stop destinations it serves
- **Click-to-explore** — click an airport to see its connections, click a connected airport to drill into that specific route, click again to back out
- **Detailed flight cards** — airline, aircraft type, registration, callsign, airport codes, and date for every flight on a route
- **Flexible column filtering** — build AND/OR filter chains over any column (airline, aircraft type, country, region, distance, flight time, timestamps, and more), with a live example value shown as you pick a column
- **Departure/Arrival Region** — countries are automatically grouped into regions (Africa, Asia, Europe, Oceania, North/Central/South America, Middle East) purely in memory, with no changes to the source data
- **Light/dark mode** — a single toggle re-themes the whole app, including the map, legend, and zoom controls, and remembers your preference
- **Multiple map styles** — Dark Mode / Light Mode (Esri, auto-selected with the theme toggle), Standard (OpenStreetMap), Satellite (Esri), and Hybrid (Google), switched instantly without needing to re-apply filters
- **Collapsible UI** — both the filter list and the airport legend hide away into small pull-tabs so they never get in the way of the map
- **Performance-minded** — airports render on canvas, routes are computed on demand per selection, and filtered map views are cached server-side per session

---

## Installation

These instructions will guide you through installing and running FSAtlas.

### Linux & MacOS

This will guide you through installing FSAtlas as a standalone command using [uv](https://docs.astral.sh/uv/).

#### Prerequisites

[**uv**](https://docs.astral.sh/uv/getting-started/installation/) — installs and runs FSAtlas in an isolated environment without you needing to manage Python versions or virtualenvs yourself:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

#### Installation

Install FSAtlas as a uv tool, which puts a `fsatlas` command onto your `PATH`, runnable from anywhere:

```bash
uv tool install git+https://github.com/Leofric99/fsatlas.git
```

Then run it:

```bash
fsatlas
```

This starts a local web server and opens FSAtlas in your default browser. If it doesn't open automatically, the terminal will print the URL to open manually (e.g. `http://127.0.0.1:PORT`).

Run `fsatlas -h` (or `--help`) at any time to see the command's usage.

To upgrade to the latest version later, use:

```bash
uv tool upgrade fsatlas
```

### Windows

Coming soon — an updated standalone `.exe` is planned for v2.0.0

### Docker

This will guide you through running FSAtlas in a container using [Docker](https://docs.docker.com/get-docker/) — useful if you'd rather not install Python/uv locally, or want to run FSAtlas on a server.

#### Prerequisites

[**Docker**](https://docs.docker.com/get-docker/) with Compose (included in Docker Desktop, or the `docker-compose-plugin` package on Linux).

#### Build and run

Clone the repo, then from the project root, run:

```bash
docker build -t fsatlas:latest .
docker compose up -d
```

The `-d` flag runs it detached (in the background), so the container keeps running after you close the terminal. FSAtlas will be available at `http://<server_ip>:8000`. To stop it, run `docker compose down`.

To rebuild after pulling updates:

```bash
docker build -t fsatlas:latest .
docker compose up -d
```

---

> **Disclaimer:** This project was developed with the assistance of [GitHub Copilot](https://github.com/features/copilot). I am not a front-end developer, so there may well be bugs, rough edges, or unconventional code patterns. Contributions and bug reports are welcome!
