import os, time, network, ntptime, ssl, ubinascii, ujson, esp32, machine, gc
import uerrno as errno
from machine import Pin, SDCard
from umqtt.simple import MQTTClient
import boot_globals as bg
from logger import log

# =======================
# ---- SD-CARD MOUNT ----
# =======================
MOUNT_POINT = "/sd"
PINS = dict(sck=14, miso=26, mosi=27, cs=12)  # SPI pins


def mount_sd():
    """Mount SD card if present."""
    try:
        sd = SDCard(
            slot=2,  # SPI mode
            sck=Pin(PINS["sck"]),
            mosi=Pin(PINS["mosi"]),
            miso=Pin(PINS["miso"]),
            cs=Pin(PINS["cs"]),
            freq=4_000_000,
        )
        os.mount(sd, MOUNT_POINT)
        log(f"SD card mounted at {MOUNT_POINT}")
        log("Contents:", os.listdir(MOUNT_POINT))
        return sd
    except OSError as e:
        log(
            f"SD card not mounted ({e})  — continuing without SD", level="ERROR")  # Common causes: no card inserted, bad wiring, wrong format
        return None


mount_sd()


# =======================
# ---- CONFIG LOAD  -----
# =======================
def load_config():
    """
    Load configuration from JSON file.

    Priority:
      1) /sd/config.json   (if SD card present)
      2) ./config.json     (flash)
    Returns dict (empty if nothing found or invalid).
    """
    paths = ("/sd/config.json", "config.json")
    for path in paths:
        try:
            with open(path) as f:
                cfg = ujson.load(f)
            log("Loaded config from", path)
            return cfg
        except OSError:
            # File not found on this path, try next
            continue
        except ValueError as e:
            # JSON parse error: stop trying further files
            log("Config JSON error in", path, ":", e, level="ERROR")
            return {}
    log("No config file found", level="ERROR")
    return {}


CONFIG = load_config()

# =======================
# ---- WIFI CONNECT -----
# =======================
wifi_cfg = CONFIG.get("wifi")
WIFI_SSID = wifi_cfg.get("ssid")
WIFI_PASSWORD = wifi_cfg.get("password")
WIFI_HOSTNAME = wifi_cfg.get("hostname", "ESP32-Sensor")


def wifi_init():
    w = network.WLAN()
    w.active(True)
    w.config(dhcp_hostname=WIFI_HOSTNAME, reconnects=5)
    return w


def wifi_hard_reset():
    global wifi
    try:
        wifi.active(False)
    except Exception as e:
        log("WiFi hard reset failed:", e, level="ERROR")
    wifi = wifi_init()
    try:
        bg.wifi = wifi
    except Exception as e:
        log("WiFi hard reset failed:", e, level="ERROR")
    log("WiFi hard reset succeeded")


wifi = wifi_init()
wifi.connect(WIFI_SSID, WIFI_PASSWORD)

log("Connecting to Wifi", end="")
while not wifi.isconnected():
    log(".", end="", to_sd=False)
    time.sleep(1)
log("Connected:", wifi.ifconfig())


