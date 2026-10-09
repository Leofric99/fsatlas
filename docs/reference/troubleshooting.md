# Troubleshooting

## The map is empty / shows no airports

- Make sure `run/database/flights.csv` exists and has at least one data row (not just
  the header) — see [Flight Data Schema](flight-data-schema.md). FSAtlas starts
  successfully either way; it just has nothing to plot.
- If you're sure the file is populated, check your filters — an overly narrow filter
  combination that matches zero flights will also show an empty map. Click **Reset**.
- If a *huge* fraction of your dataset is missing, check the "Discarded N flight(s)
  exceeding 25 hours" line printed on startup — rows with an implausible estimated flight
  time longer than that are excluded by design, see
  [Flight Data Schema](flight-data-schema.md#rows-that-get-discarded).

## Browser didn't open automatically

This is expected in a couple of cases: with `--debug`/`FSATLAS_DEBUG` set, inside Docker,
or on a headless machine (there's no display to open a browser on). Copy the URL printed
in the terminal (e.g. `http://127.0.0.1:5050`) into your browser manually, or add
`--no-browser`/`FSATLAS_NO_BROWSER` to stop FSAtlas from even trying.

## Port already in use

Another process (or another FSAtlas instance) is already bound to the port. Either stop
that process, or pick a different port:

```bash
python -m run --port 5151
# or
FSATLAS_PORT=5151 python -m run
```

For Docker, change the host-side port in your `-p`/`ports:` mapping instead (the
container-internal port set by `FSATLAS_PORT` in the Dockerfile doesn't need to change).

## My settings/bookmarks disappeared after recreating the Docker container

By default `settings.json`/`saved_items.json`/`installed_scenery.json` live *inside* the
container and are lost whenever it's removed/recreated (including on image updates). Bind
-mount `/data` to a host directory to persist them — see
[Installation → Data persistence](../getting-started/installation.md#data-persistence).
This can't recover data from a container that's already been removed without that mount,
only prevent it happening again.

## I ran `uv run fsatlas` and the UI looks completely different from this wiki's screenshots

That's expected — `uv run fsatlas` (the packaged `fsatlas` command) currently launches
FSAtlas's original, classic interface, while this wiki documents the newer dashboard that
Docker and `python -m run` launch. Both read/write the same flight data and settings; see
the note on the [homepage](../index.md#project-layout-at-a-glance) for why, and run
`uv run python -m run --no-browser` instead if you want the newer UI without Docker.

## The scenery folder picker doesn't do anything

Scenery import uses the browser's native folder-picker API
(`<input webkitdirectory>`), which needs a Chromium-based browser (Chrome, Edge, Brave,
etc.) — it isn't supported the same way in all browsers. Firefox supports it but can be
slow to list filenames in a very large folder; give it time before assuming it's stuck.

## SimBrief "Verify" fails for a Pilot ID I'm sure is correct

Verification calls SimBrief's own public lookup endpoint over the internet — if your
server/container can't reach `simbrief.com` (e.g. no outbound internet access, or a
restrictive firewall/proxy), verification will fail even for a valid ID. Exporting a
flight (the paper-plane icon) opens a link in *your* browser instead, so it isn't
affected by the server's own network access.

## Filters panel is stuck on "Loading…"

This means the `/api/airports` request hasn't come back yet — normal for a moment on a
very large dataset or a slow disk, but if it never resolves, check your server's console/
logs for a Python traceback (most likely cause: a malformed or unreadable
`flights.csv`).

## Still stuck?

Open a [GitHub Issue](https://github.com/Leofric99/fsatlas/issues) with what you tried,
what you expected, and what happened instead — see
[Contributing](../community/contributing.md) for the broader expectations around
reporting bugs.
