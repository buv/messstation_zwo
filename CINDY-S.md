# MQTT Bridge zu externem MQTT Server

Die Messstation ZWO kann Sensordaten an einen externen MQTT Server weiterleiten. Dies erfolgt über einen nicht-blockierenden MQTT-zu-MQTT Bridge Container, der lokale Topics auf Remote-Topics umschreibt und weiterleitet.

## Funktionsweise

Der `mqtt2mqtt` Container:
- Abonniert lokale MQTT Topics (z.B. `dfld/sensors/noise/#`)
- Schreibt Topics um (z.B. `dfld/sensors/noise/spl` → `sensebox/cindy-s-test/spl`)
- Leitet Nachrichten an den externen MQTT Server weiter
- Ist nicht-blockierend: Externe Verbindungsprobleme beeinträchtigen nicht die lokale Datenerfassung
- Unterstützt automatische Wiederverbindung mit exponentieller Backoff-Strategie
- Führt DNS-Auflösung nur einmal pro Stunde durch (verhindert Blockierung bei Netzwerkproblemen)

## Konfiguration in dfld.yml

### Unverschlüsselte Verbindung (Port 1883)

```yaml
mqtt_bridged_broker: "broker.example.com:1883"
mqtt_bridged_rename: "dfld/sensors/noise sensebox/my-station"
```

### Verschlüsselte Verbindung mit öffentlichem Zertifikat (Port 8883)

Für MQTT Server mit von einer offiziellen CA signierten Zertifikaten:

```yaml
mqtt_bridged_broker: "broker.example.com:8883"
mqtt_bridged_rename: "dfld/sensors/noise sensebox/my-station"
mqtt_bridged_tls: true
```

Das System verwendet automatisch die Standard-CA-Zertifikate aus `/etc/ssl/certs/ca-certificates.crt`.

### Verschlüsselte Verbindung mit selbst-signiertem Zertifikat

Für MQTT Server mit selbst-signierten Zertifikaten:

1. **CA-Zertifikat bereitstellen**: Kopiere das CA-Zertifikat nach `/opt/dfld/mqtt-certs/ca-cert.pem`

2. **Konfiguration in dfld.yml**:
```yaml
mqtt_bridged_broker: "mqtt.example.com:8883"
mqtt_bridged_rename: "dfld/sensors/noise sensebox/my-station"
mqtt_bridged_tls: true
mqtt_bridged_ca_cert: "/mqtt-certs/ca-cert.pem"
```

**Wichtig**: Das Server-Zertifikat muss einen Subject Alternative Name (SAN) enthalten, der mit dem Hostnamen übereinstimmt. Andernfalls schlägt die Hostname-Verifikation fehl.

### Verschlüsselte Verbindung mit Client-Zertifikaten (Mutual TLS)

Für MQTT Server, die Client-Authentifizierung über Zertifikate verlangen:

1. **Zertifikate bereitstellen**:
   - CA-Zertifikat: `/opt/dfld/mqtt-certs/ca-cert.pem`
   - Client-Zertifikat: `/opt/dfld/mqtt-certs/client-cert.pem`
   - Client-Schlüssel: `/opt/dfld/mqtt-certs/client-key.pem`

2. **Konfiguration in dfld.yml**:
```yaml
mqtt_bridged_broker: "mqtt.dfld.de:8883"
mqtt_bridged_rename: "dfld/sensors/noise sensebox/my-station"
mqtt_bridged_tls: true
mqtt_tls_ca_cert: "/mqtt-certs/ca-cert.pem"
mqtt_tls_client_cert: "/mqtt-certs/client-cert.pem"
mqtt_tls_client_key: "/mqtt-certs/client-key.pem"
```

## Topic-Umschreibung

Das Format für `mqtt_bridged_rename` ist:

```
lokales_prefix remote_prefix
```

