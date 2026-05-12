#!/usr/bin/env bash
# dfld-config — Bash + whiptail (raspi-config-Stil mit gruppierten Sub-Screens)
# Interaktive TUI fuer dfld.yml. Volle 18-Felder-Maske analog Schema.
#
# Laeuft sowohl auf dem Controller-Host (vor dem Deployment, schreibt
# ./dfld.yml im aktuellen Verzeichnis) als auch direkt auf der Pi
# (post-deployment, schreibt /boot/firmware/dfld.yml).
#
# WICHTIG: kein `set -e` — wuerde non-zero Exits aus edit_X-Funktionen
# durch case-Branches in while-Loop-Bodies propagieren und das Script
# beim ersten ESC/Cancel killen. `set -u` reicht fuer unset-var-Safety.
set -uo pipefail

# Locale-Default fuer Pi-Bare-Run (im Container vom Dockerfile gesetzt).
# C.UTF-8 ist via glibc immer da, kein locale-gen noetig.
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"

# === Sub-command Dispatch ===
# Default: TUI. validate-Modus laeuft non-interactive (kein whiptail),
# exit 0 wenn alle Pflichtfelder gueltig, exit 1 sonst + Stderr-Liste.
# Wird von dfld-connectors.service ExecStartPre genutzt.
VALIDATE_MODE=false
if [[ "${1:-}" == "validate" ]]; then
    VALIDATE_MODE=true
    # validate hat einen fixen Default-Pfad (Pi-internal). Optionales
    # 2. Argument ueberschreibt das (fuer CI-Tests, Controller-Lauf, etc.).
    # Default haerter als die TUI-Detection, weil systemd ExecStartPre
    # nicht von writable-Boot-Partition abhaengen soll.
    export DFLD_CONFIG="${2:-/boot/firmware/dfld.yml}"
fi

# Output-Pfad bestimmen:
#   1. Env-Var DFLD_CONFIG (höchste Priorität, z.B. für Tests)
#   2. /boot/firmware/dfld.yml wenn das Verzeichnis schreibbar ist
#      (= Skript läuft direkt auf der Pi, post-deploy-Modus)
#   3. ./dfld.yml im aktuellen Verzeichnis
#      (= Skript läuft am Controller pre-deploy; Ansible nimmt
#       die Datei beim nächsten install_messstation.sh-Lauf auf)
if [[ -n "${DFLD_CONFIG:-}" ]]; then
    OUT="$DFLD_CONFIG"
elif [[ -d /boot/firmware ]]; then
    # Pi-internal: /boot/firmware/dfld.yml ist IMMER der korrekte Pfad.
    # Falls nicht-root: READ_ONLY-Modus (Werte sind world-readable),
    # statt Silent-Fallback auf Schatten-File.
    OUT="/boot/firmware/dfld.yml"
else
    # Controller-Host (kein Pi-OS): ./dfld.yml im aktuellen Verzeichnis.
    # Ansible nimmt die Datei beim naechsten install_messstation.sh-Lauf auf.
    OUT="./dfld.yml"
fi
OUT="$(realpath -m "$OUT")"

# READ_ONLY-Modus: Pi-User ohne sudo darf Werte ansehen aber nicht aendern.
# (Wartung-Submenu hat zusaetzliche eigene root-Pruefung wegen docker/rm.)
READ_ONLY=false
if ! $VALIDATE_MODE && [[ -d /boot/firmware ]] && [[ $EUID -ne 0 ]]; then
    READ_ONLY=true
fi
# Im validate-Modus NICHT touch — wir wollen genau das feststellen,
# ob ein YAML existiert. Im TUI-Modus erzeugen wir ein leeres YAML
# damit der Operator sich durchklicken kann.
if ! $VALIDATE_MODE; then
    mkdir -p "$(dirname "$OUT")"
    [[ -f "$OUT" ]] || touch "$OUT"
fi

# === Bash-Variablen für die 18 Felder (ggf. aus existing dfld.yml laden) ===
get() { yq -r ".$1 // \"\"" "$OUT" 2>/dev/null || true; }

DFLD_REGION=$(get dfld_region)
DFLD_STATION=$(get dfld_station)
DFLD_CKSUM=$(get dfld_cksum)
DFLD_LIVEVIEW=$(get dfld_liveview)
DFLD_LEGACY=$(get dfld_legacy)

# Übertragung — zwei unabhängige Pfade.
DFLD_LIVE_ENABLED=$(get dfld_live_enabled)
DFLD_BACKFILL_INTERVAL=$(get dfld_backfill_interval)
# Migration vom alten dfld_tx_tier (live|hourly|daily|off) auf die zwei
# neuen Felder, falls nur das alte im YAML steht.
if [[ -z "$DFLD_LIVE_ENABLED" && -z "$DFLD_BACKFILL_INTERVAL" ]]; then
    legacy=$(get dfld_tx_tier)
    case "$legacy" in
        live)   DFLD_LIVE_ENABLED=true;  DFLD_BACKFILL_INTERVAL=hourly ;;
        hourly) DFLD_LIVE_ENABLED=false; DFLD_BACKFILL_INTERVAL=hourly ;;
        daily)  DFLD_LIVE_ENABLED=false; DFLD_BACKFILL_INTERVAL=daily  ;;
        off)    DFLD_LIVE_ENABLED=false; DFLD_BACKFILL_INTERVAL=off    ;;
    esac
