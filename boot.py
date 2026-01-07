import os
import network
import ujson
from machine import Pin, SDCard
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
            f"SD card not mounted ({e})  — continuing without SD", level="ERROR")
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
# ---- WIFI SETUP -------
# =======================
wifi_cfg = CONFIG.get("wifi")
WIFI_HOSTNAME = wifi_cfg.get("hostname", "ESP32-Sensor")


def wifi_init():
    w = network.WLAN(network.STA_IF)
    w.active(True)
    try:
        w.config(dhcp_hostname=WIFI_HOSTNAME)
    except Exception as e:
        log("WiFi hostname set failed:", e, level="ERROR")
    return w

wifi = wifi_init()

# =======================
# ---- EXPORT GLOBALS ---
# =======================
bg.wifi = wifi
bg.CONFIG = CONFIG
