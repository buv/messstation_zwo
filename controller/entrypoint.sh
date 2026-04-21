#!/usr/bin/env bash
set -euo pipefail

SSH_DIR="${HOME}/.ssh"
KEY="${SSH_DIR}/id_ed25519"
PUBKEY_EXPORT="/repo/controller/id_ed25519.pub"

mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

if [[ ! -f "${KEY}" ]]; then
  ssh-keygen -t ed25519 -N "" -C "dfld-controller" -f "${KEY}" >/dev/null
  cp "${KEY}.pub" "${PUBKEY_EXPORT}" 2>/dev/null || true

  cat <<EOF

============================================================
Ein neuer SSH-Schluessel wurde im Controller-Container erzeugt.
Bevor das Deployment laufen kann, muss der Public Key auf den
Raspberry Pi gebracht werden.

Option A (komfortabel, vom Host aus):

    ssh-copy-id -f -i controller/id_ed25519.pub <user>@<pi-ip>

Nach einem SD-Reflash des Pi vorher stalen Hostkey entfernen:

    ssh-keygen -R <pi-ip>

Option B (manuell): Folgenden Key in ~/.ssh/authorized_keys
auf dem Pi eintragen:

$(cat "${KEY}.pub")

Danach ./install_messstation.sh erneut starten.
============================================================
EOF
  exit 0
fi

if [[ ! -f "${SSH_DIR}/config" ]]; then
  cat > "${SSH_DIR}/config" <<EOF
Host *
  StrictHostKeyChecking no
  UserKnownHostsFile /dev/null
  LogLevel ERROR
  IdentityFile ${KEY}
  IdentitiesOnly yes
EOF
  chmod 600 "${SSH_DIR}/config"
fi

exec "$@"