# =======================
# ---- NTP SYNC ---------
# =======================
def sync_time():
    log("Syncing time via NTP...")
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()  # sets RTC to UTC
        t = time.localtime()
        log("Time synced:",
            "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*t))
    except Exception as e:
        log("NTP sync failed:", e, level="ERROR")


sync_time()

# =======================
# ---- MQTT CLIENT ------
# =======================
mqtt_cfg = CONFIG.get("mqtt")
MQTT_BROKER = mqtt_cfg.get("broker")
MQTT_PORT = int(mqtt_cfg.get("port"))
MQTT_USER = mqtt_cfg.get("user", "esp32")
MQTT_PASSWORD = mqtt_cfg.get("password")
DEVICE_ID = mqtt_cfg.get("device_id", "ESP32-Sensor")
BASE_TOPIC_ROOT = mqtt_cfg.get("base_topic_root", "sensors/esp32")
BASE_TOPIC = "{}/{}/".format(BASE_TOPIC_ROOT, DEVICE_ID)
TOPIC_TELE = BASE_TOPIC + "telemetry"
TOPIC_STATUS = BASE_TOPIC + "status"
CLIENT_ID = b"esp32-" + ubinascii.hexlify(machine.unique_id())

context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.verify_mode = ssl.CERT_NONE


def wifi_status_name(status):
    names = {
        getattr(network, "STAT_IDLE", 1000): "IDLE",
        getattr(network, "STAT_CONNECTING", 1001): "CONNECTING",
        getattr(network, "STAT_GOT_IP", 1010): "GOT_IP",
        getattr(network, "STAT_NO_AP_FOUND", 201): "NO_AP_FOUND",
        getattr(network, "STAT_WRONG_PASSWORD", 202): "WRONG_PASSWORD",
        getattr(network, "STAT_CONNECT_FAIL", 203): "CONNECT_FAIL",
    }
    return names.get(status, str(status))

# TODO inspect
def wifi_reconnect(timeout_s=30):
    if wifi.isconnected():
        return True

    start = time.ticks_ms()
    attempts = 0
    while time.ticks_diff(time.ticks_ms(), start) < timeout_s * 1000:
        status = wifi.status()
        if status == getattr(network, "STAT_CONNECTING", 1001):
            time.sleep(1)
            continue

        try:
            wifi.disconnect()
        except Exception:
            pass

        try:
            wifi.active(False)
            time.sleep(0.2)
            wifi.active(True)
            wifi.config(dhcp_hostname=WIFI_HOSTNAME)
        except Exception:
            pass

        log("WiFi reconnecting; status={}".format(wifi_status_name(status)))
        wifi.connect(WIFI_SSID, WIFI_PASSWORD)

        # Wait for connect
        for _ in range(10):
            if wifi.isconnected():
                return True
            time.sleep(1)

        attempts += 1
        status = wifi.status()

        if status in (
                getattr(network, "STAT_WRONG_PASSWORD", 202),
                getattr(network, "STAT_NO_AP_FOUND", 201),
        ):
            log(
                "WiFi reconnect failed; status={}".format(
                    wifi_status_name(status)
                ),
                level="ERROR",
            )
            time.sleep(5)
        if attempts % 3 == 0:
            wifi_hard_reset()

    return False


def _err_info(e):
    err = e.args[0] if isinstance(e, OSError) and e.args else None
    err_name = errno.errorcode.get(abs(err)) if isinstance(err, int) else None
    return err, err_name


def mqtt_client_create():
    lwt_msg = ujson.dumps({"device_id": DEVICE_ID, "status": "offline"})
    c = MQTTClient(client_id=CLIENT_ID,
                   server=MQTT_BROKER,
                   port=MQTT_PORT,
                   user=MQTT_USER,
                   password=MQTT_PASSWORD,
                   keepalive=60,
                   ssl=context
                   )
    c.set_last_will(TOPIC_STATUS, lwt_msg)
    return c


client = mqtt_client_create()

def mqtt_reset_client():
    global client
    try:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass
            try:
                sock = getattr(client, "sock", None)
                if sock is not None:
                    sock.close()
            except Exception:
                pass
    except Exception:
        pass
    client = mqtt_client_create()
    try:
        bg.client = client
    except Exception:
        pass
    gc.collect()


def mqtt_connect():
    global client
    backoff_s = 2
    while True:
        try:
            if not wifi.isconnected():
                log("WiFi disconnected; reconnecting...")
                if not wifi_reconnect(timeout_s=30):
                    log("WiFi reconnect failed; status={}".format(
                        wifi_status_name(wifi.status())
                    ), level="ERROR")
                    time.sleep(backoff_s)
                    backoff_s = min(backoff_s * 2, 30)
                    continue
            client.connect(timeout=5)
            online = ujson.dumps({"device_id": DEVICE_ID, "status": "online"})
            client.publish(TOPIC_STATUS, online, retain=True)
            log("MQTT connected:", MQTT_BROKER)
            return
        except Exception as e:
            err, err_name = _err_info(e)
            if err is not None:
                log("MQTT connect failed:", err, err_name or "UNKNOWN", level="ERROR")
            else:
                log("MQTT connect failed:", e, level="ERROR")
            time.sleep(backoff_s)
            backoff_s = min(backoff_s * 2, 30)
            mqtt_reset_client()


mqtt_connect()
log("Initial MQTT connection succeeded")

# =======================
# ---- EXPORT GLOBALS ---
# =======================
bg.wifi = wifi
bg.client = client
bg.DEVICE_ID = DEVICE_ID
bg.TOPIC_TELE = TOPIC_TELE
bg.TOPIC_STATUS = TOPIC_STATUS
bg.mqtt_connect = mqtt_connect
bg.mqtt_reset_client = mqtt_reset_client
bg.CONFIG = CONFIG
