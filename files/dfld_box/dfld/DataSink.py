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
        self.client_name = sys.argv[0].split('/')[-1].replace('.py','')
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

    def write_meta(self, metadata_dict: dict):
        """Write metadata. Default implementation does nothing."""
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
        self.data_topic = self.config.get('MQTT_DATA_TOPIC', 'dfld/default')
        self.meta_topic = self.config.get('MQTT_META_TOPIC', 'dfld/metadata/sensors')
        self.client_id = sys.argv[0].split('/')[-1].replace('.py','')
        self.logger.debug(f"MQTT DataSink config: mqtt_server={self.mqtt_server}, data_topic={self.data_topic}, meta_topic={self.meta_topic}, client_id={self.client_id}")

    def set_channel(self, topic: str):
        self.logger.info(f"Registered MQTT topic for writing: {topic}")
        self.data_topic = topic

    def set_meta_channel(self, topic: str):
        self.logger.info(f"Registered MQTT metadata topic for writing: {topic}")
        self.meta_topic = topic

    def connect(self):
        import paho.mqtt.client as mqtt
        import time
        try:
            self.logger.info(f"Connecting to MQTT broker at {self.mqtt_server}...")
            self.client = mqtt.Client(
                client_id=self.client_id,
                clean_session=True,
                userdata=None,
                protocol=mqtt.MQTTv311,
                transport="tcp"
            )
            
            mqtt_server = self.mqtt_server.split(':')
            self.client.connect(mqtt_server[0], int(mqtt_server[1]), 60)
            self.client.loop_start()
            
            # Wait for connection with retry
            for i in range(10):
                if self.client.is_connected():
                    self.connected = True
                    self.logger.info("Connected to MQTT broker.")
                    return
                time.sleep(0.5)
            
            self.logger.error("MQTT connection timeout")
            self.connected = False
        except Exception as e:
            self.logger.error(f"Failed to connect to MQTT broker: {e}")
            self.connected = False

    def write(self, line: str):
        if not self.connected:
            self.logger.error("Not connected to MQTT broker.")
            return
        try:
            self.client.publish(self.data_topic, line)
            self.logger.debug(f"Published data to topic {self.data_topic}: {line}")
        except Exception as e:
            self.logger.error(f"Failed to publish data: {e}")

    def write_meta(self, metadata_dict: dict):
        """Write metadata dictionary as individual key-value messages with timestamp.
        
        Args:
            metadata_dict: Dictionary with metadata key-value pairs
        """
        import json
        import time
        
        if not self.connected:
            self.logger.error("Not connected to MQTT broker.")
            return
        
        timestamp_ns = int(time.time() * 1_000_000_000)
        station_id = self.config.get('DFLD_STATION_ID', '')
        self.logger.debug(f"Station ID: '{station_id}', Metadata dict: {metadata_dict}")
        if not station_id:
            self.logger.error("DFLD_STATION_ID not set, skipping metadata transmission")
            return
        
        try:
            for key, value in metadata_dict.items():
                if key == "ts":  # Skip if timestamp already exists
                    continue
                    
                meta_message = {
                    "station": station_id,
                    "key": key,
                    "value": value,
                    "ts": timestamp_ns
                }
                
                json_message = json.dumps(meta_message)
                self.logger.debug(f"Publishing to {self.meta_topic}: {json_message}")
                result = self.client.publish(self.meta_topic, json_message)
                result.wait_for_publish(timeout=2.0)
                self.logger.info(f"Published metadata to topic {self.meta_topic}: {key}={value}")
        except Exception as e:
            self.logger.error(f"Failed to publish metadata: {e}")

    def close(self):
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self.connected = False
        self.logger.info(f"Disconnected from MQTT broker. Topics closed: data={self.data_topic}, meta={self.meta_topic}")
        super().close()


