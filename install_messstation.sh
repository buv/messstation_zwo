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
