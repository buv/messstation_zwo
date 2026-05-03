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

# Output-Pfad bestimmen:
#   1. Env-Var DFLD_CONFIG (höchste Priorität, z.B. für Tests)
#   2. /boot/firmware/dfld.yml wenn das Verzeichnis schreibbar ist
#      (= Skript läuft direkt auf der Pi, post-deploy-Modus)
#   3. ./dfld.yml im aktuellen Verzeichnis
#      (= Skript läuft am Controller pre-deploy; Ansible nimmt
#       die Datei beim nächsten install_messstation.sh-Lauf auf)
if [[ -n "${DFLD_CONFIG:-}" ]]; then
    OUT="$DFLD_CONFIG"
elif [[ -d /boot/firmware && -w /boot/firmware ]]; then
    OUT="/boot/firmware/dfld.yml"
else
    OUT="./dfld.yml"
fi
OUT="$(realpath -m "$OUT")"
mkdir -p "$(dirname "$OUT")"
[[ -f "$OUT" ]] || touch "$OUT"

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

# Backtitle bleibt im oberen Frame waehrend Sub-Screens — newt zentriert
# das Dialog-Fenster im verbleibenden Platz.
export NEWT_COLORS="root=,blue"
BACKTITLE="DFLD-Stations-Konfiguration  —  $OUT"

# === Validierungs-Helfer ===
v_regex() { [[ "$1" =~ $2 ]]; }
v_float_range() {
    local v="$1" lo="$2" hi="$3"
    awk "BEGIN{exit !($v == $v + 0 && $v >= $lo && $v <= $hi)}" 2>/dev/null
}
v_int_range() { v_regex "$1" '^-?[0-9]+$' && v_float_range "$1" "$2" "$3"; }
v_hostport() { v_regex "$1" '^[A-Za-z0-9.-]+:[0-9]{1,5}$'; }
v_hex() { v_regex "$1" "^[0-9a-fA-F]{$2}$"; }

err() { whiptail --backtitle "$BACKTITLE" --msgbox "Fehler: $1" 8 70; }

# Generic input-with-validation: ask <title> <prompt> <default> <validator-fn> [validator-args...]
# ESC oder Cancel -> return 1 (kein Update der Variable, eine Ebene zurueck)
ask() {
    local title="$1" prompt="$2" def="$3" check="$4"; shift 4
    local val
    while true; do
        val=$(whiptail --backtitle "$BACKTITLE" --title "$title" \
            --cancel-button "Zurueck" \
            --inputbox "$prompt" 10 70 "$def" 3>&1 1>&2 2>&3) || return 1
        if [[ -z "$val" && -z "$def" ]]; then
            whiptail --backtitle "$BACKTITLE" --yesno "Feld leer lassen (optional)?" 8 60 \
                && { echo ""; return 0; }
            continue
        fi
        if "$check" "$val" "$@"; then echo "$val"; return 0; fi
        err "Wert '$val' entspricht nicht dem erwarteten Format"
    done
}

# === Edit-Funktionen pro Feld ===

# Combined Station-ID: Format "001-322" (region-station, jeweils Ziffern)
edit_station_id() {
    local cur="" val
    [[ -n "$DFLD_REGION" && -n "$DFLD_STATION" ]] && cur="${DFLD_REGION}-${DFLD_STATION}"
    while true; do
        val=$(whiptail --backtitle "$BACKTITLE" --title "Stations-ID" \
            --cancel-button "Zurueck" \
            --inputbox "Format: REGION-STATION (z.B. 001-322)\n\nRegion: 3 Ziffern, Station: 1-3 Ziffern" \
            12 70 "$cur" 3>&1 1>&2 2>&3) || return 1
        if [[ "$val" =~ ^([0-9]{3})-([0-9]{1,3})$ ]]; then
            DFLD_REGION="${BASH_REMATCH[1]}"
            DFLD_STATION="${BASH_REMATCH[2]}"
            return 0
        fi
        err "Ungueltig: '$val' — erwartet z.B. 001-322"
    done
}

edit_station_city() { local v; v=$(ask "Stations-Name" "Stadt / Standort der Station" "$STATION_CITY" v_regex '^.+$') && STATION_CITY="$v"; }
edit_station_lon()  { local v; v=$(ask "Geo: Laengengrad" "Laengengrad in Dezimalgrad (-180..180)" "$STATION_LON" v_float_range -180 180) && STATION_LON="$v"; }
edit_station_lat()  { local v; v=$(ask "Geo: Breitengrad" "Breitengrad in Dezimalgrad (-90..90)" "$STATION_LAT" v_float_range -90 90) && STATION_LAT="$v"; }
edit_station_alt()  { local v; v=$(ask "Geo: Hoehe" "Hoehe ueber NN in Metern (-500..9000)" "$STATION_ALT" v_float_range -500 9000) && STATION_ALT="$v"; }

