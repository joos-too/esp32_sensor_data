# console and sd-card logging utility
import os
import time

# central flag for activating debugging console logs
DEBUG = True

def _sd_available():
    try:
        return "sd" in os.listdir("/")
    except Exception:
        return False


def _format_ts(ts_tuple):
    y, mo, d, h, mi, s = ts_tuple[0:6]
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(y, mo, d, h, mi, s)


def _append_to_sd(line, ts_tuple):
    if not _sd_available():
        return
    try:
        y, mo, d = ts_tuple[0:3]
        date_str = "{:04d}-{:02d}-{:02d}".format(y, mo, d)
        filepath = "/sd/system_{}.log".format(date_str)
        with open(filepath, "a") as f:
            f.write(line + "\n")
    except Exception:
        log("SD write error:", level="ERROR", to_sd=False)


def log(*args, sep=" ", end="\n", to_sd=True, level="INFO"):
    try:
        ts_tuple = time.localtime()
    except Exception:
        ts_tuple = (0, 0, 0, 0, 0, 0, 0, 0)
    ts = _format_ts(ts_tuple)
    msg = sep.join(str(a) for a in args)
    level_str = str(level or "INFO").upper()
    line = "{} {} {}".format(level_str, ts, msg) if msg else "{} {}".format(level_str, ts)
    if DEBUG:
        print(line, end=end)
    if to_sd:
        _append_to_sd(line, ts_tuple)

def log_data(ts, temp, hum, cpu, mem, anomalies):
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
                f.write(
                    "ts,temp,hum,mp_cpu,cpu_total,cpu_core0,cpu_core1,mp_used_kb,mp_free_kb,mp_total_kb,idf_used_kb,idf_free_kb,idf_total_kb,temp_zscore_anomaly,temp_ewma_anomaly,temp_rulebased_anomaly,hum_zscore_anomaly,hum_ewma_anomaly,hum_rulebased_anomaly\n")

        # timestamp line
        idf_used_kb = mem["idf_total_kb"] - mem["idf_free_kb"]
        line = f"{ts},{temp:.1f},{hum:.1f},{cpu['mp_task']:.1f},{cpu['total']:.1f},{cpu['core0']:.1f},{cpu['core1']:.1f},{mem['mp_used_kb']},{mem['mp_free_kb']},{mem['mp_total_kb']},{idf_used_kb},{mem['idf_free_kb']},{mem['idf_total_kb']},{anomalies['temp_zscore_anomaly']},{anomalies['temp_ewma_anomaly']},{anomalies['temp_rulebased_anomaly']},{anomalies['hum_zscore_anomaly']},{anomalies['hum_ewma_anomaly']},{anomalies['hum_rulebased_anomaly']}"

        # append new row
        with open(filepath, "a") as f:
            f.write(line + "\n")

    except Exception as e:
        log("SD write error: {}".format(e), level="ERROR", to_sd=False)
