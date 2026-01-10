#!/usr/bin/env python3
import argparse
import json
import os
import signal
import sys
import time
import logging
import requests
from types import SimpleNamespace
from paho.mqtt import client as mqtt

# Derive module name for MQTT client ID base
MODULE_NAME = os.path.basename(__file__).replace('.py', '')

def main():
    # create config tuple from environment variables
    config = SimpleNamespace(**{
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "mqtt_server": os.getenv("MQTT_SERVER", "mqtt:1883"),
        "topic": os.getenv("MQTT_TOPIC", "dfld/sensors/#"),
        "qos": int(os.getenv("MQTT_QOS", 0)),
        "keepalive": int(os.getenv("MQTT_KEEPALIVE", 60)),
        "client_id": f"{MODULE_NAME}-{os.getpid()}",
        "influxdb_server": os.getenv("INFLUXDB_SERVER", "victoriametrics:8428"),
        "influxdb_database": os.getenv("INFLUXDB_DATABASE", "dfld"),
    })

    logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=config.log_level)
    logging.info(f"Configuration: {config}")
    
    # VictoriaMetrics HTTP endpoint (port 8428, not 8086)
    host = config.influxdb_server.split(':')[0]
    influx_url = f"http://{host}:8428/write?db={config.influxdb_database}"
    logging.info(f'Using VictoriaMetrics at {host}:8428, database: {config.influxdb_database}')
        
    # MQTT-Client für MQTT v3.1.1 über TCP
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
        client_id=f"mqtt2tsdb-{os.getpid()}",
        clean_session=True,
        protocol=mqtt.MQTTv311,
        transport="tcp",
    )
    client.user_data_set(config)

    # Automatisches Reconnect mit Backoff
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    # keys to transfer as tags
    tag_keys = ["station"]

    # Callback: Verbindung hergestellt → Topic abonnieren
    def on_connect(cli, userdata, flags, rc):
        # rc == 0 bedeutet Erfolg
        status = "ok" if rc == 0 else f"rc={rc}"
        logging.info(f"Connected: {status}, flags={flags}")
        if rc == 0:
            res = cli.subscribe(userdata.topic, qos=userdata.qos)
            logging.info(f"Subscribe {userdata.topic} QoS={userdata.qos}: {res}")

    # Callback: Nachricht empfangen
    def on_message(cli, userdata, msg):
        topic = msg.topic
        payload = msg.payload
        logging.debug(f"Message received on topic '{topic}': {payload}")

        # Versuche JSON-Payload zu parsen
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                ts = int(time.time() * 1e9)
                if "ts" in data:
                    ts = int(data["ts"])  
                    del data["ts"]  
                
                # Extract tags
                tags = {k: str(data[k]) for k in tag_keys if k in data}
                for k in tags.keys():
                    del data[k]
                
                # Build tag string
                measurement = topic.split('/')[-1]
                tag_str = ",".join([f"{k}={v}" for k, v in tags.items()])
                if tag_str:
                    tag_str = "," + tag_str
                
                # Write each field as separate line (one field per line)
                lines = []
                for field_name, field_value in data.items():
                    line = f"{measurement}{tag_str} {field_name}={field_value} {ts}"
                    lines.append(line)
                
                # Write all lines in one request
                payload = "\n".join(lines)
                response = requests.post(influx_url, data=payload, timeout=5)
                if response.status_code == 204:
                    logging.debug(f"Data written to VictoriaMetrics: {len(lines)} fields")
                else:
                    logging.warning(f"Write failed with status {response.status_code}: {response.text}")
            else:
                logging.warning(f"Received JSON is not a dict: {data}")
        except json.JSONDecodeError:
            logging.warning(f"Failed to decode JSON from payload: {payload}")
        except Exception as e:
            logging.error(f"Error processing message: {e}")

    # Callback: Verbindung verloren/geschlossen
    def on_disconnect(cli, userdata, rc):
        # rc != 0 → ungewollte Trennung; paho versucht Reconnect (connect_async + loop_start)
        if rc != 0:
            logging.info(f"Disconnected unexpectedly (rc={rc}), will reconnect…")
        else:
            logging.info("Disconnected.")

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    # Sauberes Beenden via Signal (Strg+C, SIGTERM)
    stop = {"flag": False}
    def handle_sig(*_):
        stop["flag"] = True
        logging.info("Stopping…")
        client.disconnect()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    # Nicht-blockierender Connect: erlaubt Auto-Reconnect
    try:
        host, port = config.mqtt_server.split(":")
        port = int(port)
        client.connect_async(host, port, keepalive=config.keepalive)
        client.loop_start()
        # Hauptschleife: Warten bis Stop
        while not stop["flag"]:
            time.sleep(0.2)
    finally:
        # Loop sauber stoppen
        client.loop_stop()
    return 0

if __name__ == "__main__":
    sys.exit(main())
