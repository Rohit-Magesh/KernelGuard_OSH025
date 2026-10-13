from app.violations import classify, label_for

CFG = dict(cpu=0.5, memory_mb=256, time=10, pids=20, compile=dict(time=60, memory_mb=1024, pids=128))


def ev(**kw):
    base = dict(exit_code=0, compile_exit=0, run_started=True, backend_kill=None, cg=dict(oom=0, oom_kill=0, mem_max_events=0, pids_max=0,
                nr_throttled=0, throttled_us=0, cpu_usage_us=1000), docker_oom_killed=False, peak_mem_mb=10, peak_pids=3,
                runtime_s=1.0, text="", times={}, sysfail=None, monitor_failed=None, end_ts="T", end_offset=1.0)
    base.update(kw)
    return base


def types(r):
    return [v["type"] for v in r["violations"]]


def test_clean_completion():
    r = classify(CFG, ev())
    assert (r["status"], r["primary_reason"], r["violations"]) == ("COMPLETED", "NONE", [])


def test_timeout_is_terminated_not_error():
    r = classify(CFG, ev(exit_code=137, backend_kill=dict(reason="TIME_LIMIT", observed=10.1, t_offset=10.1, timestamp="T")))
    assert r["status"] == "TERMINATED" and r["primary_reason"] == "TIME_LIMIT"
    assert r["violations"][0]["confidence"] == "CONFIRMED" and r["violations"][0]["role"] == "PRIMARY"


def test_oom_is_memory_limit_confirmed():
    r = classify(CFG, ev(exit_code=137, cg=dict(oom=1, oom_kill=1, pids_max=0, nr_throttled=0, throttled_us=0, cpu_usage_us=0), peak_mem_mb=256))
    assert r["status"] == "TERMINATED" and r["primary_reason"] == "MEMORY_LIMIT"
    assert r["violations"][0]["action"] == "KILLED_BY_KERNEL"


def test_denied_fork_with_clean_exit_is_completed_not_terminated():
    r = classify(CFG, ev(cg=dict(oom_kill=0, pids_max=7, nr_throttled=0, throttled_us=0, cpu_usage_us=0), peak_pids=20))
    assert r["status"] == "COMPLETED"
    assert types(r) == ["PROCESS_LIMIT"] and r["violations"][0]["action"] == "DENIED"


def test_denied_fork_with_error_exit_is_runtime_error_with_violation():
    r = classify(CFG, ev(exit_code=1, cg=dict(oom_kill=0, pids_max=3, nr_throttled=0, throttled_us=0, cpu_usage_us=0), peak_pids=20))
    assert r["status"] == "RUNTIME_ERROR" and r["primary_reason"] == "NONZERO_EXIT" and types(r) == ["PROCESS_LIMIT"]
    assert label_for(r["status"], r["primary_reason"], r["violations"]).startswith("RUNTIME ERROR")


def test_multiple_violations_are_all_preserved():
    cg = dict(oom_kill=0, oom=0, pids_max=23, nr_throttled=99, throttled_us=4_900_000, cpu_usage_us=5_000_000)
    r = classify(CFG, ev(exit_code=137, cg=cg, runtime_s=10.2, backend_kill=dict(reason="TIME_LIMIT", observed=10.2, t_offset=10.2, timestamp="T")))
    assert sorted(types(r)) == ["CPU_LIMIT", "PROCESS_LIMIT", "TIME_LIMIT"]
    assert [v["role"] for v in r["violations"] if v["role"] == "PRIMARY"] == ["PRIMARY"]
    assert r["primary_reason"] == "TIME_LIMIT"


def test_memory_and_time_both_recorded():
    cg = dict(oom_kill=1, oom=1, pids_max=0, nr_throttled=0, throttled_us=0, cpu_usage_us=0)
    r = classify(CFG, ev(exit_code=137, cg=cg, backend_kill=dict(reason="TIME_LIMIT", observed=10, t_offset=10, timestamp="T")))
    assert {"MEMORY_LIMIT", "TIME_LIMIT"} <= set(types(r)) and r["primary_reason"] == "TIME_LIMIT"


def test_java_heap_oom_is_probable_not_confirmed():
    r = classify(CFG, ev(exit_code=1, text="Exception in thread main java.lang.OutOfMemoryError: Java heap space"))
    v = r["violations"][0]
    assert v["type"] == "MEMORY_LIMIT" and v["confidence"] == "PROBABLE" and r["status"] == "RUNTIME_ERROR"


def test_no_kernel_counters_means_inference_is_labelled():
    r = classify(CFG, ev(cg=None, exit_code=1, text="fork failed: Resource temporarily unavailable"))
    assert r["violations"][0]["type"] == "PROCESS_LIMIT" and r["violations"][0]["confidence"] == "PROBABLE"


def test_text_does_not_fake_pid_violation_when_counters_say_none():
    r = classify(CFG, ev(exit_code=1, text="Resource temporarily unavailable"))
    assert r["violations"] == []


def test_compile_failure_and_monitor_failure_and_cancel():
    assert classify(CFG, ev(exit_code=None, compile_exit=1, run_started=False))["status"] == "COMPILATION_FAILED"
    m = classify(CFG, ev(exit_code=137, monitor_failed=dict(detail="lost")))
    assert (m["status"], m["primary_reason"]) == ("MONITOR_FAILURE", "MONITOR_FAILURE")
    c = classify(CFG, ev(exit_code=137, backend_kill=dict(reason="USER_CANCELLED", t_offset=1, timestamp="T")))
    assert (c["status"], c["primary_reason"]) == ("TERMINATED", "USER_CANCELLED")


def test_seccomp_exit_159():
    r = classify(CFG, ev(exit_code=159))
    assert r["status"] == "TERMINATED" and types(r) == ["SECURITY_POLICY_VIOLATION"]


def test_cpu_throttle_alone_does_not_change_status():
    r = classify(CFG, ev(cg=dict(oom_kill=0, pids_max=0, nr_throttled=40, throttled_us=3_000_000, cpu_usage_us=4_000_000), runtime_s=8))
    assert r["status"] == "COMPLETED" and types(r) == ["CPU_LIMIT"] and r["violations"][0]["action"] == "THROTTLED"


def test_unavailable_measurements_are_null_not_invented():
    r = classify(CFG, ev(exit_code=159))
    v = r["violations"][0]
    assert v["limit"] is None and v["observed"] is None