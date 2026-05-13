# dfld-status

Read-only Status-TUI für eine laufende DFLD-Messstation. Go + bubbletea +
lipgloss. Live-Updates alle 2 Sekunden, einzelnes statisches Binary
(~3-4 MB, linux/arm64), keine Runtime-Dependencies auf der Pi außer
`docker` und `mosquitto-clients`.

## Aufruf

**Auf der Pi:**

```bash
ssh dfld@<pi>
dfld-status            # /usr/local/bin/dfld-status (per Ansible installiert)
```

Kein `sudo` nötig — das Tool ist read-only und braucht nur `docker`-
Gruppen-Mitgliedschaft (Standard für den `dfld`-User) plus `mosquitto-
clients` für MQTT-Samples.

## Build

Cross-Compile von einem x86_64-Host nach linux/arm64 via Container-Build:

```bash
cd tools/dfld-status
./build.sh                            # → build/dfld-status (arm64 ELF)
```

Braucht nur `podman` oder `docker` lokal — kein Go-Toolchain auf dem
Host. `build/` ist gitignored.

Production-Deployment läuft über die Ansible-Rolle (TODO), die ihren
eigenen Build-Cycle in einem golang-Container anstößt und das Binary
nach `/usr/local/bin/dfld-status` auf der Pi ablegt. Der Toplevel des
Source-Verzeichnisses bleibt bewusst frei von Artefakten, damit sich
Build-Workflows nicht ins Gehege kommen.

## Ansichten

### 1. Übersicht

Ein-Bildschirm-Dashboard mit gerahmten Sektionen:
- **Header** — Station-ID, Stadt, Freeze-Status, aktuelle Uhrzeit
- **System** — Pi-Modell, CPU-Temp, RAM, WLAN-RSSI, Uptime
- **Konfiguration** — `dfld.yml` valid?, Live-Stream, Backfill, OSM, MQTT-Bridge
- **Container-Summary** — Infrastructure / Connectors up-counts
- **Disk** — /, /opt/dfld, /boot/firmware
- **Datenfluss** — spl/min, Broker publish msg/min, InfluxDB spl/5m,
  Aircraft sichtbar, Flyover/h, tsdb2http last-tx

### 2. Container & Datenfluss

Eine Zeile pro Container mit Health-Glyph, State+Uptime, Restart-Count,
Datenfluss-Indikator.

#### Datenfluss-Quellen pro Container

| Container | Indikator |
|---|---|
| `mqtt` | `$SYS/broker/load/publish/sent/1min` (msg/min Broker-weit) |
| `influxdb` | HTTP-Code auf `/ping` |
| `grafana` | HTTP-Code auf `/api/health` |
| `homepage` | HTTP-Code auf `/` |
| `telegraf` | `Wrote.*metrics`-Lines (letzte 5min) |
| `ultrafeeder` | `aircraft.json` aircraft-Array length (sichtbar) |
| `sensor2mqtt` | 5s mosquitto_sub auf `dfld/sensors/noise/spl` (live) + InfluxDB `count(spl) WHERE time > now()-3600.5s` (tsdb) |
| `mqtt2tsdb` | InfluxDB `count(spl) WHERE time > now()-60.5s` |
| `mqtt2liveview` | TBD — geplant: parse Stats-Logger (Option D) |
| `mqtt2mqtt` | Parse periodische `Stats: forwarded=N, dropped=N, connected=Bool`-Logzeilen + Rate aus Delta |
| `tsdb2http` | mtime von `/opt/dfld/tsdb2http/last-tx.txt` |
| `tsdb2ftp` | Letzter Log-Match `transfering.*via ftp` → Docker-Timestamp |
| `tsdb2osm` | Letzter Log-Match `Successfully sent value` → Docker-Timestamp |
| `adsb2mqtt` | 5s mosquitto_sub auf `dfld/adsb/#` (rohe msgs/5s, nicht skaliert) |
| `detect_flyover` | InfluxDB `count(flyover) WHERE time > now()-1h` |

"Disabled" wird aus `dfld.yml` und Compose-Files abgeleitet:
- `tsdb2osm` disabled wenn `osm_api_key` leer
- `mqtt2mqtt` disabled wenn `mqtt_bridged_broker` leer
- `adsb2mqtt`/`detect_flyover`/`ultrafeeder` disabled wenn nicht im Compose

### 3. Logs eines Containers

Drilldown: Container-Auswahl → `docker logs --tail 200 --timestamps`
(via `2>&1` damit Python-stderr-Logs sichtbar werden) in scrollbarem
RichLog-Widget.

## Tasten

- **Übersicht / Container-Detail**: `q` quit, `Esc`/`1` zurück zu Übersicht, `2` Container-Detail, `3`/`l` Logs, `r` Refresh sofort
- **Logs-Picker**: `↑/↓`/`j/k` auswählen, `Enter` öffnen, `Esc` zurück
- **Logs-View**: `↑/↓`/`j/k`/`PgUp`/`PgDn`/`g`/`G` scrollen, `Esc` zurück

## Implementierungs-Hinweise

- **Parallele Probes**: alle Container-Stati und Overview-Probes via
  goroutines + `sync.WaitGroup`. Ohne das würden serielle Calls
  (mqttRate 5s × N Container) den Refresh-Zyklus auf >30s aufblähen.
- **InfluxDB-Glitch-Fix**: Window-Sekunden bekommen intern +500ms
  und werden als `<ms>ms` (Integer) angehängt — `now()-3600500ms`.
  Float-Sekunden (`3600.5s`) sind in InfluxQL Syntax-Error.
- **Atomic-Push beim Deploy**: das Pi-Binary mit `scp ... .new`
  hochladen, dann `mv -f` — verhindert "text file busy" wenn das
  laufende Tool sich selbst überschreiben würde.

## Status

Funktional, getestet auf Pi 5 + Debian 13 trixie.

Offen:
- Ansible-Rolle `deploy_container/install_dfld_status.yml` (Build +
  Install nach `/usr/local/bin/dfld-status`)
- Option D: Stats-Logger in `mqtt2liveview.py` (analog zu mqtt2mqtt)
  damit ein echter Sent-Counter parsbar wird statt nur errs/lastErr

## Dateien

```
build.sh        Cross-Compile-Helper (Container-basiert, dev-only)
Dockerfile      golang:1.24-bookworm multi-stage Build
go.mod / go.sum bubbletea + lipgloss + yaml.v3 + indirekte Deps
main.go         tea.Model, Keybindings, Screen-State-Machine
collect.go      Datenquellen via os/exec (docker, mosquitto_sub, curl)
ui.go           lipgloss-Styling, Render-Funktionen, Farbcodes
build/          (gitignored) → dfld-status arm64-Binary
```
