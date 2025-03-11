# messstation_zwo

Ansible Skripte zum Aufsetzen einer neuen Messstation.

## Lokale Installation
Voraussetzungen:
- Adminaccount mit Namen 'dfld'

```bash
sudo apt-get update
sudo apt-get install -y git ansible
git clone https://github.com/buv/messstation_zwo.git
cd messstation_zwo
./install_local.sh
```

## Remote Installation

Voraussetzungen:
- Adminaccount mit Namen 'dfld' auf Remote Raspi
- SSH-Logins freigegeben auf Remote Raspi
- Ansible Installation auf lokalem Rechner
- Fingerprint des Remote Raspis in den lokalen known_keys

```bash
git clone https://github.com/buv/messstation_zwo.git
cd messstation_zwo
./install_remote.sh <remote-host>
```
