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
- .gitignore ist so konfiguriert, das weder ansible.cfg noch inventory.yml nach git pupliziert werden können.
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
