# Installation

FSAtlas can be run in a few different ways depending on your platform and whether you
want to self-host it as a long-running service. Pick whichever fits:

- [Linux & macOS (via `uv`)](#linux-macos-via-uv)
- [Windows](#windows)
- [Docker](#docker)

All of them read the same flight data file and write to the same settings/bookmarks
files, so you can switch between methods later without losing anything — see
[Flight Data](#before-you-start-flight-data) and [Data persistence](#data-persistence)
below.

## Before you start: Flight Data

FSAtlas does not ship with a bundled flight dataset — you supply your own. Before running
the app for the first time, place a CSV file at:

```
run/database/flights.csv
```

The full column list and an example row are documented in
[Flight Data Schema](../reference/flight-data-schema.md). A missing or empty file won't
crash the app, but the map will have nothing to show until it's in place.

## Linux & macOS (via `uv`)

The quickest way to run FSAtlas natively on Linux or macOS.

### Prerequisites

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), which manages an
isolated Python environment and makes the `fsatlas` command available:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Install & run

Clone the repository and move into it:

```bash
git clone https://github.com/Leofric99/fsatlas.git
cd fsatlas
```

Add your [flight data](#before-you-start-flight-data), then let `uv` create the
environment, install dependencies, and start the app in one go:

```bash
uv run fsatlas
```

This opens FSAtlas in your default browser automatically. If it doesn't, copy the URL
printed in the terminal (something like `http://127.0.0.1:PORT`) into your browser
manually.

> `uv run fsatlas` launches FSAtlas's original desktop-style interface (the one with a
> system-tray-style single-instance lock and a toolbar-over-map layout) rather than the
> newer dashboard shown throughout the rest of this wiki — both read/write the same data,
> see the note in [the homepage](../index.md#project-layout-at-a-glance). To run the
> newer interface locally instead of via Docker, use:
> ```bash
> uv run python -m run --no-browser
> ```
> and open the printed URL yourself.

## Windows

Two options, depending on whether you want a standalone app or are comfortable with a
terminal:

### Option A — Packaged executable

Download the latest `FSAtlas.exe` from the
[GitHub Releases page](https://github.com/Leofric99/fsatlas/releases). Place your
[flight data](#before-you-start-flight-data) file next to it (or wherever the release
notes for that version say to), then double-click to run it. It starts a local web
server, opens your browser, and adds a small FSAtlas icon to the system tray — right-click
that icon and choose **Exit** to stop the server when you're done.

### Option B — Run from source with `uv`

Install [uv for Windows](https://docs.astral.sh/uv/getting-started/installation/#standalone-installer),
then follow the same steps as [Linux & macOS](#linux-macos-via-uv) above from a PowerShell
or Command Prompt window.

## Docker

Best if you want FSAtlas running continuously as a background service, or accessible
from other devices on your network.

### Prerequisites

[Docker](https://docs.docker.com/get-docker/) — included with Docker Desktop, or
available from your Linux distribution's package manager.

### Option A — `docker-compose` with the published image

The repository's `docker-compose.yml` references the maintainer's published image
(`leofric99/fsatlas:latest`) and sets up data persistence for you. From a clone of the
repository:

```bash
docker compose up -d
```

By default this exposes FSAtlas on **http://localhost:8777**. Your flight data needs to
already be baked into whichever image you use (see Option B if you need to supply your
own data file via a custom build), or supplied via the volume mount described below.

### Option B — Build the image yourself

Building locally bakes your own `run/database/flights.csv` into the image, which is the
simplest way to self-host your own dataset:

```bash
git clone https://github.com/Leofric99/fsatlas.git
cd fsatlas
# add your flight data to run/database/flights.csv first, see above
docker build -t fsatlas .
docker run -d --name fsatlas -p 8777:8000 fsatlas
```

The container is now reachable at `http://<server_ip>:8777`.

To stop and remove it later:

```bash
docker stop fsatlas
docker rm fsatlas
```

### Data persistence

Settings and saved items (`settings.json` / `saved_items.json`, plus `installed_scenery.json`
if you use the [scenery overlay](../tutorials/advanced-features.md#scenery-overlay)) live
inside the container by default and are **lost** when it's recreated or updated. To
persist them on the host, bind-mount a directory to `/data`:

```yaml
volumes:
  - ./data:/data
```

(this is already set up in the repository's `docker-compose.yml` — just make sure `./data`
exists, or let Docker create it). Both files are created there automatically with
sensible defaults on first start if they don't already exist.

## Updating

- **`uv run fsatlas`**: `git pull` inside your clone, then run `uv run fsatlas` again —
  `uv` re-resolves dependencies automatically if they've changed.
- **Docker (published image)**: `docker compose pull && docker compose up -d` (your
  `./data` bind mount keeps your settings/bookmarks).
- **Docker (self-built image)**: re-run the `docker build`/`docker run` steps above after
  pulling the latest source and (if needed) updating `run/database/flights.csv`.

## Next steps

Continue to the [Quickstart](quickstart.md) for a guided first run, or jump to
[Basic Usage](../tutorials/basic-usage.md) once the app is open in your browser.
