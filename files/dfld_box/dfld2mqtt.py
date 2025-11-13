# !/usr/bin/env python3

# read AK-Modul sensor data and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import AkModulDataSource
from dfld import EventLoop

source = AkModulDataSource()
sink = MqttDataSink()
event_loop = EventLoop(source, sink)
event_loop.start()
