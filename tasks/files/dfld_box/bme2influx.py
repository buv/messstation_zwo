import os
import sys
import time
import bme280
import smbus3 as smbus
import logging
import argparse
from influxdb import InfluxDBClient


I2C_ADDR = 0x76 # BME280 sensor address (default address)
INTERVAL = 60

level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

missing_env = []
for k in "INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE INFLUXDB_MEASUREMENT".split():
    if k not in os.environ:
        missing_env.append(k)
if len(missing_env)>0:
    logging.error(f'following environment variables not set: {missing_env}')
    sys.exit(1)

parser = argparse.ArgumentParser()
parser.add_argument("-i", "--influxdb-server", help="hostname and port of influxdb server", default=os.environ['INFLUXDB_SERVER'])
parser.add_argument("-u", "--username", help='username for influxdb', default=os.environ['INFLUXDB_USERNAME'])
parser.add_argument("-p", "--password", help='password for influxdb', default=os.environ['INFLUXDB_PASSWORD'])
parser.add_argument("-d", "--database", help="influxdb database name", default=os.environ['INFLUXDB_DATABASE'])
parser.add_argument("-m", "--measurement", help="influxdb measurement name for events", default=os.environ['INFLUXDB_MEASUREMENT'])
args = parser.parse_args()
logging.info(args)


def write_data(data, c):
    # send data to influx DB
    json_body = [
        {
            "measurement": args.measurement,
            "tags": {
                "source": "bme280"
            },
            "fields": {
                "temperature": data.temperature,
                "pressure": data.pressure,
                "humidity": data.humidity
            }
        }
    ]
    c.write_points(json_body)
    logging.debug(f'data written: t={json_body[0]["fields"]["temperature"]:6.2f}, '
                 f'p={json_body[0]["fields"]["pressure"]:7.2f}, '
                 f'rh={json_body[0]["fields"]["humidity"]:6.2f}')


while True:
    try:
        # create connection to influxdb v1
        logging.info(f'connecting to influx database ({args.influxdb_server})...')
        influxdb_server = args.influxdb_server.split(':')
        client = InfluxDBClient(host=influxdb_server[0], port=influxdb_server[1], username=args.username, password=args.password)
        logging.debug(f'client={client}')
        client.switch_database(args.database)
        logging.info(f'switched to database "{args.database}"')

        # Initialize I2C bus
        bus = smbus.SMBus(1)
        # Load calibration parameters
        calibration_params = bme280.load_calibration_params(bus, I2C_ADDR)
        logging.info(f'connected to i2c device at address: {hex(I2C_ADDR)}')

        while True:
            t0 = time.time()
            # Read sensor data
            data = bme280.sample(bus, I2C_ADDR, calibration_params)
            write_data(data, client)

            dt = time.time()-t0
            time.sleep(INTERVAL-dt)
    except Exception as e:
        print(e)
    time.sleep(2*INTERVAL)

