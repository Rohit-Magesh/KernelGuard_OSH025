# KernelGuard - Hostile-Code Resource Sandbox

Hackathon: Kernel Ops, problem statement OSH025 - team KernelGuard.

Upload untrusted C, C++, Python or Java code. KernelGuard runs it in a disposable, locked-down Linux container, enforces
CPU / memory / process / runtime budgets with the Linux kernel, records **every** limit that was hit with its evidence,
explains why the run ended, and produces a PDF report.

```
Windows 10/11 -> Docker Desktop (WSL2 Linux VM) -> one throw-away container per job -> your program
                  FastAPI backend (trusted, owns the Docker socket)  <-  React UI on :3000
```

## 1. Requirements (Windows)

| Need | Notes |
|---|---|
| Windows 10/11 64-bit | virtualization enabled in BIOS |
| Docker Desktop with **WSL 2 backend** | https://www.docker.com/products/docker-desktop - Linux containers (the default). Give it >= 4 GB RAM. |
| Internet on the first build | base images and packages are downloaded once |

Node.js, Python and Git are **not** required to run the product - everything is built inside Docker.

Check Docker: open PowerShell and run `docker version` and `docker run --rm hello-world`.

## 2. Start

Unzip `KernelGuard_Final.zip`, open PowerShell in the folder that contains `docker-compose.yml`, then:

```powershell
copy .env.example .env        # optional: AI keys / tuning, the app works without it
docker compose up --build
```

The first build takes several minutes. Four `sandbox-*` services build the language images and then exit with code 0
(expected). When the log says `Application startup complete`, open:

* **http://localhost:3000** - the application
* http://localhost:8000/docs - interactive API documentation
* http://localhost:8000/api/health - health check

Stop with `Ctrl+C`; remove everything including the history database with `docker compose down -v`.

## 3. Using it (30-second tour)

1. **Dashboard** - drop several source files and/or a ZIP (or pick *Load a demo program*). The project tree appears with the
   detected entry point. If the upload holds several independent programs you are asked which one to run.
2. Pick a **resource profile** (Safe / Standard / High / Saved / Custom). The four numbers stay editable; *Save for C/C++* stores
   them as the default preset for that language category (C/C++, Python, Java).
3. **Run in sandbox.** Live CPU, memory, process and runtime meters update while the job runs. If the host is busy the job shows its
   queue position.
4. Read the **result**: status, program output, compiler diagnostics, the complete list of violations (type, limit, observed value,
   confidence, action, evidence), the explanation (`AI` or `RULE_BASED`), technical details, and the **PDF report**.
5. **Executions** lists every previous run; open any of them with its full record. **Challenges** runs the five official tests.
   **Security** lists the sandbox controls and whether each was verified from inside a real container.

### Resource profiles

| Profile | CPU | Memory | Runtime | PIDs |
|---|---:|---:|---:|---:|
| Safe | 0.5 | 256 MB | 10 s | 20 |
| Standard | 1 | 512 MB | 30 s | 50 |
| High | 2 | 2048 MB | 60 s | 100 |
| Custom | any value up to 4 CPU | 4096 MB | 120 s | 200 |

Defaults for saved presets: C/C++ and Python 1 CPU / 512 MB / 30 s / 50 PIDs; Java 1 CPU / 1024 MB / 30 s / 100 PIDs (every JVM thread
counts as a PID, so the JVM needs more). The profile is resolved into an **immutable** config when the job is created and stored with
the job. Compilation uses a separate toolchain budget (768 MB / 128 PIDs / 60 s) that is replaced by the run limits before your
program starts.

Limits are execution budgets, not accusations: a recorded violation says the program exceeded its budget.

### Entry points and multi-file projects

* C/C++: one `main()` -> all uploaded sources are compiled together. Several `main()` files -> you select one; only that program and
  the sources reachable through its `#include "x.h"` -> `x.c` pairs are linked (with an automatic retry that adds the remaining
  non-`main` sources if the link fails).
* Python: `main.py` (or the only file with an `if __name__ == "__main__"` guard); other `.py` files are importable.
* Java: class `Main` by default; any other class with `public static void main` can be selected. All `.java` files are compiled together.

Limits: 20 files, 2 MB per file, 10 MB per project / archive (enforced by the browser **and** the backend).

## 4. AI explanations

Set in `.env` (never commit real keys):

```
AI_PROVIDER=anthropic          # anthropic | openai | openai_compatible | gemini
AI_API_KEY=...
AI_MODEL=...                   # a model id your provider offers
# AI_BASE_URL=                 # only for openai_compatible / gateways
```

