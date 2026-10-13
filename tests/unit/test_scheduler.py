import threading
import time

import pytest

from app.scheduler import CapacityFull, Scheduler


class Fake:
    """start_fn that 'runs' jobs on demand so tests control completion."""
    def __init__(self):
        self.started, self.events = [], {}

    def __call__(self, job_id):
        self.started.append(job_id)
        ev = threading.Event()
        self.events[job_id] = ev
        th = threading.Thread(target=ev.wait, daemon=True)
        th.start()
        return th


def mk(**kw):
    f = Fake()
    return Scheduler(f, max_active=2, max_pending=4, cpu_budget=kw.get("cpu", 8), mem_budget_mb=kw.get("mem", 8192)), f


def test_two_active_four_pending_two_rejected():
    s, f = mk()
    out = []
    for i in range(8):
        try:
            out.append(s.submit(f"j{i}", 1, 128))
        except CapacityFull:
            out.append("REJECTED")
    assert [o[0] if isinstance(o, tuple) else o for o in out] == ["RUNNING"] * 2 + ["QUEUED"] * 4 + ["REJECTED"] * 2
    snap = s.snapshot()
    assert snap["active"] == 2 and snap["pending"] == 4 and snap["counters"]["rejected"] == 2
    assert f.started == ["j0", "j1"]


def test_fifo_and_slot_release():
    s, f = mk()
    for i in range(5):
        s.submit(f"j{i}", 1, 128)
    s.release("j0")
    assert f.started == ["j0", "j1", "j2"]
    s.release("j1")
    assert f.started == ["j0", "j1", "j2", "j3"]
    assert s.snapshot()["pending"] == 1


def test_cancel_pending_frees_queue_slot():
    s, f = mk()
    for i in range(6):
        s.submit(f"j{i}", 1, 128)
    with pytest.raises(CapacityFull):
        s.submit("x", 1, 128)
    assert s.cancel_pending("j3") is True
    assert s.submit("y", 1, 128) == ("QUEUED", 4)


def test_combined_budget_is_enforced_not_just_per_job():
    s, f = mk(cpu=3.0, mem=4096)
    assert s.submit("a", 2.0, 1024)[0] == "RUNNING"
    assert s.submit("b", 2.0, 1024)[0] == "QUEUED"        # 2+2 > 3 CPU: waits although only 1 job is active
    assert s.snapshot()["active"] == 1
    s.release("a")
    assert f.started == ["a", "b"]


def test_single_job_larger_than_budget_is_rejected_immediately():
    s, _ = mk(cpu=2.0, mem=1024)
    with pytest.raises(CapacityFull) as e:
        s.submit("big", 4.0, 128)
    assert e.value.reason == "EXCEEDS_HOST_BUDGET"


def test_head_of_line_blocking_keeps_fifo():
    s, f = mk(cpu=3.0)
    s.submit("a", 2.0, 128)
    s.submit("big", 2.0, 128)     # does not fit next to a -> queued
    s.submit("small", 0.5, 128)   # would fit, but must not jump the queue
    assert f.started == ["a"]
    s.release("a")
    assert f.started[:3] == ["a", "big", "small"]


def test_dead_worker_does_not_leak_slot():
    f = lambda jid: threading.Thread(target=lambda: None)   # never started -> not alive
    s = Scheduler(lambda jid: (lambda t: (t.start(), t)[1])(threading.Thread(target=lambda: None)), max_active=1, max_pending=1)
    s.submit("a", 1, 1)
    time.sleep(0.1)
    assert s.reap_dead() == ["a"] and s.snapshot()["active"] == 0


def test_concurrent_submissions_never_exceed_caps():
    s, f = mk()
    ok, rej = [], []
    lock = threading.Lock()
    peak = []

    def go(i):
        try:
            r = s.submit(f"t{i}", 1, 64)
            with lock:
                ok.append(r)
        except CapacityFull:
            with lock:
                rej.append(i)
        snap = s.snapshot()
        peak.append((snap["active"], snap["pending"]))

    ths = [threading.Thread(target=go, args=(i,)) for i in range(60)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    assert len(ok) == 6 and len(rej) == 54
    assert max(a for a, _ in peak) <= 2 and max(p for _, p in peak) <= 4
    assert s.snapshot()["peak_active"] == 2 and s.snapshot()["peak_pending"] == 4