# Release Notes - Boot Service und Konfigurationsmanagement

## Übersicht

Dieser Release implementiert einen automatischen Boot-Service, der bei jedem Systemstart die Konfiguration überprüft und Docker-Stacks aktualisiert. Der Service unterstützt beide Deployment-Modi (mini und full) mit unterschiedlichen Strategien zur Stack-Verwaltung.

## Neue Features

### 1. Systemd Boot-Service
Ein neuer systemd-Service (`dfld-boot`) wird während der Installation registriert und führt folgende Funktionen aus:

- **Automatisch bei jedem Boot**: Läuft automatisch nach Docker-Start
- **Konfigurationsüberwachung**: Prüft ob `/boot/dfld.yml` geändert wurde
- **Intelligente Updates**: Aktualisiert Stacks nur bei Bedarf (Timestamp-Vergleich)
- **Hardware-Erkennung**: Erkennt verfügbare Sensoren und Hardware
- **Modus-Unterstützung**: Funktioniert sowohl im mini- als auch im full-Modus

### 2. Dynamische Stack-Updates (Runtime)

#### Mini-Modus
- Docker Compose Dateien werden neu generiert
- Container werden neu gestartet

#### Full-Modus
- Stacks werden über Portainer REST API aktualisiert
- Portainer verwaltet den Container-Lifecycle

### 3. Expliziter Deployment-Modus
Der Deployment-Modus wird jetzt explizit über Playbook-Variablen definiert:

```yaml
# mini.yml
vars:
  deployment_mode: mini

# full.yml
vars:
  deployment_mode: full
```

Nicht mehr implizit aus Portainer-Artefakten abgeleitet.

## Neue Dateien

### Boot-Service Komponenten
- `templates/systemd/dfld-boot.service.j2` - Systemd Service Definition
- `boot.yml` - Boot-Playbook (wird bei jedem Start ausgeführt)
- `roles/boot-service/tasks/main.yml` - Boot-Service Rolle
- `roles/boot-service/handlers/main.yml` - Service Reload Handler
- `roles/update-stacks/tasks/main.yml` - Universelle Stack-Update Rolle für beide Modi

### Basis-Konfigurationsspeicher
- `/opt/dfld/config/base_config.yml` - Alle Installations-Variablen
- Enthält: Benutzer, Passwörter, Pfade, Deployment-Modus

## Geänderte Dateien

### Playbooks
- `mini.yml`: Deployment-Modus Variable + boot-service Rolle
- `full.yml`: Deployment-Modus Variable + boot-service Rolle

### Docker Compose Restart Policies
- `templates/container/infrastructure-core-compose.yml.j2`: `unless-stopped` → `on-failure`
- `templates/container/ingress-compose.yml.j2`: `unless-stopped` → `on-failure`

**Reason**: Verhindert automatischen Start beim System-Boot (wird vom dfld-boot Service gemanagt)

### Deployment-Rollen
- `roles/deploy_infrastructure_core/tasks/main.yml`: `creates` Bedingung entfernt
- `roles/deploy_ingress_core/tasks/main.yml`: `creates` Bedingung entfernt

**Reason**: Erlaubt Updates beim Boot

### Dokumentation
- `README.md`: Neue Abschnitte zu Boot-Service, Modi, Installation

## Bugfixes

### DataSource.py - DNMSi2cDataSource
**Problem**: "list index out of range" Fehler beim Datenlesen

**Lösung**:
- Timeout-Handling für Ready-Status
- Bounds-Prüfung vor Array-Zugriff
- Verbesserte Debug-Logging

## Installation & Verwendung

### Installation

```bash
# Mini-Modus (default)
./install_messstation.sh

# Full-Modus
./install_messstation.sh --mode full

# Remote-Installation
./install_messstation.sh --mode mini <IP>
./install_messstation.sh --mode full <IP>
```

### Konfigurationsänderungen zur Laufzeit

```bash
# 1. Datei bearbeiten
nano /boot/dfld.yml

# 2. Änderungen aktivieren
sudo systemctl restart dfld-boot
```

### Service-Management

```bash
# Status prüfen
sudo systemctl status dfld-boot

# Logs anschauen
sudo journalctl -u dfld-boot -f

# Manuell ausführen
sudo ansible-playbook -i localhost, -c local /opt/dfld/boot.yml
```

## Technische Details

### Workflow beim Boot

```
1. systemd dfld-boot Service startet
2. boot.yml Playbook wird ausgeführt
3. base_config.yml wird geladen (Variablen + deployment_mode)
4. /boot/dfld.yml wird geprüft (Timestamp)
5. Bei Änderung:
   - Hardware-Erkennung
   - update-stacks Rolle wird aufgerufen
   - Modus-spezifische Stack-Updates (Docker Compose oder Portainer API)
6. Container werden gestartet/aktualisiert
7. Service beendet (RemainAfterExit=yes in systemd)
```

### Variablen-Hierarchie

```
Installation:
  ├─ Playbook (mini.yml/full.yml)
  ├─ vars: deployment_mode, dfld_user, etc.
  └─ boot-service speichert alles in base_config.yml

Runtime:
  ├─ boot.yml lädt base_config.yml
  ├─ Zusätzlich /boot/dfld.yml für stationsspezifische Werte
  └─ Variablen sind verfügbar für Templates
```

## Backward Compatibility

✅ Alte Installationen weiterhin funktional
✅ Kein Breaking Change für existierende Deployments
✅ Opt-in via systemd Service (muss manuell aktiviert werden)

## Bekannte Einschränkungen

- systemd Service nutzt 300s Timeout für Stack-Updates
- Bei full-Modus: Portainer muss erreichbar sein
- DNMSi2c: Timeout von 1 Sekunde für Daten-Ready

## Zukünftige Improvements

- [ ] Parametrisierbare Timeouts
- [ ] Webhook für Konfigurationsänderungen
- [ ] Monitoring/Alerting bei Stack-Fehlern
- [ ] Automatische Backups vor Updates
- [ ] Multi-Endpoint Unterstützung im full-Modus

## Migration Guide (für existierende Installationen)

Falls Sie bereits einen älteren Branch verwenden:

```bash
# 1. Neuinstallation durchführen oder
./install_messstation.sh --mode mini

# 2. Nach Update: Boot-Service ist automatisch aktiviert
# 3. Prüfen ob alles funktioniert
sudo systemctl status dfld-boot
sudo journalctl -u dfld-boot

# 4. /boot/dfld.yml konfigurieren (optional)
# Die Service wird dies automatisch bei nächstem Boot übernehmen
```

## Mitwirkende & Lizenz

Siehe README.md für weitere Informationen.
