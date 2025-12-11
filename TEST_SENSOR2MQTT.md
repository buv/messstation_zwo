# Test-Anleitung für sensor2mqtt.py

## Quick Test

### 1. Hardware-Erkennung testen
```bash
sudo docker run --rm --privileged -v /dev:/dev:ro dfld_box python detect_hw.py
```

**Erwartete Ausgabe:**
```json
{
  "DNMS_I2C_AVAILABLE": 0,
  "BME280_AVAILABLE": 1,    # <- Wenn BME280 an I2C vorhanden
  "ADSB_AVAILABLE": 0,
  "DFLD_LEGACY_AVAILABLE": 0,
  "DFLD_DNMS_AVAILABLE": 0,
  "SSD1306_AVAILABLE": 0
}
```

### 2. Sensor2mqtt im Test-Modus starten
```bash
# Mit lokalem MQTT-Broker
sudo docker run --rm --privileged \
  -v /dev:/dev \
  --network host \
  -e MQTT_SERVER=localhost \
  -e MQTT_PORT=1883 \
  -e MQTT_TOPIC=dfld/sensors/test \
  -e LOG_LEVEL=DEBUG \
  -e READOUT_INTERVAL=5 \
  dfld_box python sensor2mqtt.py
```

### 3. MQTT-Daten empfangen (in separatem Terminal)
```bash
# Mit mosquitto_sub
mosquitto_sub -h localhost -t 'dfld/sensors/test' -v

# Oder im Container
docker exec -it mosquitto mosquitto_sub -t 'dfld/sensors/test' -v
```

### 4. UDP-Test
```bash
# In einem Terminal: sensor2mqtt starten (siehe oben)

# In einem anderen Terminal: Test-Daten per UDP senden
echo '{"temperature": 23.5, "source": "test"}' | nc -u localhost 11883

# MQTT-Output prüfen:
# -> Sollte erscheinen: {"source": "udp", "temperature": 23.5, "ts": ...}
```

## Test-Szenarien

### Szenario 1: Nur BME280 vorhanden
```bash
# Hardware-Erkennung sollte zeigen:
# BME280_AVAILABLE: 1

# Sensor2mqtt sollte starten:
# - BME280 Thread
# - UDP Listener Thread

# Erwartete Logs:
# "Starting BME280 sensor..."
# "Starting UDP listener..."
# "Started 2 sensor threads"
```

### Szenario 2: BME280 + DNMS I2C
```bash
# Hardware-Erkennung sollte zeigen:
# BME280_AVAILABLE: 1
# DNMS_I2C_AVAILABLE: 1

# Sensor2mqtt sollte starten:
# - BME280 Thread
# - DNMS I2C Thread
# - UDP Listener Thread

# Erwartete Logs:
# "Starting BME280 sensor..."
# "Starting DNMS I2C sensor..."
# "Starting UDP listener..."
# "Started 3 sensor threads"
```

### Szenario 3: Keine Hardware (nur UDP)
```bash
# Hardware-Erkennung sollte zeigen:
# Alle *_AVAILABLE: 0

# Sensor2mqtt sollte starten:
# - UDP Listener Thread (einziger)

# Erwartete Logs:
# "No sensors detected! Only UDP listener is active."
# "Starting UDP listener..."
# "Started 1 sensor threads"
```

## Debugging

### Problem: Keine Sensordaten
```bash
# 1. Log-Level auf DEBUG setzen
-e LOG_LEVEL=DEBUG

# 2. Prüfen, ob Threads starten
docker logs sensor2mqtt 2>&1 | grep "Starting"

# 3. Prüfen, ob Verbindungen aufgebaut werden
docker logs sensor2mqtt 2>&1 | grep "connected"

# 4. Prüfen auf Fehler
docker logs sensor2mqtt 2>&1 | grep -i "error\|failed"
```

### Problem: Hardware nicht erkannt
```bash
# 1. Devices prüfen
ls -la /dev/i2c-* /dev/ttyUSB* /dev/ttyDNMS 2>&1

# 2. I2C-Scan manuell
sudo i2cdetect -y 1

# 3. Serial-Devices prüfen
python3 -c "import serial.tools.list_ports; print([p.device for p in serial.tools.list_ports.comports()])"
```

### Problem: MQTT-Verbindung schlägt fehl
```bash
# 1. Mosquitto-Status prüfen
docker ps | grep mosquitto

# 2. MQTT-Broker testen
mosquitto_pub -h localhost -t test -m "hello"
mosquitto_sub -h localhost -t test

# 3. Netzwerk prüfen
docker network ls
docker network inspect dfld_network
```

## Performance-Tests

