#!/usr/bin/env python3
"""
System Information to MQTT Publisher
Publishes system metadata once at startup
"""
import os
import sys
import json
import platform
import time
from dfld.DataSink import MqttDataSink

def get_system_info():
    """Collect system information using standard libraries"""
    # OS Information
    os_name = platform.system()
    os_version = platform.release()
    
    # CPU Information
    cpu_count_logical = os.cpu_count() or 1
    
    # Try to get physical CPU cores and model from /proc/cpuinfo on Linux
    cpu_count_physical = cpu_count_logical
    cpu_model = "Unknown"
    cpu_freq_max = None
    
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
    else:
        cpu_model = platform.processor() or "Unknown"
    
    # Memory Information from /proc/meminfo on Linux
    memory_total_mb = 0
    if os_name == "Linux":
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        # MemTotal is in kB
                        mem_kb = int(line.split()[1])
                        memory_total_mb = int(mem_kb / 1024)
                        break
        except:
            pass
    
    return {
        "os_name": os_name,
        "os_version": os_version,
        "cpu_model": cpu_model,
        "cpu_cores_physical": cpu_count_physical,
        "cpu_cores_logical": cpu_count_logical,
        "cpu_freq_max_mhz": cpu_freq_max,
        "memory_total_mb": memory_total_mb,
    }

def get_geo_position():
    """Get station geo position from environment variables"""
    geo_data = {}
    
    station_lat = os.getenv('STATION_LAT')
    station_lon = os.getenv('STATION_LON')
    station_alt = os.getenv('STATION_ALT')
    
    if station_lat:
        try:
            geo_data['station_lat'] = float(station_lat)
        except ValueError:
            pass
    
    if station_lon:
        try:
            geo_data['station_lon'] = float(station_lon)
        except ValueError:
            pass
    
    if station_alt:
        try:
            geo_data['station_alt'] = float(station_alt)
        except ValueError:
            pass
    
    return geo_data

def main():
    # Get configuration
    station_id = os.getenv('DFLD_STATION_ID', 'unknown')
    mqtt_topic = os.getenv('MQTT_META_SYSTEM_TOPIC', 'dfld/metadata/system')
    geo_topic = os.getenv('MQTT_META_GEO_TOPIC', 'dfld/metadata/geo')
    delay_seconds = int(os.getenv('STARTUP_DELAY', '30'))
    
    # Create MQTT data sink
    mqtt_sink = MqttDataSink()
    mqtt_sink.set_meta_channel(mqtt_topic)
    
    try:
        # Wait before publishing (configurable delay)
        if delay_seconds > 0:
            mqtt_sink.get_logger().info(f"Waiting {delay_seconds} seconds before publishing system metadata...")
            time.sleep(delay_seconds)
        
        # Connect to MQTT
        mqtt_sink.connect()
        if not mqtt_sink.is_connected():
            mqtt_sink.get_logger().error("Failed to connect to MQTT broker")
            sys.exit(1)
        
        # Collect and publish system metadata
        mqtt_sink.write_meta(get_system_info())
        mqtt_sink.get_logger().info(f"Published system metadata to {mqtt_topic}")
        
        # Publish geo position to separate topic if available
        geo_info = get_geo_position()
        if geo_info:
            geo_sink = MqttDataSink()
            geo_sink.set_meta_channel(geo_topic)
            geo_sink.connect()
            if geo_sink.is_connected():
                geo_sink.write_meta(geo_info)
                mqtt_sink.get_logger().info("Published geo metadata to {geo_topic}")
            geo_sink.close()
        
    except Exception as e:
        mqtt_sink.get_logger().error(f"Error publishing system metadata: {e}")
        sys.exit(1)
    finally:
        mqtt_sink.close()

if __name__ == "__main__":
    main()