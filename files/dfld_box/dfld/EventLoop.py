import os
import abc
import sys
import time
import logging
from .DataSink import DataSink
from .DataSource import DataSource
# create class for event loop

class EventLoop(abc.ABC):
    def __init__(self, data_source: DataSource, data_sink: DataSink):
        self.config = os.environ
        self.readout_interval = float(self.config.get('READOUT_INTERVAL', 60))
        self.retry_interval = float(self.config.get('RETRY_INTERVAL', 120))
        self.data_source = data_source
        self.data_sink = data_sink
        self.log_level = self.config.get('LOG_LEVEL', 'INFO').upper()
        logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=self.log_level)
        self.client_name = self.config.get('CLIENT_NAME', sys.argv[0].split('/')[-1].replace('.py',''))
        self.logger = logging.getLogger(self.client_name)
        self.logger.info(f"EventLoop starting with client_name={self.client_name}")
        self.logger.info(f"EventLoop initialized with readout_interval={self.readout_interval}, retry_interval={self.retry_interval}")
        if self.data_source:
            self.data_source.set_logger(self.logger)
        if self.data_sink:
            self.data_sink.set_logger(self.logger)
        self.running = False

    @abc.abstractmethod
    def process(self, data: dict, sink: DataSink):
        pass

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

                start_time = time.time()
                self.process(self.data_source.read(), self.data_sink)
                elapsed_time = time.time() - start_time
                sleep_time = max(0, self.readout_interval - elapsed_time)
                time.sleep(sleep_time)

            except Exception as e:
                self.logger.error(f'Error in event loop: {e}')
                time.sleep(self.retry_interval)

    def stop(self):
        self.running = False