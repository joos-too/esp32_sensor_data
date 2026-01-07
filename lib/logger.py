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
        pass


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
