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
        except Exception as e:
            self.logger.error(f"Failed to initialize SSD1306 display: {e}")
            self.connected = False

    def write(self, line: str):
        import json
        import time
        
        if not self.connected:
            self.logger.error("Display not connected.")
            return
        
        # Check if display should be cleared due to timeout
        current_time = time.time()
        if self.last_write_time > 0 and (current_time - self.last_write_time) > self.display_timeout:
            self.clear()
            self.last_write_time = 0
            return
        
        try:
            # Parse JSON data
            data = json.loads(line)
            if not isinstance(data, dict):
                self.logger.warning(f"Received data is not a dict: {data}")
                return
            
            # Extract dB_A_avg value
            if "dB_A_avg" not in data:
                self.logger.debug(f"JSON does not contain 'dB_A_avg': {data}")
                return
            
            value = float(data["dB_A_avg"])
            self.last_write_time = current_time
            
            # Draw on display using luma canvas
            with self.canvas(self.display) as draw:
                # Try to load a large font, fall back to default if not available
                try:
                    # Try to use DejaVuSans font with large size to fill display
                    font = self.ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
                except Exception:
                    try:
                        # Fallback to another common font
                        font = self.ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
                    except Exception:
                        # Use default font if no truetype fonts available
                        font = self.ImageFont.load_default()
                
                # Format the text - display value with one decimal place
                text = f"{value:.1f}\ndBA"
                
                # Get text bounding box to center it
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                # Calculate position to center text
                x = (self.width - text_width) // 2
                y = (self.height - text_height) // 2
                
                # Draw text
                draw.text((x, y), text, font=font, fill="white")
            
            self.logger.debug(f"Display updated with value: {value:.1f} dBA")
        except json.JSONDecodeError:
            self.logger.warning(f"Failed to decode JSON from line: {line}")
        except Exception as e:
            self.logger.error(f"Failed to update display: {e}")

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
