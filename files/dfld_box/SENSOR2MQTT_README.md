# Unified Sensor to MQTT Program

## Übersicht

Das Programm `sensor2mqtt.py` ersetzt die bisherigen einzelnen `*2mqtt.py` Programme und vereint alle Sensor-Datenquellen in einem einzigen Programm.

## Funktionsweise

Das Programm:
1. **Erkennt automatisch** verfügbare Hardware (I2C-Sensoren, USB/Serial-Geräte)
2. **Startet dynamisch** nur die Datenquellen für erkannte Hardware
3. **Verwaltet mehrere Sensoren** gleichzeitig in separaten Threads
4. **Sendet alle Daten** an den konfigurierten MQTT-Broker

## Unterstützte Sensoren

- **BME280** (I2C, 0x76) - Temperatur, Luftfeuchtigkeit, Luftdruck
- **DNMS I2C** (I2C, 0x55) - Lärm-Messungen über I2C
- **DNMS Serial** (ttyDNMS) - Lärm-Messungen über Serial
- **AK-Modul/DFLD Legacy** (ttyUSB0) - Legacy DFLD-Sensor
- **UDP Listener** - Empfängt externe Daten über UDP (immer aktiv)

## Vorteile gegenüber separaten Programmen

✅ **Ein Container statt mehrere** - Weniger Ressourcen-Overhead
✅ **Automatische Hardware-Erkennung** - Keine manuelle Konfiguration nötig
✅ **Zentrale Logging** - Alle Sensoren in einem Log-Stream
✅ **Einfachere Wartung** - Nur ein Programm zu aktualisieren
✅ **Konsistente Konfiguration** - Gleiche Umgebungsvariablen für alle Sensoren

## Verwendung

### Direkt starten
```bash
python3 sensor2mqtt.py
```

### Im Docker-Container
```bash
docker run -d \
  --name sensor2mqtt \
  --privileged \
  -v /dev:/dev \
  -e MQTT_BROKER=mqtt \
  -e MQTT_PORT=1883 \
  -e MQTT_TOPIC=sensors \
  -e LOG_LEVEL=INFO \
  dfld_box python sensor2mqtt.py
```

## Konfiguration via Umgebungsvariablen

### MQTT-Konfiguration
- `MQTT_BROKER` - MQTT Broker Hostname/IP (default: localhost)
- `MQTT_PORT` - MQTT Broker Port (default: 1883)
- `MQTT_TOPIC` - MQTT Topic für Sensor-Daten (default: sensors)
- `MQTT_CLIENT_ID` - MQTT Client ID (default: auto-generiert)

### Sensor-Konfiguration
- `READOUT_INTERVAL` - Ausleseintervall in Sekunden (default: 60)
- `RETRY_INTERVAL` - Retry-Intervall bei Fehlern in Sekunden (default: 120)
- `LOG_LEVEL` - Log-Level: DEBUG, INFO, WARNING, ERROR (default: INFO)

### BME280-Konfiguration
- `BME280_I2C_ADDR` - I2C-Adresse (default: 0x76)
- `I2C_BUS` - I2C-Bus-Nummer (default: 1)

### DNMS I2C-Konfiguration
- `DNMS_I2C_ADDR` - I2C-Adresse (default: 0x55)
- `DNMS_MICROPHONE_TYPE` - Mikrofon-Typ (default: 28)

### DNMS Serial-Konfiguration
- `DNMS_DEVICE` - Serial Device (auto-erkannt: /dev/ttyDNMS)
- `DNMS_BAUDRATE` - Baudrate (default: 9600)

### AK-Modul-Konfiguration
- `AK_MODUL_DEVICE` - Serial Device (auto-erkannt: /dev/ttyUSB0)
- `AK_MODUL_BAUDRATE` - Baudrate (default: 9600)

### UDP-Konfiguration
- `UDP_LISTEN_IP` - Listen IP (default: 0.0.0.0)
- `UDP_LISTEN_PORT` - Listen Port (default: 11883)

## Hardware-Erkennung

Das Programm nutzt die gleiche Logik wie `detect_hw.py`:
- **I2C-Scan** für BME280, DNMS I2C, SSD1306
- **USB-Scan** für RTL-SDR (ADS-B)
- **Serial/TTY-Scan** für DNMS und AK-Modul

## Migration von alten Skripten

Die folgenden Programme werden durch `sensor2mqtt.py` ersetzt:
- ~~`bme2mqtt.py`~~ → integriert
- ~~`dnmsiic2mqtt.py`~~ → integriert
- ~~`dnms2mqtt.py`~~ → integriert
- ~~`dfld2mqtt.py`~~ → integriert
- ~~`udp2mqtt.py`~~ → integriert

**Hinweis:** Die alten Programme bleiben für Kompatibilität erhalten, können aber durch `sensor2mqtt.py` ersetzt werden.

## Logging

Jeder Sensor-Thread loggt mit eigenem Logger-Namen:
- `sensor2mqtt` - Hauptprogramm
- `bme280` - BME280-Sensor
- `dnms_i2c` - DNMS I2C-Sensor
- `dnms_serial` - DNMS Serial-Sensor
- `ak_modul` - AK-Modul/DFLD Legacy
- `udp` - UDP-Listener

## Fehlerbehandlung

- Bei Verbindungsproblemen versucht jeder Thread automatisch neu zu verbinden
- Threads laufen unabhängig voneinander (ein Fehler stoppt nicht alle Sensoren)
- Daemon-Threads ermöglichen sauberes Beenden mit Ctrl+C

## Ausgabeformat

Alle Sensoren senden JSON-Daten mit einem `source`-Feld:

```json
{
  "source": "bme280",
  "temperature": 22.5,
  "humidity": 45.2,
  "pressure": 1013.25,
  "ts": 1732637400000000000
}
```

```json
{
  "source": "dnms_i2c",
  "dB_A_avg": 55.23,
  "dB_A_min": 45.10,
  "dB_A_max": 78.45,
  "ts": 1732637400000000000
}
```
