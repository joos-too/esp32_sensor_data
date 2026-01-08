from machine import Pin, SoftI2C
import time, ujson, os, ssl, asyncio, ntptime, ubinascii, machine
from collections import deque
import dht, ssd1306
from resources import get_cpu_usage, get_full_memory_info
import boot_globals as bg
from detectors import create_detector
from mqtt_as import MQTTClient, config
from logger import log

# setup dht22 and oled Display
dht22 = dht.DHT22(Pin(25, Pin.IN))
i2c = SoftI2C(scl=Pin(32), sda=Pin(33))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# shutdown button
shutdown_btn = Pin(13, Pin.IN, Pin.PULL_UP)
shutdown_requested = False

# anomaly detection config
DETECTOR_KEYS = {
    "zscore": ("temp_zscore_anomaly", "hum_zscore_anomaly"),
    "ewma": ("temp_ewma_anomaly", "hum_ewma_anomaly"),
    "adaptive_threshold": ("temp_adaptive_threshold_anomaly", "hum_adaptive_threshold_anomaly"),
}

MEASUREMENTS_PER_WINDOW = 15
POST_ANOMALY_SEND_COUNT = 15

# MQTT Config
MQTT_BROKER = None
DEVICE_ID = None
BASE_TOPIC_ROOT = None
BASE_TOPIC = None
TOPIC_TELE = None
TOPIC_STATUS = None


def check_shutdown_button():
    """Check if shutdown button is pressed."""
    return shutdown_btn.value() == 0  # LOW when pressed


def safe_shutdown():
    """Flush, unmount SD, and optionally deep sleep."""
    global shutdown_requested
    shutdown_requested = True
    log("Shutdown button pressed, preparing safe power-down")

    try:
        os.umount("/sd")
        log("SD unmounted safely.", to_sd=False)
    except Exception as e:
        log("SD unmount error:", e, level="ERROR", to_sd=False)

    # "turn off" oled screen
    oled.fill(0)
    oled.show()

    log("Device can now be powered off safely.", to_sd=False)


# sd logger
def log_to_sd(ts, temp, hum, cpu, mem, anomalies):
    """
    Logs a single measurement to daily CSV file on SD card.
    Creates a new file each day with header row if it doesn't exist.
    """
    if "sd" not in os.listdir("/"):
        log("No SD filesystem mounted.", to_sd=False)
        return

    try:
        # current date for filename
        date_str = "{:04d}-{:02d}-{:02d}".format(*time.localtime())
        filename = f"telemetry_{date_str}.csv"
        filepath = "/sd/" + filename

        # header if file is new
        if filename not in os.listdir("/sd"):
            with open(filepath, "w") as f:
                f.write(
                    "ts,temp,hum,mp_cpu,cpu_total,cpu_core0,cpu_core1,mp_used_kb,mp_free_kb,mp_total_kb,idf_used_kb,idf_free_kb,idf_total_kb,temp_zscore_anomaly,temp_ewma_anomaly,temp_adaptive_threshold_anomaly,hum_zscore_anomaly,hum_ewma_anomaly,hum_adaptive_threshold_anomaly\n")

        # timestamp line
        idf_used_kb = mem["idf_total_kb"] - mem["idf_free_kb"]
        line = f"{ts},{temp:.1f},{hum:.1f},{cpu['mp_task']:.1f},{cpu['total']:.1f},{cpu['core0']:.1f},{cpu['core1']:.1f},{mem['mp_used_kb']},{mem['mp_free_kb']},{mem['mp_total_kb']},{idf_used_kb},{mem['idf_free_kb']},{mem['idf_total_kb']},{anomalies['temp_zscore_anomaly']},{anomalies['temp_ewma_anomaly']},{anomalies['temp_adaptive_threshold_anomaly']},{anomalies['hum_zscore_anomaly']},{anomalies['hum_ewma_anomaly']},{anomalies['hum_adaptive_threshold_anomaly']}"

        # append new row
        with open(filepath, "a") as f:
            f.write(line + "\n")

    except Exception as e:
        log("SD write error: {}".format(e), level="ERROR", to_sd=False)


def load_detector_settings():
    cfg = getattr(bg, "CONFIG", {}) or {}
    det_cfg = cfg.get("detector", {})

    # get detector type and params from config
    det_type = det_cfg.get("type", None)
    det_params = det_cfg.get(det_type, {}) or {}

    return det_type, det_params


