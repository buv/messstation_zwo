"""tsdb2http: Pi-side HTTPS-Backfill für SPL-Daten.

Liest periodisch aus der lokalen InfluxDB (Hot-Buffer der Pi) und schickt
die Daten via HTTPS+mTLS an den DFLD-Backend-Endpoint
`https://ingest.dfld.de/backfill/spl/<station>` (PR1, server-seitige
Komponente heisst dort `spl-backfill`). State-File `last-tx.txt` haelt
fest, ab welchem Zeitpunkt der naechste Lauf weitermacht.

Intervalle via DFLD_BACKFILL_INTERVAL (oder Legacy-Fallback DFLD_TX_TIER):
- hourly : 1h-Loop. Live-MQTT laeuft typisch parallel weiter; dieser Pfad
           ist Reconciliation oder primaerer Kanal je nach Live-Setting.
- daily  : 24h-Loop. LTE-Volumen-Cap. Monitoring statt Realtime.

Legacy-Mapping fuer Bestandsstationen mit altem DFLD_TX_TIER:
- live   -> hourly (Backfill stuendlich, Live laeuft separat via mqtt2mqtt)
- hourly -> hourly
- daily  -> daily
- off    -> Container wird vom Compose nicht gerendert (siehe template).

Die Architektur-Decisions und der Wire-Format-Vertrag stehen in
docs/backfill-architecture.md im dfld_server-Repo.
"""

import gzip
import json
import logging
import os
import pathlib
import sys
import time
from datetime import datetime, timezone

import requests
from influxdb import InfluxDBClient

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=LOG_LEVEL)

REQUIRED_ENV = (
    'INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE '
    'DFLD_REGION DFLD_STATION'
).split()
missing = [k for k in REQUIRED_ENV if k not in os.environ]
if missing:
    logging.error('missing required environment variables: %s', missing)
    sys.exit(1)

INGEST_URL  = os.environ.get('DFLD_INGEST_URL', 'https://ingest.dfld.de').rstrip('/')
STATION     = f"{int(os.environ['DFLD_REGION']):03d}-{int(os.environ['DFLD_STATION']):03d}"
MEASUREMENT = os.environ.get('INFLUXDB_MEASUREMENT', 'spl')

# Tier ableiten: bevorzugt neues DFLD_BACKFILL_INTERVAL, Fallback auf
# Legacy DFLD_TX_TIER (live -> hourly mapping).
_interval = (os.environ.get('DFLD_BACKFILL_INTERVAL') or '').lower()
if not _interval:
    _legacy = (os.environ.get('DFLD_TX_TIER') or '').lower()
    if _legacy == 'live':
        _interval = 'hourly'
    elif _legacy in ('hourly', 'daily', 'off'):
        _interval = _legacy
if not _interval:
    logging.error('neither DFLD_BACKFILL_INTERVAL nor DFLD_TX_TIER set')
    sys.exit(1)
TIER = _interval

# Container-internal paths. State volume is bind-mounted to host
# /opt/dfld/tsdb2http/ so it survives container recreation.
STATE_DIR   = pathlib.Path('/var/lib/tsdb2http')
STATE_FILE  = STATE_DIR / 'last-tx.txt'
BAD_DIR     = STATE_DIR / 'bad-batches'
CERT_PATH   = '/certs/client-cert.pem'
KEY_PATH    = '/certs/client-key.pem'
# Server-Cert von ingest.dfld.de ist Let's Encrypt → System-CA-Bundle
# (requests/certifi). Die DFLD-interne ca-cert.pem ist NUR fuer das
# Server-seitige Verifizieren der Pi-Client-Certs zustaendig, nicht
# fuer Pi-seitiges Verifizieren des Server-Certs.

# Cap pro Batch — 6h × 1Hz = 21600 Zeilen, ~1.7 MB roh, ~250 KB gz.
# Server akzeptiert 16 MB. Bei Wochen-Lange Catch-up nimmt die Loop
# das in mehreren Batches in Folge ohne Sleep.
MAX_BATCH_ROWS = 21600

TIER_INTERVALS = {
    'hourly': 3600,
    'daily':  86400,
}

if TIER == 'off':
    # Operator hat per dfld.yml das Intervall auf "off" gesetzt nachdem der
    # Container schon deployed war. Sauber idle bleiben statt restart-loop.
    logging.info('DFLD_BACKFILL_INTERVAL=off — idling (no backfill activity)')
    while True:
        time.sleep(86400)

