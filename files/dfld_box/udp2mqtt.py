# !/usr/bin/env python3

# listen to UDP port and send data to MQTT broker

import os
import json
from dfld import DataSink, MqttDataSink
from dfld import UdpDataSource
from dfld import EventLoop

class MyEventLoop(EventLoop):
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = json.dumps({"source": "udp"} | data)
            sink.write(msg)
        else:
            self.logger.warning('No data to process')

source = UdpDataSource()
sink = MqttDataSink()
event_loop = MyEventLoop(source, sink)
event_loop.start()
