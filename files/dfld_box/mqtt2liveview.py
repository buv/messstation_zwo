#!/usr/bin/env python3
import argparse
import json
import os
import signal
import sys
import time
import logging
from types import SimpleNamespace
from paho.mqtt import client as mqtt
from dfld import LiveView

def main():
    # create config tuple from environment variables
    config = SimpleNamespace(**{
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "mqtt_server": os.getenv("MQTT_SERVER", "mqtt:1883"),
        "topic": os.getenv("MQTT_TOPIC", "dfld/sensors/noise/#"),
        "qos": int(os.getenv("MQTT_QOS", 0)),
        "keepalive": int(os.getenv("MQTT_KEEPALIVE", 60)),
        "client_id": os.getenv("MQTT_CLIENT_ID", f"mqtt2liveview-{os.getpid()}"),
    })

    logging.basicConfig(format='%(asctime)s - %(levelname)s:%(message)s', level=config.log_level)
    logging.info(f"Configuration: {config}")
    
    lv = LiveView()

    # MQTT-Client für MQTT v3.1.1 über TCP
    client = mqtt.Client(
        client_id=f"mqtt2liveview-{os.getpid()}",
        clean_session=True,
        protocol=mqtt.MQTTv311,
        transport="tcp",  # explizit TCP (kein WebSocket)
    )
    client.user_data_set(config)

    # Automatisches Reconnect mit Backoff
    client.reconnect_delay_set(min_delay=1, max_delay=30)

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
        logging.debug(f"LiveView active: {lv.active}")

        if not payload or not lv.active:
            return
        
        # Versuche JSON-Payload zu parsen
        try:
            data = json.loads(payload)
            if isinstance(data, dict):
                if "dB_A_avg" in data:
                    value = float(data["dB_A_avg"])
                    lv.send(value)
                    logging.debug(f"Data written to LiveView: {value} dBA")
                else:
                    logging.debug(f"JSON does not contain 'dB_A_avg'")
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
