"""Deterministic classification of an execution into: final status, primary termination reason, and a *list* of
independently observed violations.

Schema of one violation (unavailable measurements are null, never invented):
  type        TIME_LIMIT | MEMORY_LIMIT | PROCESS_LIMIT | CPU_LIMIT | OUTPUT_LIMIT | SECURITY_POLICY_VIOLATION
  timestamp   ISO-8601 UTC when it was observed;  t_offset seconds since the run phase started (or null)
  limit / observed / unit     configured budget and measured value
  action      TERMINATED (backend killed the container) | KILLED_BY_KERNEL (OOM / seccomp) | DENIED (syscall refused,
              e.g. fork -> EAGAIN) | THROTTLED (CFS quota slowed the workload) | OBSERVED (reported by the program)
  confidence  CONFIRMED (direct kernel / Docker / watchdog evidence) | PROBABLE (inferred from program output or
              sampled metrics)
  role        PRIMARY (ended the job) | CONCURRENT (independently observed, did not end the job)
  source      where the evidence came from (watchdog, cgroup, docker-stats, program-output)
  evidence    human-readable sentence stating exactly what was observed
"""
import re

TYPES = ["TIME_LIMIT", "MEMORY_LIMIT", "PROCESS_LIMIT", "CPU_LIMIT", "OUTPUT_LIMIT", "SECURITY_POLICY_VIOLATION"]

STATUS_LABEL = {
    "QUEUED": "QUEUED", "STARTING": "STARTING", "COMPILING": "COMPILING", "RUNNING": "RUNNING",
    "COMPLETED": "COMPLETED", "COMPILATION_FAILED": "COMPILATION FAILED", "RUNTIME_ERROR": "RUNTIME ERROR",
    "TERMINATED": "TERMINATED", "MONITOR_FAILURE": "MONITOR FAILURE", "CANCELLED": "CANCELLED",
    "REJECTED_CAPACITY": "REJECTED - CAPACITY FULL", "INTERRUPTED": "INTERRUPTED", "ERROR": "SANDBOX ERROR",
}
REASON_LABEL = {
    "TIME_LIMIT": "TIME LIMIT", "MEMORY_LIMIT": "MEMORY LIMIT", "PROCESS_LIMIT": "PROCESS LIMIT",
    "OUTPUT_LIMIT": "OUTPUT LIMIT", "SECURITY_POLICY_VIOLATION": "SECURITY POLICY", "USER_CANCELLED": "CANCELLED BY USER",
    "COMPILE_TIMEOUT": "COMPILE TIMEOUT", "BACKEND_RESTART": "BACKEND RESTART", "MONITOR_FAILURE": "MONITOR LOST",
}
FINAL_STATES = {"COMPLETED", "COMPILATION_FAILED", "RUNTIME_ERROR", "TERMINATED", "MONITOR_FAILURE", "CANCELLED",
                "REJECTED_CAPACITY", "INTERRUPTED", "ERROR"}
ACTIVE_STATES = {"QUEUED", "STARTING", "COMPILING", "RUNNING"}

_OOM_TEXT = re.compile(r"outofmemoryerror|memoryerror|bad_alloc|cannot allocate memory|out of memory", re.I)
_PID_TEXT = re.compile(r"fork failed|resource temporarily unavailable|unable to create (?:native )?thread|can't start new thread|"
                       r"cannot fork|eagain|pthread_create", re.I)


def label_for(status, primary_reason=None, violations=None):
    base = STATUS_LABEL.get(status, status or "UNKNOWN")
    if status == "TERMINATED" and primary_reason in REASON_LABEL:
        return f"TERMINATED - {REASON_LABEL[primary_reason]}"
    if status in ("RUNTIME_ERROR", "COMPLETED") and violations:
        main = [v for v in violations if v.get("confidence") == "CONFIRMED" and v.get("action") in ("DENIED", "KILLED_BY_KERNEL")]
        if main:
            return f"{base} - {REASON_LABEL.get(main[0]['type'], main[0]['type'])} {main[0]['action'].replace('_', ' ')}"
    if status == "MONITOR_FAILURE":
        return "MONITOR FAILURE"
    return base


