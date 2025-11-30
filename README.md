# Messstation ZWO

Ansible Playbook zum Aufsetzen einer [DFLD](https://www.dfld.de/) Messstation ZWO auf einem Raspberry Pi.

## Voraussetzungen
- Ein Raspberry Pi mit 2GB RAM oder mehr für die den Modus "full"
- Ein Raspberry Pi Zero 2 für den Modus "mini" 
- Ein Schallpegelsensor aus dem DNMS Projekt (Anbindung über i2c) oder das AK-Modulbus Mikrofon (Anbindung über USB)
- Ein ADS-B Empfänger, der die Flugzeugdaten über USB bereitstellt (nur im Modus "full")
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
- Die stationsspezifischen Parameter werden unter /boot/dfld.yml abgelegt. Beispiel: 

```yaml
# /boot/dfld.yml
# aus Mail vom DFLD:
dfld_region: "xxx"
dfld_station: "yyy"
dfld_cksum: "zzzz" # dezimal
dfld_liveview: ""  
dfld_legacy: ""    

# Beispiel für Geokodierung:
station_lon: "8.25"
station_lat: "50.0"
station_alt: "110.0"
station_city: "Mainz"
```

Vorgehen für die lokale Installation mit Mode "mini" (default):

```bash
# Ansible Controller System auf den aktuellen Patchstand bringen
sudo apt-get update

# Ansible und git installieren
sudo apt-get install -y git ansible

# Dieses Repository clone
git clone https://github.com/buv/messstation_zwo.git

# In das Repository wechseln
cd messstation_zwo

# Anpassungen an Konfiguration vornehmen
cp inventory.yml.example inventory.yml

emacs/vim/nano /boot/dfld.yml
emacs/vim/nano inventory.yml
emacs/vim/nano ansible.cfg

# Playbook lokal starten und Messstation ZWO installieren.
./install_messstation.sh

# alternativ Playbook remote ausführen.
./install_messstation.sh <IP Adresse Remote Raspi>
```

Mehr zum Thema ansible.cfg und Inventorys findet sich in der [Ansible Dokumentation](https://docs.ansible.com/ansible/latest/index.html).

## Automatischer Boot-Service

Nach der Installation wird ein systemd-Service (`dfld-boot`) eingerichtet, der bei jedem Systemstart:

1. Die Basis-Konfiguration aus `/opt/dfld/config/base_config.yml` lädt
2. Prüft, ob `/boot/dfld.yml` seit dem letzten Start geändert wurde (Timestamp-Vergleich)
3. Hardware-Erkennung durchführt (I2C-Sensoren, USB-Geräte)
4. Bei Änderungen die Docker Compose Dateien aktualisiert
5. Alle Container startet

### Konfigurationsänderungen zur Laufzeit

Um stationsspezifische Parameter zu ändern:

1. Datei `/boot/dfld.yml` bearbeiten
2. System neu starten oder den Boot-Service manuell ausführen:

```bash
sudo systemctl restart dfld-boot
```

### Service-Status prüfen

```bash
# Status des Boot-Services
sudo systemctl status dfld-boot

# Logs des Boot-Services
sudo journalctl -u dfld-boot

# Manueller Start des Boot-Playbooks
sudo ansible-playbook -i localhost, -c local /opt/dfld/boot.yml
```

### Restart-Policy der Container

Die Container verwenden die Restart-Policy `on-failure`, d.h.:
- Bei Abstürzen werden Container automatisch neu gestartet
- Beim Systemstart werden Container **nicht** automatisch gestartet (dies übernimmt der dfld-boot Service)
- Dies ermöglicht eine kontrollierte Aktualisierung der Konfiguration vor dem Start
