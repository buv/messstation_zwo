import os
import json
import math
import time
import socket
import logging
import datetime
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

    def set_influx_client(self, client, database):
        self.influx_client = client
        self.influx_database = database

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
            if self.pool[k].last_active:
                if (now-self.pool[k].last_active) > self.TIMEOUT_TRAJ:
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
        # Snapshot der Geometrie am closest-point-Sample. Frueher nur
        # min_dist als Skalar — jetzt halten wir alle Komponenten fest,
        # damit das InfluxDB-Event dist_xy / alt / dist_z separat zeigen
        # kann (statt nur einer 3D-Summe).
        self.min_dist_xy = None     # horizontaler Abstand am Min
        self.min_alt_baro = None    # baro-Hoehe in m am Min
        self.min_alt_geom = None    # geom-Hoehe in m am Min (optional)
        self.min_rssi = None        # RSSI am Min
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
        coords = [data['lon'], data['lat'], data['alt_baro'] * 0.3048, data['now'], data['rssi'],
                           data['alt_geom'] * 0.3048 if 'alt_geom' in data else None]
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

            # update min distance — am neuen Minimum auch die geometrischen
            # Komponenten snapshoten, damit write_event spaeter dist_xy /
            # alt_baro / dist_z separat ausgeben kann.
            if self.min_dist > self.dist:
                self.min_dist = self.dist
                self.min_dist_xy = self.dist_xy
                self.min_alt_baro = data['alt_baro'] * 0.3048
                self.min_alt_geom = (data['alt_geom'] * 0.3048
                                     if 'alt_geom' in data else None)
                self.min_rssi = float(data['rssi'])
                self.t0 = data['now']

            self.traj.append(coords)

            # update info dict since we might have new data
            self.info = { k: v for k, v in data.items() if k in ['hex', 'flight', 'r', 't', 'desc'] }
            self.info['rssi'] = float(data['rssi'])

            # check for flyover
            if self.dist > self.min_dist and self.lambda_ < 0 and not self.flyover_detected:
                self.flyover_detected = True
                if self.dist <= self.EVENT_RANGE:
                    self.write_event()

            logging.debug(f"{self.info['hex']}:    "
                          f"dist={self.dist:6.1f}   "
                          f"dist_0={self.dist_0:6.1f}   "
                          f"dist_xy={self.dist_xy:6.1f}   "
                          f"lambda={self.lambda_:6.1f}    FOD={self.flyover_detected}")
        else:
            # when leaving zone, reset
            if self.in_zone and len(self.traj)>1:
                self.in_zone = False
                self.reset()

    def write_event(self):
        tags = {}
        for k in ['hex', 'flight', 'r', 't']:
            if k in self.info:
                tags[k] = str(self.info[k]).strip()

        # Pre-baked Strings als FIELDS (nicht tags) — sonst entstehen
        # neue Series bei Variationen (jeder label-Wert = neue Series).
        # title:  HTML-Link für Grafana-Annotation Title (rendert klickbar)
        # text:   "r: NNN m\nh: NNN m" für Annotation-Body
        # Fallback callsign→hex weil InfluxQL kein COALESCE hat.
        callsign = tags.get('flight', '').strip()
        icao = tags.get('hex', '').strip()
        label = callsign if callsign else icao

        # Wir schreiben die Komponenten dist_xy (horizontal) + dist_z
        # (vertikal) statt einer 3D-Summe — die 3D-Distanz laesst sich
        # ueber sqrt(dist_xy^2 + dist_z^2) rekonstruieren, falls noetig.
        dxy = int(round(self.min_dist_xy)) if self.min_dist_xy is not None else 0
        dz = int(round(self.min_alt_baro - self.pool.home[2])) if self.min_alt_baro is not None else 0
        fields = {
            'rssi': float(self.min_rssi),
            # Pre-baked HTML-Link für Grafana-Annotation-Title.
            'title': (f'<a href="https://globe.adsbexchange.com/?icao={icao}" '
                      f'target="_blank">{label}</a>'),
            # Pre-baked Distance-Summary für Annotation-Body. <br> statt
            # \n, weil Grafana das annotation-text-Feld als HTML rendert
            # (wie auch den title-Link); \n wird sonst kollabiert.
            'text': f'r: {dxy} m<br>h: {dz} m',
        }
        if self.min_dist_xy is not None:
            fields['dist_xy'] = float(self.min_dist_xy)
        if self.min_alt_baro is not None:
            fields['alt_baro'] = float(self.min_alt_baro)
            # dist_z: vertikaler Abstand der Station-Hoehe zur Aircraft-Hoehe
            # am closest-Point. positive = Aircraft drueber, negative = drunter.
            fields['dist_z'] = float(self.min_alt_baro - self.pool.home[2])
        if self.min_alt_geom is not None:
            fields['alt_geom'] = float(self.min_alt_geom)
        if 'desc' in self.info:
            fields['descr'] = str(self.info['desc'])

        ts = datetime.datetime.utcfromtimestamp(time.time()).strftime('%Y-%m-%dT%H:%M:%SZ')

        json_body = [{
            "measurement": "event_raw",
            "tags": tags,
            "fields": fields,
            "time": ts
        }]
        self.pool.influx_client.write_points(json_body, database=self.pool.influx_database)
        logging.info(f'influxdb event written: tags={tags}, fields={fields}')


# main loop
level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)
logging.info('starting...')
logging.info(f'LOG_LEVEL={level}')

required_env = "DUMP1090_SERVER INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE STATION_POSITION".split()
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
        # create connection to influxdb
        influx_host, influx_port = args['INFLUXDB_SERVER'].split(':')
        logging.info(f'connecting to influxdb ({args["INFLUXDB_SERVER"]})...')
        influx_client = InfluxDBClient(host=influx_host, port=int(influx_port),
                                       username=args['INFLUXDB_USERNAME'], password=args['INFLUXDB_PASSWORD'])
        influx_client.switch_database(args['INFLUXDB_DATABASE'])
        traj_pool.set_influx_client(influx_client, args['INFLUXDB_DATABASE'])
        logging.info(f'connected to influxdb database "{args["INFLUXDB_DATABASE"]}"')

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