def _v(typ, limit, observed, unit, action, evidence, confidence, source, t_offset=None, timestamp=None):
    return dict(type=typ, timestamp=timestamp, t_offset=t_offset, limit=limit, observed=observed, unit=unit, action=action,
                evidence=evidence, confidence=confidence, role="CONCURRENT", source=source)


def classify(cfg, ev):
    """cfg: execution config.  ev: facts gathered during the run (see runner.JobCtl.evidence()).
    Returns dict(status, primary_reason, violations, notes)."""
    out, notes = [], []
    bk = ev.get("backend_kill") or {}
    exit_code = ev.get("exit_code")
    cg = ev.get("cg")                                  # run-phase counter deltas, or None when the probe was unavailable
    ts = ev.get("times", {})
    peak_mem, peak_pids = ev.get("peak_mem_mb"), ev.get("peak_pids")
    stream_text = ev.get("text", "")
    compile_failed = ev.get("compile_exit") not in (None, 0) and ev.get("run_started") is False

    # --- violations enforced by the backend watchdog ---------------------------------------------------------------
    if bk.get("reason") in ("TIME_LIMIT", "COMPILE_TIMEOUT"):
        phase = "compile" if bk["reason"] == "COMPILE_TIMEOUT" else "run"
        lim = cfg["compile"]["time"] if phase == "compile" else cfg["time"]
        out.append(_v("TIME_LIMIT", lim, bk.get("observed"), "s", "TERMINATED",
                      f"Wall-clock deadline of {lim} s reached during the {phase} phase; the watchdog sent SIGKILL to the "
                      f"container (independent of the stats monitor).", "CONFIRMED", "watchdog", bk.get("t_offset"), bk.get("timestamp")))
    if bk.get("reason") == "OUTPUT_LIMIT":
        out.append(_v("OUTPUT_LIMIT", bk.get("limit"), bk.get("observed"), "bytes", "TERMINATED",
                      f"stdout+stderr exceeded {bk.get('limit')} bytes; the container was killed to bound output.",
                      "CONFIRMED", "backend", bk.get("t_offset"), bk.get("timestamp")))

    # --- memory ------------------------------------------------------------------------------------------------------
    oom_n = (cg or {}).get("oom_kill", 0) or 0
    if oom_n > 0 or ev.get("docker_oom_killed"):
        src = "cgroup memory.events" if oom_n > 0 else "docker inspect State.OOMKilled"
        ev_txt = (f"{src}: oom_kill={oom_n}; the kernel OOM killer terminated process(es) after the memory cgroup reached "
                  f"its {cfg['memory_mb']} MB limit (swap disabled).") if oom_n > 0 else \
                 f"Docker reported OOMKilled=true: the kernel OOM killer fired in the container's memory cgroup ({cfg['memory_mb']} MB)."
        out.append(_v("MEMORY_LIMIT", cfg["memory_mb"], peak_mem, "MB", "KILLED_BY_KERNEL", ev_txt, "CONFIRMED", "cgroup",
                      ts.get("oom"), ts.get("oom_ts")))
    elif _OOM_TEXT.search(stream_text) and not bk:
        out.append(_v("MEMORY_LIMIT", cfg["memory_mb"], peak_mem, "MB", "OBSERVED",
                      "The program (or its runtime) reported an out-of-memory condition. The kernel OOM counter did not "
                      "increase, so this may be an allocation failure or a managed-runtime heap limit rather than a kernel kill.",
                      "PROBABLE", "program-output"))
    elif cg is None and exit_code == 137 and not bk and peak_mem is not None and peak_mem >= 0.9 * cfg["memory_mb"]:
        out.append(_v("MEMORY_LIMIT", cfg["memory_mb"], peak_mem, "MB", "KILLED_BY_KERNEL",
                      "Process exited with SIGKILL (137) while sampled memory was within 10% of the limit; kernel OOM "
                      "counters were unavailable, so the cause is inferred.", "PROBABLE", "docker-stats"))

    # --- processes / threads ----------------------------------------------------------------------------------------
    denied = (cg or {}).get("pids_max", 0) or 0
    if denied > 0:
        out.append(_v("PROCESS_LIMIT", cfg["pids"], cfg["pids"] if peak_pids is None else max(peak_pids, 0), "pids", "DENIED",
                      f"cgroup pids.events: max={denied}; {denied} process/thread creation attempt(s) were refused "
                      f"(EAGAIN) because the pids cgroup was at its limit of {cfg['pids']}.", "CONFIRMED", "cgroup",
                      ts.get("pids"), ts.get("pids_ts")))
    elif cg is None and (_PID_TEXT.search(stream_text) or (peak_pids is not None and peak_pids >= cfg["pids"])):
        out.append(_v("PROCESS_LIMIT", cfg["pids"], peak_pids, "pids", "DENIED",
                      "Program output indicates process/thread creation failed and/or the sampled process count reached the "
                      "limit; kernel pids.events counters were unavailable, so this is inferred.", "PROBABLE", "docker-stats"))

    # --- cpu (throttling is enforcement, not termination) -----------------------------------------------------------
    thr_us, thr_n = (cg or {}).get("throttled_us", 0) or 0, (cg or {}).get("nr_throttled", 0) or 0
    if thr_us >= 250_000 or thr_n >= 5:
        used = (cg or {}).get("cpu_usage_us")
        rt = ev.get("runtime_s") or 0
        avg = round(used / 1e6 / rt, 2) if used and rt else None
        out.append(_v("CPU_LIMIT", cfg["cpu"], avg, "CPU", "THROTTLED",
                      f"cgroup cpu.stat: the CFS bandwidth controller throttled the workload {thr_n} time(s) for "
                      f"{thr_us / 1e6:.2f} s in total (quota {cfg['cpu']:g} CPU). The program was slowed, not killed.",
                      "CONFIRMED", "cgroup", ts.get("throttle"), ts.get("throttle_ts")))

    # --- seccomp ----------------------------------------------------------------------------------------------------
    if exit_code == 159 and not bk:
        out.append(_v("SECURITY_POLICY_VIOLATION", None, None, None, "KILLED_BY_KERNEL",
                      "Process terminated with SIGSYS (exit status 159 = 128+31): the seccomp filter killed it "
                      "(SCMP_ACT_KILL_PROCESS). The offending syscall is not recorded.", "PROBABLE", "exit-status"))

    # --- final status & primary reason ------------------------------------------------------------------------------
    mem_kill = any(v["type"] == "MEMORY_LIMIT" and v["action"] == "KILLED_BY_KERNEL" for v in out)
    if ev.get("sysfail"):
        status, primary = "ERROR", "SANDBOX_ERROR"
    elif ev.get("monitor_failed"):
        status, primary = "MONITOR_FAILURE", "MONITOR_FAILURE"
    elif bk.get("reason") == "USER_CANCELLED":
        status, primary = "TERMINATED", "USER_CANCELLED"
    elif compile_failed and not bk:
        status, primary = "COMPILATION_FAILED", "COMPILATION_ERROR"
    elif bk.get("reason") in ("TIME_LIMIT", "OUTPUT_LIMIT", "COMPILE_TIMEOUT"):
        status, primary = "TERMINATED", bk["reason"]
    elif mem_kill and exit_code in (137, -9, None):
        status, primary = "TERMINATED", "MEMORY_LIMIT"
    elif exit_code == 159:
        status, primary = "TERMINATED", "SECURITY_POLICY_VIOLATION"
    elif exit_code == 0:
        status, primary = "COMPLETED", "NONE"
    else:
        status, primary = "RUNTIME_ERROR", "NONZERO_EXIT"
    for v in out:
        if v["timestamp"] is None:                       # observed no later than the end of the run
            v["timestamp"], v["t_offset"] = ev.get("end_ts"), ev.get("end_offset")
        if v["type"] == primary:
            v["role"] = "PRIMARY"
    if status == "COMPLETED" and any(v["action"] in ("DENIED", "KILLED_BY_KERNEL") for v in out):
        notes.append("The program exited normally even though the kernel enforced a limit on part of its work "
                     "(e.g. a refused fork or a killed child process).")
    return dict(status=status, primary_reason=primary, violations=out, notes=notes)


def legacy_reason(status, primary, violations):
    """Single-string summary kept for older clients / quick history display."""
    if violations:
        return " + ".join(sorted({v["type"] for v in violations}, key=TYPES.index))
    return primary if status in ("MONITOR_FAILURE", "TERMINATED") else "NONE"
