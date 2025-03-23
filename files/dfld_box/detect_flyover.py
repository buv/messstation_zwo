import os
import json
import math
import time
import socket
import logging
import argparse
import traceback
import numpy as np
from influxdb import InfluxDBClient

class TrajectoryPool:
    TIMEOUT_CACHE = 3600 # seconds of non reception before purge from cache
    TIMEOUT_TRAJ = 600 # seconds of non reception before trajectory is dumped and reset
    EARTH_RADIUS = 6371000 # earth radius in meters

    def __init__(self, home):
        self.home = home
        self.home_xyz = self.xyz(*home)
        self.pool = {}

    def __getitem__(self, key):
        return self.pool[key]

    def set_influx_client(self, client):
        self.influx_client = client

    def xyz(self, lon, lat, alt):
        lon = lon * 3.141592653589793 / 180.0
        lat = lat * 3.141592653589793 / 180.0
        x = (self.EARTH_RADIUS + alt) * math.cos(lat) * math.cos(lon)
        y = (self.EARTH_RADIUS + alt) * math.cos(lat) * math.sin(lon)
        z = (self.EARTH_RADIUS + alt) * math.sin(lat)
        return np.array([x, y, z])

    def update(self, data):
        adsb_id = data['hex']
        if adsb_id not in self.pool:
            self.pool[adsb_id] = Trajectory(self)
        self.pool[adsb_id].update(data)
        self.purge()

    def purge(self):
        # remove timed out entries
        timeout_keys = []
        now = time.time()
        for k in self.pool.keys():
            if (now-self.pool[k].last_active) > self.TIMEOUT_TRAJ:
                self.pool[k].dump_trajectory()
                self.pool[k].reset()
            
            if (now-self.pool[k].last_active) > self.TIMEOUT_CACHE:
                timeout_keys.append(k)
        for adsb_id in timeout_keys:
            del self.pool[adsb_id]



