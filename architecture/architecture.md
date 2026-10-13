# KernelGuard architecture

```
Browser (React) --/api--> nginx --> FastAPI backend --Docker API--> disposable sandbox container (one per job)
                                      |  service.py   submit / cancel / views / janitor / restart reconciliation
                                      |  validate.py  in-memory archive + path validation (before any container)
                                      |  entrypoints  main() detection, dependency closure, compile plan
                                      |  scheduler    2 active / 4 pending FIFO + combined CPU/memory budget
                                      |  runner       worker + watchdog + stats monitor + in-container probe
                                      |  violations   deterministic classification -> status, primary reason, violation list
                                      |  ai / report  explanations (AI or rule based) and ReportLab PDF
                                      '  SQLite       jobs, queued project files, presets, challenge runs
```

Supervision chain for one job (no single failure leaves a job unsupervised):

1. **worker thread** - creates the container (read-only rootfs, tmpfs workspace), streams the project in with `tar` over exec stdin,
   runs compile and run as `docker exec` phases, always removes the container in `finally`.
2. **watchdog thread** - phase deadline + absolute hard deadline + cancel flag, and a heartbeat check of the monitor. Independent of the
   monitor: it still kills on time when the stats feed is dead.
3. **monitor thread** - Docker stats feed (1 Hz). On exception/stall the watchdog restarts it once; if the feed cannot be restored the
   job is terminated as `MONITOR_FAILURE`.
4. **probe** (`kg-probe`, uid 1001, in the container) - cgroup v2 counters at 4 Hz: `pids.events`, `memory.events`, `cpu.stat`, peaks.
5. **janitor** - every 5 s frees scheduler slots of dead workers, removes orphan containers, reconciles stale RUNNING rows.
   At start-up everything labelled `hcs=1` is removed, queued jobs are re-admitted, mid-flight jobs become `INTERRUPTED`.

Job states: `QUEUED -> STARTING -> COMPILING -> RUNNING -> COMPLETED | COMPILATION_FAILED | RUNTIME_ERROR | TERMINATED | MONITOR_FAILURE`;
side exits `CANCELLED`, `REJECTED_CAPACITY`, `INTERRUPTED`, `ERROR`.