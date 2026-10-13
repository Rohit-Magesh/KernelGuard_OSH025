"""Application service layer shared by the HTTP API and the challenge runner (so challenges exercise the *real* paths)."""
import threading
import time
import uuid

from . import config as C
from . import db, entrypoints, runner
from . import violations as V
from .scheduler import CapacityFull, Scheduler
from .validate import Rejection

_sched = None
_janitor_started = False


def scheduler():
    return _sched


def _host_budget():
    cpu, mem = 4.0, 4096.0
    try:
        info = runner.client().info()
        cpu = min(float(info.get("NCPU") or 4), 4.0)
        mem = min((info.get("MemTotal") or 8 << 30) / 1048576 * 0.75, 6144.0)
    except Exception:
        pass
    if C.HOST_CPU_BUDGET:
        cpu = float(C.HOST_CPU_BUDGET)
    if C.HOST_MEM_BUDGET_MB:
        mem = float(C.HOST_MEM_BUDGET_MB)
    return cpu, mem


def _set_position(job_id, pos):
    try:
        db.update(job_id, queue_position=pos)
    except Exception:
        pass


def _launch(job_id):
    if job_id not in runner.CTRL:
        row = db.get(job_id)
        runner.prepare(job_id, row["config"], row["language"], (row["config"] or {}).get("fault_injection"))

    def target():
        try:
            runner.run_job(job_id)
        except Exception as ex:               # run_job handles its own errors; this is the last line of defence
            try:
                db.update(job_id, status="ERROR", primary_reason="SANDBOX_ERROR", error=repr(ex), finished_at=db.now_iso())
            except Exception:
                pass
        finally:
            _sched.release(job_id)             # always free the slot, even if everything above blew up
            try:
                from . import ai
                ai.auto_explain(job_id)
            except Exception:
                pass

    th = threading.Thread(target=target, daemon=True, name=f"slot-{job_id}")
    th.start()
    return th


def init():
    global _sched
    db.init()
    cpu, mem = _host_budget()
    _sched = Scheduler(_launch, on_position=_set_position, cpu_budget=cpu, mem_budget_mb=mem)
    try:
        reconcile()
    except Exception:
        pass
    _start_janitor()


# ---- submission ----------------------------------------------------------------------------------------------------
def submit_job(project, language, entry_point, cfg, challenge_id=None, fault=None, simulated=False, display_name=None):
    """Validate the build plan, persist the job, then pass it through admission control.
    Returns (job_id, state, queue_position). Raises Rejection or CapacityFull (the job row is kept as REJECTED_CAPACITY)."""
    plan = entrypoints.plan(language, project.files, entry_point)
    job_id = uuid.uuid4().hex[:12]
    stored = dict(cfg)
    if fault and C.FAULT_INJECTION_ALLOWED:
        stored["fault_injection"] = dict(fault, simulated=True)
    names = [m["path"] for m in project.manifest()]
    db.insert(dict(id=job_id, created_at=db.now_iso(), language=language,
                   filename=(display_name or ", ".join(names))[:160], entry_point=plan["entry"],
                   resource_profile=cfg.get("profile"), cpu_limit=cfg["cpu"], memory_limit=cfg["memory_mb"],
                   pid_limit=cfg["pids"], time_limit=cfg["time"], status="QUEUED", config=stored, plan=plan,
                   files=project.manifest(), challenge_id=challenge_id, simulated=1 if simulated or fault else 0))
    db.save_files(job_id, project.files)
    runner.prepare(job_id, stored, language, stored.get("fault_injection"))
    mem_cost = max(cfg["memory_mb"], cfg["compile"]["memory_mb"]) if plan["compile"] else cfg["memory_mb"]
    try:
        state, pos = _sched.submit(job_id, cfg["cpu"], mem_cost)
    except CapacityFull as ex:
        db.update(job_id, status="REJECTED_CAPACITY", primary_reason=ex.reason, error=ex.message,
                  finished_at=db.now_iso(), queue_position=None)
        db.drop_files(job_id)
        runner.CTRL.pop(job_id, None)
        runner.LIVE.pop(job_id, None)
        ex.job_id = job_id
        raise
    return job_id, state, pos


