import os
import sys
import time
import struct
import logging
import argparse
import smbus3 as smbus
from influxdb import InfluxDBClient


I2C_ADDR = 0x55 # DNMS slave address
INTERVAL = 1

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


def remove_crc(data):
    result = []
    i = 0
    while i+3 <= len(data):
        result.append(data[i])
        result.append(data[i+1])
        i += 3
    return result
        
def floats_from_bytes(data):
    data = remove_crc(data)
    result = []
    i = 0
    while i+4 <= len(data):
        if not data[i]:
            break
        result.append(struct.unpack('>f', bytes(data[i:i+4]))[0])
        i += 4
    return result

def write_data(data, c):
    # send data to influx DB
    json_body = [
        {
            "measurement": args.measurement,
            "tags": {
                "source": "i2c"
            },
            "fields": {
                "dB_A_avg": data[0],
                "dB_A_min": data[1],
                "dB_A_max": data[2]
            }
        }
    ]
    c.write_points(json_body)
    logging.debug(f'data written: avg={data[0]:.2f}, min={data[1]:.2f}, max={data[2]:.2f}')

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

        # DNMS device
        # reset
        bus.write_byte_data(I2C_ADDR, 0x00, 0x01)
        time.sleep(1.0)
        
        # read version
        write = smbus.i2c_msg.write(I2C_ADDR, [0x00, 0x02])
        read = smbus.i2c_msg.read(I2C_ADDR, 64)
        bus.i2c_rdwr(write, read)
        buf = bytes(remove_crc(list(read)))
        version = buf.decode('ascii').split('\0')[0]
        logging.info(f'connected to i2c device at address {hex(I2C_ADDR)} with version "{version}"')

        microphone = 28
        bus.write_byte_data(I2C_ADDR, 0x00, microphone)
        time.sleep(1.0)
        logging.info(f'setting microphone type to {microphone}')


        while True:
            t0 = time.time()

            # request data
            bus.write_byte_data(I2C_ADDR, 0x00, 0x03)

            # wait until available
            ready = 0
            while not ready:
                time.sleep(0.005)
                write = smbus.i2c_msg.write(I2C_ADDR, [0x00, 0x04])
                read = smbus.i2c_msg.read(I2C_ADDR, 2)
                bus.i2c_rdwr(write, read)
                ready = list(read)[1]

            # readout
            write = smbus.i2c_msg.write(I2C_ADDR, [0x00, 0x05])
            read = smbus.i2c_msg.read(I2C_ADDR, 64)
            bus.i2c_rdwr(write, read)
            data = floats_from_bytes(list(read))
            write_data(data, client)

            dt = time.time()-t0
            time.sleep(INTERVAL-dt)
    except Exception as e:
        logging.error(str(e))
    time.sleep(60*INTERVAL)

