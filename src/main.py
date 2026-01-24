from machine import Pin, SoftI2C
import time, ujson, os, asyncio, ntptime, ubinascii, machine
import dht, ssd1306
from resources import get_cpu_usage, get_full_memory_info
import boot_globals as bg
from mqtt_as import MQTTClient, config
from logger import log, log_data

# setup dht22 and oled Display
dht22 = dht.DHT22(Pin(25, Pin.IN))
i2c = SoftI2C(scl=Pin(32), sda=Pin(33))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# sizing and positioning for text and anomaly marks on oled
OLED_LINE_HEIGHT = 10
OLED_CHARS_PER_LINE = oled.width // 8

ANOMALY_MARK_SIZE = 6
ANOMALY_MARK_PAD = 2
ANOMALY_MARK_X = oled.width - ANOMALY_MARK_SIZE - ANOMALY_MARK_PAD

# shutdown button
shutdown_btn = Pin(13, Pin.IN, Pin.PULL_UP)

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


def _wrap_text(text, width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = "{} {}".format(current, word)
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def show_startup_screen(current_index):
    steps = ["Connect MQTT", "Sync Time", "Start Sensor"]

    oled.fill(0)
    oled.text("Startup:", 0, 0)
    max_lines = max((oled.height // OLED_LINE_HEIGHT) - 1, 0)
    y = OLED_LINE_HEIGHT
    for i, step in enumerate(steps):
        if y >= oled.height:
            break
        if i == current_index:
            prefix = ">"
        else:
            prefix = " "
        step_lines = _wrap_text(step, max(OLED_CHARS_PER_LINE - 2, 1))
        for line_index, line in enumerate(step_lines):
            if y >= oled.height or (y // OLED_LINE_HEIGHT) > max_lines:
                break
            if line_index == 0:
                text = "{} {}".format(prefix, line)
            else:
                text = "  {}".format(line)
            oled.text(text, 0, y)
            y += OLED_LINE_HEIGHT
    oled.show()


def _split_detector_params(det_params):
    if not isinstance(det_params, dict):
        return {}, {}
    temp_params = det_params.get("temp")
    hum_params = det_params.get("hum")
    if temp_params is None and hum_params is None:
        return det_params, det_params
    return temp_params or {}, hum_params or {}


def load_detector_settings():
    cfg = getattr(bg, "CONFIG", {}) or {}
    det_cfg = cfg.get("detector") or {}

    # get detector type and params from config
    det_type = det_cfg.get("type")
    det_params = det_cfg.get(det_type) or {}
    temp_params, hum_params = _split_detector_params(det_params)

    return det_type, temp_params, hum_params


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


async def sensor_loop(client, period_ms=2000):

    # sensor measurements every period_ms
    now = time.ticks_ms()
    next_read = time.ticks_add(now, period_ms)

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

        # oled
        oled.fill(0)
        oled.text(f"Temp: {temp:.1f}C", 0, 0)
        oled.text(f"Hum:  {hum:.1f}%", 0, 10)
        oled.text("MP Resources", 0, 30)
        oled.text(f"CPU: {cpu['mp_task']:.1f}%", 0, 40)
        oled.text(f"RAM: {mem['mp_used_kb']}/{mem['mp_total_kb']}KB", 0, 50)
        oled.show()

        # MQTT publish
        anomalies = {
            "temp_zscore_anomaly": False,
            "temp_ewma_anomaly": False,
            "temp_rulebased_anomaly": False,
            "hum_zscore_anomaly": False,
            "hum_ewma_anomaly": False,
            "hum_rulebased_anomaly": False,
        }
        payload = build_measurement_payload(ts_tuple, temp, hum, anomalies)
        payload["event"] = "anomaly_followup"
        await client.publish(TOPIC_TELE, ujson.dumps(payload))

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
    show_startup_screen(0)
    await connect_with_backoff(client)
    show_startup_screen(1)
    sync_time()
    show_startup_screen(2)
    await sensor_loop(client)


asyncio.run(main())
