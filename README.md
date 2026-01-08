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
- Ein Admin-Account mit sudo-Rechten auf dem Raspberry Pi
- SSH-Logins freigegeben falls Messstation ZWO auf Remote Raspberry Pi installiert werden soll.
- Fingerprint des Remote Raspis in den lokalen known_keys. Alternative kann `host_key_checking` in  Zeile 5 in der ansible.cfg ausgeschaltet werden.
- inventory.yml aus der .example Datei erzeugen.
- In der Datei inventory.yml können bzw. müssen die Default User und Passwörter für Portainer, Grafana und InfluxDB angepasst werden.
- Bei einer Remote-Installation müssen die Zugangsdaten für den User auf dem Remote Raspberry PI, der die Ansible Tasks ausführt eingerichtet werden. Dabei kann Username, sudo- und ssh-Password gesetzt werden. Es wird allerdings empfohlen für die Authentifizierung ein ssh Schlüsselpaar zu verwenden.
- Benötigt, bei einer lokalen Installation, der User, der das Playbook ausführt, ein Password für sudo, so kann dies in der inventory.yml eingetragen werden. 
- In der inventory.yml können/müssen vor Ausführung noch die Passwörter für die Admin-Accounts von Portainer, Grafana und der InfluxDB gesetzt werden. Die Passwörter sind freiwählbar, werden während der Installation gesetzt. Nach der Installation 
- .gitignore ist so konfiguriert, das weder ansible.cfg noch inventory.yml nach git publiziert werden können.
- Starten des Playbooks mit dem Befehl: `./install_messstation.sh` 


```bash
# Ansible Controller System auf den aktuellen Patchstand bringen
sudo apt update && sudo apt full-upgrade -y

# Ansible und git installieren
sudo apt-get install -y git ansible

# Dieses Repository clone
git clone https://github.com/buv/messstation_zwo.git

# In das Repository wechseln
cd messstation_zwo

# Anpassungen an Konfiguration vornehmen:
# dazu kann nach dem initialen Flashen der SD-Karte die Konfigurationsdatei dfld.yml 
# im obersten Verzeichnis der Partition "bootfs" abgelegt werden oder lokal hier im 
# Repository Verzeichnis angelegt und ggf. angepasst werden.
emacs/vi/vim/nano dfld.yml
# dfld.yml nach /boot auf Remote Raspi kopieren  

# Playbook lokal starten und Messstation ZWO installieren.
# Installationsmode "full" ist für Raspberry Pi, "mini" ist für Pico Zero 2W
./install_messstation.sh --mode [full|mini]

# alternativ Playbook remote ausführen.
./install_messstation.sh --mode [full|mini] <IP Adresse Remote Raspi>
```

Mehr zum Thema ansible.cfg und Inventorys findet sich in der [Ansible Dokumentation](https://docs.ansible.com/ansible/latest/index.html).

## Konfiguration nach der Installation

### Umgebungsvariablen anpassen

Die Messstation ZWO verwendet zwei zentrale Konfigurationsdateien für Umgebungsvariablen:

- `/boot/dfld.yml` - DFLD-Konfiguration im YAML-Format (Standort, Zeitzone, ADS-B, etc.)

#### Ändern der Konfiguration

**Option 1: Direkt auf dem Raspberry Pi (via SSH)**

```bash
# DFLD Konfiguration bearbeiten
sudo nano /boot/dfld.yml
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
