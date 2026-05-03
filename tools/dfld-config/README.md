# dfld-config

Interaktive TUI zum Anlegen / Bearbeiten der Stations-Konfigurationsdatei
`dfld.yml`. Bash + whiptail im raspi-config-Stil, läuft in einem Container
damit Operator nichts auf dem Host installieren muss (außer podman/docker).

## Aufruf

**Pre-Deployment (am Controller-Host, im Repo-Root):**

```bash
cd ~/projects/dfld/messstation_zwo
tools/dfld-config/dfld-config           # podman/docker-Container
```

Schreibt `./dfld.yml` ins **aktuelle Verzeichnis**. Beim nächsten
`./install_messstation.sh --mode mini <pi-ip>` legt Ansible diese Datei
automatisch nach `/boot/firmware/dfld.yml` auf der Pi ab.

Container-Runtime wird selbst gefunden (podman bevorzugt, docker als
Fallback). Override:

```bash
DFLD_CONFIG_RUNTIME=docker tools/dfld-config/dfld-config
```

**Post-Deployment (direkt auf der Pi):**

```bash
ssh dfld@pi
sudo dfld-config           # /usr/local/bin/dfld-config (per Ansible installiert)
```

Schreibt direkt nach `/boot/firmware/dfld.yml`. Der existierende
systemd-Path-Watcher (`dfld-connectors-reload.service`) erkennt die
Änderung und reloaded die Container automatisch — kein Re-Deploy
nötig für Konfig-Updates.

## Was die TUI kann

Hauptmenü mit 6 Konfigurations-Bereichen, jeder eigenes Sub-Screen:

1. **DFLD Station** — Stations-ID (`001-322`), Name, Geo (Lon/Lat/Alt)
2. **DFLD Legacy** — LiveView-Server + Auth-Geheimnisse (Cksum, Legacy-Base64)
3. **Datenübertragung** — Live-MQTT (an/aus) + Backfill-Intervall (stündl./tägl./aus)
4. **DNMS-Mikrofon** — Korrektur-Profil (5 Optionen)
5. **MQTT-Bridge** (Transform, mTLS) — externer Broker mit Topic-Rename
6. **OpenSenseMap** (optional) — Upload-Konfiguration

Pro Feld: Validierung (Regex / Range / Enum), sinnvolle Defaults aus
Inventory, Loop bis valid. Bestehende dfld.yml wird beim Start geladen
und Werte als Default angezeigt — kein Datenverlust beim Re-Edit.

## Tasten

- **Pfeile / TAB**: Navigation
- **Enter / Space**: Auswählen
- **ESC**: eine Ebene zurück (im Hauptmenü no-op)
- **"Beenden"-Button im Hauptmenü**: Beenden ohne Speichern
- **"Speichern und Beenden"** (Punkt im Hauptmenü): YAML schreiben + raus

## Status

Production. Wird via Ansible-Rolle `deploy_container` nach
`/usr/local/bin/dfld-config` auf jeder Pi installiert. Voraussetzung
`whiptail` ist in der `configure-raspi`-Rolle in den apt-Packages.

Die `Datenübertragung`-Felder schreiben das aktuelle Schema mit zwei
unabhängigen Feldern (`dfld_live_enabled`, `dfld_backfill_interval`).
Migration vom alten `dfld_tx_tier` beim Laden ist drin; der
Container-Stack (`tsdb2http`) liest beide Schemata.

## Dateien

```
dfld-config           Wrapper (Container build + run)
dfld-config.sh        TUI-Logik (Bash + whiptail), läuft im Container
Dockerfile            debian-trixie-slim + whiptail + yq + de_DE.UTF-8
.gitignore            output/, *.pyc
```
