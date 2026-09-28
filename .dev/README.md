# Development Build

`build-exe.ps1` builds FSAtlas as a standalone Windows executable.

## Usage

From the project root, run:

```powershell
powershell -ExecutionPolicy Bypass -File .dev\build-exe.ps1
```

The script creates a temporary Python environment, installs the project dependencies plus PyInstaller and Pillow, embeds the flight data, map template, settings, and logo, and uses the logo as the executable icon.

The finished executable is written to `FSAtlas.exe` in the project root. Temporary environments, build directories, generated icon files, and PyInstaller metadata are removed automatically.

Python 3 and the Windows Python launcher (`py`) must be installed. Internet access is required to download packages during the build if they are not already cached.

## Docker release

`docker-release.sh` builds the Docker image, pushes it to Docker Hub, then tears the
local deployment back down and removes what the run just created.

```bash
.dev/docker-release.sh              # build, tag latest + the pyproject.toml version, push, clean up
.dev/docker-release.sh --tag 1.2.0  # push a specific version tag instead
.dev/docker-release.sh --dry-run    # print the docker commands without running them
```

Requires Docker to already be installed and logged in (`docker login`) on the host. It
stops/removes the `fsatlas` container (however it was started - `docker compose` or a
plain `docker run`), then removes the two image tags it just built and any dangling
build layers. The `./data` bind mount (settings/saved items) is a plain host directory,
not a Docker-managed volume, so it is left untouched.
