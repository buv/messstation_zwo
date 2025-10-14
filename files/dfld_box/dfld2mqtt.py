# !/usr/bin/env python3

# read AK-Modul sensor data and send to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import AkModulDataSource
from dfld import EventLoop

class MyEventLoop(EventLoop):
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = json.dumps({"source": "ak-modul"} | data)
            sink.write(msg)
        else:
            self.logger.warning('No data to process')

event_loop = MyEventLoop(AkModulDataSource(), MqttDataSink())
event_loop.start()
