#!/usr/bin/env python3

# read noise sensor data via MQTT and display on SSD1306 i2c display

import os
import json
from dfld import DataSink, SSD1306DataSink
from dfld import MqttDataSource
from dfld import EventLoop

source = MqttDataSource()
sink = SSD1306DataSink()
event_loop = EventLoop(source, sink)
event_loop.start()
