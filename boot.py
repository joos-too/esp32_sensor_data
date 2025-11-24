import os, time, network, ntptime, ssl, ubinascii, ujson, esp32
from machine import Pin, SDCard
from umqtt.simple import MQTTClient

# =======================
# ---- CONFIG LOAD  -----
# =======================
def load_config():
    """
    Load configuration from JSON file.

    Priority:
      1) ./config.json     (flash)
      2) /sd/config.json   (if SD card present)
    Returns dict (empty if nothing found or invalid).
    """
    paths = ("config.json", "/sd/config.json")
    for path in paths:
        try:
            with open(path) as f:
                cfg = ujson.load(f)
            print("Loaded config from", path)
            return cfg
        except OSError:
            # File not found on this path, try next
            continue
        except ValueError as e:
            # JSON parse error: stop trying further files
            print("Config JSON error in", path, ":", e)
            return {}
    print("No config file found")
    return {}

CONFIG = load_config()

# =======================
# ---- SD-CARD MOUNT ----
# =======================
MOUNT_POINT = "/sd"
PINS = dict(sck=14, miso=26, mosi=27, cs=12) # SPI pins

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
        print(f"SD card mounted at {MOUNT_POINT}")
        print("Contents:", os.listdir(MOUNT_POINT))
        return sd
    except OSError as e:
        print(f"SD card not mounted ({e})  — continuing without SD") # Common causes: no card inserted, bad wiring, wrong format
        return None

mount_sd()

# =======================
# ---- WIFI CONNECT -----
# =======================
wifi_cfg = CONFIG.get("wifi")
WIFI_SSID = wifi_cfg.get("ssid")
WIFI_PASSWORD = wifi_cfg.get("password")
WIFI_HOSTNAME = wifi_cfg.get("hostname", "ESP32-Sensor")

wifi = network.WLAN(network.STA_IF)
wifi.active(True)
wifi.config(dhcp_hostname=WIFI_HOSTNAME)

wifi.connect(WIFI_SSID, WIFI_PASSWORD)

print("Connecting to Wifi", end="")
while not wifi.isconnected():
    print(".", end="")
    time.sleep(1)
print("\nConnected:", wifi.ifconfig())

# =======================
# ---- NTP SYNC ---------
# =======================
def sync_time():
    print("Syncing time via NTP...")
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()               # sets RTC to UTC
        t = time.localtime()
        print("Time synced:",
              "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*t))
    except Exception as e:
        print("NTP sync failed:", e)

sync_time()

# =======================
# ---- MQTT CLIENT ------
# =======================
mqtt_cfg = CONFIG.get("mqtt")
MQTT_BROKER   = mqtt_cfg.get("broker")
MQTT_PORT     = int(mqtt_cfg.get("port"))
MQTT_USER     = mqtt_cfg.get("user", "esp32")
MQTT_PASSWORD = mqtt_cfg.get("password")
DEVICE_ID     = mqtt_cfg.get("device_id", "ESP32-Sensor")
BASE_TOPIC_ROOT = mqtt_cfg.get("base_topic_root", "sensors/esp32")
BASE_TOPIC    = "{}/{}/".format(BASE_TOPIC_ROOT, DEVICE_ID)
TOPIC_TELE    = BASE_TOPIC + "telemetry"
TOPIC_STATUS  = BASE_TOPIC + "status"
CLIENT_ID     = b"esp32-" + ubinascii.hexlify(esp32.raw_temperature().to_bytes(2, 'big'))

context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
context.verify_mode = ssl.CERT_NONE

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

def mqtt_connect():
    global client
    try:
        client.connect()
        online = ujson.dumps({"device_id": DEVICE_ID, "status": "online"})
        client.publish(TOPIC_STATUS, online, retain=True)
        print("MQTT connected:", MQTT_BROKER)
    except Exception as e:
        print("MQTT connect failed:", e)
        time.sleep(3)
        client = mqtt_client_create()
        mqtt_connect()
        
mqtt_connect()
print("Initial MQTT connection succeeded")

# =======================
# ---- EXPORT GLOBALS ---
# =======================
import boot_globals as bg
bg.wifi = wifi
bg.client = client
bg.DEVICE_ID = DEVICE_ID
bg.TOPIC_TELE = TOPIC_TELE
bg.TOPIC_STATUS = TOPIC_STATUS
bg.mqtt_connect = mqtt_connect
