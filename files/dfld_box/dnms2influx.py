import os
import sys
import time
import socket
import logging
import argparse
import serial
from questdb.ingress import Sender, Protocol, TimestampNanos
from dfld_common import LiveView

level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

missing_env = []
for k in "QUESTDB_SERVER QUESTDB_USERNAME QUESTDB_PASSWORD QUESTDB_MEASUREMENT".split():
    if k not in os.environ:
        missing_env.append(k)
if len(missing_env)>0:
    logging.error(f'following environment variables not set: {missing_env}')
    sys.exit(1)

device_name = 'DNMS_'+os.environ['DEVICE'][-1] if 'DEVICE_NAME' not in os.environ else os.environ['DEVICE_NAME']

parser = argparse.ArgumentParser()
parser.add_argument("-q", "--questdb-server", help="hostname and port of questdb server", default=os.environ['QUESTDB_SERVER'])
parser.add_argument("-u", "--username", help='username for questdb', default=os.environ['QUESTDB_USERNAME'])
parser.add_argument("-p", "--password", help='password for questdb', default=os.environ['QUESTDB_PASSWORD'])
parser.add_argument("-m", "--measurement", help="questdb measurement name for events", default=os.environ['QUESTDB_MEASUREMENT'])
parser.add_argument("-v", "--device", help="device path for DNMS", default=os.environ['DEVICE'])
parser.add_argument("-n", "--device-name", help="device name", default=device_name)
args = parser.parse_args()
logging.info(args)

band_freq = [20,   25,   31.5, 40,   50,   63,   80,   100,   125,   160, 
             200,  250,  315,  400,  500,  630,  800,  1000,  1250,  1600, 
             2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 
             20000]

def read_line(s, c, lv):
    line = s.readline()
    data_str = line.rstrip().decode('utf-8').split(':')

    fields = None
    fields_lin = None
    # band processing
    if data_str[0]=='B':
        fields = { f'L{data_str[1]}eq'+str(k): float(v) for k, v in zip(band_freq, data_str[2:]) }

    # summary processing
    if data_str[0]=='S':
        fields = { f'dB_{data_str[1]}_{str(k)}': float(v) for k, v in zip(['avg', 'min', 'max'], data_str[2:]) }
        fields_lin = { k+'_lin': 10**(v/10.) for k, v in fields.items() }

    if fields:
        # send data to questdb
        try:
            c.row(
                args.measurement,
                symbols={"device": args.device_name},
                columns=fields | fields_lin,
                at=TimestampNanos.now())
            c.flush()
            logging.debug(f'data written: weigth=dB{data_str[1]}, device={args.device_name}, {fields}')
        except Exception as e:
            logging.error(f'error writing data to questdb: {e}')

        # check if liveview is active
        if data_str[0]=='S' and data_str[1]=='A' and lv.active:
            # send data to liveview
            lv.send(float(fields['dB_A_avg']))
            logging.debug(f'liveview data sent: {fields["dB_A_avg"]} dBA')


while True:
    try:
        liveview = LiveView()
        
        # create connection to questdb
        logging.info(f'connecting to questdb database ({args.questdb_server})...')
        questdb_server = args.questdb_server.split(':')
        sender = Sender(Protocol.Http, questdb_server[0], int(questdb_server[1]), 
                        username=os.environ['QUESTDB_USERNAME'], 
                        password=os.environ['QUESTDB_PASSWORD'])
        sender.establish()
        logging.debug(f'sender={sender}')

        # open the serial port
        ser = serial.Serial(args.device, 500000)
        logging.info(f'connected to device: {args.device}')

        while True:
            read_line(ser, sender, liveview)
            time.sleep(0.01)
    except:
        pass
    time.sleep(1.)
