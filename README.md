# DFLD

Wir machen Lärm sichtbar.

Der [Deutsche Fluglärmdienst e.V.](https://www.dfld.de/) ist ein eingetragener gemeinnütziger Verein, der sich für die quantitative Erfassung aller Emissionen des Luftverkehrs und deren transparenter Darstellung mit einer Langzeit-Archivierung engagiert.

Wir bieten Fluglärm-Messwerte, Flugspuren, Live-Radar, Bilder von Überwachungskameras sowie Lärm-, Flughafen- und Wetter-Statistiken.

Unser Archiv reicht bis ins Jahr 2002 zurück.

# Messstation ZWO

Ansible Playbook zum Aufsetzen einer [DFLD](https://www.dfld.de/) Messstation ZWO auf einem Raspberry Pi.

## Voraussetzungen
- Ein Raspberry Pi der neuesten Genaration
- BME280 Sensor (optional)
- ...

## Installation

- Ein Admin-Account mit sudo-Rechten auf dem Raspberry Pi
- SSH-Logins freigegeben falls Messstation ZWO auf Remote Raspberry Pi installiert werden soll.
- Fingerprint des Remote Raspis in den lokalen known_keys. Alternative kann `host_key_checking` in  Zeile 5 in der ansible.cfg ausgeschaltet werden.
- ansible.cfg und inventory.yml aus den .example Dateien erzeugen.
- Die Datei ansible.cfg kann nach eigenen Wünschen angepasst werden. Kopieren der ansible.cfg.exmaple ist aber ausreichend.
- In der Datei inventory.yml können bzw. müssen die Default User und Passwörter für Portainer, Grafana und InfluxDB angepasstw werden.
- Bei einer Remote-Installation müssen die Zugangsdaten für den User auf dem Remote Raspberry PI, der die Ansible Tasks ausführt eingerichtet werden. Dabei kann Username, sudo- und ssh-Password gesetzt werden. Es wird allerdings empfohlen für die Authentifizierung ein ssh Schlüsselpaar zu verwenden.
- Benötigt, bei einer lokalen Installation, der User, der das Playbook ausführt, ein Password für sudo, so kann dies in der inventory.yml eingetragen werden. 
- In der inventory.yml können/müssen vor Ausführung noch die Passwörter für die Admin-Accounts von Portainer, Grafana und der InfluxDB gesetzt werden. Die Passwörter sind freiwählbar, werden während der Installation gesetzt. Nach der Installation 
- .gitignore ist so konfiguriert, das weder ansible.cfg noch inventory.yml nach git pupliziert werden können.
- Starten des Playbooks mit dem Befehl: `./dfld.yml` 
- Optional kann man das Playbook erstmal im Check-Mode starten, um zu testen, ob die Verbindung zum Remote Raspberry Pi und sudo für den User richtig eingerichtet ist. Dies erfolgt mit dem Befehl: `./dfld.yml --check` 
Beim ersten Lauf wird das Playbook im Check-Mode allerdings mit einem Fehlerabbrechen. Da das Playbook im Check-Mode nichts installiert, kann es in späteren Verlauf sich nicht mit dem Portainer Container verbinden und bricht dann mit einer Fehlermeldung ab.


```bash
# Ansible Controller System auf den aktuellen Patchstand bringen
sudo apt-get update

# Ansible und git installieren
sudo apt-get install -y git ansible

# Dieses Repository clone
git clone https://github.com/buv/messstation_zwo.git

# In das Repository wechseln
cd messstation_zwo

# Roles aus der Ansible Galaxy installieren
ansible-galaxy role install -r requirements.yml -p ./roles

# Anpassungen an Konfiguration vornehmen
cp ansible.cfg.example ansible.cfg
cp inventory.local.yml.example inventory.yml
# oder
cp inventory.yml.example inventory.yml

vim/nano inventory.yml
vim/nano ansible.cfg

# Playbook starten und Messstation ZWO installieren.
./dfld.yml
```

Mehr zum Thema ansible.cfg und Inventorys findet sich in der [Ansible Dokumentation](https://docs.ansible.com/ansible/latest/index.html).