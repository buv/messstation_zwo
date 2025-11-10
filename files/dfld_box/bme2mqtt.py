# !/usr/bin/env python3

# read BME280 sensor data and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import Bme280DataSource
from dfld import EventLoop

class MyEventLoop(EventLoop):
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = json.dumps({"source": "bme280"} | data)
            sink.write(msg)
        else:
            self.logger.warning('No data to process')

source = Bme280DataSource()
sink = MqttDataSink()
event_loop = MyEventLoop(source, sink)
event_loop.start()
