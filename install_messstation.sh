#!/usr/bin/env bash
# Example: parses --mode (mini|full) and an optional positional IP (defaults to localhost)

set -euo pipefail

MODE="mini"
HOST="localhost"
ORIG_ARGS=("$@")

usage() {
  cat <<EOF
Usage: $0 [--mode MODE] [host_ip]

  --mode MODE   Optional. "mini" (default) or "full".
  host_ip       Optional positional IP address (default: localhost).
  -h, --help    Show this help.
EOF
}

# parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0;;
    --mode)
      if [[ -z "${2:-}" || "${2:0:1}" == "-" ]]; then
        echo "Error: --mode requires a value" >&2; usage; exit 1
      fi
      MODE="$2"; shift;;
    --mode=*)
      MODE="${1#*=}";;
    --*)
      echo "Unknown option: $1" >&2; usage; exit 1;;
    *)
      # first non-option becomes HOST
      if [[ "${HOST}" == "localhost" ]]; then
        HOST="$1"
      else
        echo "Error: multiple positional arguments: ${HOST} and $1" >&2; usage; exit 1
      fi
      ;;
  esac
  shift
done

# validate mode
if [[ "${MODE}" != "mini" && "${MODE}" != "full" ]]; then
  echo "Error: invalid mode '${MODE}'. Allowed: mini, full" >&2; usage; exit 1
fi

# Re-exec inside controller container for remote deployments, so every
# colleague gets the same pinned Ansible + clean HOME, independent of
# what's installed on the host. Bypass with DFLD_NO_CONTAINER=1.
if [[ "${DFLD_IN_CONTROLLER:-}" != "1" \
   && "${DFLD_NO_CONTAINER:-}" != "1" \
   && "${HOST}" != "localhost" ]]; then

  # Pick a container runtime. Prefer whatever the user specifies; else
  # auto-detect (docker if reachable, podman as fallback).
  RUNTIME="${DFLD_CONTAINER_RUNTIME:-}"
  if [[ -z "${RUNTIME}" ]]; then
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      RUNTIME=docker
    elif command -v podman >/dev/null 2>&1; then
      RUNTIME=podman
    else
      echo "Error: neither docker (usable) nor podman found. Install one, or set DFLD_NO_CONTAINER=1." >&2
      exit 1
    fi
  fi

  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  CONTROLLER_DIR="${SCRIPT_DIR}/controller"
  TAG="$(cat "${CONTROLLER_DIR}/Dockerfile" "${CONTROLLER_DIR}/entrypoint.sh" | sha1sum | cut -c1-12)"
  IMAGE="dfld-controller:${TAG}"

  if ! "${RUNTIME}" image inspect "${IMAGE}" >/dev/null 2>&1; then
    echo "==> Building controller image ${IMAGE} via ${RUNTIME} (one-time)..."
    "${RUNTIME}" build -t "${IMAGE}" "${CONTROLLER_DIR}"
  fi

  TTY_FLAGS="-i"
  [[ -t 0 ]] && TTY_FLAGS="-it"

  # If dfld.yml is a symlink pointing outside the repo, the link dangles
  # inside the container. Mount the target at the path the symlink resolves
  # to (in container terms) so the link works naturally.
  EXTRA_MOUNTS=()
  DFLD_YML="${SCRIPT_DIR}/dfld.yml"
  if [[ -L "${DFLD_YML}" ]]; then
    LINK_TARGET_REAL="$(readlink -f "${DFLD_YML}")"
    case "${LINK_TARGET_REAL}" in
      "${SCRIPT_DIR}"/*) : ;;
      *)
        LINK_TARGET_RAW="$(readlink "${DFLD_YML}")"
        if [[ "${LINK_TARGET_RAW}" = /* ]]; then
          CONTAINER_PATH="${LINK_TARGET_RAW}"
        else
          CONTAINER_PATH="$(realpath -m "/repo/${LINK_TARGET_RAW}")"
        fi
        EXTRA_MOUNTS+=(-v "${LINK_TARGET_REAL}:${CONTAINER_PATH}:ro")
        ;;
    esac
  fi

  exec "${RUNTIME}" run --rm ${TTY_FLAGS} \
    --security-opt label=disable \
    -v "${SCRIPT_DIR}":/repo \
    -v dfld-controller-ssh:/root/.ssh \
    "${EXTRA_MOUNTS[@]}" \
    -e DFLD_IN_CONTROLLER=1 \
    "${IMAGE}" /repo/install_messstation.sh "${ORIG_ARGS[@]}"
fi

if [[ "${HOST}" == "localhost" ]]; then
  HOST_ARG="--connection=local -i 127.0.0.1,"
else
  HOST_ARG="-i ${HOST},"
fi
echo "Installing in ${MODE} mode on host: ${HOST}"

# check if role "geerlingguy.docker" is installed
if ! ansible-galaxy list --roles-path roles 2> /dev/null | grep -q 'geerlingguy.docker'; then
  echo "Installing Ansible roles from Ansible Galaxy..."
  ansible-galaxy install -p roles -r requirements.yml
fi  

ansible-playbook ./install_messstation.yml --extra-vars deployment_mode=${MODE} ${HOST_ARG} -i inventory.yml --ssh-common-args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'