fi
[[ -n "$DFLD_LIVE_ENABLED"      ]] || DFLD_LIVE_ENABLED=true
[[ -n "$DFLD_BACKFILL_INTERVAL" ]] || DFLD_BACKFILL_INTERVAL=hourly
STATION_LON=$(get station_lon)
STATION_LAT=$(get station_lat)
STATION_ALT=$(get station_alt)
STATION_CITY=$(get station_city)
DNMS_MIC=$(get dnms_microphone_type); [[ -n "$DNMS_MIC"      ]] || DNMS_MIC=0x001D
OSM_STATION_ID=$(get osm_station_id)
OSM_SENSORS=$(get osm_sensors)
OSM_API_KEY=$(get osm_api_key)
OSM_INTERVAL=$(get osm_interval);     [[ -n "$OSM_INTERVAL"  ]] || OSM_INTERVAL=150
MQTT_BRIDGED_BROKER=$(get mqtt_bridged_broker)
MQTT_BRIDGED_RENAME=$(get mqtt_bridged_rename)
MQTT_BRIDGED_TLS=$(get mqtt_bridged_tls); [[ -n "$MQTT_BRIDGED_TLS" ]] || MQTT_BRIDGED_TLS=true

# === Pi-only Wartungs-Mechanik ===
# Freeze-Marker liegt in der Boot-Partition damit er beim SD-Klonen
# mitgenommen wird — geklonte Karten booten dann automatisch ohne aktive
# Connectors. systemd-Service dfld-connectors.service prueft den Marker
# in ExecStartPre.
FREEZE_MARKER="/boot/firmware/dfld-freeze"
CONNECTORS_DIR="/opt/dfld/connectors"
TSDB2HTTP_STATE="/opt/dfld/tsdb2http/last-tx.txt"
# Pi-Detection: nicht ueber OUT-Pfad (realpath kann FAT32-Mount-Unter-
# schiede verschleiern), sondern ueber Pi-only-Anker: das ausgerollte
# Connectors-Compose-File. Praktisch unmoeglich am Controller-Host.
ON_PI=false
[[ -f "$CONNECTORS_DIR/docker-compose.yml" ]] && ON_PI=true
freeze_active() { [[ -f "$FREEZE_MARKER" ]]; }

# Backtitle: bei aktivem Freeze pulsiert der Hinweis oben in der Maske,
# damit Operator nicht ueberraschend ohne Container weiterkonfiguriert.
export NEWT_COLORS="root=,blue"
update_backtitle() {
    if freeze_active; then
        BACKTITLE="***  FREEZE AKTIV  —  Connectors gestoppt  ***   ($OUT)"
    elif $READ_ONLY; then
        BACKTITLE="*** LESE-MODUS  —  sudo fuer Aenderungen ***   ($OUT)"
    else
        BACKTITLE="DFLD-Stations-Konfiguration  —  $OUT"
    fi
}

# Read-only-Guard. Aufruf vor jeder Edit-Aktion im Submenu-Dispatcher.
# Wenn aktiv: msgbox mit Hinweis + Caller soll mit "continue" reagieren.
_ro_check() {
    if $READ_ONLY; then
        whiptail --backtitle "$BACKTITLE" \
            --msgbox "Lese-Modus aktiv.\n\nDie Werte werden angezeigt aber nicht gespeichert.\nFuer Aenderungen: 'sudo dfld-config' verwenden." \
            10 70
        return 0
    fi
    return 1
}
update_backtitle

# === Validierungs-Helfer ===
v_regex() { [[ "$1" =~ $2 ]]; }
v_float_range() {
    # Strict-Regex-Vorcheck verhindert dass awk's "sdf"+0=0-Coercion
    # nicht-numerische Strings als 0 durchfallen laesst.
    local v="$1" lo="$2" hi="$3"
    [[ "$v" =~ ^-?([0-9]+(\.[0-9]*)?|\.[0-9]+)$ ]] || return 1
    awk "BEGIN{exit !($v >= $lo && $v <= $hi)}" 2>/dev/null
}
v_int_range() { v_regex "$1" '^-?[0-9]+$' && v_float_range "$1" "$2" "$3"; }
v_hostport() { v_regex "$1" '^[A-Za-z0-9.-]+:[0-9]{1,5}$'; }
v_hex() { v_regex "$1" "^[0-9a-fA-F]{$2}$"; }

err() { whiptail --backtitle "$BACKTITLE" --msgbox "Fehler: $1" 8 70; }