if TIER not in TIER_INTERVALS:
    logging.error('unknown backfill interval %r (allowed: hourly, daily, off)', TIER)
    sys.exit(1)

# Initial-Delay nach Container-Start: gibt InfluxDB Zeit hochzufahren
# und vermeidet Hammern bei einer Boot-Storm-Situation.
STARTUP_DELAY = int(os.environ.get('STARTUP_DELAY', 60))


def _isoformat(dt):
    """UTC-aware datetime → RFC3339 mit Z (was ingest.dfld.de erwartet)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def read_state():
    """Letzten erfolgreich uebertragenen ts lesen.

    Fehlt das File oder ist es korrupt → fallback auf now() (kein
    automatischer Catch-up beim Erst-Run; Operator triggert manuell durch
    Editieren der Datei wenn er Catch-up will).
    """
    if not STATE_FILE.is_file():
        now_str = _isoformat(datetime.now(timezone.utc))
        STATE_FILE.write_text(now_str + '\n')
        logging.info('state file initialized to %s (no historical catch-up)', now_str)
        return now_str

    text = STATE_FILE.read_text().strip()
    try:
        # round-trip durch parse+format um Format zu validieren
        dt = datetime.fromisoformat(text.replace('Z', '+00:00'))
        return _isoformat(dt)
    except ValueError:
        now_str = _isoformat(datetime.now(timezone.utc))
        logging.warning(
            'state file content %r unparseable, resetting to %s',
            text, now_str,
        )
        STATE_FILE.write_text(now_str + '\n')
        return now_str


def write_state(ts_str):
    """Atomarer State-Update via tmp+rename."""
    tmp = STATE_FILE.with_suffix('.tmp')
    tmp.write_text(ts_str + '\n')
    tmp.rename(STATE_FILE)


def fetch_chunk(client, since_ts):
    """Bis zu MAX_BATCH_ROWS Zeilen aus InfluxDB ab since_ts holen.

    Filter `dB_A_avg > 0` weil mqtt2tsdb manchmal Punkte ohne Pegel
    schreibt (z.B. nur ts oder Bad-Frame); Backend erwartet Number.
    """
    query = (
        f"SELECT dB_A_avg, dB_A_min, dB_A_max FROM {MEASUREMENT} "
        f"WHERE time > '{since_ts}' AND dB_A_avg > 0 "
        f"ORDER BY time ASC LIMIT {MAX_BATCH_ROWS}"
    )
    logging.debug('influx query: %s', query)
    result = client.query(query)
    if not result.raw.get('series'):
        return []
    series = result.raw['series'][0]
    cols = series['columns']
    return [dict(zip(cols, v)) for v in series['values']]


def _ts_to_us_iso(t):
    """Influx-ts (ISO mit ns) explizit auf μs truncaten — entspricht FORMAT_A.

    Garantiert dass Backfill-Pfad denselben ts-String produziert wie Live-
    Pfad (sensor2mqtt.iso_now_us liefert direkt μs). Verhindert off-by-1-μs
    durch unterschiedliche Praezisions-Niveaus zwischen den Pfaden.
    """
    # Influx-Output: "2026-05-10T12:30:00.779682816Z" oder ".779682Z" oder
    # auch "...:00Z" (ohne fractional). Erst Z trennen, dann fractional
    # auf max 6 Stellen string-truncaten — kein Float, exakt.
    base, _z, _ = t.partition('Z')
    if '.' in base:
        sec, frac = base.split('.', 1)
        frac_us = (frac + '000000')[:6]   # rechtsseitig auffuellen, dann croppen
        return f"{sec}.{frac_us}Z"
    return f"{base}.000000Z"


def build_jsonl(rows):
    """Influx-Rows → NDJSON-bytes nach Wire-Format-Vertrag (FORMAT_A: ISO-μs)."""
    lines = []
    for r in rows:
        rec = {
            'ts':       _ts_to_us_iso(r['time']),
            'dB_A_avg': r['dB_A_avg'],
            'dB_A_min': r.get('dB_A_min'),
            'dB_A_max': r.get('dB_A_max'),
        }
        # json.dumps ist locale-unabhaengig, immer '.' Dezimaltrenner.
        lines.append(json.dumps(rec, ensure_ascii=False))
    return ('\n'.join(lines) + '\n').encode('utf-8')


def post_batch(payload_gz):
    """POST gzip JSONL an Backfill-Endpoint. Returnt (http_code, parsed_response)."""
    url = f"{INGEST_URL}/backfill/spl/{STATION}"
    headers = {
        'Content-Type':     'application/x-ndjson',
        'Content-Encoding': 'gzip',
    }
    resp = requests.post(
        url,
        data=payload_gz,
        headers=headers,
        cert=(CERT_PATH, KEY_PATH),
        verify=True,
        timeout=60,
    )
    try:
        body = resp.json()
    except ValueError:
        body = {'raw': resp.text[:500]}
    return resp.status_code, body


def save_bad_batch(rows, response_body):
    """Bad-Batch lokal sichern fuer Operator-Inspektion (HTTP 207)."""
    BAD_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = BAD_DIR / f'{stamp}.jsonl'
    with out.open('w', encoding='utf-8') as f:
        f.write(f'# server response: {json.dumps(response_body)}\n')
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + '\n')
    logging.warning('bad batch logged to %s', out)


def run_once(client):
    """Eine Backfill-Iteration. Returnt True wenn evtl. noch mehr zu holen ist."""
    since_ts = read_state()
    rows = fetch_chunk(client, since_ts)
    if not rows:
        logging.info('nothing to send since %s', since_ts)
        return False

    max_ts = rows[-1]['time']
    logging.info(
        'fetched %d rows from influx for station=%s window=(%s..%s]',
        len(rows), STATION, since_ts, max_ts,
    )

    payload = build_jsonl(rows)
    # Level 9 statt default 6: ~30% kleinere Bodies bei vernachlässigbarem
    # Pi-CPU-Cost (~250ms statt ~100ms auf Pi Zero 2W pro Batch).
    payload_gz = gzip.compress(payload, compresslevel=9)
    logging.info('posting %d rows (%d bytes raw → %d bytes gz)',
                 len(rows), len(payload), len(payload_gz))

    try:
        code, body = post_batch(payload_gz)
    except requests.RequestException as e:
        logging.error('http error, will retry next cycle: %s', e)
        return False

    if code == 200:
        logging.info('200 OK, written=%s — advancing state to %s',
                     body.get('written'), max_ts)
        write_state(max_ts)
        # Wenn wir das Batch-Limit erreicht haben, koennten weitere
        # Rows warten — direkt nochmal laufen ohne Sleep.
        return len(rows) >= MAX_BATCH_ROWS

    if code == 207:
        # Multi-Status: Backend hat valide Zeilen geschluckt, einige
        # rejected. State trotzdem updaten (sonst Endlos-Retry derselben
        # bad row); rejected rows lokal sichern.
        logging.warning(
            '207 multi-status: written=%s errors=%s — advancing state',
            body.get('written'), body.get('error_count'),
        )
        save_bad_batch(rows, body)
        write_state(max_ts)
        return len(rows) >= MAX_BATCH_ROWS

    if code == 403:
        logging.error(
            '403 forbidden — station %s likely not in server-side '
            'SPL_BACKFILL_ALLOWED_STATIONS. Sleeping until next cycle.',
            STATION,
        )
        return False

    # 4xx (sonstig) oder 5xx: log + retry next cycle, kein State-Update
    logging.error('http %d, body=%s — will retry next cycle', code, body)
    return False


def main():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    interval = TIER_INTERVALS[TIER]
    logging.info(
        'tsdb2http starting: station=%s tier=%s interval=%ds ingest=%s',
        STATION, TIER, interval, INGEST_URL,
    )
    logging.info('startup delay %ds...', STARTUP_DELAY)
    time.sleep(STARTUP_DELAY)

    influxdb_host, influxdb_port = os.environ['INFLUXDB_SERVER'].split(':')
    while True:
        try:
            client = InfluxDBClient(
                host=influxdb_host,
                port=int(influxdb_port),
                username=os.environ['INFLUXDB_USERNAME'],
                password=os.environ['INFLUXDB_PASSWORD'],
            )
            client.switch_database(os.environ['INFLUXDB_DATABASE'])
            # Catch-up-Loop: solange das vorige Batch das Limit ausgeschoepft
            # hat, sofort weiterarbeiten.
            while run_once(client):
                pass
        except Exception as e:
            logging.error('iteration failed: %s', e, exc_info=(LOG_LEVEL == 'DEBUG'))

        logging.debug('sleeping %ds (tier=%s)', interval, TIER)
        time.sleep(interval)


if __name__ == '__main__':
    main()
