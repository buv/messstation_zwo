import json
import subprocess
import smbus3 as smbus
import serial.tools.list_ports
import os

def detect_hardware():
    env = { k+"_AVAILABLE": 0 for k in "DNMS_I2C BME280 ADSB DFLD_LEGACY DFLD_DNMS SSD1306".split() }
    #
    # I2C scan
    #
    devices = {
        0x55: "DNMS_I2C_AVAILABLE",
        0x76: "BME280_AVAILABLE",
        0x3c: "SSD1306_AVAILABLE",
    }

    # Initialize I2C bus and probe known devices
    bus = smbus.SMBus(1)
    for device, var in devices.items():
        try:
            bus.read_byte(device)
            env[var] = 1
        except:
            pass

    #
    # check for RTL-SDR on USB for ADS-B readout
    #
    supported_devices = [
        "0bda:2838", # nooElec
        "0bda:2832", # SDR ADS-B
    ]
    for s in supported_devices:
        rc = subprocess.call(f"lsusb | grep -q {s}", shell=True)
        if rc == 0:
            env["ADSB_AVAILABLE"] = 1
            break

    #
    # check tty devices if i2c DNMS sensor not yet found
    #
    if env["DNMS_I2C_AVAILABLE"] == 0:
        devices = set([str(comport.device).split('/')[-1] for comport in serial.tools.list_ports.comports(include_links=True)])
        if 'ttyDNMS' in devices:
            env['DFLD_DNMS_AVAILABLE'] = 1
            env['DEVICE_DNMS'] = '/dev/ttyDNMS'
        elif 'ttyUSB0' in devices:
            # Only treat ttyUSB0 as DFLD Legacy if no DNMS device is expected
            env['DFLD_LEGACY_AVAILABLE'] = 1
            env['DEVICE_DFLD'] = '/dev/ttyUSB0'
        # TODO: this is thin ice, needs protocol checking on the long run

        # Override with environment variables if set
        # check if set and length > 0
        if os.getenv('DNMS_DEVICE') and len(os.getenv('DNMS_DEVICE')) > 0:
            env['DFLD_DNMS_AVAILABLE'] = 1
            env['DFLD_LEGACY_AVAILABLE'] = 0
            env['DNMS_I2C_AVAILABLE'] = 0
            env['DEVICE_DNMS'] = os.getenv('DNMS_DEVICE')
        elif os.getenv('AK_MODUL_DEVICE') and len(os.getenv('AK_MODUL_DEVICE')) > 0:
            env['DFLD_LEGACY_AVAILABLE'] = 1
            env['DFLD_DNMS_AVAILABLE'] = 0
            env['DNMS_I2C_AVAILABLE'] = 0
            env['DEVICE_DFLD'] = os.getenv('AK_MODUL_DEVICE')
        
    return env


if __name__ == '__main__':
    import json
    print(json.dumps(detect_hardware(), indent=2))

