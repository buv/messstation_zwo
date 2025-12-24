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
from dfld import DataSink, MqttDataSink, SSD1306DataSink
from dfld import (
    Bme280DataSource, 
    DNMSi2cDataSource, 
    AkModulDataSource, 
    DNMSDataSource,
    UdpDataSource,
    MqttDataSource
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


def detect_sensor_protocol(device_path, timeout=3):
    """
    Detect sensor protocol by attempting communication.
    Returns 'dnms', 'dfld_legacy', or None
    """
    import serial
    import time
    
    try:
        # Try DNMS protocol first (500000 baud)
        with serial.Serial(device_path, 500000, timeout=1) as ser:
            time.sleep(0.1)
            ser.flushInput()
            
            # Wait for DNMS data (should come every second)
            start_time = time.time()
            while time.time() - start_time < timeout:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line and ';' in line:
                    # DNMS format: "timestamp;dBA;dBC;..."
                    parts = line.split(';')
                    if len(parts) >= 3:
                        try:
                            float(parts[1])  # dBA value
                            float(parts[2])  # dBC value
                            return 'dnms'
                        except ValueError:
                            pass
    except:
        pass
    
    try:
        # Try DFLD Legacy protocol (9600 baud)
        with serial.Serial(device_path, 9600, timeout=1) as ser:
            time.sleep(0.1)
            ser.flushInput()
            
            # Wait for DFLD data
            start_time = time.time()
            while time.time() - start_time < timeout:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line and ('dB' in line or line.replace('.', '').replace('-', '').isdigit()):
                    return 'dfld_legacy'
    except:
        pass
    
    return None


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
            
            # Version information from environment
            version = os.getenv('MESSSTATION_VERSION', 'unknown')
            commit = os.getenv('MESSSTATION_COMMIT', 'unknown')
            commit_date = os.getenv('MESSSTATION_COMMIT_DATE', 'unknown')
            
            system_data = {
                "os_name": os_name,
                "os_version": os_version,
                "cpu_model": cpu_model,
                "cpu_cores_physical": cpu_count_physical,
                "cpu_cores_logical": cpu_count_logical,
                "cpu_freq_max_mhz": cpu_freq_max,
                "memory_total_mb": memory_total_mb,
                "messstation_version": version,
                "messstation_commit": commit,
                "messstation_commit_date": commit_date
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
                            logger.warning(f"Invalid numeric value for {key}: {value}")
                    else:
                        geo_data[key] = value
            
            if geo_data:
                geo_sink.write_meta(geo_data)
            
            logger.info("System and geo metadata published successfully")
            
        except Exception as e:
            logger.error(f"Failed to publish system/geo metadata: {e}")
        finally:
            # Close connections
            try:
                system_sink.close()
                geo_sink.close()
            except:
                pass
    
    import threading
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def display_monitor():
    """
    Monitor I2C bus for SSD1306 display and handle data display.
    Runs in a separate thread with hot-plug support.
    """
    def run():
        import smbus3 as smbus
        import time
        
        logger = logging.getLogger('display')
        display_sink = None
        mqtt_source = None
        last_check = 0
        check_interval = 30  # Check for display every 30 seconds
        
        while True:
            try:
                current_time = time.time()
                
                # Check for display availability periodically
                if current_time - last_check > check_interval:
                    try:
                        bus = smbus.SMBus(1)
                        bus.read_byte(0x3c)  # SSD1306 address
                        bus.close()
                        
                        # Display found - initialize if not already done
                        if not display_sink or not display_sink.connected:
                            logger.info("SSD1306 display detected, initializing...")
                            display_sink = SSD1306DataSink()
                            display_sink.connect()
                            
                            if not mqtt_source:
                                mqtt_source = MqttDataSource()
                                # Use different client ID to receive own messages
                                mqtt_source.client_id = f"{MODULE_NAME}-display-{os.getpid()}"
                                mqtt_source.init()
                                
                    except Exception as e:
                        if display_sink and display_sink.connected:
                            logger.info("SSD1306 display disconnected")
                            display_sink.connected = False
                        display_sink = None
                        
                    last_check = current_time
                
                # Display data if display is available
                if display_sink and display_sink.connected and mqtt_source:
                    try:
                        data = mqtt_source.read()
                        if data:
                            logger.debug(f"Received MQTT data for display: {data}")
                            display_sink.write(json.dumps(data))
                        else:
                            logger.debug("No MQTT data received for display")
                    except Exception as e:
                        logger.debug(f"Display write error: {e}")
                        display_sink.connected = False
                        
                time.sleep(0.2)  # Display update interval
                
            except Exception as e:
                logger.error(f"Display monitor error: {e}")
                time.sleep(5)
    
    import threading
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
    import detect_hw
    hw_config = detect_hw.detect_hardware()
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
        device_path = hw_config.get('DEVICE_DNMS', '/dev/ttyDNMS')
        logger.info(f"Starting DNMS serial sensor at {device_path} with {noise_interval}s interval...")
        os.environ['DNMS_DEVICE'] = device_path
        threads.append(start_sensor_thread(DNMSDataSource, client_suffix="dnms-serial", readout_interval=noise_interval))
    elif hw_config.get('DFLD_LEGACY_AVAILABLE'):
        # Start DFLD/AK-Modul (legacy) if available (no interval - sensor-driven)
        device_path = hw_config.get('DEVICE_DFLD', '/dev/ttyUSB0')
        logger.info(f"Starting DFLD Legacy (AK-Modul) sensor at {device_path} (sensor-driven timing)...")
        os.environ['AK_MODUL_DEVICE'] = device_path
        threads.append(start_sensor_thread(AkModulDataSource, client_suffix="ak-modul"))
    else:    
        # Start UDP listener, if no local device available (for external data sources)
        logger.info("Starting UDP listener...")
        threads.append(start_sensor_thread(UdpDataSource, use_custom_loop=True, client_suffix="udp"))
    
    logger.info(f"Started {len(threads)} sensor threads")
    
    # Start system metadata publishing (one-time)
    system_thread = publish_system_metadata()
    
    # Start display monitor (hot-plug support)
    display_thread = display_monitor()
    logger.info("Started display monitor with hot-plug support")
    
    # Keep main thread alive
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
