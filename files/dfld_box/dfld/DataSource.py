import os
import abc
import sys
import time
import logging

class DataSource(abc.ABC):
    def __init__(self):
        self.connected = False
        self.config = os.environ
        self.source = "unknown"

        self.log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=self.log_level)
        self.client_name = os.getenv('CLIENT_NAME', sys.argv[0].split('/')[-1].replace('.py',''))
        self.logger = logging.getLogger(self.client_name)

        self.logger.debug(f"DataSource initialized with config: {self.config}")

    def get_logger(self) -> logging.Logger:
        return self.logger

    def set_logger(self, logger: logging.Logger):
        self.logger = logger

    @abc.abstractmethod
    def init(self):
        """Initialize the data source."""
        pass

    @abc.abstractmethod
    def read(self) -> dict:
        """Read data from the data source."""
        pass


class Bme280DataSource(DataSource, abc.ABC):
    def __init__(self):
        super().__init__()
        self.source = "bme280"
        self.i2c_addr = int(os.getenv('BME280_I2C_ADDR', '0x76'), 16)
        self.bus_num = int(os.getenv('I2C_BUS', '1'))
        self.logger.debug(f"BME280 DataSource config: i2c_addr={hex(self.i2c_addr)}, bus_num={self.bus_num}")
        self.bus = None
        self.calibration_params = None

    def init(self):
        import smbus3 as smbus
        import bme280
        try:
            self.logger.info(f"Initializing BME280 sensor at I2C address {hex(self.i2c_addr)} on bus {self.bus_num}...")
            self.bus = smbus.SMBus(self.bus_num)
            self.calibration_params = bme280.load_calibration_params(self.bus, self.i2c_addr)
            self.logger.info("BME280 sensor initialized.")
            self.connected = True
        except Exception as e:
            self.logger.error(f"Failed to initialize BME280 sensor: {e}")
            self.connected = False

    def read(self) -> dict:
        import bme280
        if not self.connected:
            raise RuntimeError("Data source not connected. Call init() first.")
        try:
            data = bme280.sample(self.bus, self.i2c_addr, self.calibration_params)
            ts = int(time.time() * 1e9)
            return {
                "temperature": data.temperature,
                "pressure": data.pressure,
                "humidity": data.humidity,
                "ts": ts
            }
        except Exception as e:
            self.logger.error(f"Failed to read from BME280 sensor: {e}")
            self.connected = False
        return {}
        

class DNMSi2cDataSource(DataSource, abc.ABC):
    def __init__(self):
        super().__init__()
        self.source = "dnms_i2c"
        self.i2c_addr = int(self.config.get('DNMS_I2C_ADDR', '0x55'), 16)
        self.bus_num = int(self.config.get('I2C_BUS', '1'))
        self.microphone = int(os.getenv('DNMS_MICROPHONE_TYPE', '28'))
        self.logger.debug(f"DNMS i2c DataSource config: i2c_addr={hex(self.i2c_addr)}, bus_num={self.bus_num}")
        self.bus = None
        self.calibration_params = None

    def init(self):
        import smbus3 as smbus
        try:
            self.logger.info(f"Initializing DNMS i2c sensor at I2C address {hex(self.i2c_addr)} on bus {self.bus_num}...")
            self.bus = smbus.SMBus(self.bus_num)

            # DNMS device
            # reset
            self.bus.write_byte_data(self.i2c_addr, 0x00, 0x01)
            time.sleep(1.0)
            
            # read version
            write = smbus.i2c_msg.write(self.i2c_addr, [0x00, 0x02])
            read = smbus.i2c_msg.read(self.i2c_addr, 64)
            self.bus.i2c_rdwr(write, read)
            buf = bytes(self.remove_crc(list(read)))
            version = buf.decode('ascii').split('\0')[0]
            logging.info(f'Connected to i2c device at address {hex(self.i2c_addr)} with version "{version}"')

            self.bus.write_byte_data(self.i2c_addr, 0x00, self.microphone)
            time.sleep(1.0)
            logging.info(f'Setting microphone type to {self.microphone}')

            self.logger.info("DNMS i2c sensor initialized.")
            self.connected = True
        except Exception as e:
            self.logger.error(f"Failed to initialize DNMS i2c sensor: {e}")
            self.connected = False

    def read(self) -> dict:
        import smbus3 as smbus
        if not self.connected:
            raise RuntimeError("Data source not connected. Call init() first.")
        try:
            # request data
            self.bus.write_byte_data(self.i2c_addr, 0x00, 0x03)

            # wait until available
            ready = 0
            while not ready:
                time.sleep(0.005)
                write = smbus.i2c_msg.write(self.i2c_addr, [0x00, 0x04])
                read = smbus.i2c_msg.read(self.i2c_addr, 2)
                self.bus.i2c_rdwr(write, read)
                ready = list(read)[1]

            # readout
            write = smbus.i2c_msg.write(self.i2c_addr, [0x00, 0x05])
            read = smbus.i2c_msg.read(self.i2c_addr, 64)
            self.bus.i2c_rdwr(write, read)
            data = self.floats_from_bytes(list(read))

            ts = int(time.time() * 1e9)
            data = {
                # round to 2 decimal places
                "dB_A_avg": round(data[0], 2),
                "dB_A_min": round(data[1], 2),
                "dB_A_max": round(data[2], 2),
                "ts": ts
            }
            logging.debug(f"Read data from DNMS i2c: {data}")
            return data
        except Exception as e:
            self.logger.error(f"Failed to read from DNMS i2c sensor: {e}")
            self.connected = False
        return {}

    @staticmethod
    def remove_crc(data):
        result = []
        i = 0
        while i+3 <= len(data):
            result.append(data[i])
            result.append(data[i+1])
            i += 3
        return result
            
    @classmethod
    def floats_from_bytes(cls, data):
        import struct
        data = cls.remove_crc(data)
        result = []
        i = 0
        while i+4 <= len(data):
            if not data[i]:
                break
            result.append(struct.unpack('>f', bytes(data[i:i+4]))[0])
            i += 4
        return result