edit_dfld_liveview() { local v; v=$(ask "DFLD-Server" "host:port des DFLD LiveView (z.B. www.dfld.de:10234)" "$DFLD_LIVEVIEW" v_hostport) && DFLD_LIVEVIEW="$v"; }
edit_dfld_cksum()    { local v; v=$(ask "DFLD-Cksum"  "Pruefsumme (4 Hex-Ziffern)" "$DFLD_CKSUM" v_regex '^[0-9a-fA-F]{4}$') && DFLD_CKSUM="$v"; }
edit_dfld_legacy()   { local v; v=$(ask "DFLD-Legacy" "Legacy-Konfiguration (Base64)" "$DFLD_LEGACY" v_regex '^[A-Za-z0-9+/]+=*$') && DFLD_LEGACY="$v"; }

edit_osm_station_id(){ local v; v=$(ask "OSM-StationId" "OpenSenseMap-ID (24 Hex)" "$OSM_STATION_ID" v_hex 24) && OSM_STATION_ID="$v"; }
edit_osm_sensors()   { local v; v=$(ask "OSM-Sensors"   "id:ns:field:unit:action" "$OSM_SENSORS" v_regex '^[^:]+:[^:]+:[^:]+:[^:]+:[^:]+$') && OSM_SENSORS="$v"; }
edit_osm_api_key()   { local v; v=$(ask "OSM-ApiKey"    "API-Key (64 Hex)" "$OSM_API_KEY" v_hex 64) && OSM_API_KEY="$v"; }
edit_osm_interval()  { local v; v=$(ask "OSM-Interval"  "Sekunden (30..3600)" "$OSM_INTERVAL" v_int_range 30 3600) && OSM_INTERVAL="$v"; }

edit_mqtt_broker()   { local v; v=$(ask "MQTT-Broker"   "host:port (z.B. mqtt.dfld.de:8883)" "$MQTT_BRIDGED_BROKER" v_hostport) && MQTT_BRIDGED_BROKER="$v"; }
edit_mqtt_rename()   { local v; v=$(ask "MQTT-Rename"   "src/ dst/ (Topic-Paar mit Leerzeichen)" "$MQTT_BRIDGED_RENAME" v_regex '^[^ ]+ [^ ]+$') && MQTT_BRIDGED_RENAME="$v"; }

edit_dnms_mic() {
    local v
    v=$(whiptail --backtitle "$BACKTITLE" --title "DNMS-Mikrofontyp" \
        --cancel-button "Zurueck" \
        --radiolist "Korrektur-Profil:" 14 75 5 \
        "0x001B" "ICS43434 mit Korrektur"            $([[ "$DNMS_MIC" == "0x001B" ]] && echo ON || echo OFF) \
        "0x001C" "IM72D128 mit Korrektur"            $([[ "$DNMS_MIC" == "0x001C" ]] && echo ON || echo OFF) \
        "0x001D" "IM72D128 DLR-Gehaeuse (empfohlen)" $([[ "$DNMS_MIC" == "0x001D" ]] && echo ON || echo OFF) \
        "0x001E" "ICS43434 ohne Korrektur"           $([[ "$DNMS_MIC" == "0x001E" ]] && echo ON || echo OFF) \
        "0x001F" "IM72D128 ohne Korrektur"           $([[ "$DNMS_MIC" == "0x001F" ]] && echo ON || echo OFF) \
        3>&1 1>&2 2>&3) && DNMS_MIC="$v"
}

edit_dfld_live() {
    local v
    v=$(whiptail --backtitle "$BACKTITLE" --title "Live-MQTT" \
        --cancel-button "Zurueck" \
        --radiolist "Echtzeit-Stream (1 Hz) an den DFLD-Server:" 11 70 2 \
        "true"  "An — sendet jeden Messwert sofort"  $([[ "$DFLD_LIVE_ENABLED" == "true"  ]] && echo ON || echo OFF) \
        "false" "Aus — kein Live-Stream"             $([[ "$DFLD_LIVE_ENABLED" == "false" ]] && echo ON || echo OFF) \
        3>&1 1>&2 2>&3) && DFLD_LIVE_ENABLED="$v"
}

edit_dfld_backfill() {
    local v
    v=$(whiptail --backtitle "$BACKTITLE" --title "Backfill-Intervall" \
        --cancel-button "Zurueck" \
        --radiolist "HTTPS-Batch-Upload (schließt Live-Lücken oder ist primärer Pfad):" 12 75 3 \
        "hourly" "Stündlich — schließt WLAN-Lücken zeitnah"   $([[ "$DFLD_BACKFILL_INTERVAL" == "hourly" ]] && echo ON || echo OFF) \
        "daily"  "Täglich — LTE-Volumen-Cap-freundlich"        $([[ "$DFLD_BACKFILL_INTERVAL" == "daily"  ]] && echo ON || echo OFF) \
        "off"    "Aus — keine Batch-Uploads"                   $([[ "$DFLD_BACKFILL_INTERVAL" == "off"    ]] && echo ON || echo OFF) \
        3>&1 1>&2 2>&3) && DFLD_BACKFILL_INTERVAL="$v"
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
    local choice
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "DFLD Station" \
            --cancel-button "Zurueck" \
            --menu "Identitaet und Standort der Messstation:" 16 78 7 \
            "1" "Stations-ID         $(station_id_disp)" \
            "2" "Name (Ort)          $(disp "$STATION_CITY" 30)" \
            "3" "Geo: Laengengrad    $(disp "$STATION_LON" 15)" \
            "4" "Geo: Breitengrad    $(disp "$STATION_LAT" 15)" \
            "5" "Geo: Hoehe (m)      $(disp "$STATION_ALT" 15)" \
            3>&1 1>&2 2>&3) || return
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
    local choice
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "DFLD Legacy" \
            --cancel-button "Zurueck" \
            --menu "DFLD-LiveView-Server und Auth-Geheimnisse:" 14 78 5 \
            "1" "LiveView-Server     $(disp "$DFLD_LIVEVIEW" 30)" \
            "2" "Cksum (Auth)        $(disp "$DFLD_CKSUM" 15)" \
            "3" "Legacy (Auth)       $(disp "$DFLD_LEGACY" 30)" \
            3>&1 1>&2 2>&3) || return
        case $choice in
            1) edit_dfld_liveview ;;
            2) edit_dfld_cksum ;;
            3) edit_dfld_legacy ;;
        esac
    done
}