def build_anomaly_dict(detector_type, temp_anomaly, hum_anomaly):
    anomalies = {
        "temp_zscore_anomaly": False,
        "temp_ewma_anomaly": False,
        "temp_adaptive_threshold_anomaly": False,
        "hum_zscore_anomaly": False,
        "hum_ewma_anomaly": False,
        "hum_adaptive_threshold_anomaly": False,
    }
    keys = DETECTOR_KEYS.get(detector_type)
    if keys:
        anomalies[keys[0]] = temp_anomaly
        anomalies[keys[1]] = hum_anomaly
    return anomalies


def build_measurement_entry(ts_tuple, temp, hum):
    entry = {
        "ts": ts_tuple,
        "temp_c": temp,
        "hum_pct": hum,
    }
    return entry


def build_measurement_payload(ts_tuple, temp, hum, anomalies):
    payload = {
        "device_id": DEVICE_ID,
        "ts": ts_tuple,
        "temp_c": temp,
        "hum_pct": hum,
    }
    payload.update(anomalies)
    return payload


def sync_time():
    log("Syncing time via NTP...")
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()  # sets RTC to UTC
        log("Time synced")
    except Exception as e:
        log("NTP sync failed:", e, level="ERROR")


async def on_mqtt_connect(client):
    sync_time()
    online = ujson.dumps({"device_id": DEVICE_ID, "status": "online"})
    await client.publish(TOPIC_STATUS, online, retain=True)
    if MQTT_BROKER:
        log("MQTT connected:", MQTT_BROKER)


def setup_mqtt_config():
    global MQTT_BROKER, DEVICE_ID, BASE_TOPIC_ROOT, BASE_TOPIC, TOPIC_TELE, TOPIC_STATUS
    cfg = getattr(bg, "CONFIG")
    wifi_cfg = cfg.get("wifi")
    mqtt_cfg = cfg.get("mqtt")

    DEVICE_ID = mqtt_cfg.get("device_id", "ESP32-Sensor")
    BASE_TOPIC_ROOT = mqtt_cfg.get("base_topic_root", "sensors/esp32")
    BASE_TOPIC = "{}/{}/".format(BASE_TOPIC_ROOT, DEVICE_ID)
    TOPIC_TELE = BASE_TOPIC + "telemetry"
    TOPIC_STATUS = BASE_TOPIC + "status"

    MQTT_BROKER = mqtt_cfg.get("broker")
    if not MQTT_BROKER:
        log("MQTT broker missing in config", level="ERROR")

    config["ssid"] = wifi_cfg.get("ssid")
    config["wifi_pw"] = wifi_cfg.get("password")
    config["server"] = MQTT_BROKER
    mqtt_ssl = mqtt_cfg.get("ssl", False)
    if isinstance(mqtt_ssl, str):
        mqtt_ssl = mqtt_ssl.strip().lower() in ("1", "true", "yes", "on")
    mqtt_ssl = bool(mqtt_ssl)
    config["ssl"] = mqtt_ssl
    if mqtt_ssl:
        config["ssl_params"] = {
            "server_hostname": MQTT_BROKER,
            "cert_reqs": ssl.CERT_NONE,
        }
    else:
        config["ssl_params"] = {}

    default_port = 8883 if mqtt_ssl else 1883
    mqtt_port = mqtt_cfg.get("port", default_port)
    try:
        config["port"] = int(mqtt_port)
    except (TypeError, ValueError):
        config["port"] = default_port
    config["user"] = mqtt_cfg.get("user", "esp32")
    config["password"] = mqtt_cfg.get("password") or ""

    client_id = b"esp32-" + ubinascii.hexlify(machine.unique_id())
    config["client_id"] = client_id

    mqtt_keepalive = mqtt_cfg.get("keepalive", 60)
    try:
        config["keepalive"] = int(mqtt_keepalive)
    except (TypeError, ValueError):
        config["keepalive"] = 60

    config["will"] = (
        TOPIC_STATUS,
        ujson.dumps({"device_id": DEVICE_ID, "status": "offline"}),
        True,
        0,
    )


async def connect_with_backoff(client):
    backoff_s = 2
    while True:
        try:
            await client.connect()
            return
        except OSError as e:
            log("MQTT connect failed:", e, level="ERROR")
            await asyncio.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, 30)


def init_detectors():
    detector_type, detector_params = load_detector_settings()
    try:
        temp_det = create_detector(detector_type, **detector_params)
        hum_det = create_detector(detector_type, **detector_params)
    except Exception as e:
        log("Detector init failed: {}".format(e), level="ERROR")
        temp_det = None
        hum_det = None
    log("Active detector:", detector_type)
    log("Detector params:", detector_params)
    return detector_type, temp_det, hum_det


