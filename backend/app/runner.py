"""Sandbox runner: one disposable, locked-down Linux container per job.

Supervision is deliberately layered so that no single failing component can leave a job unsupervised:

  worker thread   creates the container, streams the project in, runs compile + run exec phases, always cleans up
  watchdog thread independent hard deadlines (phase + absolute), user cancel, and *supervision of the monitor itself*
  monitor thread  Docker stats feed (1 Hz). May die, hang or be fault-injected; the watchdog notices and reacts
  probe thread    in-container kg-probe: cgroup v2 counters at 4 Hz (pids.events, memory.events, cpu.stat ...)

The project is streamed into a tmpfs workspace with `tar` over exec stdin: no bind mounts, no docker cp, and the root
filesystem stays genuinely read-only."""
import io
import json
import tarfile
import threading
import time
from datetime import datetime, timezone

import docker
from docker.types import Ulimit

from . import config as C
from . import db, entrypoints
from . import violations as V

_client = None
_client_lock = threading.Lock()
LIVE, CTRL = {}, {}
STATS = dict(containers_created=0, containers_removed=0)
PROBE_BIN = "/usr/local/bin/kg-probe"
MIB = 1048576.0


def client():
    global _client
    with _client_lock:
        if _client is None:
            _client = docker.from_env(version="auto", timeout=60)
        return _client


def docker_ok():
    try:
        client().ping()
        return True
    except Exception:
        return False


