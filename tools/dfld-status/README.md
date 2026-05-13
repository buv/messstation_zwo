# dfld-status

Read-only Status-TUI für eine laufende DFLD-Messstation. Bash + whiptail
im gleichen Stil wie `dfld-config`. Live-Updates alle 2 Sekunden,
Tastatur-Interaktion ohne modale "Aktualisieren"-Dialoge.

## Aufruf

**Auf der Pi (Hauptzweck):**

```bash
ssh dfld@pi
dfld-status            # /usr/local/bin/dfld-status (per Ansible installiert)
```

Kein `sudo` nötig — Skript läuft read-only und braucht nur `docker`-
Gruppen-Mitgliedschaft (Standard für den `dfld`-User).

**Pre-Deployment / Controller-Host:**

v1 hat keinen funktionsfähigen Controller-Modus, weil Status-Daten von
der laufenden Pi gesammelt werden (Docker, MQTT, InfluxDB, Logs). Der
Wrapper baut zwar das Container-Image, sagt aber beim Start, dass das
Tool direkt auf der Pi laufen muss.

## Ansichten

### 1. Übersicht

Ein-Bildschirm-Status:
- **Station** — ID, Stadt, Freeze-Status
- **System** — Pi-Modell, CPU-Temp, RAM, WLAN-RSSI, Uptime
- **Disk** — /, /opt/dfld, /boot/firmware
- **Konfiguration** — `dfld.yml` valide?, Live-Stream, Backfill, OSM, MQTT-Bridge
- **Container** — Infrastructure-Summary / Connectors-Summary
- **Datenfluss** — MQTT spl-Probe, InfluxDB-spl-Count, Flyover/h, tsdb2http last-tx

### 2. Container & Datenfluss

Pro Container eine Zeile mit:
- Health-Glyph (`●` ok, `⚠` warn, `✗` down, `⟳` restart, `–` disabled)
- State + Uptime
- Restart-Count
- Datenfluss-Indikator (Container-spezifisch, siehe unten)

#### Datenfluss-Quellen

| Container | Indikator |
|---|---|
| `mqtt` | `$SYS/broker/messages/received` (Total) |
| `influxdb` | HTTP-Code auf `/ping` |
| `grafana` | HTTP-Code auf `/api/health` |
| `homepage` | HTTP-Code auf `/` |
| `telegraf` | `Wrote.*metrics` in Logs (letzte 5min) |
| `ultrafeeder` | HTTP-Code `/data/aircraft.json` |
| `sensor2mqtt` | mosquitto_sub auf `dfld/sensors/noise/spl` (Probe-Timeout 2s) |
| `mqtt2tsdb` | `SELECT count(*) FROM spl WHERE time > now()-1m` |
| `mqtt2liveview` | Log-Hits `sent|published|UDP` (letzte 1min) |
| `mqtt2mqtt` | Log-Hits `publish` (1min) + `drop` (5min) |
| `tsdb2http` | mtime von `/opt/dfld/tsdb2http/last-tx.txt` |
| `tsdb2ftp` | Log-Hits `upload.*(success\|ok)\|HTTP/.* 2..` (30min) |
| `tsdb2osm` | Log-Hits `POST.*20[0-9]` (30min) |
| `adsb2mqtt` | mosquitto_sub auf `dfld/sensors/adsb/#` |
| `detect_flyover` | `SELECT count(*) FROM flyover WHERE time > now()-1h` |

"Disabled" wird aus `dfld.yml` abgeleitet:
- `tsdb2osm` disabled wenn `osm_api_key` leer
- `mqtt2mqtt` disabled wenn `mqtt_bridged_broker` leer
- `adsb2mqtt`/`detect_flyover`/`ultrafeeder` disabled wenn nicht im Compose
  (Mode = mini, nicht full)

### 3. Logs eines Containers

Drilldown: Container-Auswahl → `docker logs --tail 200 --timestamps`
in scrollbarem Textbox.

## Tasten

In den Live-Ansichten (1 + 2):
- **q / ESC**: zurück ins Hauptmenü
- **r** (oder beliebige andere Taste): sofortiger Refresh
- **c** (nur Übersicht): direkt in Container-Detail wechseln
- **l** (nur Container-Detail): Logs der Auswahl

## Refresh-Mechanik

Whiptail kann nicht *in-place* aktualisieren. Wir nutzen
`whiptail --infobox` (non-modal — malt die Box und kehrt sofort zurück),
gefolgt von `read -t 2 -n 1 -s` zum Lesen einer Taste mit 2s Timeout.
Ohne Taste: Auto-Refresh nach 2s. Mit Taste: sofortige Reaktion.

## Status

MVP. Nicht eingebaut in:
- Ansible-Rolle `deploy_container/install_dfld_status.yml` (analog zu
  `install_dfld_config.yml`)
- Aktionen (Container restart, Freeze toggle, TSDB-Reset) — bleiben
  vorerst in `dfld-config`. Migration in `dfld-status` ist denkbar
  (`-config` = persistenter Zustand, `-status` = Runtime).

## Dateien

```
dfld-status         Wrapper (Pi: direkt exec, Controller: Container)
dfld-status.sh      TUI-Logik (Bash + whiptail)
Dockerfile          debian-trixie-slim + whiptail + yq + jq + curl + mosquitto-clients
.gitignore          output/, *.pyc
```