# Generic input-with-validation: ask <title> <prompt> <default> <validator-fn> [validator-args...]
# ESC oder Cancel -> return 1 (kein Update der Variable, eine Ebene zurueck)
# Validierungs-Fehler werden direkt im nachfolgenden Inputbox-Prompt
# angezeigt (kein separater msgbox-Klick noetig — spart einen Enter und
# vermeidet stuck-msgbox bei SSH-Terminal-Quirks).
ask() {
    local title="$1" prompt="$2" def="$3" check="$4"; shift 4
    local val display_prompt="$prompt"
    while true; do
        val=$(whiptail --backtitle "$BACKTITLE" --title "$title" \
            --cancel-button "Zurueck" \
            --inputbox "$display_prompt" 12 70 "$def" 3>&1 1>&2 2>&3) || return 1
        if [[ -z "$val" && -z "$def" ]]; then
            whiptail --backtitle "$BACKTITLE" --yesno "Feld leer lassen (optional)?" 8 60 \
                && { echo ""; return 0; }
            display_prompt="$prompt"
            continue
        fi
        if "$check" "$val" "$@"; then echo "$val"; return 0; fi
        display_prompt="FEHLER: '$val' entspricht nicht dem erwarteten Format.\n\n$prompt"
    done
}

# === Edit-Funktionen pro Feld ===

# Combined Station-ID: Format "001-322" (region-station, jeweils Ziffern)
edit_station_id() {
    local cur="" val
    local base_prompt="Format: REGION-STATION (z.B. 001-322)\n\nJe 1-3 Ziffern, werden mit fuehrenden Nullen auf 3 Stellen aufgefuellt."
    local prompt="$base_prompt"
    [[ -n "$DFLD_REGION" && -n "$DFLD_STATION" ]] && cur="${DFLD_REGION}-${DFLD_STATION}"
    while true; do
        val=$(whiptail --backtitle "$BACKTITLE" --title "Stations-ID" \
            --cancel-button "Zurueck" \
            --inputbox "$prompt" \
            14 70 "$cur" 3>&1 1>&2 2>&3) || return 1
        if [[ "$val" =~ ^([0-9]{1,3})-([0-9]{1,3})$ ]]; then
            # 10#... erzwingt Dezimal-Parse (verhindert oktal-Fehlinterpretation von "08", "09")
            DFLD_REGION=$(printf '%03d' "$((10#${BASH_REMATCH[1]}))")
            DFLD_STATION=$(printf '%03d' "$((10#${BASH_REMATCH[2]}))")
            return 0
        fi
        prompt="FEHLER: '$val' — erwartet z.B. 001-322 oder 1-322.\n\n$base_prompt"
    done
}

edit_station_city() { local v; v=$(ask "Stations-Name" "Stadt / Standort der Station" "$STATION_CITY" v_regex '^.+$') && STATION_CITY="$v"; }
edit_station_lon()  { local v; v=$(ask "Geo: Laengengrad" "Laengengrad in Dezimalgrad (-180..180)" "$STATION_LON" v_float_range -180 180) && STATION_LON="$v"; }
edit_station_lat()  { local v; v=$(ask "Geo: Breitengrad" "Breitengrad in Dezimalgrad (-90..90)" "$STATION_LAT" v_float_range -90 90) && STATION_LAT="$v"; }
edit_station_alt()  { local v; v=$(ask "Geo: Hoehe" "Hoehe ueber NN in Metern (-500..9000)" "$STATION_ALT" v_float_range -500 9000) && STATION_ALT="$v"; }

