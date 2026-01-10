#!/usr/bin/env python3
"""
adsb2mqtt.py - Forward Beast Binary data from ultrafeeder to MQTT

Connects to ultrafeeder on port 30005 (Beast Binary format), parses individual
messages and publishes each to MQTT with timestamp and station ID.

Environment Variables:
    BEAST_HOST: ultrafeeder host (default: ultrafeeder)
    BEAST_PORT: Beast Binary port (default: 30005)
    MQTT_SERVER: MQTT broker (default: mqtt:1883)
    MQTT_TOPIC: MQTT topic (default: dfld/adsb/beast)
    DFLD_STATION_ID: Station ID
    LOG_LEVEL: Logging level (default: INFO)
"""

import os
import sys
import time
import json
import socket
import logging
from dfld import MqttDataSink

# Configure logging
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=log_level)
logger = logging.getLogger('adsb2mqtt')

# Configuration
BEAST_HOST = os.getenv('BEAST_HOST', 'ultrafeeder')
BEAST_PORT = int(os.getenv('BEAST_PORT', '30005'))
MQTT_TOPIC = os.getenv('MQTT_TOPIC', 'dfld/adsb/beast')
STATION_ID = os.getenv('DFLD_STATION_ID')

if not STATION_ID:
    logger.error('DFLD_STATION_ID not set')
    sys.exit(1)

# Beast message lengths by type
BEAST_LENGTHS = {
    0x31: 2,   # Mode-AC
    0x32: 7,   # Mode-S Short
    0x33: 14,  # Mode-S Long
    0x34: 1    # Signal Level
}


def connect_beast():
    """Connect to Beast Binary port with retry."""
    attempt = 0
    delay = 1
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((BEAST_HOST, BEAST_PORT))
            logger.info(f'Connected to {BEAST_HOST}:{BEAST_PORT}')
            return sock
        except Exception as e:
            logger.debug(f'Connection attempt {attempt + 1} failed: {e}')
            logger.warning(f'Retrying in {delay}s...')
            time.sleep(delay)
            delay = min(2 * delay, 60)
            attempt += 1


def parse_beast_messages(data):
    """Parse Beast Binary format and yield individual messages."""
    i = 0
    while i < len(data):
        # Look for escape character
        if data[i] != 0x1a:
            i += 1
            continue
        
        # Need at least escape + type
        if i + 1 >= len(data):
            break
        
        msg_type = data[i + 1]
        
        # Get expected message length
        payload_len = BEAST_LENGTHS.get(msg_type)
        if payload_len is None:
            i += 2
            continue
        
        # Total: 1 (escape) + 1 (type) + 6 (timestamp) + 1 (signal) + payload
        total_len = 2 + 6 + 1 + payload_len
        
        # Check if we have complete message
        if i + total_len > len(data):
            break
        
        # Extract message
        msg = data[i:i + total_len]
        yield msg
        
        i += total_len


def main():
    logger.info('Starting adsb2mqtt...')
    
    # Initialize MQTT sink
    sink = MqttDataSink()
    sink.set_channel(MQTT_TOPIC)
    sink.connect()
    
    while True:
        sock = connect_beast()
        
        try:
            while True:
                data = sock.recv(4096)
                if not data:
                    logger.warning('Connection closed by server')
                    break
                
                # Parse and publish individual messages
                for msg in parse_beast_messages(data):
                    hex_data = msg.hex().upper()
                    
                    payload = json.dumps({
                        'station': STATION_ID,
                        'data': hex_data
                    })
                    
                    sink.write(payload)
                    logger.debug(f'Published message: {hex_data[:20]}...')
                
        except Exception as e:
            logger.error(f'Error: {e}')
        finally:
            sock.close()
            logger.info('Reconnecting in 5s...')
            time.sleep(5)


if __name__ == '__main__':
    main()