def cancel_job(job_id):
    row = db.get(job_id)
    if not row:
        raise Rejection("NOT_FOUND", "Job not found", status=404)
    if _sched.cancel_pending(job_id):
        db.update(job_id, status="CANCELLED", primary_reason="USER_CANCELLED", finished_at=db.now_iso(), queue_position=None)
        db.drop_files(job_id)
        runner.CTRL.pop(job_id, None)
        runner.LIVE.pop(job_id, None)
        return "cancelled"
    if runner.request_stop(job_id):
        return "stopping"
    return "already_finished"


# ---- views ---------------------------------------------------------------------------------------------------------
def decorate(row):
    row["status_label"] = V.label_for(row.get("status"), row.get("primary_reason"), row.get("violations") or [])
    row["is_final"] = row.get("status") in V.FINAL_STATES
    row["simulated"] = bool(row.get("simulated"))
    return row


def job_view(job_id):
    row = db.get(job_id)
    if not row:
        return None
    if row["status"] in V.ACTIVE_STATES:
        live = runner.live_view(job_id)
        if live:
            row.update({k: v for k, v in live.items() if k != "monitor"})
            row["monitor"] = live["monitor"]
            row["current"] = live["current"]
    return decorate(row)


def history(n=50):
    return [decorate(r) for r in db.recent(n)]


# ---- recovery & housekeeping ---------------------------------------------------------------------------------------
def reconcile():
    """Run at startup: nothing from a previous process can still be supervised, so remove its containers, re-admit
    queued jobs (their files are persisted) and mark everything that was mid-flight as INTERRUPTED."""
    for c in runner.labelled_containers():
        runner.remove_container(c)
    rows = db.jobs_in_states(list(V.ACTIVE_STATES))
    for r in rows:
        jid = r["id"]
        if r["status"] == "QUEUED":
            row = db.get(jid)
            files = db.load_files(jid)
            if files:
                try:
                    runner.prepare(jid, row["config"], row["language"], (row["config"] or {}).get("fault_injection"))
                    cfg = row["config"]
                    cost = max(cfg["memory_mb"], cfg["compile"]["memory_mb"]) if row["plan"].get("compile") else cfg["memory_mb"]
                    _sched.submit(jid, cfg["cpu"], cost)
                    continue
                except Exception as ex:
                    db.update(jid, status="INTERRUPTED", primary_reason="BACKEND_RESTART", error=f"could not re-admit: {ex}",
                              finished_at=db.now_iso(), queue_position=None)
                    continue
        db.update(jid, status="INTERRUPTED", primary_reason="BACKEND_RESTART",
                  error="The backend restarted while this job was in progress; its container was removed.",
                  finished_at=db.now_iso(), queue_position=None)
        db.drop_files(jid)


def janitor_pass(grace=30):
    freed = _sched.reap_dead()
    snap = _sched.snapshot()
    live = set(snap["active_jobs"])
    removed = []
    for c in runner.labelled_containers():
        jid = (c.labels or {}).get("hcs.job")
        if jid in live:
            continue
        try:
            created = time.mktime(time.strptime(c.attrs["Created"][:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except Exception:
            created = 0
        if time.time() - created > grace and runner.remove_container(c):
            removed.append(c.name)
    for r in db.jobs_in_states(["STARTING", "COMPILING", "RUNNING"]):
        if r["id"] not in live:
            db.update(r["id"], status="INTERRUPTED", primary_reason="WORKER_LOST",
                      error="The supervising worker disappeared; job reconciled by the janitor.", finished_at=db.now_iso())
    return dict(freed=freed, removed_containers=removed)


def _janitor_loop():
    while True:
        time.sleep(5)
        try:
            if runner.docker_ok():
                janitor_pass()
        except Exception:
            pass


def _start_janitor():
    global _janitor_started
    if not _janitor_started:
        _janitor_started = True
        threading.Thread(target=_janitor_loop, daemon=True, name="janitor").start()
