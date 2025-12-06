# tsdb2osm - InfluxDB zu openSenseMap Bridge

## Übersicht

`tsdb2osm.py` ist ein Python-Skript, das Daten aus InfluxDB liest, aggregiert und per HTTP POST an die openSenseMap API sendet. Es wurde nach dem Muster von `tsdb2ftp.py` entwickelt und ermöglicht die automatische Übertragung von Sensordaten zu openSenseMap.

## Hauptfunktionen

### 1. Datenaggregation
- Liest Daten aus InfluxDB für ein konfigurierbares Zeitintervall
- **Delogarithmiert** die dB-Werte (logarithmisch → linear)
- Berechnet den **Mittelwert** der linearen Werte
- **Logarithmiert** das Ergebnis wieder (linear → logarithmisch)

### 2. HTTP POST zu openSenseMap
- Sendet Daten im JSON-Format: `{"value": "94.0"}`
- Verwendet korrekte HTTP-Headers (Content-Type, Host, Authorization, Connection)
- URL-Format: `https://api.opensensemap.org/boxes/{station_id}/{sensor_id}`

### 3. Multi-Sensor-Unterstützung
- Unterstützt mehrere Sensoren gleichzeitig
- Jeder Sensor kann auf eine eigene Datenbank und Measurement zugreifen
- Konfigurierbar über kommaseparierte Liste

## Konfiguration

### Umgebungsvariablen

#### Pflichtparameter:
- **INFLUXDB_SERVER**: InfluxDB Server-Adresse (Format: `host:port`)
- **INFLUXDB_USERNAME**: InfluxDB Benutzername
- **INFLUXDB_PASSWORD**: InfluxDB Passwort
- **OSM_STATION_ID**: 12 Byte Hex-ID der openSenseMap Station
- **OSM_SENSORS**: Kommaseparierte Liste von Sensor-Konfigurationen
- **OSM_API_KEY**: 32 Byte Hex-Wert des API-Tokens

