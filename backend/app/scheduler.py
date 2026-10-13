"""Host admission control (challenge C5).

Policy: at most MAX_ACTIVE jobs execute at once, at most MAX_PENDING wait in a bounded FIFO queue, everything else is
rejected immediately with CapacityFull. In addition the *combined* CPU/memory budget of the active jobs must fit the host
budget - a job that is individually within its limits still waits if starting it would oversubscribe the host.

All state is guarded by one lock; the only way a job can start is `_drain()` under that lock, so the active-job cap
cannot be exceeded by racing submissions. The frontend is never trusted to enforce any of this."""
import threading
import time
from collections import deque

from . import config as C


class CapacityFull(Exception):
    def __init__(self, snapshot, reason="CAPACITY_FULL", message=None):
        self.snapshot, self.reason = snapshot, reason
        self.message = message or (f"Host capacity full: {snapshot['active']}/{snapshot['max_active']} jobs running and "
                                   f"{snapshot['pending']}/{snapshot['max_pending']} queued. Try again shortly.")
        super().__init__(self.message)


class Entry:
    __slots__ = ("job_id", "cpu", "mem", "submitted")

    def __init__(self, job_id, cpu, mem):
        self.job_id, self.cpu, self.mem, self.submitted = job_id, cpu, mem, time.time()


class Scheduler:
    def __init__(self, start_fn, on_position=None, max_active=None, max_pending=None, cpu_budget=4.0, mem_budget_mb=4096):
        self.start_fn = start_fn                      # start_fn(job_id) -> threading.Thread (begins executing the job)
        self.on_position = on_position or (lambda job_id, pos: None)
        self.max_active = max_active or C.MAX_ACTIVE_JOBS
        self.max_pending = C.MAX_PENDING_JOBS if max_pending is None else max_pending
        self.cpu_budget, self.mem_budget = cpu_budget, mem_budget_mb
        self._lock = threading.RLock()
        self._active = {}                             # job_id -> (Entry, thread)
        self._pending = deque()
        self.peak_active = self.peak_pending = 0
        self.counters = dict(submitted=0, started=0, queued=0, rejected=0, finished=0, cancelled=0)
        self.timeline = deque(maxlen=400)             # (t, event, job_id, active, pending)

    # ---- helpers (call with lock held) -------------------------------------------------------------------------
    def _used(self):
        return (sum(e.cpu for e, _ in self._active.values()), sum(e.mem for e, _ in self._active.values()))

    def _fits(self, e):
        cpu, mem = self._used()
        return cpu + e.cpu <= self.cpu_budget + 1e-9 and mem + e.mem <= self.mem_budget

    def _log(self, ev, job_id):
        self.peak_active = max(self.peak_active, len(self._active))
        self.peak_pending = max(self.peak_pending, len(self._pending))
        self.timeline.append((round(time.time(), 3), ev, job_id, len(self._active), len(self._pending)))

    def _drain(self):
        while self._pending and len(self._active) < self.max_active and self._fits(self._pending[0]):
            e = self._pending.popleft()
            self._begin(e)
        for i, e in enumerate(self._pending, 1):
            self.on_position(e.job_id, i)

    def _begin(self, e):
        self._active[e.job_id] = (e, None)
        self.counters["started"] += 1
        self._log("START", e.job_id)
        try:
            th = self.start_fn(e.job_id)
            self._active[e.job_id] = (e, th)
        except Exception:
            # never leak a slot because the worker failed to launch
            self._active.pop(e.job_id, None)
            self._log("START_FAILED", e.job_id)
            raise

    # ---- public API --------------------------------------------------------------------------------------------
    def submit(self, job_id, cpu, mem_mb):
        """Returns ("RUNNING", 0) or ("QUEUED", position). Raises CapacityFull."""
        e = Entry(job_id, float(cpu), float(mem_mb))
        with self._lock:
            self.counters["submitted"] += 1
            if e.cpu > self.cpu_budget + 1e-9 or e.mem > self.mem_budget:
                self.counters["rejected"] += 1
                raise CapacityFull(self.snapshot(), "EXCEEDS_HOST_BUDGET",
                                   f"Requested {e.cpu:g} CPU / {e.mem:g} MB exceeds the host budget of "
                                   f"{self.cpu_budget:g} CPU / {self.mem_budget:g} MB for a single job.")
            if not self._pending and len(self._active) < self.max_active and self._fits(e):
                self._begin(e)
                return "RUNNING", 0
            if len(self._pending) >= self.max_pending:
                self.counters["rejected"] += 1
                self._log("REJECT", job_id)
                raise CapacityFull(self.snapshot())
            self._pending.append(e)
            self.counters["queued"] += 1
            self._log("QUEUE", job_id)
            pos = len(self._pending)
            self.on_position(job_id, pos)         # under the lock: cannot race with the job being started
            return "QUEUED", pos

    def release(self, job_id):
        """Called exactly once when a job's worker finishes (success, failure, kill, or crash)."""
        with self._lock:
            if self._active.pop(job_id, None) is not None:
                self.counters["finished"] += 1
                self._log("FINISH", job_id)
            self._drain()

    def cancel_pending(self, job_id):
        with self._lock:
            for e in list(self._pending):
                if e.job_id == job_id:
                    self._pending.remove(e)
                    self.counters["cancelled"] += 1
                    self._log("CANCEL", job_id)
                    self._drain()
                    return True
        return False

    def reap_dead(self):
        """Safety net: free slots whose worker thread died without calling release()."""
        freed = []
        with self._lock:
            for jid, (_, th) in list(self._active.items()):
                if th is not None and not th.is_alive():
                    freed.append(jid)
        for jid in freed:
            self.release(jid)
        return freed

    def is_active(self, job_id):
        with self._lock:
            return job_id in self._active

    def is_pending(self, job_id):
        with self._lock:
            return any(e.job_id == job_id for e in self._pending)

    def idle(self):
        with self._lock:
            return not self._active and not self._pending

    def snapshot(self):
        with self._lock:
            cpu, mem = self._used()
            return dict(active=len(self._active), pending=len(self._pending), max_active=self.max_active,
                        max_pending=self.max_pending, active_jobs=list(self._active),
                        pending_jobs=[e.job_id for e in self._pending], cpu_in_use=round(cpu, 2),
                        cpu_budget=self.cpu_budget, memory_in_use_mb=round(mem), memory_budget_mb=self.mem_budget,
                        peak_active=self.peak_active, peak_pending=self.peak_pending, counters=dict(self.counters))

    def reset_peaks(self):
        with self._lock:
            self.peak_active, self.peak_pending = len(self._active), len(self._pending)
            self.timeline.clear()
