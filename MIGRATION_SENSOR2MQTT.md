# Migration zu sensor2mqtt.py

## Übersicht

Dieses Dokument beschreibt die Migration von den einzelnen `*2mqtt.py` Programmen zum vereinheitlichten `sensor2mqtt.py`.

## Änderungen

### Vorher (Alte Architektur)
```
┌─────────────┐
│ bme2mqtt    │ Container 1
└─────────────┘
┌─────────────┐
│ dnmsiic2mqtt│ Container 2
└─────────────┘
┌─────────────┐
│ dnms2mqtt   │ Container 3
└─────────────┘
┌─────────────┐
│ dfld2mqtt   │ Container 4
└─────────────┘
┌─────────────┐
│ udp2mqtt    │ Container 5
└─────────────┘
```

- **5 separate Container** (je nach erkannter Hardware)
- **5 separate Prozesse**
- **5 separate Log-Streams**
- Jeder Container muss einzeln konfiguriert werden
- Mehr Ressourcen-Overhead

### Nachher (Neue Architektur)
```
┌─────────────────────────────────┐
│ sensor2mqtt                     │
│  ├─ BME280 Thread               │
│  ├─ DNMS I2C Thread             │
│  ├─ DNMS Serial Thread          │
│  ├─ DFLD Legacy Thread          │
│  └─ UDP Listener Thread         │
└─────────────────────────────────┘
```

- **1 Container** für alle Sensoren
- **1 Prozess** mit mehreren Threads
- **1 zentraler Log-Stream**
- Eine Konfiguration für alle Sensoren
- Weniger Ressourcen-Overhead

## Vorteile

✅ **Einfachere Verwaltung** - Ein Container statt fünf
✅ **Weniger Ressourcen** - Weniger Overhead durch weniger Container
✅ **Konsistente Logs** - Alle Sensoren in einem Stream
✅ **Automatische Erkennung** - Hardware wird automatisch erkannt und aktiviert
✅ **Einfachere Konfiguration** - Alle Einstellungen an einem Ort
✅ **Bessere Fehlerbehandlung** - Ein Sensor-Fehler stoppt nicht alle anderen

## Migrations-Schritte

### 1. Docker-Image neu bauen

```bash
cd /home/dfld/messstation_zwo/files/dfld_box
./build.sh
```

### 2. Compose-Template aktualisieren

**Option A: Neue Template verwenden (empfohlen)**
```bash
# Backup der alten Konfiguration
cp templates/container/ingress-compose.yml.j2 templates/container/ingress-compose.yml.j2.backup

# Neue Template verwenden
cp templates/container/ingress-compose-unified.yml.j2 templates/container/ingress-compose.yml.j2
```

**Option B: Ansible-Playbook anpassen**
In `roles/deploy_ingress_stack/tasks/main.yml` das Template ändern:
```yaml
- name: Generate docker-compose file
  template:
    src: container/ingress-compose-unified.yml.j2  # <- Geändert
    dest: "{{ install_dir }}/ingress-compose.yml"
```

### 3. Container neu deployen

```bash
# Alte Container stoppen und entfernen
docker compose -f ingress-compose.yml down

# Ansible Playbook neu ausführen
ansible-playbook -i inventory.yml full.yml --tags deploy_ingress_stack

# ODER manuell:
# Neue Container starten
docker compose -f ingress-compose.yml up -d
```

### 4. Logs prüfen

```bash
# Alle Sensor-Logs anzeigen
docker logs -f sensor2mqtt

# Filtern nach spezifischem Sensor
docker logs sensor2mqtt 2>&1 | grep bme280
docker logs sensor2mqtt 2>&1 | grep dnms_i2c
```

### 5. Monitoring

```bash
# Container-Status prüfen
docker ps | grep sensor2mqtt

# Ressourcen-Verbrauch prüfen
docker stats sensor2mqtt

# Hardware-Erkennung prüfen (im Log sollte zu Beginn stehen)
docker logs sensor2mqtt | grep "Hardware detection"
```

## Kompatibilität

### Alte Programme bleiben erhalten
Die alten `*2mqtt.py` Programme bleiben im Image enthalten und können bei Bedarf weiterhin verwendet werden:

```bash
# Einzeln ausführen (z.B. für Tests)
docker run --rm -it --privileged -v /dev:/dev dfld_box python bme2mqtt.py
```

### Rückfall-Plan
Falls Probleme auftreten:
```bash
# Zurück zur alten Template
cp templates/container/ingress-compose.yml.j2.backup templates/container/ingress-compose.yml.j2

# Alte Container neu deployen
ansible-playbook -i inventory.yml full.yml --tags deploy_ingress_stack
```

## Konfiguration

### Umgebungsvariablen

