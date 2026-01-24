import os
import network
import ujson
from machine import Pin, SDCard
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

# Setup
PINS = dict(sck=14, miso=26, mosi=27, cs=12)  # SPI pins
MOUNT_POINT = "/sd"
mount_sd(PINS, MOUNT_POINT)
