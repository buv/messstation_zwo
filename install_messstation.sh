#!/usr/bin/env bash
# Example: parses --mode (mini|full) and an optional positional IP (defaults to localhost)

set -euo pipefail

MODE="mini"
HOST="localhost"

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
      if [[ "$HOST" == "localhost" ]]; then
        HOST="$1"
      else
        echo "Error: multiple positional arguments: $HOST and $1" >&2; usage; exit 1
      fi
      ;;
  esac
  shift
done

# validate mode
if [[ "$MODE" != "mini" && "$MODE" != "full" ]]; then
  echo "Error: invalid mode '$MODE'. Allowed: mini, full" >&2; usage; exit 1
fi

if [[ "$HOST" == "localhost" ]]; then
  HOST_ARG="--connection=local -i 127.0.0.1,"
else
  HOST_ARG="-i $HOST,"
fi

if [[ "$MODE" == "mini" ]]; then
  echo "Installing in mini mode on host: $HOST"
  ansible-playbook ./mini.yml $HOST_ARG -i inventory.yml
elif [[ "$MODE" == "full" ]]; then
  echo "Installing in full mode on host: $HOST"
  ansible-playbook ./full.yml $HOST_ARG -i inventory.yml
else
  echo "Error: unknown mode '$MODE'" >&2; exit 1
fi