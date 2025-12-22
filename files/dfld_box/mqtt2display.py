#!/usr/bin/env python3

# read noise sensor data via MQTT and display on SSD1306 i2c display

import os
import json
from dfld import DataSink, SSD1306DataSink
from dfld import MqttDataSource
from dfld import EventLoop

source = MqttDataSource()
sink = SSD1306DataSink()
readout_interval = float(os.getenv('READOUT_INTERVAL', 0.2))
event_loop = EventLoop(source, sink, readout_interval)
event_loop.start()
