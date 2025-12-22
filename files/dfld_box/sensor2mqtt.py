#!/usr/bin/env python3

"""
Unified sensor to MQTT program that detects available hardware
and starts appropriate data sources dynamically.
"""

import os
import sys
import json
import time
import logging
import threading
import subprocess
import smbus3 as smbus
import serial.tools.list_ports
import socket
from dfld import DataSink, MqttDataSink
from dfld import (
    Bme280DataSource, 
    DNMSi2cDataSource, 
    AkModulDataSource, 
    DNMSDataSource,
    UdpDataSource
)
from dfld import EventLoop

# Derive module name for MQTT client ID base
MODULE_NAME = os.path.basename(__file__).replace('.py', '')


class CustomEventLoop(EventLoop):
    """EventLoop without source labeling - source is sent via metadata"""
    def __init__(self, data_source, data_sink, readout_interval=None):
        super().__init__(data_source, data_sink, readout_interval)
    
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = json.dumps({"station": self.dfld_station_id} | data)
            sink.write(msg)
        else:
            self.logger.warning('No data to process')


def check_mqtt_connectivity(mqtt_server):
    """
    Check MQTT broker connectivity with exponential backoff.
    Continues indefinitely until connection is possible.
    
    Args:
        mqtt_server: MQTT server in format 'host:port'
    """
    logger = logging.getLogger('mqtt_check')
    
    try:
        host, port = mqtt_server.split(':')
        port = int(port)
    except ValueError:
        logger.error(f"Invalid MQTT server format: {mqtt_server}")
        return False
    
    attempt = 0
    delay = 1
    while True:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            result = sock.connect_ex((host, port))
            sock.close()
            
            if result == 0:
                logger.info(f"MQTT broker {mqtt_server} is reachable")
                return True
            
        except Exception as e:
            logger.debug(f"Connection attempt {attempt + 1} failed: {e}")
        
        logger.warning(f"MQTT broker {mqtt_server} not reachable, retrying in {delay}s...")
        time.sleep(delay)
        delay = min(2 * delay, 60)  # Exponential backoff, max 60s
        attempt += 1


def wait_for_mqtt_connection(sink):
    """
    Wait for MQTT connection with exponential backoff.
    Continues indefinitely until connection is established.
    
    Args:
        sink: MqttDataSink instance
    
    Returns:
        bool: Always True (only returns when connected)
    """
    logger = logging.getLogger('mqtt_connect')
    
    attempt = 0
    delay = 1
    while True:
        try:
            sink.connect()
            if sink.is_connected():
                return True
        except Exception as e:
            logger.debug(f"Connection attempt {attempt + 1} failed: {e}")
        
        logger.warning(f"MQTT connection failed, retrying in {delay}s...")
        time.sleep(delay)
        delay = min(2 * delay, 60)  # Exponential backoff, max 60s
        attempt += 1


def detect_hardware():
    """
    Detect available hardware sensors and return configuration.
    Returns dict with availability flags for each sensor type.
    """
    env = {k + "_AVAILABLE": 0 for k in "DNMS_I2C BME280 ADSB DFLD_LEGACY DFLD_DNMS SSD1306".split()}
    
    # I2C scan
    devices = {
        0x55: "DNMS_I2C_AVAILABLE",
        0x76: "BME280_AVAILABLE",
        0x3c: "SSD1306_AVAILABLE",
    }
    
    try:
        bus = smbus.SMBus(int(os.getenv('I2C_BUS', 1)))
        for device, var in devices.items():
            try:
                bus.read_byte(device)
                env[var] = 1
            except:
                pass
    except Exception as e:
        logging.warning(f"Failed to initialize I2C bus: {e}")
    
    # Check for RTL-SDR on USB for ADS-B readout
    supported_devices = [
        "0bda:2838",  # nooElec
        "0bda:2832",  # SDR ADS-B
    ]
    for s in supported_devices:
        rc = subprocess.call(f"lsusb | grep -q {s}", shell=True)
        if rc == 0:
            env["ADSB_AVAILABLE"] = 1
            break
    
    # Check tty devices
    tty_devices = set([
        str(comport.device).split('/')[-1] 
        for comport in serial.tools.list_ports.comports(include_links=True)
    ])
    
    if 'ttyDNMS' in tty_devices or os.getenv('DNMS_DEVICE'):
        env['DFLD_DNMS_AVAILABLE'] = 1
        env['DEVICE_DNMS'] = os.getenv('DNMS_DEVICE', '/dev/ttyDNMS')
    
    if 'ttyUSB0' in tty_devices or os.getenv('AK_MODUL_DEVICE'):
        env['DFLD_LEGACY_AVAILABLE'] = 1
        env['DEVICE_DFLD'] = os.getenv('AK_MODUL_DEVICE', '/dev/ttyUSB0')
    
    return env


