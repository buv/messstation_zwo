# !/usr/bin/env python3

# read BME280 sensor data and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import DNMSi2cDataSource
from dfld import EventLoop

source = DNMSi2cDataSource()
sink = MqttDataSink()
event_loop = EventLoop(source, sink)

logger = event_loop.get_logger()
logger.info(f"Using data source: {source.__class__.__name__}")
logger.info(f"Data source Loglevel: {logger.level}")
logger.info(f"Data source ic2 address: {hex(source.i2c_addr)}")

logger.info(f"Using data sink: {sink.__class__.__name__}")
logger.info(f"Data sink Loglevel: {logger.level}")
logger.info(f"Data sink broker: {sink.mqtt_server}, topic: {sink.topic}")


event_loop.start()