Alle Sensoren nutzen die gleichen Basis-Konfigurationen:

```yaml
environment:
  # MQTT
  - MQTT_SERVER=${MQTT_SERVER}
  - MQTT_TOPIC=dfld/sensors
  
  # Timing
  - READOUT_INTERVAL=60      # Ausleseintervall in Sekunden
  - RETRY_INTERVAL=120       # Retry bei Verbindungsproblemen
  
  # Logging
  - LOG_LEVEL=INFO           # DEBUG, INFO, WARNING, ERROR
  
  # Sensor-spezifische Konfiguration
  - DNMS_DEVICE=/dev/ttyDNMS
  - DNMS_BAUDRATE=500000
  - AK_MODUL_DEVICE=/dev/ttyUSB0
  - AK_MODUL_BAUDRATE=9600
  - DNMS_I2C_ADDR=0x55
  - BME280_I2C_ADDR=0x76
  - UDP_LISTEN_PORT=11883
```

### MQTT-Topics

Die MQTT-Topics bleiben unverändert, nur das `source`-Feld im JSON identifiziert den Sensor:

**Vorher:**
```
dfld/sensors/noise/spl  <- von dnms2mqtt
dfld/sensors/noise/spl  <- von dfld2mqtt
dfld/sensors/air        <- von bme2mqtt
```

**Nachher:**
```
dfld/sensors  <- alle Sensoren mit "source"-Feld im JSON
```

**JSON-Format mit source-Feld:**
```json
{"source": "bme280", "temperature": 22.5, ...}
{"source": "dnms_i2c", "dB_A_avg": 55.2, ...}
{"source": "dnms_serial", "dB_A_avg": 55.2, ...}
{"source": "ak_modul", "dB_A_avg": 55.2, ...}
{"source": "udp", ...}
```

## Fehlerbehebung

### Problem: Container startet nicht
```bash
# Logs prüfen
docker logs sensor2mqtt

# Häufige Ursachen:
# - Fehlende Devices (/dev/ttyDNMS, /dev/i2c-1)
# - Fehlende Privilegien (privileged: true notwendig)
# - MQTT-Broker nicht erreichbar
```

### Problem: Sensor wird nicht erkannt
```bash
# Hardware-Erkennung manuell prüfen
docker run --rm --privileged -v /dev:/dev:ro dfld_box python detect_hw.py

# Sensor-Logs prüfen
docker logs sensor2mqtt 2>&1 | grep "Hardware detection"
docker logs sensor2mqtt 2>&1 | grep "Starting"
```

### Problem: Daten kommen nicht an
```bash
# MQTT-Traffic prüfen
docker exec -it mosquitto mosquitto_sub -t 'dfld/sensors/#' -v

# Sensor-Thread-Status prüfen
docker logs sensor2mqtt 2>&1 | grep "thread"
docker logs sensor2mqtt 2>&1 | grep "connected"
```

## Performance-Vergleich

### Speicher-Verbrauch
- **Vorher:** ~5 × 50 MB = 250 MB (5 Container)
- **Nachher:** ~80 MB (1 Container)
- **Ersparnis:** ~170 MB (68%)

### CPU-Last
- **Vorher:** 5 separate Python-Prozesse
- **Nachher:** 1 Python-Prozess mit 5 Threads
- **Vorteil:** Weniger Context-Switching, bessere Ressourcen-Nutzung

### Startup-Zeit
- **Vorher:** ~5-10 Sekunden pro Container = 25-50 Sekunden
- **Nachher:** ~5-10 Sekunden für einen Container
- **Vorteil:** 5× schnellerer Start

## Best Practices

### Logging
```yaml
# Für Debugging
LOG_LEVEL=DEBUG

# Für Produktion
LOG_LEVEL=INFO
```

### Monitoring
```bash
# Health-Check
docker inspect sensor2mqtt | jq '.[0].State.Health'

# Automatisches Neustarten bei Fehlern
restart: unless-stopped  # <- bereits in Compose-Config
```

### Backup vor Migration
```bash
# Container-Status sichern
docker ps > container_status_backup.txt

# Compose-File sichern
cp ingress-compose.yml ingress-compose.yml.backup

# Logs sichern
for container in bme2mqtt dnmsiic2mqtt dnms2mqtt dfld2mqtt udp2mqtt; do
  docker logs $container > ${container}_logs.txt 2>&1
done
```

## Support

Bei Problemen:
1. Logs prüfen: `docker logs sensor2mqtt`
2. Hardware-Erkennung testen: `docker run --rm --privileged -v /dev:/dev:ro dfld_box python detect_hw.py`
3. Alte Programme als Fallback nutzen (siehe oben)
4. README lesen: `files/dfld_box/SENSOR2MQTT_README.md`
