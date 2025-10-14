import os
import abc
import time
import logging

class DataSource(abc.ABC):
    def __init__(self):
        self.logger = logging.getLogger('DataSource')
        self.connected = False

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
        config = os.environ
        self.i2c_addr = int(config.get('I2C_ADDR', '0x76'), 16)
        self.bus_num = int(config.get('I2C_BUS', '1'))
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
        

class AkModulDataSource(DataSource, abc.ABC):
    def __init__(self):
        super().__init__()
        config = os.environ
        self.device = config.get('AK_MODUL_DEVICE', '/dev/USB0')
        self.logger.debug(f"AK-Modul DataSource config: device={self.device}")
        self.baudrate = config.get('AK_MODUL_BAUDRATE', 9600)
        self.ser = None
        self.connected = False

    def init(self):
        import sys
        import serial
        try:
            self.logger.info(f"Initializing AK-Modul sensor at device {self.device}...")
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
        config = os.environ
        self.device = config.get('DNMS_DEVICE', '/dev/ttyDNMS')
        self.logger.debug(f"DNMS DataSource config: device={self.device}")
        self.baudrate = config.get('DNMS_BAUDRATE', )
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
                    fields |= { f'L{data_str[1]}eq'+str(k): float(v) for k, v in zip(self.BAND_FREQ, data_str[2:]) }

                # summary processing
                if data_str[0]=='S':
                    fields |= { f'dB_{data_str[1]}_{str(k)}': float(v) for k, v in zip(['avg', 'min', 'max'], data_str[2:]) }
                
                return fields     
        except Exception as e:
            self.logger.error(f"Failed to read from DNMS sensor: {e}")
            self.connected = False
            # exit container to allow restart
            exit(0)
        return {}
               