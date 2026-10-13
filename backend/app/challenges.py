"""Official hackathon challenges C1-C5.

Every challenge drives the *same* code paths as a normal user submission (validation -> admission control -> container
runner -> classification); only the inputs are trusted fixtures from demos/challenges. Each run produces a list of named
checks so the pass/fail decision is explicit and reproducible."""
import io
import os
import threading
import time
import uuid
import warnings
import zipfile

from . import config as C
from . import db, limits, runner, service
from . import validate as VAL
from .scheduler import CapacityFull

INFO = [
    dict(id="C1", title="Normal and timed job", difficulty="Easy",
         objective="Run a finite program that prints OK, then a controlled busy loop that exceeds its runtime budget. "
                   "COMPLETED and TERMINATED must be clearly different outcomes and both containers must be removed.",
         os_mechanism="A backend watchdog thread compares wall-clock time with the job budget and sends SIGKILL to the "
                      "container init; the PID namespace dies with it, so no process survives. cgroup CPU quota keeps the "
                      "busy loop from taking more than its share while it runs.",
         how="2 jobs through the normal pipeline: c1_normal.c (SAFE-like 0.5 CPU / 128 MB / 10 s) and c1_busy_loop.c (5 s)."),
    dict(id="C2", title="Archive traversal rejection", difficulty="Medium",
         objective="Reject malicious archives before any sandbox container is created: '../' and nested traversal, absolute "
                   "Unix and Windows paths, symlinks, duplicates, too many/oversized files.",
         os_mechanism="Pure in-memory validation (zipfile metadata + bounded reads) - archives are never extracted onto the "
                      "host, and files reach the container only through a tar stream into a tmpfs workspace (no bind mounts).",
         how="14 crafted ZIPs are fed to the same validator the upload endpoint uses; the Docker container-creation counter "
             "must not move."),
    dict(id="C3", title="Bounded PID abuse", difficulty="Medium",
         objective="A trusted fixture attempts at most 40 short-lived children under a PID limit of 20 with a 20 s outer "
                   "watchdog. Denied process creation must be recorded as such - not as a termination.",
         os_mechanism="pids cgroup controller (pids.max=20): fork()/clone() fail with EAGAIN once the cgroup is full and the "
                      "kernel counts refusals in pids.events. Nothing is killed; the program decides what to do.",
         how="c3_pid_abuse.c in a private container, pids_limit=20, runtime 20 s."),
    dict(id="C4", title="Monitor / backend interruption", difficulty="Hard",
         objective="Lose the statistics feed (simulated) after two samples. The job must not stay RUNNING: the failure is "
                   "detected, the workload is terminated, the container removed and the final state persisted.",
         os_mechanism="Independent supervision: the watchdog thread (a) enforces hard deadlines without the monitor and "
                      "(b) supervises the monitor's heartbeat. If the feed cannot be restored it kills the container "
                      "(SIGKILL to init -> PID namespace teardown); try/finally in the worker removes it. A janitor "
                      "reconciles orphans and mid-flight jobs.",
         how="Fault injection (server-side only, off for all uploads) on a trusted 30 s sleeper; plus recoverable and "
             "deadline-without-monitor variants and an orphan-reconciliation check."),
    dict(id="C5", title="Host admission budget", difficulty="Hard",
         objective="Never run more than 2 jobs or hold more than 4 pending. Submit 8 finite jobs (1 CPU, 128 MB each): "
                   "6 are accepted, 2 are rejected with a capacity response, queued jobs start FIFO as slots free up.",
         os_mechanism="A lock-protected admission controller (not the UI) gates container creation; combined CPU/memory "
                      "of active jobs must also fit the host budget. cgroup limits per job make the budget enforceable.",
         how="8 trusted python fixtures submitted back-to-back; scheduler counters and an independent DB sampler verify "
             "the caps."),
]
INFO_BY_ID = {c["id"]: c for c in INFO}

RUNS = {}
_busy = threading.Lock()


class Ctx:
    def __init__(self, cid):
        self.cid, self.checks, self.log_lines, self.jobs, self.evidence = cid, [], [], [], {}
        self.t0 = time.time()

    def log(self, msg):
        self.log_lines.append(f"[{time.time() - self.t0:5.1f}s] {msg}")

    def check(self, name, ok, detail=""):
        self.checks.append(dict(name=name, passed=bool(ok), detail=str(detail)))
        self.log(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""))
        return bool(ok)


