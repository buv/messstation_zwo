#!/usr/bin/env python3
"""
Migration script for DFLD Messstation ZWO.

Exports all data from the old Messstation (PostgreSQL + InfluxDB) to local
files on the machine running this script, then imports them into the new
InfluxDB-only system. Designed to run remotely from the Ansible controller.

Credentials are read from the Ansible inventory.yml (--inventory flag), so
no passwords need to be passed on the command line.

Workflow (same hardware, remote execution from Ansible controller):

  1. BEFORE reinstallation - export from old system:
     python migrate_to_influxdb.py export --host <raspi-ip>

  2. Reinstall Messstation ZWO on the Raspi

  3. AFTER reinstallation - import into new system:
     python migrate_to_influxdb.py import --host <raspi-ip>

  4. Clean up local migration files:
     rm -rf ./migration

The script auto-detects inventory.yml in the current directory or the
repository root. Use --inventory to specify a different path.

Requirements:
  export: pip install psycopg2-binary influxdb pyyaml
  import: pip install influxdb pyyaml
"""

import argparse
import datetime
import logging
import os
import sys

BATCH_SIZE = 5000
DEFAULT_DIR = './migration'

PG_EVENTS_FILE = 'postgres_events.line'
INFLUX_DATA_FILE = 'influxdb_data.line'


# ---------------------------------------------------------------------------
# Inventory parsing
# ---------------------------------------------------------------------------

def find_inventory():
    """Auto-detect inventory.yml in current dir or repo root."""
    candidates = [
        'inventory.yml',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'inventory.yml'),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def resolve_ansible_refs(vars_dict):
    """Resolve simple {{ var }} references in Ansible variable values."""
    resolved = dict(vars_dict)
    changed = True
    max_iterations = 10
    while changed and max_iterations > 0:
        changed = False
        max_iterations -= 1
        for key, value in resolved.items():
            if isinstance(value, str) and '{{' in value:
                for ref_key, ref_value in resolved.items():
                    if isinstance(ref_value, str) and '{{' not in ref_value:
                        placeholder = '{{ ' + ref_key + ' }}'
                        if placeholder in value:
                            resolved[key] = value.replace(placeholder, ref_value)
                            changed = True
                            value = resolved[key]
    return resolved


def load_inventory(path):
    """Load credentials from Ansible inventory.yml."""
    import yaml

    logging.info(f"Reading inventory from {path}")
    with open(path, 'r') as f:
        data = yaml.safe_load(f)

    raw_vars = data.get('all', {}).get('vars', {})
    resolved = resolve_ansible_refs(raw_vars)

    return {
        'influxdb_username': resolved.get('influxdb_username', 'dfld'),
        'influxdb_password': resolved.get('influxdb_password', 'dfld'),
        'postgres_username': resolved.get('postgres_username', 'dfld'),
        'postgres_password': resolved.get('postgres_password', 'dfld'),
        'postgres_database': resolved.get('postgres_database', 'dfld'),
    }


# ---------------------------------------------------------------------------
# Line protocol helpers
# ---------------------------------------------------------------------------

def escape_measurement(s):
    return s.replace(',', r'\,').replace(' ', r'\ ')


def escape_tag(s):
    return s.replace(',', r'\,').replace('=', r'\=').replace(' ', r'\ ')


def escape_field_str(s):
    return s.replace('\\', '\\\\').replace('"', r'\"')


def rfc3339_to_ns(ts_str):
    """Convert RFC3339 timestamp string to nanoseconds since epoch."""
    if '.' in ts_str:
        dt = datetime.datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S.%fZ')
    else:
        dt = datetime.datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%SZ')
    epoch = datetime.datetime(1970, 1, 1)
    return int((dt - epoch).total_seconds() * 1e9)


def datetime_to_ns(dt):
    """Convert a datetime object to nanoseconds since epoch."""
    epoch = datetime.datetime(1970, 1, 1)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return int((dt - epoch).total_seconds() * 1e9)


def point_to_line(measurement, tags, fields, timestamp_ns):
    """Build an InfluxDB line protocol string from components."""
    tag_str = ""
    if tags:
        tag_parts = [f"{k}={escape_tag(v)}" for k, v in sorted(tags.items())]
        tag_str = "," + ",".join(tag_parts)

    field_parts = []
    for k, v in sorted(fields.items()):
        if isinstance(v, str):
            field_parts.append(f'{k}="{escape_field_str(v)}"')
        elif isinstance(v, bool):
            field_parts.append(f"{k}={'T' if v else 'F'}")
        elif isinstance(v, int):
            field_parts.append(f"{k}={v}i")
        elif isinstance(v, float):
            field_parts.append(f"{k}={repr(v)}")
    field_str = ",".join(field_parts)

    return f"{escape_measurement(measurement)}{tag_str} {field_str} {timestamp_ns}"


# ---------------------------------------------------------------------------
# Export: PostgreSQL events -> line protocol file
# ---------------------------------------------------------------------------

