import sys
import types
import datetime
import logging

# stub modules so tsdb2ftp can be imported without influxdb
for mod in ['influxdb', 'dfld', 'dfld.util']:
    sys.modules[mod] = types.ModuleType(mod)
sys.modules['influxdb'].InfluxDBClient = None
sys.modules['dfld.util'].calc_crc = None
sys.modules['dfld.util'].deobfuscate_string = None

# provide required env vars
import os
for k in "INFLUXDB_SERVER INFLUXDB_USERNAME INFLUXDB_PASSWORD INFLUXDB_DATABASE INFLUXDB_MEASUREMENT DFLD_STATION DFLD_REGION DFLD_LEGACY DFLD_CKSUM TZ".split():
    os.environ.setdefault(k, 'test')

import pytz
from tsdb2ftp import find_dst_transition, adjust_dst


def test_find_dst_spring_forward_berlin():
    """March 29, 2026: CET -> CEST at 02:00 local (23h day)"""
    tz = pytz.timezone('Europe/Berlin')
    day_start = tz.localize(datetime.datetime(2026, 3, 29)).astimezone(pytz.utc)
    hour, delta = find_dst_transition(day_start, tz)
    assert hour == 2, f"expected transition at hour 2, got {hour}"
    assert delta == 3600, f"expected +3600s, got {delta}"


def test_find_dst_fall_back_berlin():
    """October 25, 2026: CEST -> CET at 03:00 local (25h day)"""
    tz = pytz.timezone('Europe/Berlin')
    day_start = tz.localize(datetime.datetime(2026, 10, 25)).astimezone(pytz.utc)
    hour, delta = find_dst_transition(day_start, tz)
    assert hour == 2, f"expected transition at hour 2, got {hour}"
    assert delta == -3600, f"expected -3600s, got {delta}"


def test_find_dst_normal_day():
    """April 4, 2026: no DST transition"""
    tz = pytz.timezone('Europe/Berlin')
    day_start = tz.localize(datetime.datetime(2026, 4, 4)).astimezone(pytz.utc)
    hour, delta = find_dst_transition(day_start, tz)
    assert hour is None
    assert delta is None


def test_find_dst_us_eastern():
    """March 8, 2026: EST -> EDT at 02:00 local"""
    tz = pytz.timezone('US/Eastern')
    day_start = tz.localize(datetime.datetime(2026, 3, 8)).astimezone(pytz.utc)
    hour, delta = find_dst_transition(day_start, tz)
    assert hour == 2, f"expected transition at hour 2, got {hour}"
    assert delta == 3600, f"expected +3600s, got {delta}"


def test_find_dst_lord_howe():
    """Lord Howe Island: DST offset is only 30 minutes"""
    tz = pytz.timezone('Australia/Lord_Howe')
    day_start = tz.localize(datetime.datetime(2026, 4, 5)).astimezone(pytz.utc)
    hour, delta = find_dst_transition(day_start, tz)
    if hour is not None:
        assert delta == -1800, f"expected -1800s, got {delta}"


def test_adjust_dst_spring_forward():
    """23h day: hour at transition_hour should be duplicated"""
    transition_hour = 2
    # 82800 entries: hours 0,1,3,4,...23 in local time (hour 2 missing)
    data = []
    for h in range(23):
        local_h = h if h < 2 else h + 1
        data.extend([local_h] * 3600)

    result = adjust_dst(data, 82800, transition_hour)
    assert len(result) == 86400, f"expected 86400, got {len(result)}"
    assert result[0] == 0           # hour 0
    assert result[3600] == 1        # hour 1
    assert result[7200] == 3        # hour 2: copy of data at transition point
    assert result[10800] == 3       # hour 3: same (duplicated)
    assert result[14400] == 4       # hour 4


def test_adjust_dst_fall_back():
    """25h day: first occurrence of double hour should be overwritten"""
    transition_hour = 2
    # 90000 entries: hours 0,1, 2(CEST), 2(CET), 3,...23
    data = []
    for h in range(25):
        if h < 2:
            data.extend([h] * 3600)
        elif h == 2:
            data.extend([200] * 3600)   # first hour 2 (CEST) - dropped
        elif h == 3:
            data.extend([201] * 3600)   # second hour 2 (CET) - survives
        else:
            data.extend([h - 1] * 3600)

    result = adjust_dst(data, 90000, transition_hour)
    assert len(result) == 86400, f"expected 86400, got {len(result)}"
    assert result[0] == 0
    assert result[3600] == 1
    assert result[7200] == 201, f"expected 201 at hour 2, got {result[7200]}"
    assert result[10800] == 3


if __name__ == '__main__':
    tests = [name for name in sorted(dir()) if name.startswith('test_')]
    passed = failed = 0
    for test in tests:
        try:
            globals()[test]()
            print(f"  PASS  {test}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL  {test}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
