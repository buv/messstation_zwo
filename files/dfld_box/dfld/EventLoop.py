import os
import sys
import json
import time
import logging
from .DataSink import DataSink
from .DataSource import DataSource
# create class for event loop

class EventLoop(object):
    def __init__(self, data_source: DataSource, data_sink: DataSink, readout_interval=None):
        self.data_source = data_source
        self.data_sink = data_sink
        self.readout_interval = readout_interval
        self.retry_interval = float(os.getenv('RETRY_INTERVAL', 120))
        self.process_empty = float(os.getenv('PROCESS_EMPTY', 0))
        self.dfld_station_id = os.getenv('DFLD_STATION_ID', 'default-station')
        self.running = False
        self.metadata_sent = False

        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=self.log_level)
        self.client_name = sys.argv[0].split('/')[-1].replace('.py','')
        self.logger = logging.getLogger(self.client_name)

        self.logger.info(f"EventLoop starting with client_name={self.client_name}")
        self.logger.info(f"EventLoop initialized with readout_interval={self.readout_interval}, retry_interval={self.retry_interval}")

    def get_logger(self) -> logging.Logger:
        return self.logger
    
    def set_logger(self, logger: logging.Logger):
        self.logger = logger

    def process(self, data: dict, sink: DataSink):
        if (self.process_empty or data) and isinstance(data, dict):
            # Write data without metadata
            sink.write(json.dumps({"station": self.dfld_station_id} | data))
        else:
            self.logger.warning('No valid data to process')

    def start(self):
        self.running = True
        while self.running:
            try:
                if not self.data_source.connected:
                    self.logger.info('Data source not connected. Attempting to initialize...')
                    self.data_source.init()
                    if not self.data_source.connected:
                        self.logger.error('Failed to connect to data source. Retrying in {} seconds...'.format(self.retry_interval))
                        time.sleep(self.retry_interval)
                        continue

                if not self.data_sink.connected:
                    self.logger.info('Data sink not connected. Attempting to connect...')
                    self.data_sink.connect()
                    if not self.data_sink.connected:
                        self.logger.error('Failed to connect to data sink. Retrying in {} seconds...'.format(self.retry_interval))
                        time.sleep(self.retry_interval)
                        continue

                # Send metadata once after both source and sink are connected
                if not self.metadata_sent:
                    self.logger.debug(f"Metadata to send: {self.data_source.metadata}")
                    self.data_sink.write_meta(self.data_source.metadata)
                    self.metadata_sent = True

                start_time = time.time()
                self.process(self.data_source.read(), self.data_sink)
                elapsed_time = time.time() - start_time
                if self.readout_interval is not None:
                    sleep_time = max(0, self.readout_interval - elapsed_time)
                    time.sleep(sleep_time)

            except Exception as e:
                self.logger.error(f'Error in event loop: {e}')
                time.sleep(self.retry_interval)

    def stop(self):
        self.running = False