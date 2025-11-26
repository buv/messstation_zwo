#!/usr/bin/env python3

"""
Unified sensor to MQTT program that detects available hardware
and starts appropriate data sources dynamically.
"""

import os
import sys
import json
import logging
import threading
import subprocess
import smbus3 as smbus
import serial.tools.list_ports
from dfld import DataSink, MqttDataSink
from dfld import (
    Bme280DataSource, 
    DNMSi2cDataSource, 
    AkModulDataSource, 
    DNMSDataSource,
    UdpDataSource
)
from dfld import EventLoop


class CustomEventLoop(EventLoop):
    """EventLoop with custom source labeling for BME280 and UDP"""
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = json.dumps({"source": self.data_source.source} | data)
            sink.write(msg)
        else:
            self.logger.warning('No data to process')


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
        bus = smbus.SMBus(1)
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
    
    if 'ttyDNMS' in tty_devices:
        env['DFLD_DNMS_AVAILABLE'] = 1
        env['DEVICE_DNMS'] = '/dev/ttyDNMS'
    
    if 'ttyUSB0' in tty_devices:
        env['DFLD_LEGACY_AVAILABLE'] = 1
        env['DEVICE_DFLD'] = '/dev/ttyUSB0'
    
    return env


def start_sensor_thread(source_class, use_custom_loop=False):
    """
    Start a sensor data source in a separate thread.
    
    Args:
        source_class: DataSource class to instantiate
        use_custom_loop: Whether to use CustomEventLoop (for BME280, UDP)
    """
    def run():
        try:
            source = source_class()
            sink = MqttDataSink()
            
            if use_custom_loop:
                event_loop = CustomEventLoop(source, sink)
            else:
                event_loop = EventLoop(source, sink)
            
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
    
    # Detect available hardware
    hw_config = detect_hardware()
    logger.info(f"Hardware detection results: {json.dumps(hw_config, indent=2)}")
    
    # Track active threads
    threads = []
    
    # Start BME280 if available
    if hw_config.get('BME280_AVAILABLE'):
        logger.info("Starting BME280 sensor...")
        threads.append(start_sensor_thread(Bme280DataSource, use_custom_loop=True))
    
    # Start DNMS I2C if available
    if hw_config.get('DNMS_I2C_AVAILABLE'):
        logger.info("Starting DNMS I2C sensor...")
        threads.append(start_sensor_thread(DNMSi2cDataSource))
    
    # Start DFLD/AK-Modul (legacy) if available
    if hw_config.get('DFLD_LEGACY_AVAILABLE'):
        logger.info("Starting DFLD Legacy (AK-Modul) sensor...")
        # Set environment variable for device path
        os.environ['AK_MODUL_DEVICE'] = hw_config.get('DEVICE_DFLD', '/dev/ttyUSB0')
        threads.append(start_sensor_thread(AkModulDataSource))
    
    # Start DNMS serial if available
    if hw_config.get('DFLD_DNMS_AVAILABLE'):
        logger.info("Starting DNMS serial sensor...")
        # Set environment variable for device path
        os.environ['DNMS_DEVICE'] = hw_config.get('DEVICE_DNMS', '/dev/ttyDNMS')
        threads.append(start_sensor_thread(DNMSDataSource))
    
    # Always start UDP listener (for external data sources)
    logger.info("Starting UDP listener...")
    threads.append(start_sensor_thread(UdpDataSource, use_custom_loop=True))
    
    if not threads:
        logger.warning("No sensors detected! Only UDP listener is active.")
    else:
        logger.info(f"Started {len(threads)} sensor threads")
    
    # Keep main thread alive
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


if __name__ == '__main__':
    main()
