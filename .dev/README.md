# Development Build

`build-exe.ps1` builds FSAtlas as a standalone Windows executable.

## Usage

From the project root, run:

```powershell
powershell -ExecutionPolicy Bypass -File .dev\build-exe.ps1
```

The script creates a temporary Python environment, installs the project dependencies plus PyInstaller and Pillow, and embeds the flight data, Flask templates/static assets, settings, and tray icon.

The finished executable is written to `FSAtlas.exe` in the project root. It opens the Flask UI in your browser and adds an FSAtlas icon to the system tray. Closing the browser leaves the server available in the tray; right-click the tray icon and choose **Exit** to stop it cleanly. Temporary environments, build directories, generated icon files, and PyInstaller metadata are removed automatically.

Python 3 and the Windows Python launcher (`py`) must be installed. Internet access is required to download packages during the build if they are not already cached.

## Docker release

`docker-release.sh` takes down any running FSAtlas deployment on this machine, builds
the Docker image, pushes it to Docker Hub, then removes what the run just created.

```bash
.dev/docker-release.sh              # take down, build, tag latest + the pyproject.toml version, push, clean up
.dev/docker-release.sh --tag 1.2.0  # push a specific version tag instead
.dev/docker-release.sh --dry-run    # print the docker commands without running them
```

Requires Docker to already be installed and logged in (`docker login`) on the host. It
stops/removes the `fsatlas` container (however it was started - `docker compose` or a
plain `docker run`) *before* building, then removes the two image tags it just built and
any dangling build layers. The `./data` bind mount (settings/saved items/installed
scenery) is a plain host directory, not a Docker-managed volume, so it is left untouched.
If the running container's `installed_scenery.json` only lives in its writable layer
(e.g. it was started without the `./data` volume - see the plain `docker run` example
above), the script copies it onto `./data` first so it isn't lost when the container is
removed. The build always runs with `--no-cache`, so a release can never accidentally
ship stale layers - if `docker push` reports every layer as "Layer already exists", your
checkout genuinely has no code changes since the last release (check `git status`/
`git log` before re-running).
