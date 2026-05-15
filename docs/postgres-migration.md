# Migration alter PostgreSQL-Stationen nach InfluxDB

Skizze für das Migrationswerkzeug, mit dem Bestandsstationen (altes Schema
mit PostgreSQL/PostGIS) auf das aktuelle InfluxDB-only-System überführt
werden — so, dass die migrierten Überflüge in der neuen Darstellung
(Grafana-Dashboard inkl. Annotationen) vollständig erscheinen.

## Ausgangslage

Zwei Stationen laufen seit rund einem Jahr im alten Schema:

- **Flyover-Daten in PostgreSQL/PostGIS**
  - `trajectory` — Flugbahnen als `LINESTRING ZM` (lon, lat, alt, t) plus
    `icao`, `t0`, `min_dist`, `rssi[]`, `alt_baro`
  - `event_raw` — Aircraft-Stammdaten je Überflug: `hex`, `flight`, `r`
    (Registrierung), `t` (Typ), `descr`, `dist`, `eventtime`
- **Zeitreihen (SPL etc.) in der alten InfluxDB** — Measurement `spl` u. a.

## Ziel

InfluxDB-only, Schema identisch zu dem, was `detect_flyover.py` heute
schreibt — damit Dashboard, Histogramme und die Überflug-Annotationen
U1/U2/U3 ohne Sonderbehandlung auch für die migrierten Altdaten greifen.

Zielschema `event_raw` (Measurement in DB `dfld`):

| Art   | Schlüssel | Quelle |
|-------|-----------|--------|
| Tag   | `hex`, `flight`, `r`, `t` | `trajectory.icao` + JOIN `event_raw` |
| Field | `dist_xy`, `dist_z`, `alt_baro` | berechnet aus der Trajektorie |
| Field | `rssi` | `trajectory.rssi[]` am dichtesten Punkt, sonst `event_raw.rssi` |
| Field | `descr` | `event_raw.descr` |
| Field | `title`, `text` | **vorgefertigte Annotations-Strings (neu)** |
| Zeit  | Punkt-Zeitstempel | `trajectory.t0` (echter Überflugzeitpunkt) |

## Vorhandenes Werkzeug

`files/dfld_box/migrate_to_influxdb.py` deckt bereits ab:

- `export` — PostgreSQL `trajectory` LEFT JOIN `event_raw` (Aircraft-Tags
  über `icao` + Zeitfenster) → InfluxDB line protocol; zusätzlich Export
  der alten InfluxDB-Measurements
- `import` — line protocol → neue InfluxDB
- Geometrie — `compute_min_geometry()` ermittelt am 3D-dichtesten
  Trajektorienpunkt `dist_xy` (horizontal), `dist_z` (vertikal),
  `alt_baro`
- Zeitstempel — `t0` des Aircraft (genauer als die Schreib-`eventtime`)
- Orphan-Events (`event_raw` ohne passende `trajectory`) werden gezählt
  und übersprungen — ohne Geometrie kein `dist_xy`/`dist_z`

## Lücke: `title`/`text` fehlen

Die Grafana-Annotationen (vertikale Überflug-Striche U1/U2/U3) lesen zwei
vorgefertigte Felder aus `event_raw`:

- `title` — HTML-Link `<a href="…adsbexchange…">Callsign/ICAO</a>`
- `text` — Annotations-Text `r: NNN m<br>h: NNN m`

`detect_flyover.py` schreibt diese Felder für neue Überflüge mit; das
Migrationsskript tut es **nicht**. Migrierte Altflüge würden daher zwar in
Tabelle und Histogramm auftauchen, aber **nicht als Annotation**.

## Skizze der Erweiterung

In `_export_trajectories()`, im `fields`-Aufbau (direkt nachdem `geom`
vorliegt und `dist_xy` gesetzt wurde), analog zu `detect_flyover.write_event`:

```python
# label: Callsign bevorzugt, sonst ICAO als Fallback (InfluxQL kennt
# kein COALESCE — der Fallback muss beim Schreiben aufgelöst werden).
callsign = tags.get('flight', '').strip()
icao     = tags.get('hex', '').strip()
label    = callsign if callsign else (icao if icao else '?')

dxy = int(round(geom['dist_xy']))
dz  = int(round(geom['dist_z']))

# title/text als FIELDS (nicht Tags!) — als Tags würde jeder neue
# Wert eine eigene Series anlegen (Series-Explosion).
fields['title'] = (f'<a href="https://globe.adsbexchange.com/?icao={icao}" '
                   f'target="_blank">{label}</a>')
# <br> statt \n — Grafana rendert das Annotations-text-Feld als HTML.
fields['text']  = f'r: {dxy} m<br>h: {dz} m'
```

Damit sind die migrierten Events sofort vollständig — ein separater
Backfill-Lauf (wie `backfill_event_raw.py`, das nur `title`/`text` für eine
bereits migrierte Station nachreichte) entfällt.

## Workflow (pro Station, Ausführung vom Ansible-Controller)

```
# 1. VOR der Neuinstallation — Altdaten exportieren
python migrate_to_influxdb.py --host <raspi-ip> export

# 2. Messstation ZWO auf dem Raspi neu installieren

# 3. NACH der Neuinstallation — in neue InfluxDB importieren
python migrate_to_influxdb.py --host <raspi-ip> import

# 4. Lokale Migrationsdateien aufräumen
rm -rf ./migration
```

Zugangsdaten und Stationsposition (`station_lon/lat/alt`, nötig für die
`dist_xy`/`dist_z`-Berechnung) liest das Skript aus `inventory.yml`.

## Designentscheidungen und Randbedingungen

- **Echter Zeitstempel** — jeder Überflug behält `t0`, den realen
  Überflugzeitpunkt. InfluxDB akzeptiert beliebig in der Vergangenheit
  liegende Punkte; das ist Kernfunktion einer Zeitreihen-DB, kein Trick.
- **Keine Retention-Grenze auf `dfld`** — die Messdaten-DB wird bewusst
  nicht gekappt (siehe `roles/deploy_container/tasks/setup_influxdb_retention.yml`,
  das nur `_internal`/`local_monitoring` begrenzt). Ein Jahr Historie
  importiert daher vollständig; Punkte würden nur verworfen, wenn sie
  älter als eine Retention Policy wären.
- **`label` nur als Field-Inhalt, nie als Tag** — frühere Versuche mit
  `label` als Tag verursachten eine Series-Explosion. Der Wert lebt
  ausschließlich im `title`-String.
- **Orphan-Events werden übersprungen** — `event_raw`-Einträge ohne
  zugehörige `trajectory` haben keine Geometrie; im neuen Schema gibt es
  ohne `dist_xy`/`dist_z` nichts Verwertbares (die alte 3D-Skalar-`dist`
  wird nicht mehr geführt).
- **Idempotenz** — ein erneuter Import schreibt dieselben Punkte
  (gleiches Tag-Set, gleicher Zeitstempel) und überschreibt sie per
  Field-Merge, statt zu duplizieren.

## Offen / noch umzusetzen

- [x] `title`/`text`-Erzeugung in `_export_trajectories()` ergänzt
- [ ] Migration auf einer der beiden Bestandsstationen verifizieren
      (Annotationen U1/U2/U3 erscheinen, Histogramme zeigen Altflüge)