edit_dfld_liveview() { local v; v=$(ask "DFLD-Server" "host:port des DFLD LiveView (z.B. www.dfld.de:10234)" "$DFLD_LIVEVIEW" v_hostport) && DFLD_LIVEVIEW="$v"; }
edit_dfld_cksum() {
    # Cksum ist semantisch 16-bit dezimal (0-65535). LiveView.py / tsdb2ftp.py
    # parsen den YAML-Wert via int() ohne base — also dezimal-Pflicht im YAML.
    # User-Komfort: Hex-Eingabe mit "0x"-Prefix wird angenommen und ZUR Speicherung
    # nach dezimal konvertiert. Nackte Hex-Strings ("a3f4") sind explizit ungueltig.
    local v num
    local base_prompt="Pruefsumme (Dezimal 0-65535, oder Hex mit 0x-Prefix).\n\nWird im YAML stets dezimal gespeichert.\n\nAktuell: $DFLD_CKSUM"
    local prompt="$base_prompt"
    while true; do
        v=$(whiptail --backtitle "$BACKTITLE" --title "DFLD-Cksum" \
            --cancel-button "Zurueck" \
            --inputbox "$prompt" \
            16 70 "$DFLD_CKSUM" 3>&1 1>&2 2>&3) || return 1
        if [[ "$v" =~ ^0x[0-9a-fA-F]{1,4}$ ]]; then
            num=$((16#${v#0x}))
        elif [[ "$v" =~ ^[0-9]+$ ]]; then
            num=$v
        else
            prompt="FEHLER: '$v' — erwartet Dezimal 0-65535 oder 0x-Hex (max 4 Stellen).\n\n$base_prompt"
            continue
        fi
        if (( num < 0 || num > 65535 )); then
            prompt="FEHLER: Wert $num ausserhalb 0-65535 (16-Bit).\n\n$base_prompt"
            continue
        fi
        DFLD_CKSUM="$num"
        return 0
    done
}
edit_dfld_legacy()   { local v; v=$(ask "DFLD-Legacy" "Legacy-Konfiguration (Base64)" "$DFLD_LEGACY" v_regex '^[A-Za-z0-9+/]+=*$') && DFLD_LEGACY="$v"; }

edit_osm_station_id(){ local v; v=$(ask "OSM-StationId" "OpenSenseMap-ID (24 Hex)" "$OSM_STATION_ID" v_hex 24) && OSM_STATION_ID="$v"; }
edit_osm_sensors()   { local v; v=$(ask "OSM-Sensors"   "id:ns:field:unit:action" "$OSM_SENSORS" v_regex '^[^:]+:[^:]+:[^:]+:[^:]+:[^:]+$') && OSM_SENSORS="$v"; }
edit_osm_api_key()   { local v; v=$(ask "OSM-ApiKey"    "API-Key (64 Hex)" "$OSM_API_KEY" v_hex 64) && OSM_API_KEY="$v"; }
edit_osm_interval()  { local v; v=$(ask "OSM-Interval"  "Sekunden (30..3600)" "$OSM_INTERVAL" v_int_range 30 3600) && OSM_INTERVAL="$v"; }

edit_mqtt_broker()   { local v; v=$(ask "MQTT-Broker"   "host:port (z.B. mqtt.dfld.de:8883)" "$MQTT_BRIDGED_BROKER" v_hostport) && MQTT_BRIDGED_BROKER="$v"; }
edit_mqtt_rename()   { local v; v=$(ask "MQTT-Rename"   "src/ dst/ (Topic-Paar mit Leerzeichen)" "$MQTT_BRIDGED_RENAME" v_regex '^[^ ]+ [^ ]+$') && MQTT_BRIDGED_RENAME="$v"; }

edit_dnms_mic() {
    # Radiolist mit den 5 bekannten Mikrofontypen. Falls kuenftig ein neuer
    # Typ dazukommt, hier eintragen. Pre-selection case-insensitive
    # (Bestand-YAMLs koennten 0x001D oder 0x001d enthalten).
    local v cur="${DNMS_MIC,,}"
    v=$(whiptail --backtitle "$BACKTITLE" --title "DNMS-Mikrofontyp" \
        --cancel-button "Zurueck" \
        --radiolist "Korrektur-Profil:" 14 75 5 \
        "0x001B" "ICS43434 mit Korrektur"            $([[ "$cur" == "0x001b" ]] && echo ON || echo OFF) \
        "0x001C" "IM72D128 mit Korrektur"            $([[ "$cur" == "0x001c" ]] && echo ON || echo OFF) \
        "0x001D" "IM72D128 DLR-Gehaeuse (empfohlen)" $([[ "$cur" == "0x001d" ]] && echo ON || echo OFF) \
        "0x001E" "ICS43434 ohne Korrektur"           $([[ "$cur" == "0x001e" ]] && echo ON || echo OFF) \
        "0x001F" "IM72D128 ohne Korrektur"           $([[ "$cur" == "0x001f" ]] && echo ON || echo OFF) \
        3>&1 1>&2 2>&3) && DNMS_MIC="$v"
}

edit_dfld_live() {
    local v prompt
    prompt="Echtzeit-Stream (1 Hz) an den DFLD-Server.

Backfill (HTTPS-Batch aus Pi-InfluxDB) läuft unabhängig immer
stündlich und schließt jegliche Live-Lücken nach. Live ist also
nur die 'sehe ich Daten in <30s'-Frage:

  An  — Realtime-View im Dashboard, Latenz < 1s
  Aus — nur stündlicher Batch, Daten bis zu 1h verzögert"
    v=$(whiptail --backtitle "$BACKTITLE" --title "Live-Stream" \
        --cancel-button "Zurueck" \
        --radiolist "$prompt" 16 70 2 \
        "true"  "An — sendet jeden Messwert sofort"  $([[ "$DFLD_LIVE_ENABLED" == "true"  ]] && echo ON || echo OFF) \
        "false" "Aus — nur stündlicher Backfill"     $([[ "$DFLD_LIVE_ENABLED" == "false" ]] && echo ON || echo OFF) \
        3>&1 1>&2 2>&3) && DFLD_LIVE_ENABLED="$v"
}

edit_mqtt_tls() {
    local v
    v=$(whiptail --backtitle "$BACKTITLE" --title "MQTT-Bridge: TLS" \
        --cancel-button "Zurueck" \
        --radiolist "TLS aktivieren?" 11 60 2 \
        "true"  "TLS an (empfohlen)"  $([[ "$MQTT_BRIDGED_TLS" == "true"  ]] && echo ON || echo OFF) \
        "false" "TLS aus"             $([[ "$MQTT_BRIDGED_TLS" == "false" ]] && echo ON || echo OFF) \
        3>&1 1>&2 2>&3) && MQTT_BRIDGED_TLS="$v"
}

# Display-Helfer (truncate lange Werte)
short() { local s="$1" n="${2:-30}"; if (( ${#s} > n )); then echo "${s:0:n}..."; else echo "$s"; fi; }
disp()  { local s; s=$(short "$1" "${2:-30}"); echo "[${s:-<leer>}]"; }
station_id_disp() {
    if [[ -n "$DFLD_REGION" && -n "$DFLD_STATION" ]]; then
        echo "[${DFLD_REGION}-${DFLD_STATION}]"
    else
        echo "[<leer>]"
    fi
}

# === Sub-Screens — Cancel-Button "Zurueck", ESC = Zurueck ===

submenu_station() {
    local choice=""
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "DFLD Station" \
            --cancel-button "Zurueck" \
            --default-item "$choice" \
            --menu "Identitaet und Standort der Messstation:" 16 78 7 \
            "1" "Stations-ID         $(station_id_disp)" \
            "2" "Name (Ort)          $(disp "$STATION_CITY" 30)" \
            "3" "Geo: Laengengrad    $(disp "$STATION_LON" 15)" \
            "4" "Geo: Breitengrad    $(disp "$STATION_LAT" 15)" \
            "5" "Geo: Hoehe (m)      $(disp "$STATION_ALT" 15)" \
            3>&1 1>&2 2>&3) || return
        _ro_check && continue
        case $choice in
            1) edit_station_id ;;
            2) edit_station_city ;;
            3) edit_station_lon ;;
            4) edit_station_lat ;;
            5) edit_station_alt ;;
        esac
    done
}