Only structured metadata (status, limits, measurements, violation list, a few hundred characters of sanitised stderr/compiler
output) is sent - never source code, environment variables or credentials. If no key is set, the provider is down, the call times out
or the reply is not valid JSON, the deterministic **RULE_BASED** explanation is used and labelled as such; the result screen shows
a *Retry AI* button. The PDF always contains an explanation.

## 5. The five official challenges

Open **Challenges** and press *Run test* (each run is a set of named checks with evidence), or call the API:

```powershell
curl.exe -X POST http://localhost:8000/api/challenges/C1/run     # returns {"run_id": "..."}
curl.exe http://localhost:8000/api/challenges/runs/<run_id>      # status + checks
```

| ID | What happens | Expected |
|---|---|---|
| **C1** Normal & timed | `c1_normal.c` prints OK; `c1_busy_loop.c` spins past a 5 s budget | `COMPLETED` vs `TERMINATED - TIME LIMIT`, both containers removed |
| **C2** Archive traversal | 14 crafted ZIPs: `../`, nested `a/../../x`, `/etc/..`, `C:\..`, UNC, `..\`, symlink, duplicates, case conflict, bad extension, 21 files, 2 MB+1 file, >10 MB archive, plus one valid ZIP | all malicious ones rejected with a structured code **before** any container exists (container-creation counter unchanged), valid ZIP accepted |
| **C3** Bounded PID abuse | `c3_pid_abuse.c` attempts 40 children under `pids.max=20`, 20 s watchdog | ~17 created, ~23 refused by the kernel; `PROCESS_LIMIT` recorded as **DENIED** (confirmed by `pids.events`); job `COMPLETED`, not terminated |
| **C4** Monitor interruption | the stats feed is lost after 2 samples (simulated fault) for a 30 s sleeper | monitor restart attempted, then `MONITOR_FAILURE`, workload killed in seconds, container removed; extra checks: recoverable fault -> `COMPLETED`; hard deadline still enforced with a dead monitor; orphan container + stale RUNNING row reconciled |
| **C5** Admission budget | 8 finite jobs (1 CPU, 128 MB) submitted back-to-back | 2 running + 4 queued, 2 rejected (`REJECTED - CAPACITY FULL`), FIFO start order, peaks 2 / 4 measured twice (scheduler and an independent DB sampler), no leaked slot |

**Safety of fault injection:** it can only be requested by the server-side C4 runner (never through `/api/jobs`, so uploaded programs
cannot reach it), it is applied per job, and `FAULT_INJECTION_ALLOWED=0` / `CHALLENGES_ENABLED=0` switch it off entirely. C4 results are
labelled **SIMULATED** in the UI and the PDF; all kernel and container measurements remain real.

C4/C5 need an idle scheduler; the API answers `409 HOST_BUSY` otherwise.

## 6. How enforcement works

| Concern | Mechanism (all verified by the telemetry probe, see Security tab) |
|---|---|
| Isolation | One container per job: PID, mount, network (`none`), IPC namespaces |
| CPU | cgroup v2 `cpu.max` (CFS bandwidth). Throttling is recorded as `CPU_LIMIT` / `THROTTLED`; nothing is killed |
| Memory | `memory.max` with swap disabled; the kernel OOM killer ends offenders (`memory.events oom_kill`) |
| Processes | `pids.max`; refused `fork`/`clone` are counted in `pids.events` and recorded as `DENIED` |
| Time | independent **watchdog thread**: phase deadline + absolute hard deadline, SIGKILL to the container |
| Output | stdout+stderr byte cap (256 KB) terminates the job; 64 KB per stream is stored |
| Syscalls | seccomp deny-list profile (`sandbox/seccomp/sandbox-seccomp.json`): ptrace, mount, unshare, bpf, ... -> SIGSYS |
| Privilege | uid 1000, `cap_drop=ALL`, `no-new-privileges`, **read-only root filesystem**, size-capped tmpfs `/workspace` and `/tmp` |
| Files | no bind mounts: the validated project is streamed into the tmpfs with `tar` over `docker exec` stdin |
| Supervision | worker + watchdog + stats monitor + in-container probe; monitor lost -> restart -> terminate; janitor reaps orphans; startup reconciliation |
| Admission | lock-protected scheduler: max 2 active, max 4 pending (FIFO), combined CPU/memory budget of active jobs, persistent queue positions |

Telemetry comes from a tiny static helper (`sandbox/probe/kg-probe.c`) running as uid 1001 inside each container: it reads cgroup v2
counters at 4 Hz without forking, so it keeps working even when the PID limit is saturated. Docker `stats` (1 Hz) is the secondary feed
that the C4 challenge disables. Measurement reliability is shown with every result.

### Violation model

A job has a **list** of violations, each `{type, timestamp, t_offset, limit, observed, unit, action, evidence, confidence, role, source}`
plus a separate `primary_reason` for what ended the job. Unavailable values are `null` ("not measured"), never invented.
`CONFIRMED` means direct kernel/Docker/watchdog evidence, `PROBABLE` means inferred from program output or sampled metrics (for example a
Java `OutOfMemoryError`). A throttled CPU or a refused `fork` is recorded but does not change a normal exit into a termination.

## 7. API

| Method & path | Purpose |
|---|---|
| `GET /api/health` `GET /api/config` `GET /api/scheduler` `GET /api/security` | status, limits/presets, queue, verified controls |
| `POST /api/projects/validate` | validate files/ZIP, return tree + entry-point candidates (422 with details on rejection) |
| `POST /api/jobs` | multipart `files`, `language`, `entry_point`, `profile` (`safe\|standard\|high\|preset\|custom`), `cpu`, `memory_mb`, `time_limit`, `pids`. `202` accepted, `409` entry point required, `422` rejected, `429` capacity full |
| `GET /api/jobs` `GET /api/jobs/{id}` `GET /api/jobs/{id}/logs` | history, live status + metrics, outputs |
| `POST /api/jobs/{id}/cancel` | cancel queued or running job |
| `GET/POST /api/jobs/{id}/explanation` | stored explanation / regenerate (`{"use_ai": true}`) |
| `GET /api/jobs/{id}/report` | PDF report (ReportLab, built server-side) |
| `GET/PUT /api/presets[/{c_cpp\|python\|java}]` | saved per-language presets |
| `GET /api/challenges` `POST /api/challenges/{id}/run` `GET /api/challenges/runs/{run}` | official challenges |

Errors always look like `{"error": {"code", "message", "details": [...]}}`.

## 8. Tests

Unit tests (no Docker needed, run inside the backend image):

```powershell
docker compose run --rm --no-deps backend python -m pytest /app/tests/unit -q
```

Integration tests (need the stack running; they execute real jobs and all five challenges, ~4 minutes):

```powershell
docker compose exec backend python -m pytest /app/tests/integration -v
```

## 9. Project layout

```
backend/app/    config, db, validate (archives), entrypoints, limits, scheduler, runner, violations, ai, report, challenges, service, main
frontend/       React + Vite + Tailwind (Dashboard, Executions, Challenges, Security)
sandbox/        per-language Dockerfiles, seccomp profile, kg-probe telemetry helper
demos/          demo programs, catalog.json, challenges/ trusted fixtures
tests/          unit/ and integration/
docker-compose.yml  .env.example
```

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| "Docker unavailable" banner | start Docker Desktop and wait until the engine is running |
| `error during connect` / pipe not found | Docker Desktop is not running or is set to Windows containers - switch to Linux containers |
| Build fails while downloading packages | slow or blocked network: rerun `docker compose up --build`; layers are cached |
| Port 3000 / 8000 in use | change the `ports:` mapping in `docker-compose.yml` |
| Java job shows `PROCESS LIMIT` / thread errors | use Standard/High or raise the PID budget; Safe (20 PIDs) is tight for the JVM |
| Memory test is not killed | Docker Desktop's VM has less RAM than the limit; raise it (Settings -> Resources or `.wslconfig`) |
| C4 / C5 answer `HOST_BUSY` | wait for running jobs to finish |
| Leftover containers after a crash | they are removed automatically at start-up; manual: `docker ps -aq --filter label=hcs=1` then `docker rm -f <ids>` |

## 11. Honest limitations

* Containers share the Docker Desktop VM's Linux kernel; this is strong process-level isolation, not a microVM (Firecracker, gVisor,
  Kata would be the production choice). Docker Desktop adds its own virtualization layer.
* The seccomp profile is a deny-list; it is not a minimal allow-list. Docker's default profile is used if the file cannot be loaded
  (the Security tab shows which one is active).
* CPU is throttled, not killed. Memory and process peaks are sampled at 250 ms (counters such as `oom_kill` and `pids.events` are exact).
  The reported process count includes two sandbox support processes (container init and telemetry probe).
* `/workspace` and `/tmp` are RAM-backed tmpfs and count against the memory limit.
* The runtime budget covers the run phase only; compilation has its own 60 s budget.
* Backend restarts: queued jobs are re-admitted from persisted files; jobs that were mid-flight are marked `INTERRUPTED`.
* Presets are per language category and global for the installation (there is no user-account system).
* The backend is verified to run inside Docker Compose. Running it directly on Windows (outside Docker) is not covered by the tests.
