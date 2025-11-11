# !/usr/bin/env python3

# read DNMS sensor data and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import DNMSDataSource
from dfld import EventLoop

class MyEventLoop(EventLoop):
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = json.dumps({"source": "dnms"} | data)
            sink.write(msg)
        else:
            self.logger.warning('No data to process')

source = DNMSDataSource()
sink = MqttDataSink()
event_loop = MyEventLoop(source, sink)
event_loop.start()