Beispiele:
- `"dfld/sensors/noise sensebox/station-123"` 
  - `dfld/sensors/noise/spl` → `sensebox/station-123/spl`
  - `dfld/sensors/noise/leq` → `sensebox/station-123/leq`

- `"local/data remote/data"`
  - `local/data/temperature` → `remote/data/temperature`

## Testen der Verbindung

### Mit mosquitto_sub

```bash
# Unverschlüsselt
mosquitto_sub -h broker.example.com -p 1883 -t 'sensebox/#' -v

# Verschlüsselt mit öffentlichem Zertifikat
mosquitto_sub -h broker.example.com -p 8883 -t 'sensebox/#' -v

# Verschlüsselt mit selbst-signiertem Zertifikat
mosquitto_sub -h mqtt.example.com -p 8883 -t 'sensebox/#' \
  --cafile /opt/dfld/mqtt-certs/ca-cert.pem -v

# Mit Client-Zertifikat (Mutual TLS)
mosquitto_sub -h mqtt.example.com -p 8883 -t 'sensebox/#' \
  --cafile /opt/dfld/mqtt-certs/ca-cert.pem \
  --cert /opt/dfld/mqtt-certs/client-cert.pem \
  --key /opt/dfld/mqtt-certs/client-key.pem -v
```

### Mit openssl (TLS-Verbindung testen)

```bash
openssl s_client -connect mqtt.example.com:8883 \
  -CAfile /opt/dfld/mqtt-certs/ca-cert.pem \
  -servername mqtt.example.com
```

Erfolgreiche Verbindung zeigt: `Verify return code: 0 (ok)`

## Logs prüfen

```bash
# Container-Status
sudo docker ps | grep mqtt2mqtt

# Live-Logs
sudo docker logs mqtt2mqtt -f

# Letzte 50 Zeilen
sudo docker logs mqtt2mqtt --tail 50

# Nur Fehler und Warnungen
sudo docker logs mqtt2mqtt 2>&1 | grep -E "ERROR|WARNING"
```

Erfolgreiche Verbindung zeigt:
```
INFO:Resolved mqtt.example.com to 1.2.3.4:8883
INFO:TLS enabled with client certificate: /mqtt-certs/client-cert.pem
INFO:Remote MQTT connected to 1.2.3.4:8883
INFO:Local MQTT connected, subscribed to "dfld/sensors/noise/#"
```

## Fehlerbehebung

### "Connection failed, retrying"

- Prüfe ob der Remote-Server erreichbar ist: `ping mqtt.example.com`
- Prüfe ob Port offen ist: `telnet mqtt.example.com 8883`
- Prüfe Firewall-Regeln

### TLS-Fehler

- Stelle sicher, dass das CA-Zertifikat korrekt ist
- Prüfe ob Server-Zertifikat SAN enthält: `openssl x509 -in server-cert.pem -text -noout | grep -A2 "Subject Alternative Name"`
- Teste TLS-Verbindung mit openssl (siehe oben)

### "DNS lookup failed"

- Prüfe DNS-Auflösung: `nslookup mqtt.example.com`
- Prüfe `/etc/resolv.conf` im Container: `docker exec mqtt2mqtt cat /etc/resolv.conf`

### Zertifikat-Berechtigungen

Wenn Client-Zertifikate nicht gelesen werden können:

```bash
sudo chown -R 1000:1000 /opt/dfld/mqtt-certs/
sudo chmod 644 /opt/dfld/mqtt-certs/*.pem
sudo chmod 600 /opt/dfld/mqtt-certs/*-key.pem
```

## Deaktivieren der Bridge

Um die MQTT-Bridge zu deaktivieren, entferne oder kommentiere die `mqtt_bridged_*` Variablen in `dfld.yml` und führe einen Reboot aus:

```yaml
# mqtt_bridged_broker: "broker.example.com:8883"
# mqtt_bridged_rename: "dfld/sensors/noise sensebox/my-station"
```

Der `mqtt2mqtt` Container wird dann nicht gestartet.
