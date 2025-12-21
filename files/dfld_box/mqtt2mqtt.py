#!/usr/bin/env python3
"""
MQTT to MQTT Bridge - Non-blocking alternative to Mosquitto bridge
Replaces: connection to_central_broker in mosquitto.conf
"""
import os
import sys
import time
import socket
import logging
import ssl
from paho.mqtt import client as mqtt

# Config from environment (matching mosquitto bridge parameters)
LOCAL_MQTT = os.getenv('MQTT_SERVER', 'mqtt:1883')
REMOTE_MQTT = os.getenv('MQTT_BRIDGED_BROKER', '')  # e.g. "broker.emqx.io:8883"
TOPIC_REWRITE = os.getenv('MQTT_BRIDGED_RENAME', '')  # e.g. "dfld/sensors/noise/ sensebox/cindy-s-test/"
USE_TLS = os.getenv('MQTT_BRIDGED_TLS', 'false').lower() in ['true', 'yes', '1']
CLIENT_ID = os.getenv('MQTT_CLIENT_ID', f'dfld-bridge-{os.getpid()}')
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()

logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=LOG_LEVEL)

if not REMOTE_MQTT:
    logging.error('MQTT_BRIDGED_BROKER not set, exiting')
    sys.exit(1)

if not TOPIC_REWRITE:
    logging.error('MQTT_BRIDGED_RENAME not set, exiting')
    sys.exit(1)

# Parse topic rewrites: "local_prefix1 remote_prefix1:local_prefix2 remote_prefix2"
# Multiple mappings separated by colon
try:
    mappings = []
    for mapping in TOPIC_REWRITE.split(':'):
        parts = mapping.strip().split()
        if len(parts) != 2:
            raise ValueError(f'Expected 2 parts in mapping "{mapping}", got {len(parts)}')
        local_prefix, remote_prefix = parts
        # Remove trailing slashes for consistent matching
        local_prefix = local_prefix.rstrip('/')
        remote_prefix = remote_prefix.rstrip('/')
        mappings.append((local_prefix, remote_prefix))
        logging.info(f'Topic rewrite: "{local_prefix}" -> "{remote_prefix}"')
    
    if not mappings:
        raise ValueError('No mappings found')
except (ValueError, IndexError) as e:
    logging.error(f'Invalid MQTT_BRIDGED_RENAME format: "{TOPIC_REWRITE}" (expected: "local_prefix remote_prefix[:local_prefix2 remote_prefix2...]", error: {e})')
    sys.exit(1)

# Parse remote MQTT
try:
    remote_host, remote_port = REMOTE_MQTT.split(':')
    remote_port = int(remote_port)
except Exception as e:
    logging.error(f'Invalid MQTT_BRIDGED_BROKER format: {REMOTE_MQTT}')
    sys.exit(1)

# DNS resolution with periodic refresh
remote_ip = None
last_dns_lookup = 0
DNS_REFRESH_INTERVAL = 3600  # 1 hour

def resolve_remote_dns():
    global remote_ip, last_dns_lookup
    socket.setdefaulttimeout(2.0)
    try:
        new_ip = socket.gethostbyname(remote_host)
        if new_ip != remote_ip:
            logging.info(f'DNS changed: {remote_host} {remote_ip} -> {new_ip}')
            remote_ip = new_ip
        else:
            logging.debug(f'DNS unchanged: {remote_host} -> {remote_ip}')
        last_dns_lookup = time.time()
        return True
    except socket.gaierror as e:
        logging.warning(f'DNS lookup failed for {remote_host}: {e}')
        return False
    finally:
        socket.setdefaulttimeout(None)

# Initial DNS lookup
if not resolve_remote_dns():
    logging.error('Initial DNS lookup failed, exiting')
    sys.exit(1)

logging.info(f'Resolved {remote_host} to {remote_ip}:{remote_port}')

# Local MQTT client (subscriber)
local_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id=f'{CLIENT_ID}-local',
    protocol=mqtt.MQTTv311
)
local_client.reconnect_delay_set(min_delay=1, max_delay=10)

# Remote MQTT client (publisher)
remote_client = mqtt.Client(
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    protocol=mqtt.MQTTv311,
    clean_session=True
)
remote_client.reconnect_delay_set(min_delay=1, max_delay=30)

