#!/usr/bin/env bash
set -euo pipefail

IMAGE="mx-review-service"
FORCE_BUILD=0
[[ "${1:-}" == "--build" ]] && FORCE_BUILD=1

# Lees MX_LOCAL_REPO uit .env
REPO_PATH=$(grep -E '^MX_LOCAL_REPO=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")

if [[ -z "$REPO_PATH" ]]; then
  echo "Fout: MX_LOCAL_REPO is niet ingesteld in .env"
  exit 1
fi

if [[ $FORCE_BUILD -eq 1 ]] || ! podman image exists "$IMAGE"; then
  echo "Building Podman image..."
  podman build -q -t "$IMAGE" .
else
  echo "Image '$IMAGE' bestaat al — skip build (gebruik --build om te forceren)"
fi

echo "Running test_local.py in container..."
podman run --rm \
  --env-file .env \
  -e MX_LOCAL_REPO=/repo \
  -v "${REPO_PATH}:/repo:ro" \
  "$IMAGE" \
  python test_local.py
