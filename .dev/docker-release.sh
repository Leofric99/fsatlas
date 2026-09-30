#!/usr/bin/env bash
# Takes down any running FSAtlas deployment on this machine (preserving its
# installed_scenery.json onto the host bind mount first, in case that container wasn't
# started with the ./data volume - see README's plain `docker run` example), builds the
# Docker image, pushes it to Docker Hub, then removes what this script itself built
# (images) - leaving the host clean, with the new image published.
#
# Usage:
#   .dev/docker-release.sh              # take down, build, tag latest + current version, push, clean up
#   .dev/docker-release.sh --tag 1.2.0  # override the version tag (default: read from pyproject.toml)
#   .dev/docker-release.sh --dry-run    # print the commands without running them
#
# Assumes docker (with the `compose` plugin) is installed and already logged in to
# Docker Hub (`docker login`) on this host.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${DOCKER_IMAGE:-leofric99/fsatlas}"
CONTAINER_NAME="fsatlas"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yml"
DRY_RUN=0
VERSION=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag) VERSION="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION="$(grep -m1 '^version' "$REPO_ROOT/pyproject.toml" | sed -E 's/version = "(.*)"/\1/')"
fi
if [[ -z "$VERSION" ]]; then
  echo "Could not determine a version tag from pyproject.toml - pass one with --tag." >&2
  exit 1
fi

run() {
  echo "+ $*"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    "$@"
  fi
}

echo "Image:   $IMAGE"
echo "Tags:    latest, $VERSION"
echo "Dry run: $([[ $DRY_RUN -eq 1 ]] && echo yes || echo no)"
echo

if [[ "$DRY_RUN" -eq 0 ]] && ! docker info >/dev/null 2>&1; then
  echo "Docker does not appear to be running/reachable. Start Docker and try again." >&2
  exit 1
fi

echo "--- Checking for a running '$CONTAINER_NAME' deployment ---"
CONTAINER_RUNNING=0
if [[ "$DRY_RUN" -eq 0 ]]; then
  if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || true)" == "true" ]]; then
    CONTAINER_RUNNING=1
  fi
else
  echo "(dry run - skipping the actual check for a running '$CONTAINER_NAME' container)"
fi

if [[ "$CONTAINER_RUNNING" -eq 1 ]]; then
  echo "Found a running '$CONTAINER_NAME' container."
  # installed_scenery.json always lives at /data inside the container (FSATLAS_DATA_DIR,
  # set in the Dockerfile) regardless of whether that container was started with the
  # ./data bind mount (docker-compose.yml) or a plain `docker run` with no volume at all
  # (see README's manual instructions) - in the latter case the file only exists in the
  # container's writable layer and `docker rm` below would destroy it. Copying it onto the
  # host's bind-mount source directory first means it's picked up automatically the next
  # time the container is (re)started with the volume mounted, preserving it across
  # image updates either way.
  run mkdir -p "$REPO_ROOT/data"
  if docker exec "$CONTAINER_NAME" test -f /data/installed_scenery.json 2>/dev/null; then
    echo "Preserving its installed_scenery.json onto the host bind mount before taking it down."
    run docker cp "$CONTAINER_NAME:/data/installed_scenery.json" "$REPO_ROOT/data/installed_scenery.json"
  fi
fi

echo
echo "--- Bringing any running deployment down ---"
# Handles both a docker-compose based deployment and one started with a plain
# `docker run --name fsatlas ...` (see README's manual Docker instructions) - whichever
# is actually running, the other command below is a harmless no-op.
# --volumes only removes volumes/networks docker-compose itself created (there are none
# named in docker-compose.yml); the persisted ./data bind mount is a plain host directory,
# not a Docker-managed volume, so it is untouched and your settings/saved items survive.
if [[ -f "$COMPOSE_FILE" ]]; then
  run docker compose -f "$COMPOSE_FILE" down --volumes --remove-orphans || true
fi
run docker rm -f "$CONTAINER_NAME" || true

echo
echo "--- Building ---"
# --no-cache: guarantees every layer is rebuilt from the current working tree. Without
# it, a COPY step can occasionally get reused from Docker's build cache even when you
# meant to ship new code (e.g. if you build again before actually committing/pulling
# the intended changes) - "docker push" then reports every layer as "Layer already
# exists" and the deployed container silently keeps running the old code.
run docker build --no-cache -t "$IMAGE:latest" -t "$IMAGE:$VERSION" "$REPO_ROOT"

echo
echo "--- Pushing to Docker Hub ---"
run docker push "$IMAGE:latest"
run docker push "$IMAGE:$VERSION"

echo
echo "--- Removing images built by this run ---"
run docker rmi -f "$IMAGE:latest" "$IMAGE:$VERSION" || true
# Dangling intermediate build layers left behind by the build above (not other unrelated
# images on the host).
run docker image prune -f || true

echo
echo "Done. $IMAGE:latest and $IMAGE:$VERSION are on Docker Hub; the local deployment"
echo "that was running before this script started, and the images it just built, have"
echo "been removed."