submenu_legacy() {
    local choice=""
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "DFLD Legacy" \
            --cancel-button "Zurueck" \
            --default-item "$choice" \
            --menu "DFLD-LiveView-Server und Auth-Geheimnisse:" 14 78 5 \
            "1" "LiveView-Server     $(disp "$DFLD_LIVEVIEW" 30)" \
            "2" "Cksum (Auth)        $(disp "$DFLD_CKSUM" 15)" \
            "3" "Legacy (Auth)       $(disp "$DFLD_LEGACY" 30)" \
            3>&1 1>&2 2>&3) || return
        _ro_check && continue
        case $choice in
            1) edit_dfld_liveview ;;
            2) edit_dfld_cksum ;;
            3) edit_dfld_legacy ;;
        esac
    done
}

# submenu_backfill entfernt — Backfill ist jetzt fix stündlich, nur noch
# Live als ein/aus konfigurierbar. Hauptmenü ruft edit_dfld_live direkt auf.
# dfld_backfill_interval bleibt im YAML als versteckter Knopf für künftige
# LTE-Tier-Erweiterung (siehe docs/backfill-architecture.md).

# === Wartungs-Funktionen (Pi-only) ===

# Connectors-Stack stoppen/starten via systemd-Service. Faellt zurueck auf
# direkten compose-Aufruf falls der Service noch nicht installiert ist
# (Migrationspfad waehrend Roll-out).
connectors_stop() {
    if systemctl list-unit-files dfld-connectors.service &>/dev/null; then
        systemctl stop dfld-connectors.service 2>&1
    else
        cd "$CONNECTORS_DIR" && docker compose \
            --env-file "$CONNECTORS_DIR/dfld_user.env" \
            --env-file "$CONNECTORS_DIR/docker-compose.env" stop 2>&1
    fi
}
connectors_start() {
    if systemctl list-unit-files dfld-connectors.service &>/dev/null; then
        systemctl start dfld-connectors.service 2>&1
    else
        cd "$CONNECTORS_DIR" && docker compose \
            --env-file "$CONNECTORS_DIR/dfld_user.env" \
            --env-file "$CONNECTORS_DIR/docker-compose.env" up -d 2>&1
    fi
}

action_freeze_set() {
    whiptail --backtitle "$BACKTITLE" --title "Freeze setzen" \
        --yesno "Pi fuer SD-Klon vorbereiten?\n\n• Marker $FREEZE_MARKER wird gesetzt\n• Connectors werden sofort gestoppt\n• Geklonte SD-Karten booten ebenfalls im Freeze-Zustand\n  (Marker liegt in der Boot-Partition und wird mitkopiert)" 14 72 \
        --yes-button "Freeze setzen" --no-button "Abbruch" || return
    local out
    out=$(touch "$FREEZE_MARKER" 2>&1) || {
        whiptail --msgbox "Marker konnte nicht gesetzt werden:\n\n$out\n\n(dfld-config als root ausfuehren?)" 12 70
        return
    }
    out=$(connectors_stop)
    update_backtitle
    whiptail --msgbox "Freeze AKTIV.\n\nMarker: $FREEZE_MARKER\nConnectors gestoppt.\n\nLog:\n$out" 16 78
}

