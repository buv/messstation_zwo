# !/usr/bin/env python3

# read BME280 sensor data and send to MQTT broker

import os
from dfld import DataSink, MqttDataSink
from dfld import AkModulDataSource
from dfld import EventLoop

class MyEventLoop(EventLoop):
    def process(self, data: dict, sink: DataSink):
        if data:
            msg = f'{{"source": "ak-modul", "temperature": {data["temperature"]}, "pressure": {data["pressure"]}, "humidity": {data["humidity"]}, "ts": {data["ts"]}}}'
            sink.write(msg)
        else:
            self.logger.warning('No data to process')

event_loop = MyEventLoop(AkModulDataSource(), MqttDataSink())
event_loop.start()
