import io
import os
import re
import sys
import time
import ftplib
import logging
import pathlib
import datetime

import pytz

from influxdb import InfluxDBClient
from dfld.util import calc_crc, deobfuscate_string

level = os.environ['LOG_LEVEL'].upper() if 'LOG_LEVEL' in os.environ else logging.INFO 
logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=level)

missing_env = []
for k in "INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE INFLUXDB_MEASUREMENT DFLD_STATION DFLD_REGION DFLD_LEGACY DFLD_CKSUM TZ".split():
    if k not in os.environ:
        missing_env.append(k)
if len(missing_env)>0:
    logging.error('following environment variables not set: %s', missing_env)
    sys.exit(1)


def delta_t(t1_str, t2_datetime):
    """
    calculate the time difference between t1_str and t2_datetime in seconds
    :param t1_str: time string in format YYYY-MM-DD HH:MM:SS
    :param t2_datetime: datetime object
    :return: time difference in seconds
    """
    t1 = pytz.utc.localize(datetime.datetime.strptime(t1_str, '%Y-%m-%dT%H:%M:%S.%fZ'))
    delta = t1- t2_datetime
    return delta.total_seconds()


def find_dst_transition(day_start, local_tz):
    """
    find the DST transition hour within a day, if any.
    :param day_start: start of day in UTC (aware datetime)
    :param local_tz: local timezone (pytz timezone)
    :return: (transition_hour, offset_delta) or (None, None)
             transition_hour: local hour before the transition
             offset_delta: positive = spring forward, negative = fall back
    """
    offset = day_start.astimezone(local_tz).utcoffset()
    for h in range(1, 25):
        t = day_start + datetime.timedelta(hours=h)
        new_offset = t.astimezone(local_tz).utcoffset()
        if new_offset != offset:
            offset_delta = (new_offset - offset).total_seconds()
            before = (t - datetime.timedelta(hours=1)).astimezone(local_tz)
            if offset_delta > 0:
                # spring forward: the hour after 'before' is skipped
                transition_hour = before.hour + 1
            else:
                # fall back: the hour of 'before' occurs twice
                transition_hour = before.hour
            return transition_hour, int(offset_delta)
    return None, None


def adjust_dst(data, day_seconds, transition_hour):
    """
    adjust a data buffer for DST transitions so the result is always 86400 entries.
    - 23h day (spring forward): repeat the transition hour to fill the missing hour
    - 25h day (fall back): overwrite the first occurrence with the second
    :param data: source data array (day_seconds entries)
    :param day_seconds: actual number of seconds in the local day
    :param transition_hour: local hour where DST transition occurs
    :return: data array with exactly 86400 entries
    """
    t = transition_hour * 3600

    if day_seconds == 82800:
        # spring forward: insert a copy of the transition hour
        logging.info('DST spring forward: repeating hour %d to fill 23h day', transition_hour)
        result = data[:t] + data[t:t+3600] + data[t:]
    elif day_seconds == 90000:
        # fall back: drop the first occurrence of the double hour
        logging.info('DST fall back: overwriting hour %d with following hour', transition_hour)
        result = data[:t] + data[t+3600:]
    else:
        logging.warning('unexpected day length: %d seconds', day_seconds)
        result = data[:86400]

    return result[:86400]


def map_one_day(start_date, res, full_transfer, day_seconds=86400, transition_hour=None):
    """
    map one day of data from influxdb to a 1Hz data array
    :param start_date: start date of the day
    :param res: result from influxdb query
    :param full_transfer: if True, transfer all data from yesterday
    :param day_seconds: actual number of seconds in the local day
    :param transition_hour: local hour of DST transition, or None
    :return: data array with 86400 entries (1Hz)
    """

    # check if result is empty
    if len(res.raw['series']) == 0:
        logging.warning('no data found for date %s', start_date)
        return None

    # fill data array for one day
    src = res.raw['series'][0]['values']
    t0 = start_date

    if full_transfer:
        n_day = day_seconds
    else:
        # calculate the number of seconds from start date to last measurement
        last_time = datetime.datetime.fromisoformat(src[-1][0])
        n_day = int((last_time - t0).total_seconds()) + 1

    n_src = len(src)
    dst_idx = 0
    src_idx = 0
    times = [0] * n_src
    values = [0] * n_src
    data = [0] * day_seconds
    for idx, v in enumerate(src):
        times[idx] = datetime.datetime.fromisoformat(v[0])
        val = round(v[1])
        val = min(val, 255)
        val = max(val, 0)
        values[idx] = int(val)

    while dst_idx < n_day:
        t_idx = t0 + datetime.timedelta(seconds=dst_idx)
        # find closest measurment to bin time t_idx,
        # skip forward while second (later) value is closer
        while src_idx+1 < n_src and abs(times[src_idx]-t_idx) > abs(times[src_idx+1]-t_idx):
            src_idx += 1
        data[dst_idx] = values[src_idx]
        dst_idx += 1

    # adjust for DST transition to produce exactly 86400 entries
    if transition_hour is not None and day_seconds != 86400:
        data = adjust_dst(data, day_seconds, transition_hour)

    return data