# TLS configuration (matching mosquitto bridge)
if USE_TLS:
    try:
        ca_cert = os.getenv('MQTT_TLS_CA_CERT', '/etc/ssl/certs/ca-certificates.crt')
        client_cert = os.getenv('MQTT_TLS_CLIENT_CERT', '')
        client_key = os.getenv('MQTT_TLS_CLIENT_KEY', '')
        insecure = os.getenv('MQTT_TLS_INSECURE', 'false').lower() in ['true', 'yes', '1']
        
        logging.info(f'TLS Configuration:')
        logging.info(f'  CA Certificate: {ca_cert}')
        logging.info(f'  Client Certificate: {client_cert or "None"}')
        logging.info(f'  Client Key: {client_key or "None"}')
        logging.info(f'  Insecure Mode: {insecure}')
        
        # Client certificates are optional (for mutual TLS)
        if client_cert and client_key:
            remote_client.tls_set(
                ca_certs=ca_cert,
                certfile=client_cert,
                keyfile=client_key,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS
            )
        else:
            remote_client.tls_set(
                ca_certs=ca_cert,
                cert_reqs=ssl.CERT_REQUIRED,
                tls_version=ssl.PROTOCOL_TLS
            )
        
        # Disable hostname verification for self-signed certificates if needed
        remote_client.tls_insecure_set(insecure)
        if insecure:
            logging.warning('TLS hostname verification DISABLED - use only for testing!')
    except Exception as e:
        logging.error(f'Failed to configure TLS: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)

remote_connected = False
dropped_messages = 0
forwarded_messages = 0
last_stats_log = time.time()

def on_remote_connect(cli, userdata, flags, reason_code, properties):
    global remote_connected, dropped_messages
    rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
    remote_connected = (rc == 0)
    if remote_connected:
        logging.info(f'Remote MQTT connected to {remote_ip}:{remote_port} (dropped {dropped_messages} messages while disconnected)')
        dropped_messages = 0
    else:
        # MQTT error codes: https://github.com/eclipse/paho.mqtt.python/blob/master/src/paho/mqtt/client.py
        error_messages = {
            1: 'Connection refused - incorrect protocol version',
            2: 'Connection refused - invalid client identifier',
            3: 'Connection refused - server unavailable',
            4: 'Connection refused - bad username or password',
            5: 'Connection refused - not authorized'
        }
        error_msg = error_messages.get(rc, f'Unknown error code {rc}')
        logging.error(f'Remote MQTT connection failed: {error_msg}')

def on_remote_disconnect(cli, userdata, disconnect_flags, reason_code, properties):
    global remote_connected, last_dns_lookup
    remote_connected = False
    rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
    if rc != 0:
        logging.warning(f'Remote MQTT disconnected (rc={rc}), will auto-reconnect')
        # Force DNS refresh on disconnect
        last_dns_lookup = 0

def on_remote_log(cli, userdata, level, buf):
    # Log all paho messages with appropriate level
    if level == mqtt.MQTT_LOG_ERR:
        logging.error(f'MQTT Client: {buf}')
    elif level == mqtt.MQTT_LOG_WARNING:
        logging.warning(f'MQTT Client: {buf}')
    elif level == mqtt.MQTT_LOG_INFO:
        logging.info(f'MQTT Client: {buf}')
    else:
        logging.debug(f'MQTT Client: {buf}')

remote_client.on_connect = on_remote_connect
remote_client.on_disconnect = on_remote_disconnect
remote_client.on_log = on_remote_log
remote_client.enable_logger()

def on_local_message(cli, userdata, msg):
    global dropped_messages, forwarded_messages
    
    if not remote_connected:
        dropped_messages += 1
        logging.debug(f'Remote not connected, dropped message on {msg.topic}')
        return
    
    # Try to match topic against all mappings
    remote_topic = None
    for local_prefix, remote_prefix in mappings:
        if msg.topic.startswith(local_prefix):
            # Rewrite topic: replace local_prefix with remote_prefix
            if msg.topic == local_prefix:
                # Exact match, no suffix
                remote_topic = remote_prefix
            elif msg.topic.startswith(local_prefix + '/'):
                # Has suffix after prefix
                suffix = msg.topic[len(local_prefix) + 1:]  # +1 to skip the '/'
                remote_topic = f'{remote_prefix}/{suffix}'
            break
    
    if not remote_topic:
        logging.debug(f'Topic {msg.topic} does not match any mapping, ignoring')
        return
    
    try:
        # Match mosquitto bridge: out direction, qos 1, no retain
        result = remote_client.publish(remote_topic, msg.payload, qos=1, retain=False)
        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            forwarded_messages += 1
            logging.debug(f'Forwarded: {msg.topic} -> {remote_topic}')
        else:
            logging.warning(f'Publish failed with rc={result.rc}: {msg.topic} -> {remote_topic}')
            dropped_messages += 1
    except Exception as e:
        logging.warning(f'Failed to forward message: {e}')
        dropped_messages += 1

def on_local_connect(cli, userdata, flags, reason_code, properties):
    rc = reason_code.value if hasattr(reason_code, 'value') else reason_code
    if rc == 0:
        # Subscribe to all topics for all mappings
        for local_prefix, remote_prefix in mappings:
            subscribe_topic = f'{local_prefix}/#'
            result = cli.subscribe(subscribe_topic, qos=1)
            logging.info(f'Local MQTT connected, subscribed to "{subscribe_topic}" (result: {result})')
    else:
        logging.error(f'Local MQTT connection failed (rc={rc})')

local_client.on_connect = on_local_connect
local_client.on_message = on_local_message

# Connect
local_host, local_port = LOCAL_MQTT.split(':')
local_client.connect_async(local_host, int(local_port), keepalive=60)
local_client.loop_start()

try:
    # Use hostname for TLS (certificate verification), IP for non-TLS
    connect_host = remote_host if USE_TLS else remote_ip
    logging.info(f'Attempting connection to remote MQTT at {connect_host}:{remote_port} (TLS: {USE_TLS})...')
    remote_client.connect_async(connect_host, remote_port, keepalive=60)
    remote_client.loop_start()
    logging.info(f'MQTT bridge running with {len(mappings)} mapping(s)')
except Exception as e:
    logging.error(f'Failed to start remote MQTT connection: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Keep alive with periodic DNS refresh and stats
try:
    while True:
        time.sleep(60)
        
        # Periodic stats logging (every 10 minutes)
        if time.time() - last_stats_log > 600:
            logging.info(f'Stats: forwarded={forwarded_messages}, dropped={dropped_messages}, connected={remote_connected}')
            last_stats_log = time.time()
        
        # Periodic DNS refresh
        if time.time() - last_dns_lookup > DNS_REFRESH_INTERVAL:
            logging.debug('Periodic DNS refresh')
            old_ip = remote_ip
            if resolve_remote_dns() and old_ip != remote_ip:
                # DNS changed, reconnect
                logging.info('DNS changed, reconnecting to remote MQTT')
                remote_client.disconnect()
                time.sleep(1)
                remote_client.connect_async(remote_ip, remote_port, keepalive=60)
except KeyboardInterrupt:
    logging.info('Shutting down')
    local_client.loop_stop()
    remote_client.loop_stop()
