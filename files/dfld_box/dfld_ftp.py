# read one day of data from influxdb v1


import os
import sys
import json
import time
import pytz
import datetime
import psycopg2
from influxdb import InfluxDBClient

def delta_t(t1_str, t2_datetime):
    """
    calculate the time difference between t1_str and t2_datetime in seconds
    :param t1_str: time string in format YYYY-MM-DD HH:MM:SS
    :param t2_datetime: datetime object
    :return: time difference in seconds
    """
    t1 = pytz.utc.localize(datetime.datetime.strptime(t1_str, '%Y-%m-%dT%H:%M:%S.%fZ'))
    print(f't1: {t1}')
    print(f't2: {t2_datetime}')
    delta = t1- t2_datetime
    return delta.total_seconds()

client = InfluxDBClient(
    host='10.2.1.42',
    port=8086,
    username='dfld',
    password='dfld'
)
client.switch_database('dfld')


today_str = datetime.datetime.now().strftime('%Y-%m-%d')
# today_epoch = int(time.mktime(time.strptime(today_str, '%Y-%m-%d')))

# set time zone to Europe/Berlin
local_tz = pytz.timezone('Europe/Berlin')
# convert today_str in local time zone to UTC
yesterday = datetime.datetime.strptime(today_str, '%Y-%m-%d') - datetime.timedelta(days=1)
yesterday_start = local_tz.localize(yesterday).astimezone(pytz.utc)


# read one day of data from influxdb v1
query = (f"SELECT dB_A_avg FROM dnms_serial WHERE "
         f"time >= ('{today_str}' - 86401s) AND "
         f"time < '{today_str}' "
         f"tz('Europe/Berlin')")
print(f'query: {query}')
result = client.query(query)
print(f'number of points: {len(result.raw["series"][0]["values"])}')
# print(json.dumps(result.raw, indent=4))


print()
print(yesterday_start)
print(delta_t(result.raw['series'][0]['values'][0][0], yesterday_start))
print(delta_t(result.raw['series'][0]['values'][-1][0], yesterday_start))

# create connection to postgresql
conn = psycopg2.connect( 
    host='10.2.1.42', 
    port=5432, 
    dbname='dfld',
    user='dfld',
    password='dfld'
)
cur = conn.cursor()
# read timestamps from flyover table
sql = (f"SELECT eventtime, dist FROM event_raw WHERE "
       f"eventtime >= '2025-04-12' "
       f"ORDER BY eventtime")
print(f'sql: {sql}')
cur.execute(sql)
rows = cur.fetchall()
print(f'number of rows: {len(rows)}')
for row in rows:
    print(row)
