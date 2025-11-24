# ressource monitor functions
import gc, esp32

_last_total = None
_last_runtime = {}

def get_cpu_usage():
    """
    Return dictionary with CPU usage info:
    {
        "mp_task": <float>,
        "core0": <float>,
        "core1": <float>,
        "total": <float>
    }
    """
    global _last_total, _last_runtime

    usage = {"mp_task": 0.0, "core0": 0.0, "core1": 0.0, "total": 0.0}

    try:
        total, tasks = esp32.idf_task_info()
        if not tasks:
            return usage

        # find main MicroPython task
        main_task = next((t for t in tasks if t[1] == 'mp_task'), None)

        # only calculate deltas if previous measurement exists
        if _last_total is not None:
            total_diff = (total - _last_total) & 0xFFFFFFFF
            if total_diff > 0:
                # accumulate runtime deltas per core
                core_runtime_diff = {0: 0, 1: 0}
                for (
                    task_id,
                    task_name,
                    task_state,
                    task_priority,
                    task_runtime,
                    task_stackhighwatermark,
                    task_coreid,
                ) in tasks:
                    if "IDLE" in task_name.upper():
                        continue
                    
                    prev_runtime = _last_runtime.get(task_id, task_runtime)
                    diff = (task_runtime - prev_runtime) & 0xFFFFFFFF
                    if task_coreid in (0, 1):
                        core_runtime_diff[task_coreid] += diff

                    # check if this is mp_task
                    if main_task and task_id == main_task[0]:
                        usage["mp_task"] = 100.0 * diff / total_diff

                usage["core0"] = 100.0 * core_runtime_diff[0] / total_diff
                usage["core1"] = 100.0 * core_runtime_diff[1] / total_diff
                usage["total"] = usage["core0"] + usage["core1"] / 2

        # update last runtimes
        _last_total = total
        for t in tasks:
            _last_runtime[t[0]] = t[4]

    except Exception as e:
        # debugging
        print(e)
        usage = {"mp_task": -1, "core0": -1, "core1": -1, "total": -1}

    return usage


def get_full_memory_info():
    """Return a dict with MicroPython heap + total ESP-IDF heap statistics."""
    # --- MicroPython heap (Python objects only) ---
    mp_alloc = gc.mem_alloc()
    mp_free  = gc.mem_free()
    mp_total = mp_alloc + mp_free

    # --- ESP-IDF heap regions (system-level memory) ---
    total_bytes = 0
    free_bytes  = 0
    for cap in (esp32.HEAP_DATA, esp32.HEAP_EXEC):
        try:
            heaps = esp32.idf_heap_info(cap)
            total_bytes += sum(h[0] for h in heaps)
            free_bytes  += sum(h[1] for h in heaps)
        except Exception as e:
            pass

    return {
        "mp_used_kb":   mp_alloc // 1024,
        "mp_free_kb":   mp_free  // 1024,
        "mp_total_kb":  mp_total // 1024,
        "idf_total_kb": total_bytes // 1024,
        "idf_free_kb":  free_bytes // 1024,
    }