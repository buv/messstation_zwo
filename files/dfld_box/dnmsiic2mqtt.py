# !/usr/bin/env python3

# read DNMS sensor data via i2c and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import DNMSi2cDataSource
from dfld import EventLoop

source = DNMSi2cDataSource()
sink = MqttDataSink()
event_loop = EventLoop(source, sink)
event_loop.start()
