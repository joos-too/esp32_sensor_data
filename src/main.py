from machine import Pin, SoftI2C
import time, ujson, os, asyncio, ntptime, ubinascii, machine
import dht, ssd1306
from resources import get_cpu_usage, get_full_memory_info
from logger import log, log_data

# setup dht22 and oled Display
dht22 = dht.DHT22(Pin(25, Pin.IN))
i2c = SoftI2C(scl=Pin(32), sda=Pin(33))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# shutdown button
shutdown_btn = Pin(13, Pin.IN, Pin.PULL_UP)


async def safe_shutdown():
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

    log("Device can now be powered off safely.", to_sd=False)


def sync_time():
    log("Syncing time via NTP...")
    try:
        ntptime.host = "pool.ntp.org"
        ntptime.settime()  # sets RTC to UTC
        log("Time synced")
    except Exception as e:
        log("NTP sync failed:", e, level="ERROR")




async def sensor_loop(period_ms=2000):
    # sensor measurements every period_ms
    now = time.ticks_ms()
    next_read = time.ticks_add(now, period_ms)

    while True:
        # shutdown button pressed
        if shutdown_btn.value() == 0:
            await safe_shutdown()
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

        # sd card data log
        log_data(ts, temp, hum, cpu, mem)

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
    sync_time()
    await sensor_loop()


asyncio.run(main())
