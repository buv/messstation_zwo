import os
import json
import math
import time
import socket
import logging
import argparse
import numpy as np
from influxdb import InfluxDBClient

level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)
logging.info('starting...')
logging.info(f'LOG_LEVEL={level}')

missing_env = []
for k in "DUMP1090_SERVER INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE INFLUXDB_MEASUREMENT STATION_POSITION".split():
    if k not in os.environ:
        missing_env.append(k)
if len(missing_env)>0:
    logging.error(f'following environment variables not set: {missing_env}')
    exit(1)

parser = argparse.ArgumentParser()
parser.add_argument("-a", "--dump1090-server", help="hostname and port of dump1090 server", default=os.environ['DUMP1090_SERVER'])
parser.add_argument("-i", "--influxdb-server", help="hostname and port of influxdb server", default=os.environ['INFLUXDB_SERVER'])
parser.add_argument("-u", "--username", help='username for influxdb', default=os.environ['INFLUXDB_USERNAME'])
parser.add_argument("-p", "--password", help='password for influxdb', default=os.environ['INFLUXDB_PASSWORD'])
parser.add_argument("-d", "--database", help="influxdb database name", default=os.environ['INFLUXDB_DATABASE'])
parser.add_argument("-m", "--measurement", help="influxdb measurement name for events", default=os.environ['INFLUXDB_MEASUREMENT'])
parser.add_argument("-x", "--position", help="longitude,latitude,altitude in degrees and meters of station", default=os.environ['STATION_POSITION'])
args = parser.parse_args()

logging.info(args)

lon_lat_alt = [float(x) for x in args.position.split(':')]
logging.info(f'Station Position lon, lat, alt = {lon_lat_alt}')

r_0 = 6371000.0
ICAO_TIMEOUT = 1000 # seconds of non reception before purge from cache
info = {}

def xyz(lon, lat, alt):
    lon = lon * 3.141592653589793 / 180.0
    lat = lat * 3.141592653589793 / 180.0
    x = (r_0 + alt) * math.cos(lat) * math.cos(lon)
    y = (r_0 + alt) * math.cos(lat) * math.sin(lon)
    z = (r_0 + alt) * math.sin(lat)
    return np.array([x, y, z])

def update_info(sign, key, value):
    if sign not in info:
        info[sign] = { 'last_active': None }
        info[sign]['flyover_detected'] = False
    info[sign][key] = value

xyz0 = xyz(*lon_lat_alt)

while True:
    try:
        # create connection to influxdb v1
        logging.info(f'connecting to influx database ({args.influxdb_server})...')
        influxdb_server = args.influxdb_server.split(':')
        client = InfluxDBClient(host=influxdb_server[0], port=influxdb_server[1], username=args.username, password=args.password)
        logging.debug(f'client={client}')
        client.switch_database(args.database)
        logging.info(f'switched to database "{args.database}"')

        # connect to dump1090 process and loop over lines
        logging.info(f'connecting to dump1090 server ({args.dump1090_server})')
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dump1090_server = args.dump1090_server.split(':')
        s.connect((dump1090_server[0], int(dump1090_server[1])))
        logging.info(f'connection established.')
        
        with s.makefile('r') as f:
            for line in f:
                # parse line as json
                try:
                    data = json.loads(line)
                except:
                    continue
                # sample data: {"now" : 1742043492.321,"hex":"3c4594","type":"adsb_icao","flight":"BOX457  ","r":"D-AALT","t":"B77L",
                #               "desc":"BOEING 777-200LR","alt_baro":3700,"alt_geom":3825,"gs":176.3,"ias":186,"tas":194,"mach":0.300,
                #               "wd":78,"ws":18,"track":69.41,"roll":1.58,"mag_heading":65.21,"true_heading":68.67,"baro_rate":-1152,
                #               "geom_rate":-1152,"squawk":"1162","emergency":"none","category":"A5","nav_qnh":1014.4,"nav_altitude_mcp":4992,
                #               "nav_heading":66.09,"lat":49.982300,"lon":8.269290,"nic":8,"rc":186,"seen_pos":0.000,"r_dst":0.449,"r_dir":226.2,
                #               "version":2,"nic_baro":1,"nac_p":10,"nac_v":2,"sil":3,"sil_type":"perhour","gva":2,"sda":2,"alert":0,"spi":0,
                #               "mlat":[],"tisb":[],"messages":405,"seen":0.0,"rssi":-17.1}
                logging.debug(f'data read: {data}')


                adsb_id = data['hex']
                update_info(adsb_id, 'last_active', time.time())

                # extract the longitude, latitude, and altitude
                lon = data['lon']
                lat = data['lat']
                alt = data['alt_geom'] * 0.3048
                # convert to xyz coordinates
                xyz1 = xyz(lon, lat, alt)
                v1 = xyz1 - xyz0
                # calculate the distance between the two points
                dist = np.linalg.norm(v1)
                # calculate the distance in x-y plane
                dist_xy = np.linalg.norm(xyz(lon, lat, lon_lat_alt[2])-xyz0)
                # we look at a cylinder with given radius, height is ignored
                if dist_xy < 5000:
                    if 'last_coords' in info[adsb_id]:
                        v0 = info[adsb_id]['last_coords']
                        # TODO: scale d to seconds by dividing by time difference t1 - t0
                        d = v1 - v0
                        lambda_ = -np.dot(v0, d) / np.dot(d, d)
                        dist_0 = np.linalg.norm(v0 + lambda_ * d)
                        logging.info(f"{adsb_id}:    "
                                        f"dist={dist:6.1f}   "
                                        f"dist_0={dist_0:6.1f}   "
                                        f"dist_xy={dist_xy:6.1f}   "
                                        f"lambda={lambda_:6.1f}    FOD={info[adsb_id]['flyover_detected']}")
                        if -5 < lambda_ < 0 and dist_0 < 3000 and not info[adsb_id]['flyover_detected']:
                            update_info(adsb_id, 'flyover_detected', True)
                            fields = { k: v for k, v in data.items() if k in ['hex', 'flight', 'r', 't', 'desc', 'rssi'] }
                            fields['dist'] = dist_0
                            fields['text'] = f"{dist_0:.0f} m"
                            fields['title'] = f'<a href="https://globe.adsbexchange.com/?icao={adsb_id}" target="_blank">{fields["flight"]}</a>'
                            
                            json_body = [
                                {
                                    "measurement": args.measurement,
                                    "tags": {
                                        "type": "flyover"
                                    },
                                    "fields": fields
                                }
                            ]
                            client.write_points(json_body)
                            logging.info(f'event written: icao={adsb_id}, callsign={data['flight']}, dist={dist_0:.0f}m')
                        if lambda_ <= -20:
                            info[adsb_id]['flyover_detected'] = False
                update_info(adsb_id, 'last_coords', v1)

                # remove timed out entries
                timeout_keys = []
                now = time.time()
                for k, v in info.items():
                    if (now-v['last_active'])>ICAO_TIMEOUT:
                        timeout_keys.append(k)
                for adsb_id in timeout_keys:
                    del info[adsb_id]
    except:
        pass
    time.sleep(1.)
