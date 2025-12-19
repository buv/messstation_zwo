# Architektur Messstation ZWO

## Überblick

Die Messstation ZWO ist eine containerbasierte Lösung zur Erfassung, Verarbeitung und Weiterleitung von Umweltdaten (primär Schallpegel) auf Raspberry Pi Systemen. Die Architektur folgt dem Prinzip der Microservices mit klarer Trennung zwischen Datenerfassung, -verarbeitung und -weiterleitung.

## Systemarchitektur

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Raspberry Pi                                    │
│                                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │ DNMS Sensor  │  │ DNMS Sensor  │  │ BME280       │  │ ADS-B USB    │    │
│  │ (USB)        │  │ (I2C)        │  │ (I2C)        │  │ Empfänger    │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                  │                  │                  │             │
│  ┌──────▼──────────────────▼──────────────────▼──────────────────▼───────┐  │
│  │                  Docker Container Infrastruktur                        │  │
│  │                                                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │              Datenerfassung (Ingress / Producers)              │  │  │
│  │  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │  │  │
│  │  │  │dnms2mqtt │  │dnms2mqtt │  │bme2mqtt  │  │dump1090  │       │  │  │
│  │  │  │  (USB)   │  │  (I2C)   │  │          │  │          │       │  │  │
│  │  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │  │  │
│  │  └───────┼─────────────┼─────────────┼─────────────┼────────────┘  │  │
│  │          │             │             │             │                 │  │
│  │          └─────────────┴─────────────┴─────────────┘                 │  │
│  │                              │                                        │  │
│  │  ┌───────────────────────────▼────────────────────────────────────┐  │  │
│  │  │              Mosquitto MQTT Broker (Message Bus)               │  │  │
│  │  │                    Topics: dfld/sensors/#                       │  │  │
│  │  └───────────────────────────┬────────────────────────────────────┘  │  │
│  │                              │                                        │  │
│  │          ┌───────────────────┴───────────────────┐                   │  │
│  │          │                                       │                   │  │
│  │  ┌───────▼──────────┐                   ┌────────▼────────────┐     │  │
│  │  │   mqtt2tsdb      │                   │   mqtt2liveview     │     │  │
│  │  │   (Consumer)     │                   │   (Consumer)        │     │  │
│  │  └───────┬──────────┘                   │   mqtt2mqtt         │     │  │
│  │          │                              │   (Consumer)        │     │  │
│  │          │                              │   ssd1306_display   │     │  │
│  │          │                              │   (Consumer)        │     │  │
│  │  ┌───────▼──────────────────────────┐   └─────────────────────┘     │  │
│  │  │      InfluxDB (Time-Series DB)   │                               │  │
│  │  │      Retention: Konfigurierbar   │                               │  │
│  │  └───────┬──────────────────────────┘                               │  │
│  │          │                                                           │  │
│  │          └───────────────────┬──────────────────┐                    │  │
│  │                              │                  │                    │  │
│  │  ┌───────▼──────────┐  ┌─────▼────────┐  ┌─────▼────────┐          │  │
│  │  │   tsdb2ftp       │  │  tsdb2osm    │  │   Grafana    │          │  │
│  │  │   (Consumer)     │  │  (Consumer)  │  │  (Consumer)  │          │  │
│  │  └──────────────────┘  └──────────────┘  └──────────────┘          │  │
│  │                                                                      │  │
│  │  ┌──────────────────────────────────────────────────────────────┐  │  │
│  │  │         Weitere Dienste (PostgreSQL, Portainer, Nginx)       │  │  │
│  │  └──────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                               │
│  ┌──────────────┐                                                            │
│  │  SSD1306     │◄─── I2C ─── ssd1306_display Container                     │
│  │  Display     │                                                            │
│  └──────────────┘                                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  Externe Dienste      │
              │  - DFLD LiveView      │
              │  - DFLD FTP           │
              │  - openSenseMap       │
              │  - Externer MQTT      │
              └───────────────────────┘
