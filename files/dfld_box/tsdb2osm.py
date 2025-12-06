#!/usr/bin/env python3
"""
tsdb2osm.py - Transfer aggregated InfluxDB data to openSenseMap

This script reads data from InfluxDB, aggregates it over a configurable interval,
and sends it to the openSenseMap API via HTTP POST.

Environment Variables:
    INFLUXDB_SERVER: InfluxDB server address (format: host:port)
    INFLUXDB_USERNAME: InfluxDB username
    INFLUXDB_PASSWORD: InfluxDB password
    OSM_STATION_ID: 12 byte hex ID of the openSenseMap station/account
    OSM_SENSORS: Comma-separated list of sensor configurations
                 Format: sensor_id:db_name:measurement:column_name:aggr_mode,...
                 Example: 5f8e9a1b2c3d4e5f6a7b8c9d:noise_db:noise_measurement:dB_A_avg:log
                 aggr_mode: "lin" for linear aggregation, "log" for logarithmic aggregation
    OSM_API_KEY: 32 byte hex value of the API token
    OSM_INTERVAL: Time in seconds between aggregation jobs (default: 300)
    OSM_API_URL: Base URL for openSenseMap API (default: https://api.opensensemap.org)
    TZ: Timezone (default: UTC)
    LOG_LEVEL: Logging level (default: INFO)
"""

import os
import sys
import time
import json
import logging
import datetime
import math
from typing import List, Dict, Optional, Tuple

import pytz
import requests
from influxdb import InfluxDBClient

# Configure logging
level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

# Required environment variables
REQUIRED_ENV_VARS = [
    'INFLUXDB_SERVER',
    'INFLUXDB_USERNAME',
    'INFLUXDB_PASSWORD',
    'OSM_STATION_ID',
    'OSM_SENSORS',
    'OSM_API_KEY'
]

# Optional environment variables with defaults
OSM_INTERVAL = int(os.environ.get('OSM_INTERVAL', '300'))
OSM_API_URL = os.environ.get('OSM_API_URL', 'https://api.opensensemap.org')
TZ = os.environ.get('TZ', 'UTC')


def validate_environment():
    """Validate that all required environment variables are set."""
    missing_env = [var for var in REQUIRED_ENV_VARS if var not in os.environ]
    if missing_env:
        logging.error('Following environment variables not set: %s', missing_env)
        sys.exit(1)


def parse_sensor_config(sensor_config_str: str) -> List[Dict[str, str]]:
    """
    Parse sensor configuration string into list of sensor dictionaries.
    
    Args:
        sensor_config_str: Comma-separated list of sensor configurations
                          Format: sensor_id:db_name:measurement:column_name:aggr_mode,...
    
    Returns:
        List of dictionaries with keys: sensor_id, database, measurement, column, aggr_mode
    """
    sensors = []
    for config in sensor_config_str.split(','):
        config = config.strip()
        if not config:
            continue
        
        parts = config.split(':')
        if len(parts) != 5:
            logging.error('Invalid sensor configuration: %s (expected format: sensor_id:db_name:measurement:column_name:aggr_mode)', config)
            continue
        
        sensor_id, database, measurement, column, aggr_mode = parts
        aggr_mode = aggr_mode.strip().lower()
        
        if aggr_mode not in ['lin', 'log']:
            logging.error('Invalid aggr_mode "%s" for sensor %s (must be "lin" or "log")', aggr_mode, sensor_id)
            continue
        
        sensors.append({
            'sensor_id': sensor_id.strip(),
            'database': database.strip(),
            'measurement': measurement.strip(),
            'column': column.strip(),
            'aggr_mode': aggr_mode
        })
    
    if not sensors:
        logging.error('No valid sensor configurations found')
        sys.exit(1)
    
    return sensors


def get_influxdb_client(database: str) -> Optional[InfluxDBClient]:
    """
    Create and return an InfluxDB client connected to the specified database.
    
    Args:
        database: Database name to connect to
    
    Returns:
        InfluxDBClient instance or None on error
    """
    try:
        influxdb_server = os.environ['INFLUXDB_SERVER'].split(':')
        client = InfluxDBClient(
            host=influxdb_server[0],
            port=int(influxdb_server[1]),
            username=os.environ['INFLUXDB_USERNAME'],
            password=os.environ['INFLUXDB_PASSWORD']
        )
        client.switch_database(database)
        logging.debug('Connected to InfluxDB database: %s', database)
        return client
    except Exception as e:
        logging.error('Failed to connect to InfluxDB: %s', e)
        return None


def delogarithmize(value: float) -> float:
    """
    Convert logarithmic dB value to linear scale.
    
    Args:
        value: Value in dB
    
    Returns:
        Linear value
    """
    return math.pow(10, value / 10.0)


def logarithmize(value: float) -> float:
    """
    Convert linear value to logarithmic dB scale.
    
    Args:
        value: Linear value
    
    Returns:
        Value in dB
    """
    if value <= 0:
        return 0
    return 10.0 * math.log10(value)