# SSD1306DataSink inherits from DataSink
class SSD1306DataSink(DataSink):
    def __init__(self):
        super().__init__()
        self.display = None
        self.i2c_bus = int(self.config.get('I2C_BUS', '1'))
        self.i2c_addr = int(self.config.get('SSD1306_I2C_ADDR', '0x3C'), 16)
        self.width = int(self.config.get('SSD1306_WIDTH', '128'))
        self.height = int(self.config.get('SSD1306_HEIGHT', '64'))
        self.display_timeout = float(self.config.get('DISPLAY_TIMEOUT', '2.0'))
        self.last_write_time = 0
        self.FONTSIZE_LARGE = 42
        self.FONTSIZE_SMALL = 10
        self.font_large = None
        self.font_small = None
        self.logger.debug(f"SSD1306 DataSink config: i2c_bus={self.i2c_bus}, addr={hex(self.i2c_addr)}, size={self.width}x{self.height}, timeout={self.display_timeout}s")

    def connect(self):
        try:
            from luma.core.interface.serial import i2c
            from luma.oled.device import ssd1306
            from luma.core.render import canvas
            from PIL import ImageFont
            
            self.logger.info(f"Initializing SSD1306 display on I2C bus {self.i2c_bus} at address {hex(self.i2c_addr)}...")
            
            # Create I2C interface
            serial = i2c(port=self.i2c_bus, address=self.i2c_addr)
            
            # Create display object
            self.display = ssd1306(serial, width=self.width, height=self.height)
            
            # Clear display
            self.display.clear()
            
            # Store modules for later use
            self.canvas = canvas
            self.ImageFont = ImageFont
            
            self.connected = True
            self.logger.info("SSD1306 display initialized successfully.")
            
            # Test display with startup message
            try:
                with self.canvas(self.display) as draw:
                    draw.text((10, 10), "DFLD", font=self.ImageFont.load_default(), fill="white")
                    draw.text((10, 30), "Starting...", font=self.ImageFont.load_default(), fill="white")
                self.logger.info("Display test pattern shown")
            except Exception as e:
                self.logger.error(f"Failed to show test pattern: {e}")

            # Try to load a large font sized for 3-digit values to fill display
            # For 128x64 display and format "XXX.X"
            found = False
            paths = [
                     'ttf-dejavu/DejaVuSans-Bold.ttf',
                     'ttf-dejavu/DejaVuSans.ttf',
                     'truetype/dejavu/DejaVuSans-Bold.ttf'
            ]
            for sub_path in paths:
                path = f"/usr/share/fonts/{sub_path}"
                try:
                    self.font_large = self.ImageFont.truetype(path, self.FONTSIZE_LARGE)
                    self.font_small = self.ImageFont.truetype(path, self.FONTSIZE_SMALL)
                    found = True
                    break
                except Exception:
                    pass
            if not found:
                self.font_large = self.ImageFont.load_default()
                self.font_small = self.ImageFont.load_default()
        except Exception as e:
            self.logger.error(f"Failed to initialize SSD1306 display: {e}")
            self.connected = False

    def write(self, line: str):
        import json
        import time
        
        if not self.connected:
            self.logger.error("Display not connected.")
            return
        
        try:
            # Parse JSON data
            data = json.loads(line)
            if not isinstance(data, dict):
                self.logger.warning(f"Received data is not a dict: {data}")
                return
            
            current_time = time.time()
            # Extract dB_A_avg value
            if "dB_A_avg" not in data:
                self.logger.debug(f"JSON does not contain 'dB_A_avg': {data}")

                # Check if display should be cleared due to timeout
                if self.last_write_time > 0 and (current_time - self.last_write_time) > self.display_timeout:
                    value_text = "--.-"
                else:
                    return
            else:
                value_text = f"{float(data["dB_A_avg"]):.1f}"
                self.last_write_time = current_time
            
            # Draw on display using luma canvas
            with self.canvas(self.display) as draw:
                # Format the text - only numeric value with one decimal place
                text = value_text
                text_width, text_height = self.calc_bb(draw, text, self.font_large)
                # Calculate position to center text
                x = (self.width - text_width) // 2
                y = 0
                # Draw text
                draw.text((x, y), text, font=self.font_large, fill="white")

                text = "dB (A)"
                text_width, text_height = self.calc_bb(draw, text, self.font_small)
                # Calculate position to center text
                x = (self.width - text_width) // 2
                y = self.height - 24
                # Draw text
                draw.text((x, y), text, font=self.font_small, fill="white")

                if "dB_A_min" in data and "dB_A_max" in data:
                    text = f"min: {float(data['dB_A_min']):.1f} max: {float(data['dB_A_max']):.1f}"
                    text_width, text_height = self.calc_bb(draw, text, self.font_small)
                    # Calculate position to center text
                    x = (self.width - text_width) // 2
                    y = self.height - 10
                    # Draw text
                    draw.text((x, y), text, font=self.font_small, fill="white")
            
            self.logger.debug(f"Display updated with text: {value_text} dBA")
        except json.JSONDecodeError:
            self.logger.warning(f"Failed to decode JSON from line: {line}")
        except Exception as e:
            self.logger.error(f"Failed to update display: {e}")

    def calc_bb(self, draw, text, font):
        # Get text bounding box to center it
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def clear(self):
        if not self.connected:
            return
        
        try:
            self.display.clear()
            self.logger.debug("Display cleared.")
        except Exception as e:
            self.logger.error(f"Failed to clear display: {e}")

    def close(self):
        if self.display:
            try:
                self.display.clear()
                self.display.cleanup()
            except Exception:
                pass
        self.connected = False
        self.logger.info("SSD1306 display closed.")
        super().close()
