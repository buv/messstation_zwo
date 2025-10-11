# !/usr/bin/env python3

# read BME280 sensor data and send to MQTT broker
# 
# requires environment variables:
#   MQTT_SERVER: hostname:port of mqtt server (default: mqtt:1883)
#   MQTT_TOPIC: topic name on mqtt server (default: sensors/bme280)
#   READOUT_INTERVAL: readout interval in seconds (default: 60) 


import os
import sys
import time
import bme280
import smbus3 as smbus
import logging
import argparse
import paho.mqtt.client as mqtt


I2C_ADDR = 0x76 # BME280 sensor address (default address)

# setup logging
level = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

def connect_mqtt_publisher(args):
    global logging
    client = None
    # create connection to mqtt
    try:
        logging.info(f'connecting to mqtt server ({args.mqtt_server})...')
        client = mqtt.Client(
            client_id="bme2mqtt",
            clean_session=True,
            userdata=None,
            protocol=mqtt.MQTTv311,
            transport="tcp",
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2    
        )

        mqtt_server = args.mqtt_server.split(':')
        client.connect(mqtt_server[0], int(mqtt_server[1]), 60)
        client.loop_start()
        logging.info(f'connected to mqtt server "{args.mqtt_server}"')
        logging.debug(f'client={client}')
    except Exception as e:
        logging.error(f'error connecting to mqtt server: {e}')
    return client

def write_data(data, c):
    # send data to mqtt server as json string
    global logging
    line = f'{{"source": "bme280", "temperature": {data.temperature}, "pressure": {data.pressure}, "humidity": {data.humidity}, "ts": {int(time.time()*1e9)}}}'
    logging.debug(f'sensor data: {line}')
    c.publish(args.topic, line)

parser = argparse.ArgumentParser()
parser.add_argument("-m", "--mqtt-server", help="hostname and port of mqtt server", default=os.environ.get('MQTT_SERVER', 'mqtt:1883'))
parser.add_argument("-t", "--topic", help="topic name on mqtt server", default=os.environ.get('MQTT_TOPIC', 'sensors/bme280'))
parser.add_argument("-i", "--interval", type=int, help="readout interval in seconds", default=int(os.environ.get('READOUT_INTERVAL', 60)))
args = parser.parse_args()
logging.info(args)


while True:
    try:
        client = connect_mqtt_publisher(args)
        # Initialize I2C bus
        bus = smbus.SMBus(1)
        # Load calibration parameters
        calibration_params = bme280.load_calibration_params(bus, I2C_ADDR)
        logging.info(f'connected to i2c device at address: {hex(I2C_ADDR)}')

        while client is not None:
            t0 = time.time()
            # Read sensor data
            data = bme280.sample(bus, I2C_ADDR, calibration_params)
            write_data(data, client)

            dt = time.time()-t0
            time.sleep(args.interval-dt)
    except Exception as e:
        print(e)
    time.sleep(2*args.interval)

