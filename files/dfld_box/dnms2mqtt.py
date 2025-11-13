# !/usr/bin/env python3

# read DNMS sensor data and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import DNMSDataSource
from dfld import EventLoop

source = DNMSDataSource()
sink = MqttDataSink()
event_loop = EventLoop(source, sink)
event_loop.start()
