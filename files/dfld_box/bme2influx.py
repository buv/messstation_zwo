import os
import sys
import time
import bme280
import smbus3 as smbus
import logging
import argparse
from questdb.ingress import Sender, Protocol, TimestampNanos

I2C_ADDR = 0x76 # BME280 sensor address (default address)
INTERVAL = 60

level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

missing_env = []
for k in "QUESTDB_SERVER QUESTDB_USERNAME QUESTDB_PASSWORD QUESTDB_MEASUREMENT".split():
    if k not in os.environ:
        missing_env.append(k)
if len(missing_env)>0:
    logging.error(f'following environment variables not set: {missing_env}')
    sys.exit(1)

parser = argparse.ArgumentParser()
parser.add_argument("-q", "--questdb-server", help="hostname and port of questdb server", default=os.environ['QUESTDB_SERVER'])
parser.add_argument("-u", "--username", help='username for questdb', default=os.environ['QUESTDB_USERNAME'])
parser.add_argument("-p", "--password", help='password for questdb', default=os.environ['QUESTDB_PASSWORD'])
parser.add_argument("-m", "--measurement", help="questdb measurement name for events", default=os.environ['QUESTDB_MEASUREMENT'])
args = parser.parse_args()
logging.info(args)


def write_data(data, c):
    # send data to questdb
    try:
        c.row(
            args.measurement,
            symbols={"source": "bme280"},
            columns={"temperature": data.temperature, "pressure": data.pressure, "humidity": data.humidity},
            at=TimestampNanos.now())
        c.flush()
        logging.debug(f'data written: t={data.temperature:6.2f}, p={data.pressure:7.2f}, rh={data.humidity:6.2f}')
    except Exception as e:
        logging.error(f'error writing data to questdb: {e}')


while True:
    try:
        # create connection to questdb
        logging.info(f'connecting to questdb database ({args.questdb_server})...')
        questdb_server = args.questdb_server.split(':')
        sender = Sender(Protocol.Http, questdb_server[0], int(questdb_server[1]), 
                        username=os.environ['QUESTDB_USERNAME'], 
                        password=os.environ['QUESTDB_PASSWORD'])
        sender.establish()
        logging.debug(f'sender={sender}')

        # Initialize I2C bus
        bus = smbus.SMBus(1)
        # Load calibration parameters
        calibration_params = bme280.load_calibration_params(bus, I2C_ADDR)
        logging.info(f'connected to i2c device at address: {hex(I2C_ADDR)}')

        while True:
            t0 = time.time()
            # Read sensor data
            data = bme280.sample(bus, I2C_ADDR, calibration_params)
            write_data(data, sender)

            dt = time.time()-t0
            time.sleep(INTERVAL-dt)
    except Exception as e:
        print(e)
    time.sleep(2*INTERVAL)

