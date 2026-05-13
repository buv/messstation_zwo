#!/usr/bin/env bash
# build.sh — Cross-Compile von dfld-status nach linux/arm64 (Pi 5 / 4 / Zero 2W).
#
# Developer-Convenience-Skript: laeuft komplett im Container, kein Go-
# Toolchain auf dem Host noetig. Output: ./build/dfld-status (statisches
# Binary, ~3-4 MB). Der build/-Subdir ist gitignored.
#
# Production-Deployment laeuft ueber die Ansible-Rolle, die ihren eigenen
# Build-Cycle anstoesst. Daher legen wir das Artefakt bewusst nicht in
# tools/dfld-status/ direkt ab — der Source-Toplevel soll frei von
# Build-Outputs bleiben.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RUNTIME="${DFLD_STATUS_RUNTIME:-}"
if [[ -z "$RUNTIME" ]]; then
    if   command -v podman >/dev/null 2>&1; then RUNTIME=podman
    elif command -v docker >/dev/null 2>&1; then RUNTIME=docker
    else
        echo "Fehler: weder podman noch docker gefunden." >&2; exit 1
    fi
fi

echo "==> Building dfld-status (linux/arm64) via $RUNTIME..."

# go.sum erzeugen falls nicht da (erster build).
if [[ ! -f go.sum ]]; then
    echo "==> go.sum fehlt — go mod download im Container..."
    "$RUNTIME" run --rm -v "$PWD":/src:Z -w /src --security-opt label=disable \
        golang:1.24-bookworm go mod download
fi

# Build ueber temporaere Container-Stage; das Binary kopieren wir mit
# `docker cp` raus statt einen Bind-Mount-Output zu nehmen (vermeidet
# UID/SELinux-Quirks).
IMAGE_TAG="dfld-status-build:latest"
"$RUNTIME" build -q -t "$IMAGE_TAG" --target build -f Dockerfile . >/dev/null

# Container starten, Binary aus dem Image kopieren, weg.
mkdir -p build
CID=$("$RUNTIME" create "$IMAGE_TAG" /bin/true)
"$RUNTIME" cp "$CID:/out/dfld-status" ./build/dfld-status
"$RUNTIME" rm -f "$CID" >/dev/null

chmod +x build/dfld-status
ls -lh build/dfld-status
file build/dfld-status 2>/dev/null || true

echo "==> Done. Binary: tools/dfld-status/build/dfld-status"
