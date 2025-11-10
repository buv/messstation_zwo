import os
import abc
import sys
import logging  

class DataSink(abc.ABC):
    def __init__(self):
        self.connected = False
        self.config = os.environ

        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=self.log_level)
        self.client_name = os.getenv('CLIENT_NAME', sys.argv[0].split('/')[-1].replace('.py',''))
        self.logger = logging.getLogger(self.client_name)

        self.logger.debug(f"DataSink initialized with config: {self.config}")

    def get_logger(self) -> logging.Logger:
        return self.logger

    def set_logger(self, logger: logging.Logger):
        self.logger = logger

    @abc.abstractmethod
    def connect(self):
        """Connect to the data sink."""
        pass

    @abc.abstractmethod
    def write(self, line: str):
        """Write data to the data sink."""
        pass

    def is_connected(self) -> bool:
        """Check if the data sink is connected."""
        return self.connected
    
    def set_channel(self, channel: str):
        """Set the channel or topic for the data sink."""
        pass

    @abc.abstractmethod
    def close(self):
        """Close the connection to the data sink."""
        self.connected = False
        self.logger.info("Connection to data sink closed.")


# MqttDataSink inherits from DataSink
class MqttDataSink(DataSink, abc.ABC):
    def __init__(self):
        super().__init__()
        self.client = None
        self.mqtt_server = self.config.get('MQTT_SERVER', 'mqtt:1883')
        self.topic = self.config.get('MQTT_TOPIC', 'dfld/default')
        self.client_id = self.config.get('MQTT_CLIENT_ID', sys.argv[0].split('/')[-1].replace('.py',''))
        self.logger.debug(f"MQTT DataSink config: mqtt_server={self.mqtt_server}, topic={self.topic}, client_id={self.client_id}")

    def set_channel(self, topic: str):
        self.topic = topic

    def connect(self):
        import paho.mqtt.client as mqtt
        try:
            self.logger.info(f"Connecting to MQTT broker at {self.mqtt_server}...")
            self.client = mqtt.Client(
                client_id=self.client_id,
                clean_session=True,
                userdata=None,
                protocol=mqtt.MQTTv311,
                transport="tcp",
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2
            )
            mqtt_server = self.mqtt_server.split(':')
            self.client.connect(mqtt_server[0], int(mqtt_server[1]), 60)
            self.client.loop_start()
            self.logger.info("Connected to MQTT broker.")
            self.connected = True
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            self.connected = False

    def write(self, line: str):
        if not self.connected:
            self.logger.error("Not connected to MQTT broker.")
            return
        try:
            self.client.publish(self.topic, line)
            self.logger.debug(f"Published data to topic {self.topic}: {line}")
        except Exception as e:
            self.logger.error(f"Failed to publish data: {e}")

    def close(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self.connected = False
        self.logger.info("Disconnected from MQTT broker.")
        super().close()
