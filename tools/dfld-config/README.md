# dfld-config

Interaktive TUI zum Anlegen / Bearbeiten der Stations-Konfigurationsdatei
`dfld.yml`. Bash + whiptail im raspi-config-Stil, läuft in einem Container
damit Operator nichts auf dem Host installieren muss (außer podman/docker).

## Aufruf

```bash
cd ~/projects/dfld/messstation_zwo
tools/dfld-config/dfld-config
```

Schreibt `./dfld.yml` ins **aktuelle Verzeichnis** (= Repo-Root). Beim
nächsten `./install_messstation.sh --mode mini <pi-ip>` legt Ansible
diese Datei automatisch nach `/boot/firmware/dfld.yml` auf der Pi ab.

Container-Runtime wird selbst gefunden (podman bevorzugt, docker als
Fallback). Override:

```bash
DFLD_CONFIG_RUNTIME=docker tools/dfld-config/dfld-config
```

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

Pre-Production-Demo. Noch NICHT als `/usr/local/bin/dfld-config` per
Ansible-Rolle deployed — das ist ein eigener PR. Hier liegt das Tool
zum Evaluieren und Iterieren bereit.

Die `Datenübertragung`-Felder schreiben das **neue** Schema mit zwei
unabhängigen Feldern (`dfld_live_enabled`, `dfld_backfill_interval`).
Der bestehende Pi-Container-Stack (`tsdb2http` aus PR2) erwartet aktuell
noch das alte `dfld_tx_tier`-Feld — Backend-Migration auf das neue
Schema ist ein separater PR. Migration vom alten `dfld_tx_tier` beim
Laden ist bereits drin.

## Dateien

```
dfld-config           Wrapper (Container build + run)
dfld-config.sh        TUI-Logik (Bash + whiptail), läuft im Container
Dockerfile            debian-trixie-slim + whiptail + yq + de_DE.UTF-8
.gitignore            output/, *.pyc
```
