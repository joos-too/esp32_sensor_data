from machine import Pin, SoftI2C
import time, ujson, esp32, os
from collections import deque
import dht, ssd1306
from resources import get_cpu_usage, get_full_memory_info
import boot_globals as bg
import uerrno as errno
from detectors import create_detector
from logger import log

# setup dht22 and oled Display
dht22 = dht.DHT22(Pin(25, Pin.IN))
i2c = SoftI2C(scl=Pin(32), sda=Pin(33))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# shutdown button
shutdown_btn = Pin(13, Pin.IN, Pin.PULL_UP)
shutdown_requested = False

DETECTOR_KEYS = {
    "zscore": ("temp_zscore_anomaly", "hum_zscore_anomaly"),
    "ewma": ("temp_ewma_anomaly", "hum_ewma_anomaly"),
    "adaptive_threshold": ("temp_adaptive_threshold_anomaly", "hum_adaptive_threshold_anomaly"),
}

MEASUREMENTS_PER_WINDOW = 15
PING_EVERY_N = 15
POST_ANOMALY_SEND_COUNT = 15

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
                f.write("ts,temp,hum,mp_cpu,cpu_total,cpu_core0,cpu_core1,mp_used_kb,mp_free_kb,mp_total_kb,idf_used_kb,idf_free_kb,idf_total_kb,temp_zscore_anomaly,temp_ewma_anomaly,temp_adaptive_threshold_anomaly,hum_zscore_anomaly,hum_ewma_anomaly,hum_adaptive_threshold_anomaly\n")

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
        "device_id": bg.DEVICE_ID,
        "ts": ts_tuple,
        "temp_c": temp,
        "hum_pct": hum,
    }
    payload.update(anomalies)
    return payload

last_read = 0
log("Starting main programm")

detector_type, detector_params = load_detector_settings()
try:
    temp_det = create_detector(detector_type, **detector_params)
    hum_det = create_detector(detector_type, **detector_params)
except Exception as e:
    log("Detector init failed: {}".format(e), level="ERROR")
log("Active detector:", detector_type)
log("Detector params:", detector_params)

measurement_history = deque((), MEASUREMENTS_PER_WINDOW)
post_anomaly_remaining = 0
ping_counter = 0

# main loop
while True:
    # Shutdown button check
    if check_shutdown_button():
        safe_shutdown()
        break
    
    # debugging logs
    now = time.ticks_ms()
    log("delta_t =", time.ticks_diff(now, last_read))
    last_read = now
    
    try:
        # sensor measurements every ~2s
        try:
            dht22.measure()
            temp = round(dht22.temperature(), 1)
            hum = round(dht22.humidity(), 1)
            ts_tuple = time.localtime()
            ts = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*ts_tuple)
        except OSError as e:
            log("DHT read error: {}".format(e), level="ERROR")
            time.sleep(2)
            continue
        
        # get system stats
        cpu = get_cpu_usage()
        mem = get_full_memory_info()

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
        
        # MQTT publish only on anomaly windows, otherwise send periodic ping.
        if temp_anomaly or hum_anomaly:
            log("Anomaly detected, publishing to MQTT...")
            payload = build_measurement_payload(ts_tuple, temp, hum, anomalies)
            payload["event"] = "anomaly"
            payload["window_before"] = list(measurement_history)
            measurement_history = deque((), MEASUREMENTS_PER_WINDOW) # clear history
            bg.client.publish(bg.TOPIC_TELE, ujson.dumps(payload))
            post_anomaly_remaining = POST_ANOMALY_SEND_COUNT
            ping_counter = 0
        elif post_anomaly_remaining > 0:
            payload = build_measurement_payload(ts_tuple, temp, hum, anomalies)
            payload["event"] = "anomaly_followup"
            bg.client.publish(bg.TOPIC_TELE, ujson.dumps(payload))
            post_anomaly_remaining -= 1
            ping_counter = 0
        else:
            measurement_entry = build_measurement_entry(ts_tuple, temp, hum)
            measurement_history.append(measurement_entry)
            ping_counter += 1
            if ping_counter >= PING_EVERY_N:
                bg.client.ping()
                ping_counter = 0

        # sd card log
        log_to_sd(ts, temp, hum, cpu, mem, anomalies)

        
        # debugging logs
        log(f"{ts} Temp:{temp:.1f}C Hum:{hum:.1f}% MP_CPU:{cpu['mp_task']:.1f}% MP_RAM:{mem['mp_used_kb']}/{mem['mp_total_kb']}KB IDF_RAM:{mem['idf_total_kb']-mem['idf_free_kb']}/{mem['idf_total_kb']}KB CPU_TOTAL:{cpu['total']:.1f}% CPU0:{cpu['core0']:.1f}% CPU1:{cpu['core1']:.1f}%", to_sd=False)
        time.sleep(1.5)
    except OSError as e:
        # Network/MQTT error, try to get error code
        err = e.args[0] if e.args else None
        err_name = errno.errorcode.get(abs(err)) if isinstance(err, int) else None

        # try to get wifi status
        try:
            wifi_connected = bg.wifi.isconnected()
            wifi_status = bg.wifi.status()
            wifi_ip = bg.wifi.ifconfig()
        except Exception:
            log("WiFi status fetch failed:", e, level="ERROR")
            wifi_connected = None
            wifi_status = None
            wifi_ip = None

        if err is not None:
            msg = "MQTT/Network/OS error: {} {} wifi_connected={} wifi_status={} ip={}".format(
                err, err_name or "UNKNOWN", wifi_connected, wifi_status, wifi_ip
            )
        else:
            msg = "MQTT/Network/OS error: {} wifi_connected={} wifi_status={} ip={}".format(
                e, wifi_connected, wifi_status, wifi_ip
            )
        log(msg, level="ERROR")
        bg.mqtt_connect()
        time.sleep(2)
    except Exception as e:
        # catch-all
        log("Unexpected error: {}".format(e), level="ERROR")
