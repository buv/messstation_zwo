# Messstation ZWO

Ansible Playbook zum Aufsetzen einer [DFLD](https://www.dfld.de/) Messstation ZWO auf einem Raspberry Pi.

## Ausbaustufen
### Version Mini
Die Version Mini dient dem Erfassen der A-gewichteten Schalldruckpegel und der Weiterleitung an verschiedene Plattformen. Derzeit werden unterstützt 
- [DFLD](https://www.dfld.de/) (Liveview und Historie)
- [openSenseMap](https://opensensemap.org/)
- benutzerdefinierter, offener MQTT-Server

#### Voraussetzungen
- Ein Raspberry Pi Zero 2W oder mehr
- Ein Schallpegelsensor, entweder AK-Modul Bus oder vorzugsweise [DNMS](https://github.com/hbitter/DNMS), der über USB mit 1Hz dBA-gewichtete Schallpegeldaten liefert

#### Ressourcenverbrauch
Die Version Mini benötigt etwa 38 MiB RAM für alle Container:

```bash
sudo docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}"
```
```bash
NAME            MEM USAGE / LIMIT     MEM %     CPU %
mqtt2tsdb       5.676MiB / 416.1MiB   1.36%     1.00%
sensor2mqtt     2.68MiB / 416.1MiB    0.64%     0.29%
tsdb2ftp        612KiB / 416.1MiB     0.14%     0.00%
mqtt2display    4.117MiB / 416.1MiB   0.99%     2.07%
mqtt2mqtt       2.711MiB / 416.1MiB   0.65%     0.45%
mqtt2liveview   2.32MiB / 416.1MiB    0.56%     0.27%
tsdb2osm        7.547MiB / 416.1MiB   1.81%     0.00%
mqtt            808KiB / 416.1MiB     0.19%     0.12%
influxdb        11.88MiB / 416.1MiB   2.85%     0.66%
```

### Version Full
Die Version Full enthält alle Funktionen der Version Mini und unterstützt zusätzlich noch einen lokal angebundenen USB ADS-B Empfänger sowie ein Grafana-Dashboard. Der ADS-B Datenstrom wird verwendet um lokal einen Überflug zu erkennen und ihn im Grafana-Dashboard des Schalldruckpegels zu markieren. Zu jedem dieser erkannten Überflüge sind lokal Details des Flugzeugs (Kennzeichen, Typ) und ein Link bei adsbexchange.com auf das Exemplar dargestellt.

#### Voraussetzungen
- Ein Raspberry Pi mit 2GB RAM oder mehr
- Ein Schallpegelsensor, entweder AK-Modul Bus oder vorzugsweise [DNMS](https://github.com/hbitter/DNMS), der über USB mit 1Hz dBA-gewichtete Schallpegeldaten liefert
- Ein ADS-B Empfänger, der die Flugzeugdaten über USB bereitstellt
- BME280 Sensor (optional)

## Installation

### Voraussetzungen

**Auf dem Raspberry Pi:**
- Admin-Account mit sudo-Rechten
- SSH-Login freigegeben (für Remote-Installation)

**Auf dem Controller-Host (bei Remote-Installation):**
- `git`
- `docker` **oder** `podman` — für Remote-Ziele startet das Install-Skript automatisch einen vorkonfigurierten Controller-Container mit gepinnter Ansible-Version, damit jede Umgebung identisch ist. Eine Ansible-Installation auf dem Host ist nicht erforderlich.

**Auf dem Raspberry Pi (bei lokaler Installation):**
- `git` und `ansible` (direkter Ansible-Flow ohne Container)

### Konfiguration

- `inventory.yml` aus der `.example`-Datei erzeugen. Dort werden Benutzer, sudo-/SSH-Passwörter und die Admin-Passwörter für Grafana und InfluxDB gesetzt.
- `dfld.yml` anlegen (siehe [Struktur der dfld.yml](#struktur-der-dfldyml)) — entweder
  - im Repo-Verzeichnis (das Playbook legt die Datei beim Deployment nach `/boot/firmware/dfld.yml` auf dem Pi ab), **oder**
  - direkt nach dem SD-Karten-Flash ins oberste Verzeichnis der Boot-Partition (via Kartenleser unter `/boot` bzw. `bootfs` sichtbar). Damit startet der Pi schon beim ersten Boot mit der korrekten Konfiguration, und das Playbook übernimmt die vorhandene Datei.
- `.gitignore` stellt sicher, dass `ansible.cfg`, `inventory.yml` und `dfld.yml` nicht versehentlich committed werden.

### Remote-Installation

```bash
git clone https://github.com/buv/messstation_zwo.git
cd messstation_zwo

vi inventory.yml
vi dfld.yml
```

**Erster Aufruf** — baut den Controller-Container (einmalig) und erzeugt im Container einen SSH-Schlüssel. Das Skript druckt anschließend Hinweise und beendet sich ohne Deployment:

```bash
./install_messstation.sh --mode [full|mini] <IP>
```

Pubkey auf den Pi bringen (einmalig, vom Host aus):

```bash
# Nach einem SD-Reflash zuerst stalen Hostkey entfernen:
# ssh-keygen -R <IP>
ssh-copy-id -f -i controller/id_ed25519.pub <user>@<IP>
```

**Optional — mTLS-Zertifikate für externen MQTT-Broker:** Wird der externe MQTT-Broker per mTLS geschützt, müssen die Client-Zertifikate *vor* dem Deployment als Archiv auf den Pi gelegt werden. Die Ansible-Rolle entpackt sie während der Installation automatisch nach `/opt/dfld`:

```bash
scp mqtt-client-certs.tgz <user>@<IP>:/home/dfld/
```

**Zweiter Aufruf** — führt das eigentliche Deployment aus:

```bash
./install_messstation.sh --mode [full|mini] <IP>
```

**Escape-Hatches:**
- `DFLD_NO_CONTAINER=1` — Controller-Container überspringen, Ansible vom Host verwenden
- `DFLD_CONTAINER_RUNTIME=docker|podman` — Runtime-Auswahl erzwingen (sonst Auto-Detection)

### Lokale Installation (direkt auf dem Pi)

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt-get install -y git ansible

git clone https://github.com/buv/messstation_zwo.git
cd messstation_zwo

vi inventory.yml
vi dfld.yml

./install_messstation.sh --mode [full|mini]
```

Mehr zum Thema `ansible.cfg` und Inventorys findet sich in der [Ansible Dokumentation](https://docs.ansible.com/ansible/latest/index.html).

## Konfiguration nach der Installation

### WLAN-Konfiguration ändern (Offline)

Die WLAN-Konfiguration kann ohne Netzwerkzugang über die Boot-Partition der SD-Karte geändert werden:

1. Raspberry Pi herunterfahren und SD-Karte entnehmen
2. SD-Karte in einen Kartenleser stecken (die Boot-Partition ist als FAT32 auf jedem Betriebssystem zugänglich)
3. Datei `network-config` auf der Boot-Partition bearbeiten
4. SD-Karte zurück in den Raspberry Pi einsetzen und starten

Die Datei verwendet das Netplan-Format:

```yaml
network:
  version: 2
  wifis:
    wlan0:
      dhcp4: true
      regulatory-domain: "DE"
      access-points:
        "SSID":
          password: "WPA-PSK-HASH"
      optional: true
```

Den WPA-PSK-Hash erzeugen Sie mit `wpa_passphrase "SSID"` auf einem Linux-System. Alternativ kann das Klartext-Passwort eingetragen werden.

**Voraussetzung:** Beim Erstellen des Raspberry Pi OS Images muss im Raspberry Pi Imager eine WLAN-Verbindung konfiguriert werden. Dadurch wird die initiale `network-config` auf der Boot-Partition angelegt.

Beim nächsten Boot wird die Konfiguration automatisch über einen systemd Service nach `/etc/netplan/` übernommen.

### Umgebungsvariablen anpassen

Die Messstation ZWO verwendet zwei zentrale Konfigurationsdateien für Umgebungsvariablen:

- `/boot/firmware/dfld.yml` - DFLD-Konfiguration im YAML-Format (Standort, Zeitzone, ADS-B, etc.)

#### Ändern der Konfiguration

**Option 1: Direkt auf dem Raspberry Pi (via SSH)**

```bash
# DFLD Konfiguration bearbeiten
sudo nano /boot/firmware/dfld.yml
```

**Option 2: Über Windows/PC mit SD-Kartenleser**

1. Raspberry Pi herunterfahren
2. SD-Karte entnehmen und in einen PC-Kartenleser stecken
3. Die Boot-Partition wird als Laufwerk angezeigt
4. Datei `dfld.yml` mit einem Texteditor öffnen und bearbeiten (YAML-Format)
5. Änderungen speichern, SD-Karte sicher entfernen und wieder in den Raspberry Pi einsetzen
6. Raspberry Pi starten

**Gewünschte Variablen ändern** (z.B. LAT, LON, ALT, TZ)

**Automatischer Neustart der Container:**

Die Container werden automatisch über systemd `.path` Units neu gestartet, sobald systemd eine Änderung an den Konfigurationsdateien erkennt. Es ist kein manueller Eingriff notwendig.

### Struktur der dfld.yml

Die Datei `dfld.yml` enthält alle stationsspezifischen Konfigurationsparameter im YAML-Format. Nachfolgend sind alle verfügbaren Parameter mit ihrer Bedeutung aufgelistet:

#### DFLD-spezifische Parameter

Diese Daten erhalten Sie vom DFLD.

```yaml
dfld_region: "123"              # DFLD Region-ID (z.B. "001" für Frankfurt)
dfld_station: "123"             # DFLD Stations-ID (eindeutige Stationsnummer)
dfld_cksum: "0123"              # DFLD Prüfsumme für Authentifizierung
dfld_liveview: "www.dfld.de:10234"  # DFLD Liveview Server und Port
dfld_legacy: "c2djLnF..."       # DFLD Legacy-Konfiguration (Base64-kodiert)
```

#### Standort-Parameter

```yaml
station_lon: "8.25"             # Längengrad (Longitude) der Station
station_lat: "50.0"             # Breitengrad (Latitude) der Station
station_alt: "125.0"            # Höhe über NN in Metern
station_city: "Mainz"           # Stadt/Ort der Station
```

#### DNMS Mikrofon-Konfiguration

```yaml
# DNMS Mikrofontyp - wählt das verwendete Mikrofon und dessen Korrekturfaktoren
dnms_microphone_type: "0x001D"
```

Verfügbare Mikrofontypen:
- `0x001B` - `DNMS_CMD_SET_ICS43434` - ICS43434 Mikrofon mit Korrektur
- `0x001C` - `DNMS_CMD_SET_IM72D128` - IM72D128 Mikrofon mit Korrektur
- `0x001D` - `DNMS_CMD_SET_IM72D128_DLR_case` - IM72D128 im DLR-Gehäuse mit Korrektur (empfohlen für DLR-DNMS)
- `0x001E` - `DNMS_CMD_SET_ICS43434_no_correction` - ICS43434 ohne Korrektur
- `0x001F` - `DNMS_CMD_SET_IM72D128_no_correction` - IM72D128 ohne Korrektur

**Hinweis:** Der korrekte Mikrofontyp ist wichtig für präzise Messungen. Verwenden Sie den Typ, der zu Ihrem DNMS-Sensor passt.

#### OpenSenseMap (OSM) Parameter

```yaml
osm_station_id: "6037c9f37ea4c9001b3b17b5"  # OpenSenseMap Stations-ID
osm_sensors: "6037c9f37ea4c9001b3b17b6:dfld:spl:dB_A_avg:log"  # Sensor-Mapping
osm_api_key: "5ffd2c327ecabeaa..."          # API-Schlüssel für OpenSenseMap
osm_interval: "150"                          # Upload-Intervall in Sekunden
```

#### MQTT Bridge Parameter (optional)

```yaml
mqtt_bridged_broker: "broker.emqx.io:8883"   # Externer MQTT-Broker (Host:Port)
mqtt_bridged_rename: "dfld/sensors/noise/ sensebox/cindy-s-test/"  # Topic-Umbennennung
mqtt_bridged_tls: "true"                      # TLS-Verschlüsselung aktivieren
```

**Beispiel einer vollständigen dfld.yml:**

```yaml
dfld_region: "001"
dfld_station: "319"
dfld_cksum: "7536"
dfld_liveview: "www.dfld.de:10234"
dfld_legacy: "c2djLnFzeXEucXI6MjE6R05PSFo6Wmhnbm9iZQ=="

station_lon: "8.277687"
station_lat: "49.98747"
station_alt: "125.0"
station_city: "Mainz"

dnms_microphone_type: "0x001D"

osm_station_id: "6037c9f37ea4c9001b3b17b5"
osm_sensors: "6037c9f37ea4c9001b3b17b6:dfld:spl:dB_A_avg:log"
osm_api_key: "5ffd2c327ecabeaa2852e9c0eabd9a6c5af63cab9903e7cd47a286f0f4efacbe"
osm_interval: "150"

mqtt_bridged_broker: "broker.emqx.io:8883"
mqtt_bridged_rename: "dfld/sensors/noise/ sensebox/cindy-s-test/"
mqtt_bridged_tls: "true"
```