action_freeze_clear() {
    whiptail --backtitle "$BACKTITLE" --title "Freeze aufheben" \
        --yesno "Freeze aufheben?\n\n• Marker wird entfernt\n• Connectors werden gestartet\n\nVorher pruefen:\n  ✓ Stations-ID/Geo passen zur Hardware?\n  ✓ TSDB ggf. zurueckgesetzt?" 14 72 \
        --yes-button "Freeze aufheben" --no-button "Abbruch" || return
    rm -f "$FREEZE_MARKER"
    local out
    out=$(connectors_start)
    update_backtitle
    whiptail --msgbox "Freeze AUFGEHOBEN.\n\nConnectors werden gestartet.\n\nLog:\n$out" 16 78
}

toggle_freeze() {
    if freeze_active; then
        action_freeze_clear
    else
        action_freeze_set
    fi
}

action_reset_tsdb() {
    local station_id
    if [[ -n "$DFLD_REGION" && -n "$DFLD_STATION" ]]; then
        station_id="${DFLD_REGION}-${DFLD_STATION}"
    else
        station_id="UNCONFIGURED"
    fi

    # Stufe 1: Warnung
    whiptail --backtitle "$BACKTITLE" --title "TSDB ZURUECKSETZEN" \
        --yesno "ACHTUNG — destruktiver Eingriff!\n\nAlle SPL-Messungen, Flyover-Events und tsdb2http-State der Pi-lokalen InfluxDB werden GELOESCHT.\n\nSinnvoll z.B. nach Klonen einer SD-Karte mit Daten unter alter Stations-ID.\n\nFortfahren?" 16 72 \
        --yes-button "Weiter" --no-button "Abbruch" || return

    # Stufe 2: explizite Eingabe
    local expected="RESET ${station_id}"
    local confirm
    confirm=$(whiptail --backtitle "$BACKTITLE" --title "Bestaetigung" \
        --inputbox "Zur finalen Bestaetigung tippen Sie genau ein:\n\n  ${expected}\n\nGroß-/Kleinschreibung beachten." 14 72 "" \
        3>&1 1>&2 2>&3) || return
    if [[ "$confirm" != "$expected" ]]; then
        whiptail --msgbox "Eingabe stimmt nicht ueberein.\nReset abgebrochen." 9 50
        return
    fi

    # Ausfuehrung — Output sammeln und am Ende anzeigen
    local log=""
    log+="$(date -Is) Stoppe schreibende Container...\n"
    log+="$(cd "$CONNECTORS_DIR" && docker compose --env-file "$CONNECTORS_DIR/dfld_user.env" --env-file "$CONNECTORS_DIR/docker-compose.env" stop mqtt2tsdb tsdb2http tsdb2osm tsdb2ftp 2>&1)\n"
    log+="$(date -Is) Loesche InfluxDB-Measurements...\n"
    log+="$(docker exec influxdb influx -database dfld -execute 'DROP MEASUREMENT spl' 2>&1)\n"
    log+="$(docker exec influxdb influx -database dfld -execute 'DROP MEASUREMENT flyover' 2>&1)\n"
    log+="$(date -Is) Loesche tsdb2http State-File...\n"
    log+="$(rm -fv "$TSDB2HTTP_STATE" 2>&1)\n"
    if ! freeze_active; then
        log+="$(date -Is) Starte schreibende Container neu...\n"
        log+="$(cd "$CONNECTORS_DIR" && docker compose --env-file "$CONNECTORS_DIR/dfld_user.env" --env-file "$CONNECTORS_DIR/docker-compose.env" start mqtt2tsdb tsdb2http tsdb2osm tsdb2ftp 2>&1)\n"
    else
        log+="(Freeze aktiv — Container werden NICHT automatisch gestartet)\n"
    fi
    log+="$(date -Is) TSDB-Reset abgeschlossen."

    # whiptail --msgbox stuff. echo -e for newlines.
    whiptail --backtitle "$BACKTITLE" --title "TSDB-Reset abgeschlossen" \
        --msgbox "$(echo -e "$log")" 22 78
}