class AkModulDataSource(DataSource, abc.ABC):
    def __init__(self):
        super().__init__()
        self.source = "ak_modul"
        self.device = os.getenv('AK_MODUL_DEVICE', '/dev/ttyUSB0')
        self.baudrate = int(os.getenv('AK_MODUL_BAUDRATE', '9600'))
        self.logger.debug(f"AK-Modul DataSource config: device={self.device}, baudrate={self.baudrate}")
        self.ser = None
        self.connected = False

    def init(self):
        import sys
        import serial
        try:
            self.logger.info(f"Initializing AK-Modul sensor at device {self.device}, baudrate {self.baudrate}...")
            self.ser = serial.Serial(self.device, self.baudrate, timeout=1)
            self.connected = True
        except Exception as e:
            self.logger.error(f"Failed to initialize AK-Modul sensor: {e}")
            self.connected = False

    def read(self) -> dict:
        import serial
        if not self.connected:
            raise RuntimeError("Data source not connected. Call init() first.")
        try:
            b = int.from_bytes(self.ser.read(1))
            ts = int(time.time() * 1e9)
            if b>0:
                data = (b-50)/2
                return {"dB_A_avg": data, "ts": ts}
        except Exception as e:
            self.logger.error(f"Failed to read from AK-Modul sensor: {e}")
            self.connected = False
            # exit container to allow restart
            exit(0)
        return {}
        

class DNMSDataSource(DataSource, abc.ABC):
    BAND_FREQ = [20,   25,   31.5, 40,   50,   63,   80,   100,   125,   160,
                 200,  250,  315,  400,  500,  630,  800,  1000,  1250,  1600,
                 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 
                 20000]

    def __init__(self):
        super().__init__()
        self.source = "dnms_serial"        
        config = os.environ
        self.device = config.get('DNMS_DEVICE', '/dev/ttyDNMS')
        self.logger.debug(f"DNMS DataSource config: device={self.device}")
        self.baudrate = int(config.get('DNMS_BAUDRATE', '9600'))
        self.ser = None
        self.connected = False

    def init(self):
        import serial
        try:
            self.logger.info(f"Initializing DNMS sensor at device {self.device}...")
            self.ser = serial.Serial(self.device, self.baudrate)
            self.connected = True
        except Exception as e:
            self.logger.error(f"Failed to initialize DNMS sensor: {e}")
            self.connected = False

    def read(self) -> dict:
        import sys
        import serial
        if not self.connected:
            raise RuntimeError("Data source not connected. Call init() first.")
        try:
            line = self.ser.readline()
            ts = int(time.time() * 1e9)
            self.logger.debug(f"Read line from DNMS: {line}")
            if line:
                data_str = line.rstrip().decode('utf-8').split(':')

                fields = {"ts": ts} 
                # band processing
                if data_str[0]=='B':
                    fields |= { f'L{data_str[1]}eq'+str(k): round(float(v), 2) for k, v in zip(self.BAND_FREQ, data_str[2:]) }

                # summary processing
                if data_str[0]=='S':
                    fields |= { f'dB_{data_str[1]}_{str(k)}': round(float(v), 2) for k, v in zip(['avg', 'min', 'max'], data_str[2:]) }
                
                return fields     
        except Exception as e:
            self.logger.error(f"Failed to read from DNMS sensor: {e}")
            self.connected = False
            # exit container to allow restart
            exit(0)
        return {}
               

class UdpDataSource(DataSource, abc.ABC):
    def __init__(self):
        super().__init__()
        self.source = "udp"
        self.udp_host = os.getenv('UDP_LISTEN_IP', '0.0.0.0')
        self.udp_port = int(os.getenv('UDP_LISTEN_PORT', '11883'))
        self.logger.debug(f"UDP DataSource config: udp_ip={self.udp_host}, udp_port={self.udp_port}")
        self.sock = None
        self.connected = False

    def init(self):
        import socket
        try:
            self.logger.info(f"Initializing UDP socket to {self.udp_host}:{self.udp_port}...")
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((self.udp_host, self.udp_port))
            self.connected = True
        except Exception as e:
            self.logger.error(f"Failed to initialize UDP socket: {e}")
            self.connected = False

    def read(self) -> dict:
        import json
        import socket
        if not self.connected:
            raise RuntimeError("Data source not connected. Call init() first.")
        try:
            data_bytes, addr = self.sock.recvfrom(1024)  # buffer size is 1024 bytes
            ts = int(time.time() * 1e9)
            self.logger.debug(f"Received UDP data from {addr}: {data_bytes}")
            if data_bytes:
                data_json = data_bytes.decode('utf-8')
                data = json.loads(data_json)
                if isinstance(data, dict):
                    if "ts" not in data:
                        data["ts"] = ts
                    return data
        except Exception as e:
            self.logger.error(f"Failed to read from UDP socket: {e}")
            self.connected = False
        return {}