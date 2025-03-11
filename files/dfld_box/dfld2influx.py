import os
import sys
import time
import logging
import argparse
import serial
from influxdb import InfluxDBClient

level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

missing_env = []
for k in "INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE INFLUXDB_MEASUREMENT DEVICE".split():
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
parser.add_argument("-v", "--device", help="device path for DFLD", default=os.environ['DEVICE'])
args = parser.parse_args()
logging.info(args)


def read_line(s, c):
    b = int.from_bytes(s.read(1))
    if b>0:
        data = (b-50)/2

        # send data to influx DB
        json_body = [
            {
                "measurement": args.measurement,
                "tags": {
                    "source": "ak-modul-bus"
                },
                "fields": {
                    "dB_A_avg": data
                }
            }
        ]
        c.write_points(json_body)
        logging.debug(f'data written: avg={data:.2f}')

while True:
    try:
        # create connection to influxdb v1
        logging.info(f'connecting to influx database ({args.influxdb_server})...')
        influxdb_server = args.influxdb_server.split(':')
        client = InfluxDBClient(host=influxdb_server[0], port=influxdb_server[1], username=args.username, password=args.password)
        logging.debug(f'client={client}')
        client.switch_database(args.database)
        logging.info(f'switched to database "{args.database}"')

        # open the serial port
        ser = serial.Serial(args.device, 9600)
        logging.info(f'connected to device: {args.device}')

        while True:
            read_line(ser, client)
            time.sleep(0.01)

    except:
        pass
    time.sleep(1.)