submenu_wartung() {
    if ! $ON_PI; then
        whiptail --msgbox "Wartungs-Aktionen sind nur direkt auf der Pi verfuegbar.\n\n(Aktueller Konfig-Pfad: $OUT)" 10 70
        return
    fi
    if [[ $EUID -ne 0 ]]; then
        whiptail --msgbox "Wartungs-Aktionen erfordern root-Rechte.\n\nBitte 'sudo dfld-config' verwenden." 10 60
        return
    fi
    local choice="" freeze_label
    while true; do
        if freeze_active; then
            freeze_label="Freeze AKTIV         — aufheben + Connectors starten"
        else
            freeze_label="Freeze inaktiv       — setzen + Connectors stoppen"
        fi
        choice=$(whiptail --backtitle "$BACKTITLE" --title "Wartung" \
            --cancel-button "Zurueck" \
            --default-item "$choice" \
            --menu "Wartungs-Aktionen fuer SD-Klon-Workflow:" 13 76 3 \
            "1" "$freeze_label" \
            "2" "TSDB zuruecksetzen   — loescht SPL-Daten der lokalen InfluxDB" \
            3>&1 1>&2 2>&3) || return
        case $choice in
            1) toggle_freeze ;;
            2) action_reset_tsdb ;;
        esac
    done
}

submenu_dnms() {
    local choice=""
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "DNMS-Mikrofon" \
            --cancel-button "Zurueck" \
            --default-item "$choice" \
            --menu "Korrektur-Profil bestimmt Pegel-Genauigkeit:" 11 78 3 \
            "1" "Mikrofontyp         $(disp "$DNMS_MIC" 10)" \
            3>&1 1>&2 2>&3) || return
        _ro_check && continue
        case $choice in
            1) edit_dnms_mic ;;
        esac
    done
}

submenu_osm() {
    local choice=""
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "OpenSenseMap (optional)" \
            --cancel-button "Zurueck" \
            --default-item "$choice" \
            --menu "Upload an opensensemap.org. Felder leer = deaktiviert." 14 78 6 \
            "1" "Station-ID          $(disp "$OSM_STATION_ID" 30)" \
            "2" "Sensors             $(disp "$OSM_SENSORS" 30)" \
            "3" "API-Key             $(disp "$OSM_API_KEY" 30)" \
            "4" "Intervall (s)       $(disp "$OSM_INTERVAL" 10)" \
            3>&1 1>&2 2>&3) || return
        _ro_check && continue
        case $choice in
            1) edit_osm_station_id ;;
            2) edit_osm_sensors ;;
            3) edit_osm_api_key ;;
            4) edit_osm_interval ;;
        esac
    done
}

submenu_bridge() {
    local choice=""
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "MQTT-Bridge (Transform, mTLS)" \
            --cancel-button "Zurueck" \
            --default-item "$choice" \
            --menu "Bridge zu externem MQTT-Broker. Broker leer = deaktiviert." 13 78 5 \
            "1" "Broker              $(disp "$MQTT_BRIDGED_BROKER" 30)" \
            "2" "Topic-Rename        $(disp "$MQTT_BRIDGED_RENAME" 30)" \
            "3" "TLS                 $(disp "$MQTT_BRIDGED_TLS" 10)" \
            3>&1 1>&2 2>&3) || return
        _ro_check && continue
        case $choice in
            1) edit_mqtt_broker ;;
            2) edit_mqtt_rename ;;
            3) edit_mqtt_tls ;;
        esac
    done
}

# === Save ===
save_yaml() {
    {
        echo "# DFLD-Stations-Konfiguration — generiert via dfld-config"
        echo
        echo "# DFLD Station (ID, Standort)"
        echo "dfld_region: \"$DFLD_REGION\""
        echo "dfld_station: \"$DFLD_STATION\""
        echo "station_city: \"$STATION_CITY\""
        echo "station_lon: \"$STATION_LON\""
        echo "station_lat: \"$STATION_LAT\""
        echo "station_alt: \"$STATION_ALT\""
        echo
        echo "# DFLD Legacy (Server + Auth)"
        echo "dfld_liveview: \"$DFLD_LIVEVIEW\""
        echo "dfld_cksum: \"$DFLD_CKSUM\""
        echo "dfld_legacy: \"$DFLD_LEGACY\""
        echo
        echo "# Datenübertragung (zwei unabhängige Pfade)"
        echo "dfld_live_enabled: $DFLD_LIVE_ENABLED"
        echo "dfld_backfill_interval: \"$DFLD_BACKFILL_INTERVAL\""
        echo
        echo "# DNMS-Mikrofon"
        echo "dnms_microphone_type: \"$DNMS_MIC\""
        if [[ -n "$OSM_STATION_ID$OSM_SENSORS$OSM_API_KEY" ]]; then
            echo
            echo "# OpenSenseMap"
            echo "osm_station_id: \"$OSM_STATION_ID\""
            echo "osm_sensors: \"$OSM_SENSORS\""
            echo "osm_api_key: \"$OSM_API_KEY\""
            echo "osm_interval: \"$OSM_INTERVAL\""
        fi
        if [[ -n "$MQTT_BRIDGED_BROKER" ]]; then
            echo
            echo "# MQTT-Bridge"
            echo "mqtt_bridged_broker: \"$MQTT_BRIDGED_BROKER\""
            echo "mqtt_bridged_rename: \"$MQTT_BRIDGED_RENAME\""
            echo "mqtt_bridged_tls: \"$MQTT_BRIDGED_TLS\""
        fi
    } > "$OUT"
    whiptail --backtitle "$BACKTITLE" --msgbox "Gespeichert nach $OUT\n\n$(cat "$OUT")" 22 78
}