#### Optionale Parameter:
- **OSM_INTERVAL**: Zeit in Sekunden zwischen Aggregations-Jobs (Standard: 300)
- **OSM_API_URL**: Basis-URL für openSenseMap API (Standard: https://api.opensensemap.org)
- **TZ**: Zeitzone (Standard: UTC)
- **LOG_LEVEL**: Logging-Level (Standard: INFO)

### Sensor-Konfigurationsformat

```
sensor_id:db_name:measurement:column_name
```

**Beispiel:**
```
5f8e9a1b2c3d4e5f6a7b8c9d:noise_db:spl:dB_A_avg
```

**Mehrere Sensoren:**
```
5f8e9a1b2c3d4e5f6a7b8c9d:noise_db:spl:dB_A_avg,507f191e810c19729de860eb:temperature_db:temp:temp_avg
```

## Integration in Ansible/Docker

### 1. Inventory-Konfiguration (inventory.yml)

```yaml
osm_station_id: "507f1f77bcf86cd799439011"
osm_sensors: "507f191e810c19729de860ea:dfld:spl:dB_A_avg,507f191e810c19729de860eb:temperature_db:temp:temp_avg"
osm_api_key: "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
osm_interval: "300"  # optional, Standard ist 300 Sekunden
```

### 2. Automatische Template-Integration

Der `tsdb2osm` Container wird automatisch erstellt, wenn alle drei Pflichtparameter definiert sind:
- `osm_station_id`
- `osm_sensors`
- `osm_api_key`

### 3. Docker Compose Templates

Die folgenden Templates wurden erweitert:
- `templates/container/compose.env.j2` - Fügt OSM-Umgebungsvariablen hinzu
- `templates/container/ingress-compose.yml.j2` - Definiert tsdb2osm Container
- `templates/container/ingress-compose-unified.yml.j2` - Definiert tsdb2osm Container

### 4. Container-Konfiguration

```yaml
tsdb2osm:
  image: dfld_box
  restart: unless-stopped
  container_name: tsdb2osm
  entrypoint: python /dfld/tsdb2osm.py
  environment:
    - INFLUXDB_USERNAME=${INFLUX_ADMIN_USERNAME}
    - INFLUXDB_PASSWORD=${INFLUX_ADMIN_PASSWORD}
    - INFLUXDB_SERVER=${INFLUXDB_SERVERNAME}:${INFLUXDB_SERVERPORT}
    - OSM_STATION_ID=${OSM_STATION_ID}
    - OSM_SENSORS=${OSM_SENSORS}
    - OSM_API_KEY=${OSM_API_KEY}
    - OSM_INTERVAL=${OSM_INTERVAL}  # optional
    - TZ=${TZ}
    - LOG_LEVEL=INFO
```

## Verwendungsbeispiele

### Beispiel 1: Einzelner Sensor
```bash
export OSM_STATION_ID="507f1f77bcf86cd799439011"
export OSM_SENSORS="507f191e810c19729de860ea:dfld:spl:dB_A_avg"
export OSM_API_KEY="a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6"
export OSM_INTERVAL="300"
export INFLUXDB_SERVER="localhost:8086"
export INFLUXDB_USERNAME="admin"
export INFLUXDB_PASSWORD="password"
python3 tsdb2osm.py
```

### Beispiel 2: Mehrere Sensoren
```bash
export OSM_SENSORS="507f191e810c19729de860ea:noise_db:spl:dB_A_avg,507f191e810c19729de860eb:temperature_db:temp:temp_avg,507f191e810c19729de860ec:humidity_db:humidity:humidity_avg"
```

## curl-Beispiel für manuelle Anfragen

```bash
curl -X POST "https://api.opensensemap.org/boxes/{osm_station_id}/{osm_sensor_id}" \
  -H "Content-Type: application/json" \
  -H "Host: ingress.opensensemap.org" \
  -H "Authorization: {osm_api_key}" \
  -H "Connection: close" \
  -d '{"value": "94.0"}'
```

## Abhängigkeiten

Neue Dependencies in `requirements.txt`:
- `requests==2.32.3` - HTTP-Anfragen zu openSenseMap
- `pytz==2024.2` - Zeitzonen-Unterstützung

Vorhandene Dependencies:
- `influxdb==5.3.2` - InfluxDB-Client

## Logging

Das Skript verwendet strukturiertes Logging mit folgenden Informationen:
- Verbindungsstatus zu InfluxDB
- Anzahl gefundener Datenpunkte
- Aggregierte Werte
- HTTP-Request-Status
- Fehler und Warnungen

**Log-Level:**
- DEBUG: SQL-Queries, detaillierte HTTP-Payloads
- INFO: Standard-Betriebsinformationen
- WARNING: Keine Daten gefunden
- ERROR: Verbindungsfehler, HTTP-Fehler

## Funktionsweise

1. **Initialisierung** (60 Sekunden Wartezeit beim Start)
2. **Hauptschleife** (läuft alle `OSM_INTERVAL` Sekunden):
   - Für jeden konfigurierten Sensor:
     - Verbindung zur spezifischen InfluxDB-Datenbank
     - Abfrage der Daten für das Zeitintervall
     - Delogarithmierung der Werte
     - Berechnung des Mittelwerts
     - Logarithmierung des Ergebnisses
     - HTTP POST zu openSenseMap
3. **Fehlerbehandlung**: Bei Fehlern wird der Sensor übersprungen, das Skript läuft weiter

## Wartung und Monitoring

### Homepage-Integration
Der Container wird automatisch in Homepage integriert:
- Gruppe: Infrastructure
- Name: tsdb2osm
- Icon: server-upload-icon.svg
- Beschreibung: "Push data to openSenseMap"
- Statistiken: Aktiviert

### Container-Status prüfen
```bash
docker logs tsdb2osm
docker stats tsdb2osm
```

## Technische Details

### Delogarithmierung / Logarithmierung
```python
def delogarithmize(value: float) -> float:
    """dB → linear"""
    return math.pow(10, value / 10.0)

def logarithmize(value: float) -> float:
    """linear → dB"""
    return 10.0 * math.log10(value)
```

### Aggregation
1. Alle Werte im Zeitintervall werden delogarithmiert
2. Arithmetischer Mittelwert der linearen Werte
3. Ergebnis wird wieder logarithmiert

Dies stellt sicher, dass die physikalisch korrekte Mittelwertbildung für logarithmische Größen (wie dB) erfolgt.

## Troubleshooting

### Problem: Container startet nicht
- Prüfen Sie, ob alle drei Pflichtparameter in `inventory.yml` gesetzt sind
- Prüfen Sie die Logs: `docker logs tsdb2osm`

### Problem: Keine Daten werden gesendet
- Prüfen Sie die InfluxDB-Konfiguration (Server, Username, Password)
- Prüfen Sie, ob Daten in der Datenbank vorhanden sind
- Erhöhen Sie LOG_LEVEL auf DEBUG für detaillierte Ausgaben

### Problem: HTTP-Fehler
- Prüfen Sie die openSenseMap API-Key-Gültigkeit
- Prüfen Sie Station-ID und Sensor-IDs
- Prüfen Sie die Netzwerkverbindung

## Dateien

- `files/dfld_box/tsdb2osm.py` - Hauptskript
- `files/dfld_box/requirements.txt` - Python-Dependencies
- `templates/container/compose.env.j2` - Umgebungsvariablen-Template
- `templates/container/ingress-compose.yml.j2` - Docker Compose für separate Services
- `templates/container/ingress-compose-unified.yml.j2` - Docker Compose für unified Services

## Autor und Lizenz

Entwickelt für das DFLD-Projekt (Deutsches Fluglärm-Dienst)
Basierend auf dem Muster von `tsdb2ftp.py`