def iso(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat(timespec="milliseconds")


# ---- container lifecycle -------------------------------------------------------------------------------------------
def seccomp_opt():
    try:
        with open(C.SECCOMP_PATH) as f:
            return ["seccomp=" + json.dumps(json.load(f))], "custom"
    except Exception:
        return [], "docker-default"       # Docker's built-in default profile is then in force


def create_container(job_id, language, cfg):
    mem = cfg["compile"]["memory_mb"]     # toolchain budget first; tightened to the run budget before user code runs
    sec, sec_name = seccomp_opt()
    tm = "rw,exec,nosuid,nodev,uid=1000,gid=1000,mode=0755"
    c = client().containers.create(
        C.LANGS[language]["image"], command=["sleep", "3600"], name=f"hcs-{job_id}", user="1000:1000",
        working_dir="/workspace", network_mode="none", read_only=True, cap_drop=["ALL"],
        security_opt=["no-new-privileges"] + sec, mem_limit=f"{mem}m", memswap_limit=f"{mem}m",
        nano_cpus=int(cfg["cpu"] * 1e9), pids_limit=cfg["compile"]["pids"],
        ulimits=[Ulimit(name="nofile", soft=256, hard=256), Ulimit(name="fsize", soft=32 << 20, hard=32 << 20),
                 Ulimit(name="core", soft=0, hard=0)],
        tmpfs={"/workspace": tm + ",size=64m", "/tmp": tm + ",size=64m"},
        environment={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/workspace", "HOME": "/workspace", "TMPDIR": "/tmp"},
        ipc_mode="private", labels={"hcs": "1", "hcs.job": job_id}, log_config={"type": "none"}, detach=True)
    STATS["containers_created"] += 1
    c.start()
    return c, sec_name


def remove_container(c):
    try:
        c.remove(force=True, v=True)
        STATS["containers_removed"] += 1
        return True
    except docker.errors.NotFound:
        return True
    except Exception:
        return False


def _tar(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        dirs = set()
        for p in files:
            parts = p.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        for d in sorted(dirs):
            ti = tarfile.TarInfo(d)
            ti.type, ti.mode, ti.uid, ti.gid = tarfile.DIRTYPE, 0o755, 1000, 1000
            t.addfile(ti)
        for p, data in files.items():
            ti = tarfile.TarInfo(p)
            ti.size, ti.mode, ti.uid, ti.gid = len(data), 0o644, 1000, 1000
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def copy_project(cid, files):
    """Stream the project into /workspace (tmpfs) through exec stdin. `head -c N` makes the receiver stop after exactly
    N bytes, so no half-close of the hijacked socket is needed (works on unix sockets and Windows named pipes)."""
    data = _tar(files)
    api = client().api
    eid = api.exec_create(cid, ["sh", "-c", f"head -c {len(data)} | tar x -C /workspace"], stdin=True, user="1000:1000")["Id"]
    sock = api.exec_start(eid, socket=True)
    raw = getattr(sock, "_sock", sock)
    try:
        raw.sendall(data)
        for _ in range(400):
            i = api.exec_inspect(eid)
            if not i.get("Running"):
                if i.get("ExitCode") not in (0, None):
                    raise RuntimeError(f"project copy failed (exit {i['ExitCode']})")
                return
            time.sleep(0.05)
        raise RuntimeError("project copy timed out")
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _update_limits(cid, mem_mb, pids):
    """Tighten the compile-phase budget to the job's run budget (raw API: docker-py has no pids_limit on update)."""
    api = client().api
    body = {"Memory": mem_mb * 1024 * 1024, "MemorySwap": mem_mb * 1024 * 1024, "PidsLimit": pids}
    res = api._post_json(api._url("/containers/{0}/update", cid), data=body)
    api._raise_for_status(res)


# ---- per-job control block -----------------------------------------------------------------------------------------
class JobCtl:
    def __init__(self, job_id, cfg, language, fault=None):
        self.job_id, self.cfg, self.language = job_id, cfg, language
        self.fault = fault if (fault and C.FAULT_INJECTION_ALLOWED) else None
        self.lock = threading.RLock()
        self.stop = False
        self.done = threading.Event()
        self.container = self.cid = None
        self.phase = "INIT"                       # INIT | COMPILE | RUN | FINALIZE | DONE
        self.deadline = self.hard_deadline = self.run_start = None
        self.backend_kill = None
        self.monitor_failed = None
        self.monitor_events = []
        self.samples = 0
        self.last_sample = None                   # time.monotonic() of last docker-stats sample
        self.monitor_started = None
        self.monitor_restarts = 0
        self.monitor_gen = 0
        self.monitor_thread = None
        self.probe = None
        self.probe_hello = None
        self.probe_seq = 0
        self.probe_ok = False
        self.base = None                          # probe counters at run start
        self.run_limits = None                    # kernel-reported pids.max / memory.max when the run phase began
        self.first_seen = {}
        self.cur = dict(cpu=0.0, mem=0.0, pids=0)
        self.peaks = dict(cpu_pct=0.0, mem_mb=0.0, pids=0)
        self.series = []
        self._last_cpu = None
        self.out, self.err, self.cout = bytearray(), bytearray(), bytearray()
        self.docker_samples_phase = 0

    def event(self, name, detail=""):
        with self.lock:
            self.monitor_events.append(dict(ts=iso(), event=name, detail=detail))

    def reset_peaks(self):
        self.peaks = dict(cpu_pct=0.0, mem_mb=0.0, pids=0)
        self.series = []
        self._last_cpu = None

    def note_sample(self, cpu_pct=None, mem_mb=None, pids=None):
        if cpu_pct is not None:
            self.cur["cpu"] = cpu_pct
            self.peaks["cpu_pct"] = max(self.peaks["cpu_pct"], cpu_pct)
        if mem_mb is not None:
            self.cur["mem"] = mem_mb
            self.peaks["mem_mb"] = max(self.peaks["mem_mb"], mem_mb)
        if pids is not None:
            self.cur["pids"] = pids
            self.peaks["pids"] = max(self.peaks["pids"], pids)

    def runtime(self):
        return round(time.time() - self.run_start, 2) if self.run_start else 0.0

    def kill_for(self, reason, **info):
        """Record who ended the job (first writer wins), then SIGKILL the whole container."""
        with self.lock:
            if self.backend_kill is None:
                self.backend_kill = dict(reason=reason, timestamp=iso(), t_offset=self.runtime() if self.run_start else None,
                                         observed=info.get("observed", self.runtime() if reason == "TIME_LIMIT" else None),
                                         limit=info.get("limit"))
        try:
            if self.container is not None:
                self.container.kill()
        except Exception:
            pass


def _lines(chunks):
    buf = b""
    for ch in chunks:
        buf += ch
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line


# ---- threads -------------------------------------------------------------------------------------------------------
def _monitor(ctl, gen):
    """Docker stats feed. Anything may go wrong here; the watchdog supervises this thread."""
    local = 0
    inc = gen - 1                                   # 0 = first incarnation, >0 = restarted by the watchdog
    try:
        if ctl.fault and ctl.fault.get("persistent") and inc > 0:
            raise RuntimeError("simulated monitor fault (persistent): stats feed could not be re-established")
        for s in client().api.stats(ctl.cid, decode=True, stream=True):
            if ctl.done.is_set() or gen != ctl.monitor_gen:
                return
            local += 1
            if ctl.fault and inc == 0 and local > ctl.fault.get("after_samples", 2):
                if ctl.fault.get("mode") == "stall":
                    ctl.event("FAULT_INJECTED", f"stats feed stalled after {local - 1} samples (simulated)")
                    while not ctl.done.is_set() and gen == ctl.monitor_gen:
                        time.sleep(0.2)
                    return
                raise RuntimeError(f"simulated monitor exception after {local - 1} samples")
            ctl.samples += 1
            ctl.last_sample = time.monotonic()
            cs, ps = s.get("cpu_stats", {}), s.get("precpu_stats", {})
            cd = cs.get("cpu_usage", {}).get("total_usage", 0) - ps.get("cpu_usage", {}).get("total_usage", 0)
            sd = cs.get("system_cpu_usage", 0) - ps.get("system_cpu_usage", 0)
            n = cs.get("online_cpus") or 1
            cpu = (cd / sd) * n * 100 if sd > 0 and cd >= 0 else None
            m = s.get("memory_stats", {})
            st = m.get("stats", {})
            used = max(m.get("usage", 0) - st.get("inactive_file", st.get("cache", 0)), 0) / MIB
            pids = s.get("pids_stats", {}).get("current")
            if ctl.phase == "RUN" or not ctl.probe_ok:
                ctl.note_sample(cpu, used, pids)
    except Exception as ex:
        if not ctl.done.is_set() and gen == ctl.monitor_gen:
            ctl.event("MONITOR_EXCEPTION", f"{type(ex).__name__}: {ex}")
            ctl.monitor_exc = str(ex)


def _start_monitor(ctl):
    ctl.monitor_gen += 1
    ctl.monitor_started = time.monotonic()
    ctl.last_sample = None
    th = threading.Thread(target=_monitor, args=(ctl, ctl.monitor_gen), daemon=True, name=f"mon-{ctl.job_id}")
    ctl.monitor_thread = th
    th.start()


def _check_monitor(ctl, now_m):
    th = ctl.monitor_thread
    if th is None:
        return
    age = now_m - (ctl.last_sample or ctl.monitor_started or now_m)
    dead = not th.is_alive()
    if not dead and age <= C.MONITOR_STALE_SECONDS:
        return
    why = "monitor thread terminated unexpectedly" if dead else f"no statistics sample for {age:.1f}s"
    if ctl.monitor_restarts < C.MONITOR_RESTARTS:
        ctl.monitor_restarts += 1
        ctl.event("MONITOR_RESTART", f"{why}; re-establishing stats feed (attempt {ctl.monitor_restarts}/{C.MONITOR_RESTARTS})")
        _start_monitor(ctl)
        return
    detail = f"{why}; {ctl.monitor_restarts} restart attempt(s) did not restore the feed"
    ctl.event("MONITOR_FAILURE", detail)
    ctl.monitor_failed = dict(detail=detail, samples_received=ctl.samples, restarts=ctl.monitor_restarts)
    ctl.kill_for("MONITOR_FAILURE")


def _watchdog(ctl):
    while not ctl.done.wait(0.2):
        now = time.time()
        if ctl.stop and ctl.backend_kill is None:
            ctl.kill_for("USER_CANCELLED")
        if ctl.phase in ("COMPILE", "RUN") and ctl.backend_kill is None:
            if ctl.deadline and now > ctl.deadline:
                ctl.kill_for("COMPILE_TIMEOUT" if ctl.phase == "COMPILE" else "TIME_LIMIT",
                             observed=round(now - (ctl.run_start or now), 2))
            elif ctl.hard_deadline and now > ctl.hard_deadline:
                ctl.kill_for("TIME_LIMIT", observed=round(now - (ctl.run_start or now), 2))
        if ctl.phase in ("COMPILE", "RUN") and ctl.monitor_failed is None and ctl.backend_kill is None:
            try:
                _check_monitor(ctl, time.monotonic())
            except Exception as ex:                       # the supervisor itself must never die silently
                ctl.event("WATCHDOG_ERROR", repr(ex))
        if ctl.hard_deadline and now > ctl.hard_deadline + 5 and ctl.container is not None:
            try:
                ctl.container.kill()
            except Exception:
                pass


def _probe(ctl):
    """Streams kg-probe JSON lines from inside the container; absence of the probe degrades to docker-stats only."""
    try:
        api = client().api
        eid = api.exec_create(ctl.cid, [PROBE_BIN, str(C.PROBE_INTERVAL_MS)], user="1001:1001", workdir="/")["Id"]
        for line in _lines(api.exec_start(eid, stream=True)):
            if ctl.done.is_set():
                return
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "hello":
                ctl.probe_hello = d
                continue
            _on_probe(ctl, d)
    except Exception:
        pass


def _on_probe(ctl, d):
    prev = ctl.probe
    ctl.probe, ctl.probe_ok = d, True
    ctl.probe_seq += 1
    ws = max(d["mc"] - max(d.get("if", 0), 0), 0) / MIB if d.get("mc", -1) >= 0 else None
    cpu = None
    if prev and d.get("cu", -1) >= 0 and prev.get("cu", -1) >= 0 and d["t"] > prev["t"]:
        cpu = (d["cu"] - prev["cu"]) / ((d["t"] - prev["t"]) * 1000.0) * 100.0      # 100 = one full core
    if ctl.phase == "RUN":
        ctl.note_sample(cpu, ws, d.get("pc") if d.get("pc", -1) >= 0 else None)
        if len(ctl.series) < 240:
            ctl.series.append([round(ctl.runtime(), 2), round(cpu or 0, 1), round(ws or 0, 1), d.get("pc", 0)])
    elif ctl.phase == "COMPILE":
        ctl.cur.update(cpu=cpu or 0.0, mem=ws or 0.0, pids=max(d.get("pc", 0), 0))
    b = ctl.base
    if b and ctl.phase in ("RUN", "FINALIZE") and ctl.run_start:
        off = ctl.runtime()
        for key, name, thresh in (("mk", "oom", 0), ("pe", "pids", 0)):
            if d.get(key, 0) > b.get(key, 0) and name not in ctl.first_seen:
                ctl.first_seen[name] = (off, iso())
        if d.get("tu", 0) - b.get("tu", 0) >= 250_000 and "throttle" not in ctl.first_seen:
            ctl.first_seen["throttle"] = (off, iso())


# ---- exec phases ---------------------------------------------------------------------------------------------------
def _exec(ctl, argv, out, cap_total, which):
    api = client().api
    eid = api.exec_create(ctl.cid, argv, workdir="/workspace", user="1000:1000")["Id"]
    err = ctl.err if out is ctl.out else out
    try:
        for so, se in api.exec_start(eid, stream=True, demux=True):
            if so:
                out += so
            if se:
                err += se
            if len(ctl.out) + len(ctl.err) + len(ctl.cout) > cap_total:
                ctl.kill_for("OUTPUT_LIMIT", observed=len(ctl.out) + len(ctl.err) + len(ctl.cout), limit=cap_total)
                break
    except Exception:
        pass
    code = None
    for _ in range(40):
        try:
            i = api.exec_inspect(eid)
            if not i.get("Running"):
                code = i.get("ExitCode")
                break
        except Exception:
            break
        time.sleep(0.05)
    return code


def _wait_probe_fresh(ctl, seq, timeout=0.7):
    t0 = time.time()
    while time.time() - t0 < timeout and ctl.probe_seq <= seq + 1 and ctl.probe_ok and not ctl.backend_kill:
        time.sleep(0.05)


def _text(b, limit=C.STORED_OUTPUT):
    s = bytes(b).decode("utf-8", "replace")
    return s if len(s) <= limit else s[:limit] + f"\n[... output truncated at {limit} characters ...]\n"


def _deltas(ctl):
    p, b = ctl.probe, ctl.base
    if not (ctl.probe_ok and p and b):
        return None
    g = lambda k: max((p.get(k, 0) or 0) - (b.get(k, 0) or 0), 0)
    return dict(oom=g("mo"), oom_kill=g("mk"), mem_max_events=g("mm"), pids_max=g("pe"), nr_throttled=g("nt"),
                throttled_us=g("tu"), cpu_usage_us=g("cu"),
                kernel_mem_peak_mb=round(p["mp"] / MIB, 1) if p.get("mp", -1) > b.get("mp", 0) else None,
                kernel_pids_peak=p["pp"] if p.get("pp", -1) > b.get("pp", 0) else None)


# ---- the worker ----------------------------------------------------------------------------------------------------
def run_job(job_id):
    row = db.get(job_id)
    cfg, language = row["config"], row["language"]
    files = db.load_files(job_id)
    plan = row["plan"]
    ctl = CTRL[job_id]
    live = LIVE[job_id]
    t_start = time.time()
    code = comp_exit = None
    run_started = None
    container, sec_name, sysfail, oomk = None, None, None, False
    try:
        db.update(job_id, status="STARTING", started_at=iso(), queue_position=None)
        live["status"] = "STARTING"
        ctl.hard_deadline = t_start + cfg["compile"]["time"] + cfg["time"] + C.HARD_DEADLINE_GRACE
        container, sec_name = create_container(job_id, language, cfg)
        ctl.container, ctl.cid = container, container.id
        threading.Thread(target=_watchdog, args=(ctl,), daemon=True, name=f"wd-{job_id}").start()
        copy_project(ctl.cid, files)
        threading.Thread(target=_probe, args=(ctl,), daemon=True, name=f"probe-{job_id}").start()
        _start_monitor(ctl)
        for _ in range(30):                                  # wait (<=1.5 s) for the probe's first sample
            if ctl.probe_ok and ctl.probe_hello:
                break
            time.sleep(0.05)

        if plan.get("compile"):
            db.update(job_id, status="COMPILING")
            live["status"] = "COMPILING"
            ctl.phase = "COMPILE"
            ctl.deadline = time.time() + cfg["compile"]["time"]
            for n, argv in enumerate(plan["compile"]):
                comp_exit = _exec(ctl, argv, ctl.cout, C.MAX_OUTPUT, "compile")
                if comp_exit == 0 or ctl.backend_kill:
                    break
                if n + 1 < len(plan["compile"]) and b"undefined reference" in bytes(ctl.cout):
                    ctl.cout += b"\n[kernelguard] link failed with the dependency-closure source set; retrying with all " \
                                b"non-entry source files...\n"
                    continue
                break
        if not ctl.backend_kill and (comp_exit in (None, 0)):
            if plan.get("compile"):
                try:                                         # restore the job's real budget before user code runs
                    _update_limits(ctl.cid, cfg["memory_mb"], cfg["pids"])
                except Exception as ex:
                    ctl.event("LIMIT_UPDATE_FAILED", repr(ex))
                    raise RuntimeError(f"could not apply run-phase limits: {ex}")
            else:
                _update_limits(ctl.cid, cfg["memory_mb"], cfg["pids"])
            _wait_probe_fresh(ctl, ctl.probe_seq, 0.4)
            ctl.base = dict(ctl.probe) if ctl.probe else None
            if ctl.base:
                ctl.run_limits = dict(pids_max=ctl.base.get("pm"), memory_max=ctl.base.get("mx"))
            ctl.reset_peaks()
            run_started = True
            ctl.phase = "RUN"
            ctl.run_start = time.time()
            ctl.deadline = ctl.run_start + cfg["time"]
            db.update(job_id, status="RUNNING")
            live["status"] = "RUNNING"
            code = _exec(ctl, plan["run"], ctl.out, C.MAX_OUTPUT, "run")
        else:
            run_started = False
    except Exception as ex:
        sysfail = f"{type(ex).__name__}: {ex}"
        ctl.err += f"\n[sandbox error] {sysfail}\n".encode()
    finally:
        ctl.phase = "FINALIZE"
        t_end = time.time()
        try:
            if ctl.base and not ctl.backend_kill:
                _wait_probe_fresh(ctl, ctl.probe_seq, 0.7)
            if container is not None:
                try:
                    container.reload()
                    oomk = bool(container.attrs["State"].get("OOMKilled"))
                except Exception:
                    pass
            ctl.done.set()                                   # stop watchdog / monitor / probe: evidence is captured
            if container is not None:                        # the container is gone BEFORE the final state is published
                for _ in range(3):
                    if remove_container(container):
                        container = None
                        break
                    time.sleep(0.5)
            finish(job_id, ctl, cfg, language, plan, code, comp_exit, run_started, sysfail, oomk, sec_name, t_start, t_end, live)
        except Exception as ex:                              # persisting must not raise past cleanup
            try:
                db.update(job_id, status="ERROR", primary_reason="SANDBOX_ERROR", error=f"{type(ex).__name__}: {ex}",
                          finished_at=iso())
            except Exception:
                pass
        finally:
            ctl.done.set()
            ctl.phase = "DONE"
            if container is not None:                        # last resort if the exception path skipped removal
                remove_container(container)
            LIVE.pop(job_id, None)
            CTRL.pop(job_id, None)


def finish(job_id, ctl, cfg, language, plan, code, comp_exit, run_started, sysfail, oomk, sec_name, t_start, t_end, live):
    cg = _deltas(ctl)
    if ctl.probe and ctl.base:                        # fold the final post-run sample into the peaks
        d = ctl.probe
        if d.get("mc", -1) >= 0:
            ctl.peaks["mem_mb"] = max(ctl.peaks["mem_mb"], max(d["mc"] - max(d.get("if", 0), 0), 0) / MIB)
        ctl.peaks["pids"] = max(ctl.peaks["pids"], d.get("pc", 0) or 0)
    peak_mem, peak_pids = ctl.peaks["mem_mb"] or None, ctl.peaks["pids"] or None
    if cg:
        if cg["kernel_mem_peak_mb"] is not None:
            peak_mem = max(peak_mem or 0, cg["kernel_mem_peak_mb"])
        if cg["kernel_pids_peak"] is not None:
            peak_pids = max(peak_pids or 0, cg["kernel_pids_peak"])
        if cg["pids_max"] > 0:
            peak_pids = max(peak_pids or 0, cfg["pids"])
    runtime = round((t_end - ctl.run_start), 2) if ctl.run_start else 0.0
    text = (bytes(ctl.out) + b"\n" + bytes(ctl.err))[-8192:].decode("utf-8", "replace")
    times = {}
    for name in ("oom", "pids", "throttle"):
        if name in ctl.first_seen:
            times[name], times[name + "_ts"] = ctl.first_seen[name]
    ev = dict(exit_code=code, compile_exit=comp_exit, run_started=run_started, backend_kill=ctl.backend_kill, cg=cg,
              docker_oom_killed=oomk, peak_mem_mb=round(peak_mem, 1) if peak_mem else None, peak_pids=peak_pids,
              runtime_s=runtime, text=text, times=times, sysfail=sysfail, monitor_failed=ctl.monitor_failed,
              end_ts=iso(t_end), end_offset=runtime if ctl.run_start else None)
    if ctl.backend_kill and ctl.backend_kill.get("reason") == "OUTPUT_LIMIT":
        ctl.backend_kill.setdefault("limit", C.MAX_OUTPUT)
    result = V.classify(cfg, ev)
    cpu_peak = round(ctl.peaks["cpu_pct"], 1)
    hello = ctl.probe_hello or {}
    telemetry = dict(
        source="cgroup-v2 (kg-probe) + docker stats" if ctl.probe_ok else "docker stats only (probe unavailable)",
        probe_available=ctl.probe_ok, docker_stats_samples=ctl.samples, probe_samples=ctl.probe_seq,
        cgroup_run_delta=cg, series=ctl.series, run_phase_seconds=runtime,
        peaks=dict(cpu_pct=cpu_peak, memory_mb=round(peak_mem, 1) if peak_mem else 0.0, pids=peak_pids or 0),
        reliability=dict(
            memory="Working set (memory.current - inactive_file) sampled every 250 ms; kernel memory.peak used when it "
                   "exceeded the pre-run level. Includes tmpfs workspace and page cache.",
            cpu="CFS usage_usec delta per 250 ms sample (100 = one core); very short bursts are averaged.",
            pids="pids.current sampled every 250 ms; pids.events counter is exact. Includes 2 sandbox support processes "
                 "(init + telemetry probe).",
            runtime="Wall-clock measured by the backend from the start of the run phase (compile excluded).",
            simulated=False),
        security_verified=hello or None, limits_verified=ctl.run_limits, seccomp_profile=sec_name)
    monitor = dict(samples_received=ctl.samples, restarts=ctl.monitor_restarts, failed=ctl.monitor_failed,
                   events=ctl.monitor_events, fault_injection=ctl.fault, simulated=bool(ctl.fault),
                   probe_available=ctl.probe_ok)
    status, primary = result["status"], result["primary_reason"]
    if status == "TERMINATED" and primary == "USER_CANCELLED":
        pass
    out_txt = _text(ctl.out)
    err_txt = _text(ctl.err)
    db.update(job_id, status=status, primary_reason=primary, exit_code=code if code is not None else comp_exit, cpu_usage=cpu_peak,
              peak_memory=round(peak_mem, 1) if peak_mem else 0.0, process_count=peak_pids or 0, runtime=runtime,
              stdout=out_txt, stderr=err_txt, compile_output=_text(ctl.cout), violations=result["violations"],
              violation_reason=V.legacy_reason(status, primary, result["violations"]), telemetry=telemetry,
              monitor=monitor, finished_at=iso(), error=sysfail, queue_position=None)
    if hello and hello.get("uid") is not None:
        db.meta_put("security_verified", dict(at=iso(), job_id=job_id, probe=hello, run_limits=ctl.run_limits,
                                                  seccomp_profile=sec_name))
    db.drop_files(job_id)


# ---- public ------------------------------------------------------------------------------------------------------
def prepare(job_id, cfg, language, fault=None):
    LIVE[job_id] = dict(status="QUEUED")
    CTRL[job_id] = JobCtl(job_id, cfg, language, fault)


def start(job_id):
    """Scheduler callback: spawn the worker thread. The control block is created at submit time."""
    if job_id not in CTRL:
        row = db.get(job_id)
        prepare(job_id, row["config"], row["language"], (row["config"] or {}).get("fault_injection"))
    th = threading.Thread(target=run_job, args=(job_id,), daemon=True, name=f"job-{job_id}")
    th.start()
    return th


def live_view(job_id):
    ctl, l = CTRL.get(job_id), LIVE.get(job_id)
    if not ctl or not l:
        return None
    return dict(status=l.get("status", ctl.phase), cpu_usage=round(ctl.peaks["cpu_pct"], 1),
                peak_memory=round(ctl.peaks["mem_mb"], 1), process_count=ctl.peaks["pids"], runtime=ctl.runtime(),
                current=dict(cpu_pct=round(ctl.cur["cpu"], 1), memory_mb=round(ctl.cur["mem"], 1), pids=ctl.cur["pids"]),
                stdout=_text(ctl.out), stderr=_text(ctl.err), compile_output=_text(ctl.cout),
                monitor=dict(samples_received=ctl.samples, restarts=ctl.monitor_restarts, events=ctl.monitor_events[-6:],
                             failed=ctl.monitor_failed))


def request_stop(job_id):
    c = CTRL.get(job_id)
    if not c:
        return False
    c.stop = True
    try:
        if c.container is not None:
            c.kill_for("USER_CANCELLED")
    except Exception:
        pass
    return True


def labelled_containers():
    try:
        return client().containers.list(all=True, filters={"label": "hcs=1"})
    except Exception:
        return []