# === Hauptmenü ===
# ESC (rc=255) wird als no-op behandelt -> bleibt im Hauptmenü.
# Cancel-Button "Beenden" (rc=1) ist der explizite Exit ohne Speichern.
# Wichtig: if/else statt direkter command-substitution, weil unter
# `set -e` ein non-zero Exit der Substitution das ganze Skript killen würde.
main_menu() {
    local choice="" rc wartung_label
    while true; do
        update_backtitle
        if $ON_PI; then
            if freeze_active; then
                wartung_label="Wartung                  *** FREEZE AKTIV ***"
            else
                wartung_label="Wartung                  (Freeze, TSDB-Reset)"
            fi
        else
            wartung_label="Wartung                  (nur direkt auf Pi)"
        fi
        if choice=$(whiptail --backtitle "$BACKTITLE" --title "Hauptmenü" \
                --cancel-button "Beenden" --ok-button "Auswählen" \
                --default-item "$choice" \
                --menu "Konfigurations-Bereich auswählen:" 17 78 9 \
                "1" "DFLD Station             (ID, Name, Geo)" \
                "2" "DFLD Legacy              (Server, Auth)" \
                "3" "Live-Stream              $(disp "$DFLD_LIVE_ENABLED" 10)" \
                "4" "DNMS-Mikrofon            (Mikrofontyp)" \
                "5" "MQTT-Bridge              (Transform, mTLS)" \
                "6" "OpenSenseMap             (optional)" \
                "W" "$wartung_label" \
                "S" "Speichern und Beenden" \
                3>&1 1>&2 2>&3); then
            rc=0
        else
            rc=$?
        fi
        case $rc in
            255) continue ;;   # ESC -> no-op, bleibt im Menü
            1)   break ;;      # Cancel-Button "Beenden" -> exit ohne speichern
        esac
        case $choice in
            1) submenu_station ;;
            2) submenu_legacy ;;
            3) if _ro_check; then :; else edit_dfld_live; fi ;;
            4) submenu_dnms ;;
            5) submenu_bridge ;;
            6) submenu_osm ;;
            W) submenu_wartung ;;
            S) if _ro_check; then :; else save_yaml; break; fi ;;
        esac
    done
}

# === Validate-Modus: Pflichtfelder-Check, exit 0/1 ===
# Pflicht = was die Container brauchen um sinnvoll an DFLD zu schreiben.
# Optional bleibt OSM-Block, MQTT-Bridge-Block (compose-template handelt
# das via Jinja-Guards), dfld_live_enabled/backfill_interval (haben
# Defaults im YAML-Save bzw. Loader-Migration).
validate_main() {
    local errs=()
    [[ -f "$OUT" ]] || { echo "INVALID: file does not exist: $OUT" >&2; exit 1; }
    v_regex "$DFLD_REGION"   '^[0-9]{3}$'                  || errs+=("dfld_region: '$DFLD_REGION' (3 Ziffern erwartet)")
    v_regex "$DFLD_STATION"  '^[0-9]{3}$'                  || errs+=("dfld_station: '$DFLD_STATION' (3 Ziffern erwartet)")
    v_int_range "$DFLD_CKSUM" 0 65535                      || errs+=("dfld_cksum: '$DFLD_CKSUM' (Dezimal 0-65535 erwartet)")
    v_hostport "$DFLD_LIVEVIEW"                            || errs+=("dfld_liveview: '$DFLD_LIVEVIEW' (host:port erwartet)")
    v_regex "$DFLD_LEGACY"   '^[A-Za-z0-9+/]+=*$'          || errs+=("dfld_legacy: leer oder ungueltig (Base64 erwartet)")
    v_regex "$DNMS_MIC"      '^0x[0-9a-fA-F]{1,4}$'        || errs+=("dnms_microphone_type: '$DNMS_MIC' (0x[hex] erwartet)")
    v_float_range "$STATION_LON" -180 180                  || errs+=("station_lon: '$STATION_LON' (-180..180 erwartet)")
    v_float_range "$STATION_LAT" -90 90                    || errs+=("station_lat: '$STATION_LAT' (-90..90 erwartet)")
    v_float_range "$STATION_ALT" -500 9000                 || errs+=("station_alt: '$STATION_ALT' (-500..9000 erwartet)")
    v_regex "$STATION_CITY"  '^.+$'                        || errs+=("station_city: leer (Lokationsname erwartet)")
    if (( ${#errs[@]} > 0 )); then
        echo "INVALID dfld.yml ($OUT):" >&2
        printf '  %s\n' "${errs[@]}" >&2
        exit 1
    fi
    exit 0
}

if $VALIDATE_MODE; then
    validate_main
else
    main_menu
fi
