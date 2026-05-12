# shellcheck shell=bash
# /etc/profile.d/dfld-firstboot.sh
#
# Auto-launch dfld-config beim interaktiven Login wenn die Station noch
# nicht voll konfiguriert ist — analog zum raspi-config-First-Boot-Workflow.
#
# Trigger:
#   1. dfld.yml fehlt oder Pflichtfelder ungueltig (`dfld-config validate` exit 1)
#   2. Freeze-Marker aktiv (/boot/firmware/dfld-freeze) — Station gerade
#      von SD-Klon, Connectors gestoppt, Operator soll konfigurieren bevor er aktiviert
#
# Laeuft nur bei interaktivem TTY-Login + nicht im SSH-Forward / SCP / etc.

# Nicht in non-interactive Shells (cron, ssh-tunnel, scp)
[ -t 0 ] || return 0
[ -t 1 ] || return 0
# Nicht waehrend Login von Service-Accounts
case "$USER" in
    root|dfld) : ;;
    *) return 0 ;;
esac

_dfld_needs_config() {
    if [ -f /boot/firmware/dfld-freeze ]; then
        echo "*** FREEZE AKTIV — Connectors gestoppt, Marker /boot/firmware/dfld-freeze ***"
        return 0
    fi
    if ! /usr/local/bin/dfld-config validate >/dev/null 2>&1; then
        echo "*** Stations-Konfiguration unvollstaendig oder ungueltig ***"
        return 0
    fi
    return 1
}

if _dfld_needs_config; then
    printf 'Soll dfld-config jetzt gestartet werden? [Y/n] '
    read -r _answer
    case "$_answer" in
        ""|y|Y|yes|YES|j|J|ja|JA)
            sudo /usr/local/bin/dfld-config
            ;;
        *)
            echo "Spaeter aufrufen mit: sudo dfld-config"
            ;;
    esac
fi
unset -f _dfld_needs_config 2>/dev/null
unset _answer