def export_postgres_events(output_file, pg_host, pg_port, pg_user, pg_password, pg_database):
    """Export flyover events from PostgreSQL to InfluxDB line protocol file."""
    import psycopg2
    import psycopg2.extras

    logging.info(f"Connecting to PostgreSQL at {pg_host}:{pg_port} db={pg_database}...")
    conn = psycopg2.connect(
        host=pg_host, port=pg_port,
        user=pg_user, password=pg_password,
        dbname=pg_database,
    )
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cursor.execute("SELECT COUNT(*) FROM event_raw")
    total = cursor.fetchone()[0]
    logging.info(f"Found {total} flyover events in PostgreSQL.")

    if total == 0:
        logging.info("No events to export.")
        conn.close()
        return 0

    cursor.execute(
        "SELECT eventtime, dist, hex, flight, r, t, descr, rssi "
        "FROM event_raw ORDER BY eventtime ASC"
    )

    count = 0
    with open(output_file, 'w') as f:
        for row in cursor:
            tags = {}
            for k in ['hex', 'flight', 'r', 't']:
                if row[k] is not None:
                    val = str(row[k]).strip()
                    if val:
                        tags[k] = val

            fields = {}
            if row['dist'] is not None:
                fields['dist'] = float(row['dist'])
            if row['rssi'] is not None:
                fields['rssi'] = float(row['rssi'])
            if row['descr'] is not None and str(row['descr']).strip():
                fields['descr'] = str(row['descr']).strip()

            if not fields:
                continue

            ts_ns = datetime_to_ns(row['eventtime'])
            line = point_to_line("event_raw", tags, fields, ts_ns)
            f.write(line + "\n")
            count += 1

            if count % 1000 == 0:
                logging.info(f"  exported {count}/{total} events...")

    conn.close()
    logging.info(f"PostgreSQL export complete: {count} events -> {output_file}")
    return count


# ---------------------------------------------------------------------------
# Export: InfluxDB -> line protocol file
# ---------------------------------------------------------------------------

def export_influxdb(output_file, host, port, user, password, database, measurements=None):
    """Export all measurements from InfluxDB to line protocol file."""
    from influxdb import InfluxDBClient

    logging.info(f"Connecting to InfluxDB at {host}:{port} db={database}...")
    client = InfluxDBClient(host=host, port=int(port), username=user, password=password)
    client.switch_database(database)

    if measurements is None:
        result = client.get_list_measurements()
        measurements = [m['name'] for m in result]

    logging.info(f"Exporting measurements: {measurements}")
    total_points = 0

    with open(output_file, 'w') as f:
        for measurement in measurements:
            logging.info(f"  exporting '{measurement}'...")
            # Query SHOW TAG KEYS to distinguish tags from fields
            tag_keys = set()
            tag_result = client.query(f'SHOW TAG KEYS FROM "{measurement}"', database=database)
            for row in tag_result.get_points():
                tag_keys.add(row['tagKey'])

            offset = 0
            measurement_points = 0

            while True:
                query = f'SELECT * FROM "{measurement}" LIMIT {BATCH_SIZE} OFFSET {offset}'
                result = client.query(query, database=database)
                points = list(result.get_points())

                if not points:
                    break

                for point in points:
                    ts_str = point.pop('time')
                    tags = {}
                    fields = {}

                    for key, value in point.items():
                        if value is None:
                            continue
                        if key in tag_keys:
                            tags[key] = str(value)
                        elif isinstance(value, str):
                            fields[key] = value
                        elif isinstance(value, bool):
                            fields[key] = value
                        elif isinstance(value, int):
                            fields[key] = value
                        elif isinstance(value, float):
                            fields[key] = value

                    if not fields:
                        continue

                    ts_ns = rfc3339_to_ns(ts_str)
                    line = point_to_line(measurement, tags, fields, ts_ns)
                    f.write(line + "\n")
                    measurement_points += 1

                offset += BATCH_SIZE

            logging.info(f"  exported {measurement_points} points from '{measurement}'.")
            total_points += measurement_points

    logging.info(f"InfluxDB export complete: {total_points} points -> {output_file}")
    return total_points


# ---------------------------------------------------------------------------
# Import: line protocol file -> InfluxDB
# ---------------------------------------------------------------------------