submenu_backfill() {
    local choice prompt
    prompt="Zwei unabhängige Übertragungspfade zum DFLD-Server:

  • Live-MQTT  — Echtzeit-Stream (1 Hz). Braucht stabiles Netz.
  • Backfill   — periodischer HTTPS-Batch-Upload aus dem lokalen
                 Hot-Buffer; schließt WLAN-Lücken oder ist primärer
                 Pfad für LTE-Stationen.

Übliche Kombinationen:
  Standard-Pi (gutes Netz):  Live=an,  Backfill=stündlich
  LTE bandbreitensparend:    Live=aus, Backfill=stündlich
  LTE Volumen-Cap:           Live=aus, Backfill=täglich
  Diagnose / offline:        Live=aus, Backfill=aus"

    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "Datenübertragung" \
            --cancel-button "Zurueck" \
            --menu "$prompt" 22 78 4 \
            "1" "Live-MQTT             $(disp "$DFLD_LIVE_ENABLED" 10)" \
            "2" "Backfill-Intervall    $(disp "$DFLD_BACKFILL_INTERVAL" 10)" \
            3>&1 1>&2 2>&3) || return
        case $choice in
            1) edit_dfld_live ;;
            2) edit_dfld_backfill ;;
        esac
    done
}

submenu_dnms() {
    local choice
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "DNMS-Mikrofon" \
            --cancel-button "Zurueck" \
            --menu "Korrektur-Profil bestimmt Pegel-Genauigkeit:" 11 78 3 \
            "1" "Mikrofontyp         $(disp "$DNMS_MIC" 10)" \
            3>&1 1>&2 2>&3) || return
        case $choice in
            1) edit_dnms_mic ;;
        esac
    done
}

submenu_osm() {
    local choice
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "OpenSenseMap (optional)" \
            --cancel-button "Zurueck" \
            --menu "Upload an opensensemap.org. Felder leer = deaktiviert." 14 78 6 \
            "1" "Station-ID          $(disp "$OSM_STATION_ID" 30)" \
            "2" "Sensors             $(disp "$OSM_SENSORS" 30)" \
            "3" "API-Key             $(disp "$OSM_API_KEY" 30)" \
            "4" "Intervall (s)       $(disp "$OSM_INTERVAL" 10)" \
            3>&1 1>&2 2>&3) || return
        case $choice in
            1) edit_osm_station_id ;;
            2) edit_osm_sensors ;;
            3) edit_osm_api_key ;;
            4) edit_osm_interval ;;
        esac
    done
}

submenu_bridge() {
    local choice
    while true; do
        choice=$(whiptail --backtitle "$BACKTITLE" --title "MQTT-Bridge (Transform, mTLS)" \
            --cancel-button "Zurueck" \
            --menu "Bridge zu externem MQTT-Broker. Broker leer = deaktiviert." 13 78 5 \
            "1" "Broker              $(disp "$MQTT_BRIDGED_BROKER" 30)" \
            "2" "Topic-Rename        $(disp "$MQTT_BRIDGED_RENAME" 30)" \
            "3" "TLS                 $(disp "$MQTT_BRIDGED_TLS" 10)" \
            3>&1 1>&2 2>&3) || return
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
    local choice rc
    while true; do
        if choice=$(whiptail --backtitle "$BACKTITLE" --title "Hauptmenü" \
                --cancel-button "Beenden" --ok-button "Auswählen" \
                --menu "Konfigurations-Bereich auswählen:" 16 78 8 \
                "1" "DFLD Station             (ID, Name, Geo)" \
                "2" "DFLD Legacy              (Server, Auth)" \
                "3" "Datenübertragung         (Live, Backfill)" \
                "4" "DNMS-Mikrofon            (Mikrofontyp)" \
                "5" "MQTT-Bridge              (Transform, mTLS)" \
                "6" "OpenSenseMap             (optional)" \
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
            3) submenu_backfill ;;
            4) submenu_dnms ;;
            5) submenu_bridge ;;
            6) submenu_osm ;;
            S) save_yaml; break ;;
        esac
    done
}

main_menu
