#!/usr/bin/env bash
# Builds the FSAtlas Docker image, pushes it to Docker Hub, then tears the local
# deployment back down and removes what this script created (container, its volumes,
# and the images it just built) - leaving the host clean, with the new image published.
#
# Usage:
#   .dev/docker-release.sh              # build, tag latest + current version, push, clean up
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

echo "--- Building ---"
run docker build -t "$IMAGE:latest" -t "$IMAGE:$VERSION" "$REPO_ROOT"

echo
echo "--- Pushing to Docker Hub ---"
run docker push "$IMAGE:latest"
run docker push "$IMAGE:$VERSION"

echo
echo "--- Bringing the running deployment down ---"
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
echo "--- Removing images built by this run ---"
run docker rmi -f "$IMAGE:latest" "$IMAGE:$VERSION" || true
# Dangling intermediate build layers left behind by the build above (not other unrelated
# images on the host).
run docker image prune -f || true

echo
echo "Done. $IMAGE:latest and $IMAGE:$VERSION are on Docker Hub; local container, its"
echo "compose-managed volumes/networks, and the images just built have been removed."
