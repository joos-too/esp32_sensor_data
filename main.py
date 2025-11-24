from machine import Pin, SoftI2C
import time, ujson, gc, esp32, os
import dht, ssd1306
import sys
from resources import get_cpu_usage, get_full_memory_info
from boot_globals import wifi, client, DEVICE_ID, TOPIC_TELE, mqtt_connect

# setup dht22 and oled Display
dht22 = dht.DHT22(Pin(25, Pin.IN))
i2c = SoftI2C(scl=Pin(32), sda=Pin(33))
oled = ssd1306.SSD1306_I2C(128, 64, i2c)

# shutdown button
shutdown_btn = Pin(13, Pin.IN, Pin.PULL_UP)
shutdown_requested = False

def check_shutdown_button():
    """Check if shutdown button is pressed."""
    return shutdown_btn.value() == 0  # LOW when pressed


def safe_shutdown():
    """Flush, unmount SD, and optionally deep sleep."""
    global shutdown_requested
    shutdown_requested = True
    print("Shutdown button pressed, preparing safe power-down")

    try:
        os.umount("/sd")
        print("SD unmounted safely.")
    except Exception as e:
        print("SD unmount error:", e)
    
    # "turn off" oled screen
    oled.fill(0)
    oled.show()
    
    print("Device can now be powered off safely.")

# sd logger
def log_to_sd(ts, temp, hum, cpu, mem):
    """
    Logs a single measurement to daily CSV file on SD card.
    Creates a new file each day with header row if it doesn't exist.
    """
    global debug
    
    if "sd" not in os.listdir("/"):
        print("No SD filesystem mounted.")
        return

    try:
        # current date for filename
        date_str = "{:04d}-{:02d}-{:02d}".format(*time.localtime())
        filename = f"telemetry_{date_str}.csv"
        filepath = "/sd/" + filename

        # header if file is new
        if filename not in os.listdir("/sd"):
            with open(filepath, "w") as f:
                f.write("ts,temp,hum,mp_cpu,mp_used_kb,mp_free_kb\n")

        # timestamp line
        line = f"{ts},{temp:.1f},{hum:.1f},{cpu['mp_task']:.1f},{mem['mp_used_kb']},{mem['mp_free_kb']}"

        # append new row
        with open(filepath, "a") as f:
            f.write(line + "\n")

    except Exception as e:
        print("SD write error:", e)


# main loop
last_read = 0
debug=True
print("Starting main programm loop with debugging={}".format(debug))
while True:
    # Shutdown button check
    if check_shutdown_button():
        safe_shutdown()
        break
    
    # debugging logs
    if debug:
        now = time.ticks_ms()
        print("delta_t =", time.ticks_diff(now, last_read))
        last_read = now
    
    try:
        # sensor measurements every ~2s
        try:
            dht22.measure()
            temp = round(dht22.temperature(), 1)
            hum = round(dht22.humidity(), 1)
            ts = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(*time.localtime())
        except OSError as e:
            print("DHT read error:", e)
            time.sleep(2)
            continue
        
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
        
        # MQTT Publish (JSON)
        payload = {
            "device_id": DEVICE_ID,
            "ts": time.localtime(),
            "temp_c": temp,
            "hum_pct": hum,
        }
        client.publish(TOPIC_TELE, ujson.dumps(payload))
        
        # sd card log
        log_to_sd(ts, temp, hum, cpu, mem)

        
        # debugging logs
        if debug:
            print(f"{ts} Temp:{temp:.1f}C Hum:{hum:.1f}% MP_CPU:{cpu['mp_task']:.1f}% MP_RAM:{mem['mp_used_kb']}/{mem['mp_total_kb']}KB IDF_RAM:{mem['idf_total_kb']-mem['idf_free_kb']}/{mem['idf_total_kb']}KB CPU_TOTAL:{cpu['total']:.1f}% CPU0:{cpu['core0']:.1f}% CPU1:{cpu['core1']:.1f}%")

        time.sleep(1.6)
    except OSError as e:
        # Network/MQTT error
        print("MQTT/Network/OS error:", e)
        try:
            client.disconnect()
        except Exception:
            pass
        mqtt_connect()
        time.sleep(2)