def import_line_file(input_file, host, port, user, password, database):
    """Import a line protocol file into InfluxDB."""
    from influxdb import InfluxDBClient

    if not os.path.exists(input_file):
        logging.warning(f"File not found, skipping: {input_file}")
        return 0

    file_size = os.path.getsize(input_file)
    if file_size == 0:
        logging.info(f"File is empty, skipping: {input_file}")
        return 0

    logging.info(f"Connecting to InfluxDB at {host}:{port} db={database}...")
    client = InfluxDBClient(host=host, port=int(port), username=user, password=password)

    databases = [db['name'] for db in client.get_list_database()]
    if database not in databases:
        logging.info(f"Creating database '{database}'...")
        client.create_database(database)
    client.switch_database(database)

    logging.info(f"Importing {input_file} ({file_size / 1024:.1f} KiB)...")
    total_lines = 0
    batch = []

    with open(input_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            batch.append(line)
            total_lines += 1

            if len(batch) >= BATCH_SIZE:
                payload = "\n".join(batch)
                client.write_points(payload, database=database, protocol='line')
                logging.info(f"  imported {total_lines} points...")
                batch = []

    if batch:
        payload = "\n".join(batch)
        client.write_points(payload, database=database, protocol='line')

    logging.info(f"Import complete: {total_lines} points from {input_file}")
    return total_lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Migration tool for DFLD Messstation ZWO",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--log-level', default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'])
    parser.add_argument('--dry-run', action='store_true',
                        help='Simulate without writing files or data')
    parser.add_argument('--host', required=True,
                        help='Raspi hostname/IP')
    parser.add_argument('--inventory', default=None,
                        help='Path to Ansible inventory.yml (auto-detected if not specified)')
    subparsers = parser.add_subparsers(dest='command', required=True)

    # --- export ---
    p_export = subparsers.add_parser(
        'export',
        help='Export all data from old system (PostgreSQL + InfluxDB) to local files')
    p_export.add_argument('--output-dir', default=DEFAULT_DIR,
                          help=f'Local directory for export files (default: {DEFAULT_DIR})')
    p_export.add_argument('--measurements', nargs='*',
                          help='InfluxDB measurements to export (default: all)')

    # --- import ---
    p_import = subparsers.add_parser(
        'import',
        help='Import local migration files into new InfluxDB')
    p_import.add_argument('--input-dir', default=DEFAULT_DIR,
                          help=f'Local directory with export files (default: {DEFAULT_DIR})')

    args = parser.parse_args()
    logging.basicConfig(format='%(asctime)s - %(levelname)s: %(message)s',
                        level=args.log_level)

    # Load credentials from inventory
    inventory_path = args.inventory or find_inventory()
    if inventory_path is None:
        parser.error(
            "Could not find inventory.yml. Run this script from the "
            "messstation_zwo repository directory, or use --inventory."
        )
    creds = load_inventory(inventory_path)

    if args.command == 'export':
        output_dir = args.output_dir
        os.makedirs(output_dir, exist_ok=True)
        logging.info(f"Export directory: {os.path.abspath(output_dir)}")

        pg_file = os.path.join(output_dir, PG_EVENTS_FILE)
        influx_file = os.path.join(output_dir, INFLUX_DATA_FILE)

        # Step 1: PostgreSQL flyover events
        logging.info("=== Step 1/2: Exporting PostgreSQL flyover events ===")
        if not args.dry_run:
            pg_count = export_postgres_events(
                pg_file,
                pg_host=args.host, pg_port=5432,
                pg_user=creds['postgres_username'],
                pg_password=creds['postgres_password'],
                pg_database=creds['postgres_database'],
            )
        else:
            pg_count = 0
            logging.info("Dry run - skipping PostgreSQL export.")

        # Step 2: InfluxDB time series
        logging.info("=== Step 2/2: Exporting InfluxDB time series ===")
        if not args.dry_run:
            influx_count = export_influxdb(
                influx_file,
                host=args.host, port=8086,
                user=creds['influxdb_username'],
                password=creds['influxdb_password'],
                database='dfld',
                measurements=args.measurements,
            )
        else:
            influx_count = 0
            logging.info("Dry run - skipping InfluxDB export.")

        logging.info("=== Export complete ===")
        logging.info(f"  PostgreSQL events: {pg_count} -> {pg_file}")
        logging.info(f"  InfluxDB points:   {influx_count} -> {influx_file}")
        logging.info(f"")
        logging.info(f"Next steps:")
        logging.info(f"  1. Reinstall Messstation ZWO on the Raspi")
        logging.info(f"  2. Run: python migrate_to_influxdb.py --host <raspi-ip> import --input-dir {output_dir}")
        logging.info(f"  3. Clean up: rm -rf {output_dir}")

    elif args.command == 'import':
        input_dir = args.input_dir
        if not os.path.isdir(input_dir):
            logging.error(f"Input directory not found: {input_dir}")
            return 1

        pg_file = os.path.join(input_dir, PG_EVENTS_FILE)
        influx_file = os.path.join(input_dir, INFLUX_DATA_FILE)

        total = 0

        # Step 1: Import PostgreSQL events (as InfluxDB line protocol)
        logging.info("=== Step 1/2: Importing flyover events ===")
        if not args.dry_run:
            total += import_line_file(
                pg_file,
                host=args.host, port=8086,
                user=creds['influxdb_username'],
                password=creds['influxdb_password'],
                database='dfld',
            )
        else:
            logging.info("Dry run - skipping import.")

        # Step 2: Import InfluxDB time series
        logging.info("=== Step 2/2: Importing time series data ===")
        if not args.dry_run:
            total += import_line_file(
                influx_file,
                host=args.host, port=8086,
                user=creds['influxdb_username'],
                password=creds['influxdb_password'],
                database='dfld',
            )
        else:
            logging.info("Dry run - skipping import.")

        logging.info(f"=== Import complete: {total} points total ===")
        logging.info(f"You can now remove the migration files: rm -rf {input_dir}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
