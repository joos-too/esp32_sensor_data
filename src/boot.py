import os
import network
import ujson
from machine import Pin, SDCard
import boot_globals as bg
from logger import log

def mount_sd(pins, mount_point):
    """Mount SD card."""
    sd = SDCard(
        slot=2,  # SPI mode
        sck=Pin(pins["sck"]),
        mosi=Pin(pins["mosi"]),
        miso=Pin(pins["miso"]),
        cs=Pin(pins["cs"]),
        freq=4_000_000,
    )
    os.mount(sd, mount_point)
    log(f"SD card mounted at {mount_point}")
    log("Contents:", os.listdir(mount_point))
    return sd

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

def wifi_init(hostname):
    """Initialize WiFi (station mode) and set hostname."""
    try:
        w = network.WLAN(network.STA_IF)
        w.active(True)
        w.config(dhcp_hostname=hostname)
        return w
    except Exception as e:
        log("WiFi init failed:", e, level="ERROR")

# Setup
PINS = dict(sck=14, miso=26, mosi=27, cs=12)  # SPI pins
MOUNT_POINT = "/sd"
mount_sd(PINS, MOUNT_POINT)

CONFIG = load_config()
wifi_cfg = CONFIG.get("wifi")
WIFI_HOSTNAME = wifi_cfg.get("hostname", "ESP32-Sensor")
wifi = wifi_init(WIFI_HOSTNAME)

# Export globals for use in main.py
bg.wifi = wifi
bg.CONFIG = CONFIG
