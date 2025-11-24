# Why a custom build?

esp32.idf_task_info() only exists & works if the firmware was built with FreeRTOS tracing enabled. Concretely you must
turn on:

CONFIG_FREERTOS_USE_TRACE_FACILITY = y

CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS = y

CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID = y (recommended)

These toggles live in ESP-IDF “menuconfig” and are mentioned in the official MicroPython docs for the esp32 module.

# Prepare Windows + WSL2 (one-time)

1. Install WSL2 (Ubuntu) if you haven’t:
    - Windows Terminal (Admin):
      `wsl --install -d Ubuntu`
    - Reboot when asked
2. Make ESP32 USB devices visible inside WSL using usbipd-win (optional when flashing from Windows):
    - Install in usbipd in powershell: `winget install dorssel.usbipd-win`
    - Plug the ESP32 and list it: `usbipd list`
    - Attach it to WSL: `usbipd attach --wsl --busid <BUSID>`

# Install ESP-IDF toolchain in WSL

```bash
sudo apt update && sudo apt -y install git wget flex bison gperf cmake ninja-build ccache libffi-dev libssl-dev dfu-util python3-venv python3-pip
mkdir -p ~/esp && cd ~/esp
git clone -b v5.4.2 --recursive https://github.com/espressif/esp-idf.git esp-idf-v5.4.2
cd esp-idf-v5.4.2/
# Use the current stable (or the version MicroPython recommends for your tag, here v5.4.2).
./install.sh    # installs tools into a Python venv
# Every new shell (!): source the environment
. ./export.sh
```

ESP-IDF’s Windows/WSL setup and building basics are
documented [here](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/get-started/windows-setup.html).

# Get MicroPython sources & build mpy-cross

```bash
cd ~
git clone -b v1.26.1 https://github.com/micropython/micropython.git
cd micropython/mpy-cross
make -j
```

# Pull ESP32 submodules for MicroPython

```bash
cd ~/micropython/ports/esp32
make submodules
```

# Configure build (enable FreeRTOS stats)

```bash
cd ~/micropython/ports/esp32
# Choose your board; GENERIC works for most ESP32-WROOM DevKits.
idf.py -D MICROPY_BOARD=ESP32_GENERIC set-target esp32

# Open menuconfig to enable stats:
idf.py -D MICROPY_BOARD=ESP32_GENERIC menuconfig
```

In menuconfig: Component config → FreeRTOS

- Enable FreeRTOS trace facility (CONFIG_FREERTOS_USE_TRACE_FACILITY)
- Enable FreeRTOS run time stats (CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS)
- (optional) Include core ID in vTaskList (CONFIG_FREERTOS_VTASKLIST_INCLUDE_COREID)

Save & exit. Now build: `idf.py -D MICROPY_BOARD=ESP32_GENERIC build`

# Flash the firmware

From WSL (if you attached USB with usbipd-win)

1. Find the port: `ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null`
2. Flash:
   ```bash
    # Erase (optional for clean install):
    python3 -m esptool --chip esp32 --port /dev/ttyUSB0 erase_flash
    # Flash (adapt paths if your build dir name differs):
    idf.py -D MICROPY_BOARD=ESP32_GENERIC -p /dev/ttyUSB0 flash
   ```

From Windows (alternatively)
1. Locate the build outputs in Windows via `cd \\wsl.localhost\Ubuntu\home\jlzmk\micropython\ports\esp32\build`.
2. Install esptool on windows (Python3 required): `py -m pip install esptool`
3. Put ESP32 in bootloader mode (BOOT+EN as needed), note COM port in Device Manager, then:
   ```powershell
   esptool --chip esp32 --port COM3 --baud 921600 erase-flash
   esptool --chip esp32 --port COM3 --baud 921600 write-flash -z 0x1000 bootloader/bootloader.bin 0x8000 partition_table/partition-table.bin 0x10000 micropython.bin
   ```

   
# Verify on the device
Connect with Thonny or mpremote and run:
```python
import esp32
esp32.idf_task_info()
```

If you enabled the configs, you’ll get a list of per-task dicts including runtime counters and (optionally) core IDs. (Exact fields depend on IDF version & options.) The MicroPython docs describe these fields and the required config flags [here](https://docs.micropython.org/en/latest/library/esp32.html#esp32.idf_task_info)..