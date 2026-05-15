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

# Pfad 1: native Go vorhanden (z.B. im Ansible-Controller-Container, oder
# auf einer Dev-Workstation mit Go installiert). Schneller, kein
# podman/docker noetig, funktioniert auch in Container-in-Container-
# Setups (Ansible-Run aus controller-image heraus).
if command -v go >/dev/null 2>&1; then
    echo "==> Building dfld-status (linux/arm64) natively via $(go version | awk '{print $3}')..."
    mkdir -p build
    if [[ ! -f go.sum ]]; then
        echo "==> go.sum fehlt — go mod download..."
        go mod download
    fi
    CGO_ENABLED=0 GOOS=linux GOARCH=arm64 \
        go build -ldflags="-s -w" -trimpath -o build/dfld-status .
    chmod +x build/dfld-status
    ls -lh build/dfld-status
    file build/dfld-status 2>/dev/null || true
    echo "==> Done. Binary: tools/dfld-status/build/dfld-status"
    exit 0
fi

# Pfad 2: kein Go auf dem Host — Build laeuft im golang-Container via
# podman/docker. Klassischer Pfad fuer Dev-Setups ohne Go-Toolchain.
RUNTIME="${DFLD_STATUS_RUNTIME:-}"
if [[ -z "$RUNTIME" ]]; then
    if   command -v podman >/dev/null 2>&1; then RUNTIME=podman
    elif command -v docker >/dev/null 2>&1; then RUNTIME=docker
    else
        echo "Fehler: weder go noch podman/docker gefunden." >&2; exit 1
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

mkdir -p build
# Container starten, Binary aus dem Image kopieren, weg.
CID=$("$RUNTIME" create "$IMAGE_TAG" /bin/true)
"$RUNTIME" cp "$CID:/out/dfld-status" ./build/dfld-status
"$RUNTIME" rm -f "$CID" >/dev/null

chmod +x build/dfld-status
ls -lh build/dfld-status
file build/dfld-status 2>/dev/null || true

echo "==> Done. Binary: tools/dfld-status/build/dfld-status"