# ---- helpers -------------------------------------------------------------------------------------------------------
def _fixture(name):
    return (C.DEMOS_DIR / "challenges" / name).read_bytes()


def submit(ctx, fname, language, cfg_key, label, fault=None):
    proj = VAL.build_project([(fname, _fixture(fname))], language)
    cfg = limits.resolve(language, "custom", cpu=C.CHALLENGE_CONFIG[cfg_key]["cpu"],
                         memory_mb=C.CHALLENGE_CONFIG[cfg_key]["memory_mb"], time_limit=C.CHALLENGE_CONFIG[cfg_key]["time"],
                         pids=C.CHALLENGE_CONFIG[cfg_key]["pids"], label=f"challenge-{ctx.cid}")
    jid, state, pos = service.submit_job(proj, language, None, cfg, challenge_id=ctx.cid, fault=fault, simulated=bool(fault),
                                         display_name=fname)
    ctx.jobs.append(dict(id=jid, label=label))
    ctx.log(f"submitted {fname} as job {jid} ({state})")
    return jid


def wait(jid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = db.get(jid)
        if r and r["status"] in ("COMPLETED", "COMPILATION_FAILED", "RUNTIME_ERROR", "TERMINATED", "MONITOR_FAILURE",
                                 "CANCELLED", "REJECTED_CAPACITY", "INTERRUPTED", "ERROR"):
            return r
        time.sleep(0.2)
    return db.get(jid)


def container_gone(jid, tries=20):
    for _ in range(tries):
        try:
            runner.client().containers.get(f"hcs-{jid}")
        except Exception:
            return True
        time.sleep(0.25)
    return False


def has_violation(row, typ, **kw):
    for v in row.get("violations") or []:
        if v["type"] == typ and all(v.get(k) == val for k, val in kw.items()):
            return v
    return None


# ---- C1 -----------------------------------------------------------------------------------------------------------
def run_c1(ctx):
    a = submit(ctx, "c1_normal.c", "c", "C1_OK", "normal program")
    b = submit(ctx, "c1_busy_loop.c", "c", "C1_BUSY", "controlled busy loop")
    ra, rb = wait(a), wait(b)
    ctx.check("normal job COMPLETED", ra["status"] == "COMPLETED", f"status={ra['status']} exit={ra['exit_code']}")
    ctx.check("normal job printed OK", "OK" in (ra["stdout"] or ""), repr((ra["stdout"] or "")[:40]))
    ctx.check("normal job has no violations", not ra["violations"], f"{len(ra['violations'])} violation(s)")
    ctx.check("busy loop TERMINATED (not COMPLETED / RUNTIME_ERROR)", rb["status"] == "TERMINATED", f"status={rb['status']}")
    ctx.check("termination reason is TIME_LIMIT", rb["primary_reason"] == "TIME_LIMIT", f"primary_reason={rb['primary_reason']}")
    v = has_violation(rb, "TIME_LIMIT")
    ctx.check("TIME_LIMIT violation recorded with evidence", bool(v and v["confidence"] == "CONFIRMED" and v["observed"] is not None),
              (v or {}).get("evidence", "missing"))
    ctx.check("busy loop ran for ~the configured budget", 4.5 <= (rb["runtime"] or 0) <= 9,
              f"runtime={rb['runtime']} s (budget {rb['time_limit']} s)")
    ctx.check("both containers removed", container_gone(a) and container_gone(b))
    ctx.evidence.update(runtime_normal=ra["runtime"], runtime_busy=rb["runtime"])


# ---- C2 -----------------------------------------------------------------------------------------------------------
def _zip(entries):
    """entries: list of (name, data|bytes, opts) -> zip bytes. Names are stored verbatim (that is the point)."""
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data, *opt in entries:
                zi = zipfile.ZipInfo(name)
                if opt and opt[0] == "symlink":
                    zi.external_attr = (0o120777 << 16)
                else:
                    zi.external_attr = (0o100644 << 16)
                zi.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(zi, data)
    return buf.getvalue()


def run_c2(ctx):
    before_created = runner.STATS["containers_created"]
    before_ct = len(runner.labelled_containers())
    ok_src = b"#include <stdio.h>\nint main(void){puts(\"hi\");return 0;}\n"
    cases = [
        ("valid project (main.c, util.c, util.h, data.txt)", [("main.c", ok_src), ("util.c", b"int u(void){return 1;}\n"),
                                                               ("util.h", b"int u(void);\n"), ("data.txt", b"1 2 3\n")], None),
        ("'../escape.txt'", [("main.c", ok_src), ("../escape.txt", b"x")], "PATH_TRAVERSAL"),
        ("nested 'docs/../../escape.c'", [("main.c", ok_src), ("docs/../../escape.c", b"x")], "PATH_TRAVERSAL"),
        ("absolute Unix '/etc/cron.d/evil.c'", [("main.c", ok_src), ("/etc/cron.d/evil.c", b"x")], "ABSOLUTE_PATH"),
        ("Windows drive path 'C:\\Windows\\evil.c'", [("main.c", ok_src), ("C:\\Windows\\evil.c", b"x")], "WINDOWS_ABSOLUTE_PATH"),
        ("Windows UNC path '\\\\server\\share\\x.c'", [("main.c", ok_src), ("\\\\server\\share\\x.c", b"x")], "WINDOWS_ABSOLUTE_PATH"),
        ("backslash traversal '..\\evil.c'", [("main.c", ok_src), ("..\\evil.c", b"x")], "PATH_TRAVERSAL"),
        ("ZIP symbolic link", [("main.c", ok_src), ("link.c", b"/etc/passwd", "symlink")], "SYMLINK"),
        ("duplicate entry names", [("main.c", ok_src), ("main.c", b"int x;")], "DUPLICATE_PATH"),
        ("case-conflicting names", [("main.c", ok_src), ("MAIN.C", b"int x;")], "CASE_CONFLICT"),
        ("unsupported file type 'run.sh'", [("main.c", ok_src), ("run.sh", b"rm -rf /")], "UNSUPPORTED_EXTENSION"),
        ("too many files (21)", [(f"f{i}.c", b"int a;") for i in range(21)], "TOO_MANY_FILES"),
        ("oversized file (2 MB + 1)", [("main.c", ok_src), ("big.txt", b"\0" * (C.MAX_FILE_BYTES + 1))], "FILE_TOO_LARGE"),
        ("oversized archive (> 10 MB)", [("main.c", ok_src), ("noise.dat", os.urandom(C.MAX_ARCHIVE_BYTES + 4096))], "ARCHIVE_TOO_LARGE"),
    ]
    results = []
    for name, entries, expect in cases:
        data = _zip(entries)
        try:
            proj = VAL.build_project([("project.zip", data)], "c")
            got, detail = None, f"accepted {len(proj.files)} files"
        except VAL.Rejection as r:
            # several problems may be reported together; the expected code must be among them
            codes = [d["code"] for d in r.details] or [r.code]
            got = expect if expect in codes else codes[0]
            detail = f"rejected: {r.message}"
        ok = got == expect
        results.append(dict(case=name, expected=expect or "ACCEPTED", got=got or "ACCEPTED", passed=ok))
        ctx.check(("accepted: " if expect is None else "rejected: ") + name, ok, f"{detail} (expected {expect or 'ACCEPTED'})")
    ctx.check("no sandbox container was created for any archive",
              runner.STATS["containers_created"] == before_created and len(runner.labelled_containers()) == before_ct,
              f"containers_created counter {before_created} -> {runner.STATS['containers_created']}")
    ctx.evidence["cases"] = results


# ---- C3 -----------------------------------------------------------------------------------------------------------
def run_c3(ctx):
    jid = submit(ctx, "c3_pid_abuse.c", "c", "C3", "PID abuse fixture")
    r = wait(jid, 90)
    cfg = r["config"]
    ctx.check("PID limit configured to 20", cfg["pids"] == 20, f"pids_limit={cfg['pids']}")
    ctx.check("outer watchdog is 20 s", cfg["time"] == 20, f"time={cfg['time']} s")
    out = r["stdout"] or ""
    att = created = refused = None
    for tok in out.replace("\n", " ").split():
        if tok.startswith("attempted="):
            att = int(tok.split("=")[1])
        if tok.startswith("created="):
            created = int(tok.split("=")[1])
        if tok.startswith("refused="):
            refused = int(tok.split("=")[1])
    ctx.check("fixture attempted at most 40 children", att is not None and att <= 40, f"attempted={att}")
    ctx.check("some process creation was denied by the kernel", (refused or 0) > 0, f"created={created} refused={refused}")
    v = has_violation(r, "PROCESS_LIMIT")
    ctx.check("PROCESS_LIMIT violation recorded as DENIED (not TERMINATED)",
              bool(v and v["action"] == "DENIED"), (v or {}).get("evidence", "missing"))
    ctx.check("denial confirmed by kernel counter (pids.events)", bool(v and v["confidence"] == "CONFIRMED"),
              f"confidence={(v or {}).get('confidence')}")
    ctx.check("observed process count never exceeded the limit", (r["process_count"] or 0) <= 20,
              f"peak={r['process_count']} / 20 (includes 2 sandbox support processes)")
    ctx.check("workload continued and COMPLETED (not terminated)", r["status"] == "COMPLETED" and r["primary_reason"] == "NONE",
              f"status={r['status']} primary_reason={r['primary_reason']}")
    ctx.check("finished well inside the 20 s watchdog", (r["runtime"] or 99) < 20, f"runtime={r['runtime']} s")
    ctx.check("container removed", container_gone(jid))
    ctx.evidence.update(attempted=att, created=created, refused=refused, peak_pids=r["process_count"])


# ---- C4 -----------------------------------------------------------------------------------------------------------
def run_c4(ctx):
    # A: persistent feed loss after 2 samples -> supervision lost -> terminate + clean up
    a = submit(ctx, "c4_sleeper.py", "python", "C4", "A: persistent monitor failure",
               fault=dict(mode="exception", after_samples=2, persistent=True))
    ra = wait(a, 90)
    mon = ra.get("monitor") or {}
    evs = [e["event"] for e in mon.get("events", [])]
    ctx.check("A: job did not stay RUNNING - final status MONITOR_FAILURE", ra["status"] == "MONITOR_FAILURE", f"status={ra['status']}")
    ctx.check("A: monitor exception detected", "MONITOR_EXCEPTION" in evs, ", ".join(evs))
    ctx.check("A: monitor restart attempted before giving up", "MONITOR_RESTART" in evs and "MONITOR_FAILURE" in evs)
    ctx.check("A: workload terminated before its 30 s sleep / 25 s deadline", (ra["runtime"] or 99) < 15,
              f"runtime={ra['runtime']} s")
    ctx.check("A: reason persisted", ra["primary_reason"] == "MONITOR_FAILURE" and bool((mon.get("failed") or {}).get("detail")),
              (mon.get("failed") or {}).get("detail", ""))
    ctx.check("A: container removed (no orphan)", container_gone(a))
    # B: transient failure -> monitor restarted, job completes
    b = submit(ctx, "c4_sleeper_short.py", "python", "C4", "B: transient monitor failure (recovered)",
               fault=dict(mode="exception", after_samples=2, persistent=False))
    rb = wait(b, 90)
    evb = [e["event"] for e in (rb.get("monitor") or {}).get("events", [])]
    ctx.check("B: monitor restarted and job recovered to COMPLETED", rb["status"] == "COMPLETED" and "MONITOR_RESTART" in evb,
              f"status={rb['status']} events={evb}")
    ctx.check("B: container removed", container_gone(b))
    # C: hard deadline works with the monitor dead (3 s budget < 4 s staleness detection)
    proj = VAL.build_project([("c4_sleeper.py", _fixture("c4_sleeper.py"))], "python")
    cfg = limits.resolve("python", "custom", cpu=0.5, memory_mb=128, time_limit=3, pids=20, label="challenge-C4")
    c_id, _, _ = service.submit_job(proj, "python", None, cfg, challenge_id="C4",
                                    fault=dict(mode="stall", after_samples=1, persistent=True), simulated=True,
                                    display_name="c4_sleeper.py (3 s deadline)")
    ctx.jobs.append(dict(id=c_id, label="C: hard deadline independent of the monitor"))
    rc = wait(c_id, 60)
    ctx.check("C: watchdog enforced the 3 s deadline although the monitor stalled",
              rc["status"] == "TERMINATED" and rc["primary_reason"] == "TIME_LIMIT",
              f"status={rc['status']} primary={rc['primary_reason']} runtime={rc['runtime']} s")
    ctx.check("C: container removed", container_gone(c_id))
    # D: orphan reconciliation (what happens after a backend crash)
    fake = "orphan" + uuid.uuid4().hex[:6]
    try:
        runner.client().containers.run("hcs-sandbox-python", ["sleep", "300"], detach=True, name=f"hcs-{fake}",
                                       labels={"hcs": "1", "hcs.job": fake}, network_mode="none", mem_limit="64m")
        db.insert(dict(id=fake, created_at=db.now_iso(), language="python", filename="(simulated orphan)", status="RUNNING",
                       resource_profile="challenge-C4", cpu_limit=0.5, memory_limit=64, pid_limit=10, time_limit=10,
                       challenge_id="C4", simulated=1))
        res = service.janitor_pass(grace=0)
        row = db.get(fake)
        ctx.check("D: orphaned container removed by reconciliation", container_gone(fake) and f"hcs-{fake}" in res["removed_containers"],
                  str(res))
        ctx.check("D: stale RUNNING job reconciled to INTERRUPTED", row["status"] == "INTERRUPTED", f"status={row['status']}")
    except Exception as ex:
        ctx.check("D: orphan reconciliation", False, repr(ex))
    ctx.evidence.update(monitor_events=mon.get("events", []), simulated=True)


# ---- C5 -----------------------------------------------------------------------------------------------------------
def run_c5(ctx):
    sch = service.scheduler()
    sch.reset_peaks()
    sampler_stop = threading.Event()
    seen = dict(max_active=0, max_pending=0, series=[])
    ids = []

    def sampler():
        t0 = time.time()
        while not sampler_stop.is_set():
            if ids:
                rows = [db.get(j) for j in list(ids)]
                act = sum(1 for r in rows if r and r["status"] in ("STARTING", "COMPILING", "RUNNING"))
                pen = sum(1 for r in rows if r and r["status"] == "QUEUED")
                seen["max_active"], seen["max_pending"] = max(seen["max_active"], act), max(seen["max_pending"], pen)
                seen["series"].append([round(time.time() - t0, 1), act, pen])
            time.sleep(0.1)

    th = threading.Thread(target=sampler, daemon=True)
    th.start()
    accepted, rejected = [], []
    try:
        for n in range(1, 9):
            proj = VAL.build_project([("c5_finite.py", _fixture("c5_finite.py"))], "python")
            cfg = limits.resolve("python", "custom", cpu=C.CHALLENGE_CONFIG["C5"]["cpu"], memory_mb=C.CHALLENGE_CONFIG["C5"]["memory_mb"],
                                 time_limit=C.CHALLENGE_CONFIG["C5"]["time"], pids=C.CHALLENGE_CONFIG["C5"]["pids"], label="challenge-C5")
            try:
                jid, state, pos = service.submit_job(proj, "python", None, cfg, challenge_id="C5", display_name=f"c5_finite.py #{n}")
                ids.append(jid)
                accepted.append(jid)
                ctx.jobs.append(dict(id=jid, label=f"job {n}: {state}" + (f" (position {pos})" if pos else "")))
                ctx.log(f"job {n}: accepted -> {state}{f' position {pos}' if pos else ''}")
            except CapacityFull as ex:
                ids.append(ex.job_id)
                rejected.append(ex.job_id)
                ctx.jobs.append(dict(id=ex.job_id, label=f"job {n}: REJECTED - capacity full"))
                ctx.log(f"job {n}: rejected ({ex.reason})")
        snap_after_submit = sch.snapshot()
        rows = [wait(j, 150) for j in accepted]
    finally:
        sampler_stop.set()
        th.join(2)
    snap = sch.snapshot()
    ctx.check("2 jobs accepted straight into execution + 4 queued = 6 accepted", len(accepted) == 6, f"accepted={len(accepted)}")
    ctx.check("remaining 2 submissions rejected with a capacity response", len(rejected) == 2, f"rejected={len(rejected)}")
    ctx.check("rejections persisted as REJECTED_CAPACITY",
              all((db.get(j) or {}).get("status") == "REJECTED_CAPACITY" for j in rejected))
    ctx.check("never more than 2 active jobs (admission controller)", snap["peak_active"] <= 2, f"peak_active={snap['peak_active']}")
    ctx.check("never more than 2 active jobs (independent DB sampler)", seen["max_active"] <= 2, f"max observed={seen['max_active']}")
    ctx.check("never more than 4 pending jobs", snap["peak_pending"] <= 4 and seen["max_pending"] <= 4,
              f"peak_pending={snap['peak_pending']} (sampler {seen['max_pending']})")
    ctx.check("queue actually filled to 4", snap["peak_pending"] == 4, f"peak_pending={snap['peak_pending']}")
    starts = [r["started_at"] for r in rows]
    ctx.check("queued jobs started in FIFO order", starts == sorted(starts) and all(starts), "start order matches submission order")
    ctx.check("all accepted jobs COMPLETED", all(r["status"] == "COMPLETED" for r in rows),
              ", ".join(r["status"] for r in rows))
    ctx.check("no slot leaked: scheduler idle afterwards", snap["active"] == 0 and snap["pending"] == 0,
              f"active={snap['active']} pending={snap['pending']}")
    ctx.check("all containers removed", all(container_gone(j) for j in accepted))
    ctx.evidence.update(peak_active=snap["peak_active"], peak_pending=snap["peak_pending"], sampler=seen,
                        timeline=[list(t) for t in list(sch.timeline)[-60:]], budget=dict(cpu=snap["cpu_budget"],
                        memory_mb=snap["memory_budget_mb"]))


RUNNERS = dict(C1=run_c1, C2=run_c2, C3=run_c3, C4=run_c4, C5=run_c5)


# ---- orchestration --------------------------------------------------------------------------------------------------
def _go(run_id, cid):
    ctx = Ctx(cid)
    RUNS[run_id]["ctx"] = ctx
    status = "ERROR"
    try:
        RUNNERS[cid](ctx)
        status = "PASSED" if ctx.checks and all(c["passed"] for c in ctx.checks) else "FAILED"
    except Exception as ex:
        ctx.log(f"ERROR {type(ex).__name__}: {ex}")
        ctx.checks.append(dict(name="challenge executed without error", passed=False, detail=f"{type(ex).__name__}: {ex}"))
    finally:
        info = INFO_BY_ID[cid]
        result = dict(id=cid, title=info["title"], passed=status == "PASSED", checks=ctx.checks, jobs=ctx.jobs,
                      log=ctx.log_lines, evidence=ctx.evidence, duration_s=round(time.time() - ctx.t0, 1),
                      os_mechanism=info["os_mechanism"], simulated=cid == "C4")
        RUNS[run_id].update(status=status, result=result)
        db.save_challenge_run(run_id, cid, RUNS[run_id]["created_at"], status, result, db.now_iso())
        _busy.release()


def start_run(cid):
    if not C.CHALLENGES_ENABLED:
        raise VAL.Rejection("CHALLENGES_DISABLED", "Challenge endpoints are disabled on this deployment", status=403)
    if cid not in RUNNERS:
        raise VAL.Rejection("NOT_FOUND", f"Unknown challenge {cid}", status=404)
    if not runner.docker_ok():
        raise VAL.Rejection("DOCKER_UNAVAILABLE", "Docker is not reachable", status=503)
    if not _busy.acquire(blocking=False):
        raise VAL.Rejection("CHALLENGE_BUSY", "Another challenge is running; wait for it to finish", status=409)
    if cid in ("C4", "C5") and not service.scheduler().idle():
        _busy.release()
        raise VAL.Rejection("HOST_BUSY", f"{cid} needs an idle scheduler - wait for running jobs to finish", status=409)
    run_id = uuid.uuid4().hex[:10]
    RUNS[run_id] = dict(id=run_id, challenge_id=cid, status="RUNNING", created_at=db.now_iso(), result=None, ctx=None)
    db.save_challenge_run(run_id, cid, RUNS[run_id]["created_at"], "RUNNING", {})
    threading.Thread(target=_go, args=(run_id, cid), daemon=True, name=f"challenge-{cid}").start()
    return run_id


def run_view(run_id):
    r = RUNS.get(run_id)
    if r:
        ctx = r.get("ctx")
        return dict(id=run_id, challenge_id=r["challenge_id"], status=r["status"], created_at=r["created_at"],
                    log=(ctx.log_lines[-40:] if ctx else []), checks=(ctx.checks if ctx else []), jobs=(ctx.jobs if ctx else []),
                    result=r["result"])
    d = db.get_challenge_run(run_id)
    if not d:
        return None
    return dict(id=run_id, challenge_id=d["challenge_id"], status=d["status"], created_at=d["created_at"],
                log=d["result"].get("log", []), checks=d["result"].get("checks", []), jobs=d["result"].get("jobs", []),
                result=d["result"])


def catalog():
    latest = db.latest_challenge_runs()
    out = []
    for c in INFO:
        last = latest.get(c["id"])
        out.append(dict(c, last_run=dict(id=last["id"], status=last["status"], finished_at=last["finished_at"],
                                         passed=last["result"].get("passed")) if last else None,
                        simulated=c["id"] == "C4"))
    return out