class Trajectory:
    ACTIVE_RANGE = 5000 # meters
    EVENT_RANGE = 3000 # meters
    
    def __init__(self, pool):
        self.pool = pool
        self.info = {}
        self.last_active = None
        self.reset()
        
    def reset(self):
        self.traj = []
        self.min_dist = 1e9
        self.lambda_ = None
        self.v0 = None
        self.t0 = None
        self.dist = None
        self.dist_0 = None
        self.dist_xy = None
        self.flyover_detected = False
        self.in_zone = False

    def update(self, data):
        # check for required fields
        missing = [ k for k in ['lon', 'lat', 'alt_baro', 'now', 'rssi'] if k not in data ]
        if len(missing)>0:
            logging.debug(f"data set rejected, missing key(s): {missing}")
            return

        self.last_active = data['now']
        coords = np.array([data['lon'], data['lat'], data['alt_baro'] * 0.3048, data['now'], data['rssi']])
        v1 = self.pool.xyz(*coords[:3]) - self.pool.home_xyz
        dist_xy = np.linalg.norm(self.pool.xyz(coords[0], coords[1], self.pool.home[2])-self.pool.home_xyz)

        logging.debug(f"distances: dist={np.linalg.norm(v1)}, dist_xy={dist_xy}")

        if dist_xy <= self.ACTIVE_RANGE:
            self.dist_xy = dist_xy
            self.in_zone = True
            self.dist = np.linalg.norm(v1)

            # calculate lambda and closest point on trajectory
            self.lambda_ = 1e9
            self.dist_0 = 1e9
            if len(self.traj) > 1:
                d = v1 - self.v0
                self.lambda_ = -np.dot(self.v0, d) / np.dot(d, d)
                self.dist_0 = np.linalg.norm(self.v0 + self.lambda_ * d)
            self.v0 = v1

            # update min distance
            if self.min_dist > self.dist:
                self.min_dist = self.dist
                self.t0 = data['now']

            self.traj.append(coords)

            # update info dict since we might have new data
            self.info = { k: v for k, v in data.items() if k in ['hex', 'flight', 'r', 't', 'desc'] }

            # check for flyover
            if self.dist > self.min_dist and self.lambda_ < 0 and not self.flyover_detected:
                self.flyover_detected = True
                if self.dist <= self.EVENT_RANGE:
                    self.write_influx()

            logging.debug(f"{self.info['hex']}:    "
                          f"dist={self.dist:6.1f}   "
                          f"dist_0={self.dist_0:6.1f}   "
                          f"dist_xy={self.dist_xy:6.1f}   "
                          f"lambda={self.lambda_:6.1f}    FOD={self.flyover_detected}")
        else:
            # when leaving zone, dump trajectory and reset
            if self.in_zone:
                self.in_zone = False
                self.dump_trajectory()
                self.reset()

    def write_influx(self):
        fields = { k: v for k, v in self.info.items() if k in ['hex', 'flight', 'r', 't', 'desc', 'rssi'] }
        anchor_text = self.info['flight'] if 'flight' in self.info else self.info['hex']
        fields['dist'] = self.dist
        fields['text'] = f"{self.dist:.0f} m"
        fields['title'] = f'<a href="https://globe.adsbexchange.com/?icao={self.info["hex"]}" target="_blank">{anchor_text}</a>'
        json_body = [
            {
                "measurement": args['INFLUXDB_MEASUREMENT'],
                "tags": {
                    "type": "flyover"
                },
                "fields": fields
            }
        ]
        self.pool.influx_client.write_points(json_body)
        logging.info(f'event written: icao={self.info["hex"]}, callsign={self.info["flight"]}, dist={self.dist:.0f}m')

    def dump_trajectory(self):
        if len(self.traj) > 0:
            # dump trajectory as geojson to file
            with open(f'/tmp/{self.info["hex"]}.geojson', 'w') as f:
                rssi = [float(t[4]) for t in self.traj]
                tr = [float(t[3]) for t in self.traj]

                f.write('{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [')
                for i, t in enumerate(self.traj):
                    f.write(f'[{t[0]:.6f}, {t[1]:.6f}, {t[2]:.2f}]')
                    if i < len(self.traj)-1:
                        f.write(',')
                f.write(']},')
                f.write('"properties": {')
                f.write(f'"icao": \"{self.info["hex"]}\",')
                f.write(f'"t0": {self.t0:.6f},')
                f.write(f'"min_dist": {self.min_dist:.3f},')
                f.write(f'"rssi": {rssi},')
                f.write(f'"t": {tr}')
                f.write('}')
                f.write(']}')
            
            logging.info(f'trajectory dumped: {self.info["hex"]}')



level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)
logging.info('starting...')
logging.info(f'LOG_LEVEL={level}')

required_env = "DUMP1090_SERVER INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE INFLUXDB_MEASUREMENT STATION_POSITION".split()
missing_env = []
for k in required_env:
    if k not in os.environ:
        missing_env.append(k)
if len(missing_env)>0:
    logging.error(f'following environment variables not set: {missing_env}')
    exit(1)

args = { k: os.environ[k] for k in required_env}
logging.info(f'all environment variables set: {args}')

lon_lat_alt = [float(x) for x in args['STATION_POSITION'].split(':')]
logging.info(f'Station Position lon, lat, alt = {lon_lat_alt}')

# active plane trajectories
traj_pool = TrajectoryPool(lon_lat_alt)

while True:
    try:
        # create connection to influxdb v1
        logging.info(f'connecting to influx database ({args["INFLUXDB_SERVER"]})...')
        influxdb_server = args['INFLUXDB_SERVER'].split(':')
        client = InfluxDBClient(host=influxdb_server[0], port=influxdb_server[1], username=args['INFLUXDB_USERNAME'], password=args['INFLUXDB_PASSWORD'])
        logging.debug(f'client={client}')
        client.switch_database(args["INFLUXDB_DATABASE"])
        traj_pool.set_influx_client(client)
        logging.info(f'switched to database "{args["INFLUXDB_DATABASE"]}"')

        # connect to dump1090 process and loop over lines
        logging.info(f'connecting to dump1090 server ({args["DUMP1090_SERVER"]})...')
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        dump1090_server = args['DUMP1090_SERVER'].split(':')
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

                # update trajectory pool
                traj_pool.update(data)

    except Exception as e:
        logging.error(f'exception: {e}')
        logging.error(f'traceback: {traceback.print_tb(e.__traceback__)}')
    time.sleep(1.)