def get_data(now_dt, full_transfer):
    """
    get data from influxdb v1
    :param now_dt: datetime object
    :param full_transfer: if True, transfer all data from yesterday
    :return: bytearray with data
    """
    
    # if full_transfer is True, set date_str to yesterday
    if full_transfer:
        yesterday = now_dt - datetime.timedelta(days=1)
        date_str = yesterday.strftime('%Y-%m-%d')
    else:
        date_str = now_dt.strftime('%Y-%m-%d')

    try:
        # create connection to influxdb v1
        logging.info('connecting to influx database (%s)...', os.environ["INFLUXDB_SERVER"])
        influxdb_server = os.environ["INFLUXDB_SERVER"].split(':')
        client = InfluxDBClient(
            host=influxdb_server[0],
            port=influxdb_server[1],
            username=os.environ["INFLUXDB_USERNAME"],
            password=os.environ["INFLUXDB_PASSWORD"]
        )
        logging.debug('client=%s', client)
        client.switch_database(os.environ["INFLUXDB_DATABASE"])
        logging.debug('switched to database "%s"', os.environ["INFLUXDB_DATABASE"])
    except Exception as e:
        logging.error('failed to connect to influxdb: %s', e)
        return None
    
    measurement = os.environ["INFLUXDB_MEASUREMENT"]

    # calculate local day boundaries (handles DST transitions correctly)
    tz = os.environ['TZ']
    local_tz = pytz.timezone(tz)
    day = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    day_start = local_tz.localize(day).astimezone(pytz.utc)
    next_day = day + datetime.timedelta(days=1)
    next_day_start = local_tz.localize(next_day).astimezone(pytz.utc)
    day_seconds = int((next_day_start - day_start).total_seconds())

    # detect DST transition
    transition_hour, offset_delta = find_dst_transition(day_start, local_tz)
    if transition_hour is not None:
        logging.info('DST transition at local hour %d (offset change: %+ds)', transition_hour, offset_delta)

    # read one day of data from influxdb v1
    # use next day's midnight as boundary instead of +86400s
    # so DST transition days (23h or 25h) are queried correctly
    next_date_str = next_day.strftime('%Y-%m-%d')
    query = (f"SELECT dB_A_avg FROM {measurement} WHERE "
             f"time >= ('{date_str}' -     1s) AND "
             f"time <  '{next_date_str}' "
             f"tz('{tz}')")
    logging.debug('SQL query: %s', query)
    result = client.query(query)
    if len(result.raw["series"]) == 0:
        logging.warning('no data found for date %s', date_str)
        return None
    logging.debug('number of points in result: %s', len(result.raw["series"][0]["values"]))

    data = map_one_day(day_start, result, full_transfer, day_seconds, transition_hour)
    bb = None
    if data:
        bb = bytearray(data)
        bb.extend([calc_crc(data) & 0xff, 0x00])
        logging.debug('length of bytebuffer: %s', len(bb))
    return bb
 

def check_for_transfer():
    last_transfer_filename = "last_transfer.txt"

    # set date of transfer day
    now_dt = datetime.datetime.now()
    yesterday_str = (now_dt - datetime.timedelta(days=1)).strftime('%Y%m%d')

    # transfer is due, if last transfer date is before yesterday
    full_transfer_due = True
    if pathlib.Path(last_transfer_filename).is_file():
        with open(last_transfer_filename, mode="r", encoding="utf-8") as f:
            last_transfer_date_str = f.readline().rstrip()
            if re.search(r'^\d{8}$', last_transfer_date_str) and last_transfer_date_str >= yesterday_str:
                full_transfer_due = False
    else:
        # do not transfer if last transfer date is not set,
        # but initialize instead
        with open(last_transfer_filename, mode="w", encoding="utf-8") as f:
            print(yesterday_str, file=f)
        logging.info('last transfer date set to %s', yesterday_str)
        full_transfer_due = False

    day_str = yesterday_str if full_transfer_due else now_dt.strftime('%Y%m%d')
    logging.info('processing %s fullday=%s ...', day_str, full_transfer_due)
    ftp_filename = f"{int(os.environ['DFLD_CKSUM']):04x}-{int(os.environ['DFLD_REGION']):03d}-{day_str}-{int(os.environ['DFLD_STATION']):03d}.wwx"

    buf = get_data(now_dt, full_transfer_due)
    if buf and len(buf) > 0:
        try:
            ftp_dst = deobfuscate_string(os.environ['DFLD_LEGACY']).split(':')
            ftp = ftplib.FTP()
            ftp.connect(ftp_dst[0], int(ftp_dst[1]))
            ftp.login(ftp_dst[2], ftp_dst[3])
            logging.info('transfering %s bytes of data to file %s via ftp...', len(buf), ftp_filename)

            with io.BytesIO(buf) as f:
                ftp.storbinary(f'STOR {ftp_filename}', f)
        
            ftp.quit()
            if full_transfer_due:
                with open(last_transfer_filename, mode="w", encoding="utf-8") as f:
                    print(day_str, file=f)
        except ftplib.all_errors as e:
            logging.error('ftp error: %s', e)
            logging.error('transfer failed, retry in 1 hour')

if __name__ == '__main__':
    # inital delay to wait for system startup
    logging.info('waiting 60 seconds for system startup...')
    time.sleep(60)
    while True:
        check_for_transfer()
        time.sleep(3600)