def publish_system_metadata():
    """
    Publish system and geo metadata once at startup.
    This runs in a separate thread and exits after publishing.
    """
    def run():
        try:
            # Wait for startup delay
            delay = int(os.getenv('STARTUP_DELAY', 30))
            time.sleep(delay)
            
            logger = logging.getLogger('system2mqtt')
            logger.info("Publishing system and geo metadata...")
            
            # System metadata
            system_sink = MqttDataSink()
            system_sink.meta_topic = os.getenv('MQTT_META_SYSTEM_TOPIC', 'dfld/metadata/system')
            station_id = os.getenv('DFLD_STATION_ID')
            if not station_id:
                logger.warning("DFLD_STATION_ID not set. Skipping system metadata publishing.")
                return
            
            system_sink.client_id = f"{MODULE_NAME}-system"
            
            # Connect to MQTT with retry
            wait_for_mqtt_connection(system_sink)
            logger.info("Connected to MQTT broker for system metadata")
            
            import platform
            import multiprocessing
            
            # Detailed system information like original system2mqtt.py
            os_name = platform.system()
            os_version = platform.release()
            cpu_count_logical = os.cpu_count() or 1
            cpu_count_physical = cpu_count_logical
            cpu_model = "Unknown"
            cpu_freq_max = None
            memory_total_mb = 0
            
            # Get detailed CPU info on Linux
            if os_name == "Linux":
                try:
                    with open('/proc/cpuinfo', 'r') as f:
                        cores = set()
                        for line in f:
                            if line.startswith('model name'):
                                cpu_model = line.split(':', 1)[1].strip()
                            elif line.startswith('core id'):
                                cores.add(line.split(':', 1)[1].strip())
                            elif line.startswith('cpu MHz'):
                                if not cpu_freq_max:
                                    cpu_freq_max = float(line.split(':', 1)[1].strip())
                        if cores:
                            cpu_count_physical = len(cores)
                except:
                    pass
                
                # Get memory from /proc/meminfo
                try:
                    with open('/proc/meminfo', 'r') as f:
                        for line in f:
                            if line.startswith('MemTotal:'):
                                mem_kb = int(line.split()[1])
                                memory_total_mb = int(mem_kb / 1024)
                                break
                except:
                    pass
            else:
                cpu_model = platform.processor() or "Unknown"
            
            system_data = {
                "os_name": os_name,
                "os_version": os_version,
                "cpu_model": cpu_model,
                "cpu_cores_physical": cpu_count_physical,
                "cpu_cores_logical": cpu_count_logical,
                "cpu_freq_max_mhz": cpu_freq_max,
                "memory_total_mb": memory_total_mb,
            }
            
            # Publish all system data at once
            filtered_system_data = {k: v for k, v in system_data.items() if v is not None}
            if filtered_system_data:
                system_sink.write_meta(filtered_system_data)
            
            # Geo metadata
            geo_sink = MqttDataSink()
            geo_sink.meta_topic = os.getenv('MQTT_META_GEO_TOPIC', 'dfld/metadata/geo')
            geo_sink.client_id = f"{MODULE_NAME}-geo"
            
            # Connect to MQTT with retry
            wait_for_mqtt_connection(geo_sink)
            logger.info("Connected to MQTT broker for geo metadata")
            
            geo_values = {
                "station_lat": os.getenv('STATION_LAT', ''),
                "station_lon": os.getenv('STATION_LON', ''),
                "station_alt": os.getenv('STATION_ALT', ''),
                "station_city": os.getenv('STATION_CITY', '')
            }
            
            # Convert numeric values and only publish non-empty values
            geo_data = {}
            for key, value in geo_values.items():
                if value:
                    if key in ['station_lat', 'station_lon', 'station_alt']:
                        try:
                            geo_data[key] = float(value)
                        except ValueError:
                            pass
                    else:
                        geo_data[key] = value
            
            # Publish all geo data at once
            if geo_data:
                geo_sink.write_meta(geo_data)
            
            logger.info("System and geo metadata published successfully")
            
            # Close connections
            system_sink.close()
            if geo_data:
                geo_sink.close()
            
        except Exception as e:
            logging.error(f"Error publishing system metadata: {e}")
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def start_sensor_thread(source_class, use_custom_loop=False, client_suffix="", readout_interval=None):
    """
    Start a sensor data source in a separate thread.
    
    Args:
        source_class: DataSource class to instantiate
        use_custom_loop: Whether to use CustomEventLoop (for BME280, UDP)
        client_suffix: Suffix for MQTT client ID to ensure uniqueness
        readout_interval: Custom readout interval for this sensor
    """
    def run():
        try:
            source = source_class()
            sink = MqttDataSink()
            
            # Set unique client ID
            if client_suffix:
                sink.client_id = f"{MODULE_NAME}-{client_suffix}"
            
            if use_custom_loop:
                event_loop = CustomEventLoop(source, sink, readout_interval)
            else:
                event_loop = EventLoop(source, sink, readout_interval)
            
            event_loop.start()
        except Exception as e:
            logging.error(f"Error in sensor thread for {source_class.__name__}: {e}")
    
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def main():
    # Setup logging
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    logging.basicConfig(
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        level=log_level
    )
    logger = logging.getLogger('sensor2mqtt')
    
    logger.info("Starting unified sensor to MQTT program...")
    
    # Check if station ID is set
    station_id = os.getenv('DFLD_STATION_ID')
    if not station_id:
        logger.error("DFLD_STATION_ID not set. Exiting without logging data.")
        sys.exit(1)
    
    logger.info(f"Station ID: {station_id}")
    
    # Check MQTT broker connectivity before starting threads
    mqtt_server = os.getenv('MQTT_SERVER', 'mqtt:1883')
    logger.info(f"Checking MQTT broker connectivity: {mqtt_server}")
    
    check_mqtt_connectivity(mqtt_server)
    logger.info("MQTT broker is reachable, proceeding with sensor initialization")
    
    # Detect available hardware
    hw_config = detect_hardware()
    logger.info(f"Hardware detection results: {json.dumps(hw_config, indent=2)}")
    
    # Get sensor-specific intervals
    noise_interval = float(os.getenv('READOUT_INTERVAL_NOISE', 1.0))
    air_interval = float(os.getenv('READOUT_INTERVAL_AIR', 60.0))
    
    # Track active threads
    threads = []
    
    # Start BME280 if available (uses air interval)
    if hw_config.get('BME280_AVAILABLE'):
        logger.info(f"Starting BME280 sensor with {air_interval}s interval...")
        threads.append(start_sensor_thread(Bme280DataSource, use_custom_loop=True, client_suffix="bme280", readout_interval=air_interval))
    
    if hw_config.get('DNMS_I2C_AVAILABLE'):
        # Start DNMS I2C if available (uses noise interval)
        logger.info(f"Starting DNMS I2C sensor with {noise_interval}s interval...")
        threads.append(start_sensor_thread(DNMSi2cDataSource, client_suffix="dnms-i2c", readout_interval=noise_interval))
    elif hw_config.get('DFLD_DNMS_AVAILABLE'):
        # Start DNMS serial if available (uses noise interval)
        logger.info(f"Starting DNMS serial sensor with {noise_interval}s interval...")
        # Set environment variables for DNMS
        os.environ['DEVICE'] = hw_config.get('DEVICE_DNMS', '/dev/ttyDNMS')
        os.environ['DNMS_BAUDRATE'] = os.getenv('DNMS_BAUDRATE', '500000')
        threads.append(start_sensor_thread(DNMSDataSource, client_suffix="dnms-serial", readout_interval=noise_interval))
    elif hw_config.get('DFLD_LEGACY_AVAILABLE'):
        # Start DFLD/AK-Modul (legacy) if available (no interval - sensor-driven)
        logger.info("Starting DFLD Legacy (AK-Modul) sensor (sensor-driven timing)...")
        # Set environment variables for AK-Modul
        os.environ['DEVICE'] = hw_config.get('DEVICE_DFLD', '/dev/ttyUSB0')
        os.environ['DFLD_BAUDRATE'] = os.getenv('AK_MODUL_BAUDRATE', '9600')
        threads.append(start_sensor_thread(AkModulDataSource, client_suffix="ak-modul"))
    else:    
        # Start UDP listener, if no local device available (for external data sources)
        logger.info("Starting UDP listener...")
        threads.append(start_sensor_thread(UdpDataSource, use_custom_loop=True, client_suffix="udp"))
    
    logger.info(f"Started {len(threads)} sensor threads")
    
    # Start system metadata publishing (one-time)
    system_thread = publish_system_metadata()
    
    # Keep main thread alive
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