```

## Architekturprinzipien

### 1. Isolation durch Container
Jeder Dienst läuft in einem eigenen Docker-Container mit definierten Ressourcen und Abhängigkeiten.

### 2. Lose Kopplung
- **MQTT als zentraler Message Bus**: Alle Sensordaten fließen über den lokalen MQTT Broker
- **Unabhängige Egress-Container**: Externe Verbindungsprobleme beeinträchtigen nicht die Datenerfassung
- **Non-blocking Design**: DNS-Timeouts und Netzwerkfehler blockieren nicht die Sensor-Pipelines

### 3. Resilience & Fehlertoleranz
- Automatische Wiederverbindung mit exponentieller Backoff-Strategie
- DNS-Caching (1 Stunde) zur Vermeidung von Blockierungen
- Lokale Datenpufferung bei Ausfall externer Dienste
- Graceful Degradation: Ausfall eines Egress-Containers beeinträchtigt andere nicht

### 4. Konfiguration über Umgebungsvariablen
Zentrale Konfiguration in `/boot/firmware/dfld.yml` (YAML-Format), automatisch in Container-Umgebungsvariablen konvertiert.

## Komponenten-Details

### Datenerfassung (Ingress Layer)

#### dnms2mqtt
- **Funktion**: Liest Schallpegeldaten vom DNMS-Sensor (USB)
- **Output**: MQTT Topics `dfld/sensors/noise/spl`, `dfld/sensors/noise/leq`
- **Frequenz**: 1 Hz
- **Resilience**: 3s Timeout für I2C-Polling, non-blocking

#### bme2mqtt
- **Funktion**: Liest Temperatur, Luftfeuchtigkeit, Luftdruck vom BME280 (I2C)
- **Output**: MQTT Topics `dfld/sensors/climate/*`
- **Frequenz**: Konfigurierbar (typisch 60s)

#### dump1090
- **Funktion**: Empfängt ADS-B Flugzeugdaten (USB SDR)
- **Output**: JSON über HTTP, Beast-Format über TCP
- **Nur in**: Version Full

### Message Bus

#### Mosquitto MQTT Broker
- **Funktion**: Zentraler lokaler Message Bus
- **Port**: 1883 (intern)
- **Persistenz**: Optional
- **Bridge**: Entfernt (ersetzt durch mqtt2mqtt Container)

### Datenverarbeitung & Speicherung

#### mqtt2tsdb
- **Funktion**: Schreibt MQTT-Daten in InfluxDB
- **Resilience**: Auto-Reconnect bei InfluxDB-Ausfall
- **Subscriptions**: `dfld/sensors/#`

#### InfluxDB
- **Funktion**: Time-Series Database für Sensordaten
- **Retention**: Konfigurierbar
- **Port**: 8086 (intern)

#### PostgreSQL
- **Funktion**: Relationale Datenbank für ADS-B Flugzeugdaten
- **Nur in**: Version Full
- **Port**: 5432 (intern)

### Datenweiterleitung (Egress Layer)

#### mqtt2liveview
- **Funktion**: Sendet Schallpegeldaten an DFLD LiveView (UDP)
- **Resilience**: 
  - DNS-Auflösung einmal beim Start (2s Timeout)
  - Socket-Timeout 0.5s
  - Retry-Interval 300s
  - Non-blocking, dropped messages werden gezählt

#### tsdb2ftp
- **Funktion**: Lädt aggregierte Daten per FTP zu DFLD hoch
- **Resilience**:
  - FTP-Timeout 10s
  - DNS-Timeout 2s
  - Retry bei Verbindungsfehlern

#### tsdb2osm
- **Funktion**: Sendet Daten an openSenseMap (HTTP POST)
- **Resilience**:
  - HTTP-Timeout 10s
  - DNS-Timeout 2s
  - Retry bei Fehlern

#### mqtt2mqtt
- **Funktion**: Bridge zu externem MQTT Broker (ersetzt Mosquitto Bridge)
- **Features**:
  - Non-blocking: Externe Probleme beeinträchtigen lokalen Broker nicht
  - DNS-Caching: Auflösung alle 60 Minuten
  - Topic-Rewriting: `dfld/sensors/noise` → `sensebox/station-id`
  - TLS-Support: Optional mit Client-Zertifikaten
  - Auto-Reconnect mit exponentieller Backoff
- **Resilience**:
  - Separate Container-Isolation
  - Dropped messages werden gezählt
  - Verbindungsstatistiken

### Visualisierung & Management

#### Grafana
- **Funktion**: Dashboards für Sensordaten und Flugzeugüberflüge
- **Datenquellen**: InfluxDB, PostgreSQL
- **Port**: 3000 (über Nginx)

#### Portainer
- **Funktion**: Container-Management UI
- **Port**: 9000 (über Nginx)

#### Nginx
- **Funktion**: Reverse Proxy für Web-Interfaces
- **Port**: 80 (extern)
- **Routen**: `/grafana`, `/portainer`

#### SSD1306 Display (Optional)
- **Funktion**: Lokale Anzeige von Schallpegel und Status
- **Interface**: I2C
- **Update**: Alle 1-2 Sekunden

## Datenfluss

### Schallpegelmessung
```
DNMS Sensor (USB)
  → dnms2mqtt Container
    → Mosquitto MQTT (dfld/sensors/noise/spl)
      → mqtt2tsdb → InfluxDB → Grafana
      → mqtt2liveview → DFLD LiveView (UDP)
      → mqtt2mqtt → Externer MQTT Broker
      → tsdb2ftp → DFLD FTP Server
      → tsdb2osm → openSenseMap API
```

### Flugzeugdaten (Version Full)
```
ADS-B USB Empfänger
  → dump1090 Container
    → PostgreSQL (via Python Script)
      → Grafana (Annotations für Überflüge)
```

## Netzwerk-Resilience

### Problem: Blockierende Netzwerkoperationen
Ursprünglich blockierten DNS-Lookups und Verbindungstimeouts (30-35s) die Datenerfassung.

### Lösung: Mehrschichtige Resilience-Strategie

1. **DNS-Caching**
   - Auflösung einmal beim Start / alle 60 Minuten
   - Timeout 2s
   - Verwendung der aufgelösten IP-Adresse

2. **Kurze Timeouts**
   - Socket: 0.5s
   - HTTP: 10s
   - FTP: 10s

3. **Non-blocking Architektur**
   - EventLoop blockiert nicht bei Sink-Fehlern
   - Sensor-Polling läuft unabhängig weiter
   - Separate Egress-Container

4. **Auto-Reconnect**
   - Exponentieller Backoff (1s → 30s)
   - Verbindungsstatus-Callbacks
   - Statistiken über dropped messages

5. **Container-Isolation**
   - mqtt2mqtt als separater Container (nicht im Mosquitto-Prozess)
   - Ausfall eines Egress-Containers beeinträchtigt andere nicht

## Deployment & Installation

### Ansible-basierte Installation
```
Ansible Controller
  → SSH zum Raspberry Pi
    → Ansible Playbook (install_messstation.yml)
      → Rollen:
        - prepare_system (Updates, Pakete)
        - deploy_container (Docker, Docker Compose)
        - configure_services (Systemd Units)
```

### Systemd Integration
- `.path` Units überwachen `/boot/firmware/dfld.yml`
- Automatischer Container-Neustart bei Konfigurationsänderung
- Keine manuelle Intervention notwendig

### Konfigurationsdateien
- `/boot/firmware/dfld.yml` - Hauptkonfiguration (YAML)
- `/opt/dfld/` - Container-Daten und Logs
- `/opt/dfld/mqtt-certs/` - TLS-Zertifikate für MQTT Bridge

## Skalierbarkeit & Erweiterbarkeit

### Neue Sensoren hinzufügen
1. Container mit `sensor2mqtt` Pattern erstellen
2. MQTT Topic unter `dfld/sensors/` publizieren
3. mqtt2tsdb abonniert automatisch `dfld/sensors/#`

### Neue Egress-Ziele
1. Container mit `mqtt2*` oder `tsdb2*` Pattern erstellen
2. Non-blocking Design mit Timeouts implementieren
3. In `ingress-compose.yml` Template hinzufügen

### Neue Visualisierungen
1. Grafana Dashboard erstellen
2. InfluxDB oder PostgreSQL als Datenquelle
3. Dashboard-JSON in Ansible-Rolle integrieren

## Sicherheit

### Netzwerk-Isolation
- Container kommunizieren über interne Docker-Netzwerke
- Nur Nginx exponiert Port 80 nach außen
- MQTT Broker nur intern erreichbar

### Authentifizierung
- Grafana: Username/Password
- Portainer: Username/Password
- InfluxDB: Username/Password
- Externer MQTT: Optional TLS mit Client-Zertifikaten

### TLS-Verschlüsselung
- MQTT Bridge: Optional TLS 1.2/1.3
- Hostname-Verifikation mit SAN-Zertifikaten
- Mutual TLS mit Client-Zertifikaten möglich

### Credential Management
- Passwörter in `inventory.yml` (nicht in Git)
- Umgebungsvariablen in Containern
- Zertifikate in `/opt/dfld/mqtt-certs/` (600 Permissions)

## Monitoring & Debugging

### Container-Logs
```bash
docker logs <container-name> -f
docker logs <container-name> --tail 50
```

### MQTT-Monitoring
```bash
mosquitto_sub -h localhost -t '#' -v
```

### Systemd-Status
```bash
systemctl status dfld-*
```

### Grafana-Dashboards
- Schallpegel-Zeitreihen
- Flugzeugüberflüge (Version Full)
- System-Metriken

## Performance

### Ressourcenverbrauch
- **Version Mini**: ~512 MB RAM, Raspberry Pi Zero 2W ausreichend
- **Version Full**: ~2 GB RAM, Raspberry Pi 4 empfohlen

### Datenraten
- DNMS: 1 Hz (1 Messung/Sekunde)
- BME280: 0.017 Hz (1 Messung/60 Sekunden)
- ADS-B: Variable Rate (abhängig von Flugverkehr)

### Speicherbedarf
- InfluxDB: ~100 MB/Monat (bei 1 Hz Schallpegel)
- PostgreSQL: ~10 MB/Tag (bei mittlerem Flugverkehr)

## Wartung

### Updates
```bash
cd messstation_zwo
git pull
./install_messstation.sh --mode [full|mini] [IP]
```

### Backup
- `/opt/dfld/` - Alle Container-Daten
- `/boot/firmware/dfld.yml` - Konfiguration
- InfluxDB: `influx backup`
- PostgreSQL: `pg_dump`

### Logs rotieren
- Docker-Log-Rotation automatisch konfiguriert
- Systemd-Journal: `journalctl --vacuum-time=7d`