async def sensor_loop(client, period_ms=2000):
    last_read = None
    measurement_history = deque((), MEASUREMENTS_PER_WINDOW)
    post_anomaly_remaining = 0

    detector_type, temp_det, hum_det = init_detectors()

    while True:
        if check_shutdown_button():
            safe_shutdown()
            try:
                await client.disconnect()
            except Exception as e:
                log("MQTT disconnect failed:", e, level="ERROR", to_sd=False)
            return

        try:
            # sensor measurements every ~2s
            now = time.ticks_ms()
            next_read = time.ticks_add(now, period_ms)
            try:
                dht22.measure()
                temp = round(dht22.temperature(), 1)
                hum = round(dht22.humidity(), 1)
                ts_tuple = time.localtime()
                ts = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*ts_tuple)
            except OSError as e:
                log("DHT read error: {}".format(e), level="ERROR")
                await asyncio.sleep(2)
                continue

            # get system stats
            cpu = get_cpu_usage()
            mem = get_full_memory_info()

            if temp_det is None or hum_det is None:
                temp_anomaly = False
                hum_anomaly = False
            else:
                # anomaly detection (temperature)
                temp_anomaly = temp_det.update(temp)
                hum_anomaly = hum_det.update(hum)
            anomalies = build_anomaly_dict(detector_type, temp_anomaly, hum_anomaly)

            if temp_anomaly:
                log("Anomaly detected: temp_{} temp={:.1f}C ts={}".format(detector_type, temp, ts))
            if hum_anomaly:
                log("Anomaly detected: hum_{} hum={:.1f}% ts={}".format(detector_type, hum, ts))

            # oled
            oled.fill(0)
            oled.text(f"Temp: {temp:.1f}C", 0, 0)
            oled.text(f"Hum:  {hum:.1f}%", 0, 10)
            oled.text("MP Resources", 0, 30)
            oled.text(f"CPU: {cpu['mp_task']:.1f}%", 0, 40)
            oled.text(f"RAM: {mem['mp_used_kb']}/{mem['mp_total_kb']}KB", 0, 50)
            oled.show()

            # MQTT publish only on anomaly windows, otherwise keep history.
            if temp_anomaly or hum_anomaly:
                log("Anomaly detected, publishing to MQTT...")
                payload = build_measurement_payload(ts_tuple, temp, hum, anomalies)
                payload["event"] = "anomaly"
                payload["window_before"] = list(measurement_history)
                measurement_history = deque((), MEASUREMENTS_PER_WINDOW)  # clear history
                await client.publish(TOPIC_TELE, ujson.dumps(payload))
                post_anomaly_remaining = POST_ANOMALY_SEND_COUNT
            elif post_anomaly_remaining > 0:
                payload = build_measurement_payload(ts_tuple, temp, hum, anomalies)
                payload["event"] = "anomaly_followup"
                await client.publish(TOPIC_TELE, ujson.dumps(payload))
                post_anomaly_remaining -= 1
            else:
                measurement_entry = build_measurement_entry(ts_tuple, temp, hum)
                measurement_history.append(measurement_entry)

            # sd card log
            log_to_sd(ts, temp, hum, cpu, mem, anomalies)

            # debugging logs
            log(
                f"{ts} Temp:{temp:.1f}C Hum:{hum:.1f}% MP_CPU:{cpu['mp_task']:.1f}% MP_RAM:{mem['mp_used_kb']}/{mem['mp_total_kb']}KB IDF_RAM:{mem['idf_total_kb'] - mem['idf_free_kb']}/{mem['idf_total_kb']}KB CPU_TOTAL:{cpu['total']:.1f}% CPU0:{cpu['core0']:.1f}% CPU1:{cpu['core1']:.1f}%",
                to_sd=False,
            )
            now = time.ticks_ms()
            remaining = time.ticks_diff(next_read, now)
            if remaining > 0:
                await asyncio.sleep_ms(remaining)
                next_read = time.ticks_add(next_read, period_ms)
            else:
                # Overran the period; reschedule from now to avoid drift.
                next_read = time.ticks_add(now, period_ms)
        except Exception as e:
            log("Unexpected error: {}".format(e), level="ERROR")
            await asyncio.sleep(2)


async def main():
    log("Starting main programm")
    setup_mqtt_config()
    try:
        client = MQTTClient(config)
    except Exception as e:
        log("MQTT client init failed:", e, level="ERROR")
        return
    await connect_with_backoff(client)
    await on_mqtt_connect(client)
    await sensor_loop(client)


asyncio.run(main())