### CPU-Last messen
```bash
# Container starten
docker run -d --name sensor2mqtt_test \
  --privileged -v /dev:/dev \
  --network host \
  -e MQTT_SERVER=localhost \
  dfld_box python sensor2mqtt.py

# CPU-Last überwachen
docker stats sensor2mqtt_test

# Nach Test aufräumen
docker stop sensor2mqtt_test
docker rm sensor2mqtt_test
```

### Speicher-Verbrauch messen
```bash
# Speicher vor Start
free -h

# Container starten (siehe oben)

# Speicher nach Start
free -h

# Container-Speicher
docker stats sensor2mqtt_test --no-stream
```

### Daten-Durchsatz testen
```bash
# Readout-Intervall reduzieren für Test
-e READOUT_INTERVAL=1

# MQTT-Messages zählen
timeout 60 mosquitto_sub -h localhost -t 'dfld/sensors/#' -v | wc -l

# Erwartung: Bei 1 Sekunde Intervall ca. 60 Messages pro Minute pro Sensor
```

## Vergleich Alt vs. Neu

### Ressourcen-Vergleich
```bash
# Alt: Alle einzelnen Container starten
docker compose -f ingress-compose.yml up -d

# Ressourcen messen
docker stats --no-stream bme2mqtt dnmsiic2mqtt dnms2mqtt dfld2mqtt udp2mqtt

# Neu: Unified Container starten
docker run -d --name sensor2mqtt_unified \
  --privileged -v /dev:/dev --network host \
  -e MQTT_SERVER=localhost \
  dfld_box python sensor2mqtt.py

# Ressourcen messen
docker stats --no-stream sensor2mqtt_unified

# Vergleich dokumentieren
```

### Startup-Zeit vergleichen
```bash
# Alt
time docker compose -f ingress-compose.yml up -d

# Neu
time docker run -d --name sensor2mqtt_unified \
  --privileged -v /dev:/dev --network host \
  -e MQTT_SERVER=localhost \
  dfld_box python sensor2mqtt.py
```

## Automatisierte Tests

### Test-Script erstellen
```bash
cat > test_sensor2mqtt.sh << 'EOF'
#!/bin/bash
set -e

echo "=== Hardware Detection Test ==="
sudo docker run --rm --privileged -v /dev:/dev:ro dfld_box python detect_hw.py

echo ""
echo "=== Starting sensor2mqtt (10 seconds) ==="
CONTAINER_ID=$(sudo docker run -d --privileged -v /dev:/dev \
  --network host \
  -e MQTT_SERVER=localhost \
  -e LOG_LEVEL=DEBUG \
  -e READOUT_INTERVAL=5 \
  dfld_box python sensor2mqtt.py)

sleep 10

echo ""
echo "=== Logs ==="
sudo docker logs $CONTAINER_ID

echo ""
echo "=== Cleanup ==="
sudo docker stop $CONTAINER_ID
sudo docker rm $CONTAINER_ID

echo ""
echo "=== Test Complete ==="
EOF

chmod +x test_sensor2mqtt.sh
./test_sensor2mqtt.sh
```

## Checkliste für Produktiv-Deployment

- [ ] Docker-Image gebaut: `cd files/dfld_box && sudo ./build.sh`
- [ ] Hardware-Erkennung getestet: `sudo docker run --rm --privileged -v /dev:/dev:ro dfld_box python detect_hw.py`
- [ ] Lokaler Test erfolgreich: Container startet und sendet Daten
- [ ] MQTT-Topics korrekt: Daten kommen bei `dfld/sensors` an
- [ ] Logs sauber: Keine ERROR-Messages
- [ ] Backup erstellt: Alte Compose-Files gesichert
- [ ] Compose-Template aktualisiert: `ingress-compose-unified.yml.j2`
- [ ] Ansible-Deployment getestet: `ansible-playbook -i inventory.yml full.yml --tags deploy_ingress_stack`
- [ ] Alte Container gestoppt: `docker compose down`
- [ ] Neuer Container läuft: `docker ps | grep sensor2mqtt`
- [ ] Monitoring aktiv: Logs und Metriken werden überwacht

## Rollback-Plan

Falls Probleme auftreten:
```bash
# 1. Neuen Container stoppen
docker stop sensor2mqtt
docker rm sensor2mqtt

# 2. Alte Compose-Config wiederherstellen
cp templates/container/ingress-compose.yml.j2.backup \
   templates/container/ingress-compose.yml.j2

# 3. Alte Container neu starten
ansible-playbook -i inventory.yml full.yml --tags deploy_ingress_stack
# ODER
docker compose -f ingress-compose.yml up -d

# 4. Funktionalität prüfen
mosquitto_sub -h localhost -t 'dfld/sensors/#' -v
```
