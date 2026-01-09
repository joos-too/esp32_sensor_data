from machine import Pin, SoftI2C
import time, ujson, os, asyncio, ntptime, ubinascii, machine
from collections import deque
import dht, ssd1306
from resources import get_cpu_usage, get_full_memory_info
from src import boot_globals as bg
from detectors import create_detector
from mqtt_as import MQTTClient, config
from logger import log, log_data

# setup dht22 and oled Display
dht22 = dht.DHT22(Pin(25, Pin.IN))
i2c = SoftI2C(scl=Pin(32), sda=Pin(33))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# sizing and positioning for anomaly marks on oled
ANOMALY_MARK_SIZE = 6
ANOMALY_MARK_PAD = 2
ANOMALY_MARK_X = oled.width - ANOMALY_MARK_SIZE - ANOMALY_MARK_PAD

# shutdown button
shutdown_btn = Pin(13, Pin.IN, Pin.PULL_UP)

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


async def safe_shutdown(client):
    """Unmount SD card, turn off oled screen and disconnect MQTT client."""
    log("Shutdown button pressed, preparing safe power-down")

    try:
        os.umount("/sd")
        log("SD unmounted safely.", to_sd=False)
    except Exception as e:
        log("SD unmount error:", e, level="ERROR", to_sd=False)

    # "turn off" oled screen
    oled.fill(0)
    oled.show()

    try:
        await client.disconnect()
    except Exception as e:
        log("MQTT disconnect failed:", e, level="ERROR", to_sd=False)

    log("Device can now be powered off safely.", to_sd=False)


def load_detector_settings():
    cfg = getattr(bg, "CONFIG")
    det_cfg = cfg.get("detector")

    # get detector type and params from config
    det_type = det_cfg.get("type")
    det_params = det_cfg.get(det_type)

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
    config["ssl"] = False
    config["port"] = 1883
    config["user"] = mqtt_cfg.get("user", "esp32")
    config["password"] = mqtt_cfg.get("password") or ""
    config["client_id"] = b"esp32-" + ubinascii.hexlify(machine.unique_id())
    config["keepalive"] = 60
    config["will"] = (
        TOPIC_STATUS,
        ujson.dumps({"device_id": DEVICE_ID, "status": "offline"}),
        True,
        0,
    )
    config["connect_coro"] = on_mqtt_connect


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
    measurement_history = deque((), MEASUREMENTS_PER_WINDOW)
    post_anomaly_remaining = 0

    # sensor measurements every period_ms
    now = time.ticks_ms()
    next_read = time.ticks_add(now, period_ms)

    detector_type, temp_det, hum_det = init_detectors()

    while True:
        # shutdown button pressed
        if shutdown_btn.value() == 0:
            await safe_shutdown(client)
            return

        try:
            dht22.measure()
            temp = round(dht22.temperature(), 1)
            hum = round(dht22.humidity(), 1)
            ts_tuple = time.localtime()
            ts = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*ts_tuple)
        except OSError as e:
            log("DHT read error: {}".format(e), level="ERROR")

        # get system stats
        cpu = get_cpu_usage()
        mem = get_full_memory_info()

        if temp_det is None or hum_det is None:
            temp_anomaly = False
            hum_anomaly = False
        else:
            # anomaly detection
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
        if temp_anomaly:
            oled.fill_rect(ANOMALY_MARK_X, 0, ANOMALY_MARK_SIZE, ANOMALY_MARK_SIZE, 1)
        if hum_anomaly:
            oled.fill_rect(ANOMALY_MARK_X, 10, ANOMALY_MARK_SIZE, ANOMALY_MARK_SIZE, 1)
        oled.text("MP Resources", 0, 30)
        oled.text(f"CPU: {cpu['mp_task']:.1f}%", 0, 40)
        oled.text(f"RAM: {mem['mp_used_kb']}/{mem['mp_total_kb']}KB", 0, 50)
        oled.show()

        # MQTT publish only on anomaly windows, otherwise keep history.
        if temp_anomaly or hum_anomaly:
            log("Publishing to MQTT...")
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
            if post_anomaly_remaining == 0:
                log(f"Stop publishing to MQTT after {POST_ANOMALY_SEND_COUNT} followup messages.")
        else:
            measurement_entry = build_measurement_entry(ts_tuple, temp, hum)
            measurement_history.append(measurement_entry)

        # sd card data log
        log_data(ts, temp, hum, cpu, mem, anomalies)

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
            # Overran the period; reschedule from now.
            next_read = time.ticks_add(now, period_ms)


async def main():
    log("Starting main programm")
    setup_mqtt_config()
    try:
        client = MQTTClient(config)
    except Exception as e:
        log("MQTT client init failed:", e, level="ERROR")
        return
    await connect_with_backoff(client)
    sync_time()
    await sensor_loop(client)


asyncio.run(main())