def aggregate_data(sensor_config: Dict[str, str], interval_seconds: int) -> Optional[float]:
    """
    Query InfluxDB for data in the last interval and aggregate according to aggr_mode.
    
    Args:
        sensor_config: Dictionary with sensor_id, database, measurement, column, and aggr_mode
        interval_seconds: Time interval in seconds to query
    
    Returns:
        Aggregated value or None on error
    """
    client = get_influxdb_client(sensor_config['database'])
    if not client:
        return None
    
    try:
        # Query data from the last interval
        now = datetime.datetime.now(pytz.utc)
        start_time = now - datetime.timedelta(seconds=interval_seconds)
        
        # Build query using the specified measurement and column
        measurement = sensor_config['measurement']
        column = sensor_config['column']
        aggr_mode = sensor_config['aggr_mode']
        
        query = (f"SELECT {column} FROM {measurement} "
                f"WHERE time >= '{start_time.isoformat()}' AND time <= '{now.isoformat()}'")
        logging.debug('Query: %s', query)
        
        result = client.query(query)
        
        # Extract values from query result
        values = []
        if result and len(result.raw.get('series', [])) > 0:
            for series in result.raw['series']:
                for point in series.get('values', []):
                    if len(point) > 1 and point[1] is not None:
                        values.append(float(point[1]))
        
        if not values:
            logging.warning('No data found for sensor %s in last %d seconds (database: %s, measurement: %s, column: %s)',
                          sensor_config['sensor_id'], interval_seconds, 
                          sensor_config['database'], measurement, column)
            return None
        
        logging.debug('Found %d values for sensor %s (aggr_mode: %s)', len(values), sensor_config['sensor_id'], aggr_mode)
        
        # Aggregate based on mode
        if aggr_mode == 'log':
            # Logarithmic aggregation: delogarithmize → mean → logarithmize
            linear_values = [delogarithmize(v) for v in values]
            mean_linear = sum(linear_values) / len(linear_values)
            result_value = logarithmize(mean_linear)
        else:  # aggr_mode == 'lin'
            # Linear aggregation: simple arithmetic mean
            result_value = sum(values) / len(values)
        logging.debug(f"Aggregated value for sensor {sensor_config['sensor_id']}: {result_value:.2f} dB ({aggr_mode} mode, from {len(values)} samples)")
        
        return result_value
        
    except Exception as e:
        logging.error('Error aggregating data for sensor %s: %s', sensor_config['sensor_id'], e)
        return None
    finally:
        client.close()


def send_to_opensensemap(station_id: str, sensor_id: str, value: float, api_key: str) -> bool:
    """
    Send measurement value to openSenseMap via HTTP POST.
    
    Args:
        station_id: openSenseMap station ID (12 byte hex)
        sensor_id: openSenseMap sensor ID (12 byte hex)
        value: Measurement value to send
        api_key: API key for authentication
    
    Returns:
        True if successful, False otherwise
    """
    url = f"{OSM_API_URL}/boxes/{station_id}/{sensor_id}"
    
    headers = {
        'Content-Type': 'application/json',
        'Host': 'ingress.opensensemap.org',
        'Authorization': api_key,
        'Connection': 'close'
    }
    
    payload = {
        'value': f"{value:.1f}"
    }
    
    try:
        logging.debug(f'Sending to openSenseMap: {url}, payload: {payload}')
        
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200 or response.status_code == 201:
            logging.info('Successfully sent value %.2f to sensor %s', value, sensor_id)
            return True
        else:
            logging.error('Failed to send data: HTTP %d - %s', response.status_code, response.text)
            return False
            
    except requests.exceptions.RequestException as e:
        logging.error('HTTP request failed: %s', e)
        return False


def process_sensors():
    """Process all configured sensors: aggregate data and send to openSenseMap."""
    station_id = os.environ['OSM_STATION_ID']
    api_key = os.environ['OSM_API_KEY']
    
    sensor_configs = parse_sensor_config(os.environ['OSM_SENSORS'])
    
    logging.debug('Processing %d sensor(s) with interval %d seconds', len(sensor_configs), OSM_INTERVAL)
    
    for sensor_config in sensor_configs:
        try:
            # Aggregate data from InfluxDB
            aggregated_value = aggregate_data(sensor_config, OSM_INTERVAL)
            
            if aggregated_value is not None:
                # Send to openSenseMap
                send_to_opensensemap(
                    station_id,
                    sensor_config['sensor_id'],
                    aggregated_value,
                    api_key
                )
            else:
                logging.warning('Skipping sensor %s - no data available', sensor_config['sensor_id'])
                
        except Exception as e:
            logging.error('Error processing sensor %s: %s', sensor_config['sensor_id'], e)


def main():
    """Main entry point."""
    validate_environment()
    
    logging.info('Starting tsdb2osm.py')
    logging.info('Station ID: %s', os.environ['OSM_STATION_ID'])
    logging.info('Interval: %d seconds', OSM_INTERVAL)
    logging.info('API URL: %s', OSM_API_URL)
    logging.info('Timezone: %s', TZ)
    
    # Initial delay to wait for system startup
    logging.info('Waiting 60 seconds for system startup...')
    time.sleep(60)
    
    # Main loop
    while True:
        t0 = datetime.datetime.now(pytz.timezone(TZ))
        try:
            process_sensors()
        except Exception as e:
            logging.error('Error in main loop: %s', e)
        
        logging.debug('Sleeping for %d seconds...', OSM_INTERVAL)
        sleeping_time = OSM_INTERVAL - (datetime.datetime.now(pytz.timezone(TZ)) - t0).total_seconds()
        time.sleep(sleeping_time if sleeping_time > 0 else 0)


if __name__ == '__main__':
    main()
