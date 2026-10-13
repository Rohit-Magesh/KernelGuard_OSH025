"""Execution explanations.

Two sources, always labelled: `AI` (optional, provider-agnostic HTTPS call) and `RULE_BASED` (deterministic, offline).
Only structured execution metadata and short sanitised diagnostics are sent to a provider - never source code, files,
environment variables or credentials. Any AI failure (no key, timeout, HTTP error, malformed JSON) falls back to the
rule-based explanation, so the product never depends on an external service."""
import json
import os
import re
import threading
import urllib.error
import urllib.request

from . import config as C
from . import db
from . import violations as V

MECHANISM = {
    "TIME_LIMIT": "Wall-clock watchdog: the backend compares elapsed time with the job's runtime budget on its own thread "
                  "(independent of the stats monitor). At the deadline it sends SIGKILL to the container's init process, "
                  "which makes the kernel destroy every process in the container's PID namespace.",
    "MEMORY_LIMIT": "memory cgroup (memory.max, swap disabled): when the group cannot reclaim enough pages to satisfy an "
                    "allocation, the kernel OOM killer sends SIGKILL to a task in that cgroup. Evidence is the oom_kill "
                    "counter in memory.events.",
    "PROCESS_LIMIT": "pids cgroup (pids.max): fork()/clone() - including thread creation - fail with EAGAIN once the cgroup "
                     "holds the maximum number of tasks. The kernel counts each refusal in pids.events. Nothing is killed; "
                     "the program must handle the failed call.",
    "CPU_LIMIT": "CFS bandwidth control (cpu.max = quota/period): when the cgroup has consumed its quota in a period the "
                 "scheduler throttles all its tasks until the next period. The program is slowed down, never killed.",
    "OUTPUT_LIMIT": "The backend counts stdout+stderr bytes streamed from the exec session and kills the container when "
                    "the cap is exceeded, protecting the host from unbounded output.",
    "SECURITY_POLICY_VIOLATION": "seccomp-bpf: a filter attached to every task inspects each system call; calls on the deny "
                                 "list (ptrace, mount, unshare, bpf, ...) kill the process with SIGSYS.",
}
SUGGEST = {
    "TIME_LIMIT": ["Look for loops without a terminating condition, blocking calls (sleep, waiting on input) or work that "
                   "scales badly with input size.",
                   "If the work is legitimately long, raise the runtime budget (platform maximum 120 s) or reduce the input."],
    "MEMORY_LIMIT": ["Reduce peak memory: stream or chunk data instead of loading it all, free buffers you no longer need, "
                     "avoid unbounded containers.",
                     "If the footprint is expected, raise the memory budget (platform maximum 4096 MB). Remember /workspace "
                     "and /tmp are RAM-backed and count against it."],
    "PROCESS_LIMIT": ["Bound the number of child processes/threads (use a fixed-size worker pool) and always check the "
                      "return value of fork()/pthread_create().",
                      "Reap children (wait/join) so finished tasks stop counting against the limit; Java programs may need "
                      "a larger PID budget because every JVM thread is a task."],
    "CPU_LIMIT": ["The program is CPU-bound and was throttled to its quota. Optimise hot loops, reduce parallelism, or "
                  "request a larger CPU budget."],
    "OUTPUT_LIMIT": ["Print less: aggregate results and write a summary instead of one line per iteration."],
    "SECURITY_POLICY_VIOLATION": ["Remove calls to privileged or introspection system calls (ptrace, mount, unshare, "
                                  "bpf, ...). They are denied inside the sandbox by design."],
}
SIGNALS = {134: "SIGABRT", 136: "SIGFPE", 137: "SIGKILL", 139: "SIGSEGV", 132: "SIGILL", 135: "SIGBUS", 143: "SIGTERM", 159: "SIGSYS"}
KEYS = ("summary", "why_ended", "violations", "limits_and_metrics", "observed_facts", "probable_causes",
        "os_mechanism", "outcome", "relationships", "suggestions")


def configured():
    return bool(C.AI_PROVIDER and C.AI_API_KEY and C.AI_MODEL) or bool(C.AI_PROVIDER == "openai_compatible" and C.AI_BASE_URL and C.AI_MODEL)


def public_status():
    return dict(configured=configured(), provider=C.AI_PROVIDER or None, model=C.AI_MODEL or None)


# ---- payload ------------------------------------------------------------------------------------------------------
_REDACT = [(re.compile(r"(?i)\b(api[_-]?key|token|secret|passw(?:or)?d|authorization)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
           (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "[REDACTED]"), (re.compile(r"(?i)bearer\s+\S+"), "Bearer [REDACTED]"),
           (re.compile(r"[A-Za-z0-9+/_\-]{40,}"), "[REDACTED-LONG-TOKEN]"),
           (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+"), r"C:\\Users\\[user]"), (re.compile(r"/home/[^/\s]+"), "/home/[user]")]


def sanitize(text, limit=1200, tail=True):
    t = "".join(ch for ch in (text or "") if ch in "\n\t" or 32 <= ord(ch) < 127 or ord(ch) > 159)
    for rx, rep in _REDACT:
        t = rx.sub(rep, t)
    t = t.strip()
    if len(t) > limit:
        t = ("[...]" + t[-limit:]) if tail else (t[:limit] + "[...]")
    return t


def build_payload(job):
    cfg = job.get("config") or {}
    tel = job.get("telemetry") or {}
    mon = job.get("monitor") or {}
    return dict(
        status=job.get("status"), status_label=job.get("status_label"), primary_reason=job.get("primary_reason"),
        exit_code=job.get("exit_code"), language=job.get("language"),
        entry_point=os.path.basename(job.get("entry_point") or ""), profile=job.get("resource_profile"),
        limits=dict(cpu=cfg.get("cpu"), memory_mb=cfg.get("memory_mb"), runtime_s=cfg.get("time"), pids=cfg.get("pids")),
        metrics=dict(peak_cpu_percent=job.get("cpu_usage"), peak_memory_mb=job.get("peak_memory"),
                     peak_pids=job.get("process_count"), runtime_s=job.get("runtime")),
        telemetry=dict(kernel_counters_available=bool(tel.get("probe_available")), reliability=tel.get("reliability")),
        violations=[{k: v.get(k) for k in ("type", "confidence", "role", "action", "limit", "observed", "unit", "evidence")}
                   for v in (job.get("violations") or [])],
        monitor=dict(failed=mon.get("failed"), restarts=mon.get("restarts"), samples=mon.get("samples_received"),
                     simulated_fault=bool(mon.get("simulated"))),
        queue=dict(rejected=job.get("status") == "REJECTED_CAPACITY"),
        challenge=job.get("challenge_id"), simulated=bool(job.get("simulated")),
        compiler_output_tail=sanitize(job.get("compile_output"), 800),
        stderr_tail=sanitize(job.get("stderr"), 800), stdout_head=sanitize(job.get("stdout"), 300, tail=False))


# ---- rule based ---------------------------------------------------------------------------------------------------
def _fmt(v):
    if v is None:
        return "not measured"
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def rule_based(job):
    cfg = job.get("config") or {}
    viol = job.get("violations") or []
    status, primary = job.get("status"), job.get("primary_reason") or "NONE"
    lang = C.LANGS.get(job.get("language"), {}).get("label", job.get("language"))
    entry = job.get("entry_point") or "the program"
    rt = job.get("runtime")
    limits = (f"{_fmt(cfg.get('cpu'))} CPU, {_fmt(cfg.get('memory_mb'))} MB memory, {_fmt(cfg.get('time'))} s runtime, "
              f"{_fmt(cfg.get('pids'))} PIDs (profile {job.get('resource_profile')}, network disabled)")
    exit_code = job.get("exit_code")
    types = [v["type"] for v in viol]
    first_err = ""
    for line in (job.get("compile_output") or "").splitlines():
        if "error" in line.lower():
            first_err = line.strip()[:240]
            break

    if status == "COMPLETED":
        what = f"The {lang} program {entry} ran to completion and exited with status 0 after {_fmt(rt)} s."
        why = "It finished on its own; no limit ended the run."
        if viol:
            why += " The kernel did enforce limits on part of its work (listed below), but the program handled it and exited normally."
        outcome = "COMPLETED"
    elif status == "COMPILATION_FAILED":
        what = f"The {lang} compiler exited with status {_fmt(job.get('exit_code') if exit_code is not None else None)} so the program never started."
        why = "Compilation failed" + (f": {first_err}" if first_err else " (see the compiler diagnostics).")
        outcome = "FAILED"
    elif status == "RUNTIME_ERROR":
        sig = SIGNALS.get(exit_code)
        what = (f"{entry} started but exited with non-zero status {_fmt(exit_code)}" +
                (f" (killed by {sig})" if sig else "") + f" after {_fmt(rt)} s.")
        why = "The program ended itself with an error; the sandbox did not terminate it."
        outcome = "FAILED"
    elif status == "MONITOR_FAILURE":
        fail = (job.get("monitor") or {}).get("failed") or {}
        what = "Execution supervision was lost, so the job was stopped for safety."
        why = ("The statistics monitor stopped delivering data and could not be restored (" + str(fail.get("detail", "")) +
               "). Because safe supervision could not be maintained the workload was terminated and its container removed.")
        outcome = "TERMINATED"
    elif status == "TERMINATED":
        reasons = {
            "TIME_LIMIT": f"The runtime budget of {_fmt(cfg.get('time'))} s elapsed and the watchdog killed the container.",
            "COMPILE_TIMEOUT": "Compilation exceeded its time budget and the container was killed.",
            "MEMORY_LIMIT": f"The program exceeded the {_fmt(cfg.get('memory_mb'))} MB memory budget and the kernel OOM killer ended it.",
            "OUTPUT_LIMIT": "The program produced more output than the sandbox allows and was killed.",
            "USER_CANCELLED": "The run was cancelled by the user.",
            "SECURITY_POLICY_VIOLATION": "The program issued a system call that the seccomp policy forbids.",
        }
        what = f"{entry} was terminated after {_fmt(rt)} s."
        why = reasons.get(primary, f"Terminated ({primary}).")
        outcome = "TERMINATED"
    elif status == "INTERRUPTED":
        what, why, outcome = "The job was interrupted.", job.get("error") or "The backend restarted or lost the worker.", "TERMINATED"
    elif status == "REJECTED_CAPACITY":
        what = "The job was not started."
        why = job.get("error") or "Host capacity was full."
        outcome = "REJECTED"
    elif status == "CANCELLED":
        what, why, outcome = "The job was cancelled while queued.", "No resources were used.", "TERMINATED"
    elif status in V.ACTIVE_STATES:
        what, why, outcome = "The job is still in progress.", "Not finished yet.", "RUNNING"
    else:
        what, why, outcome = f"The sandbox reported {status}.", job.get("error") or "Internal sandbox error.", "FAILED"

    vlist = []
    for v in viol:
        t = v["type"]
        vlist.append(dict(type=t, confidence=v.get("confidence"), role=v.get("role"),
                          explanation=f"{v.get('evidence')} Limit {_fmt(v.get('limit'))} {v.get('unit') or ''}, observed "
                                      f"{_fmt(v.get('observed'))} {v.get('unit') or ''} ({v.get('confidence')}, action "
                                      f"{v.get('action')}).".replace("  ", " ")))
    rel = "Only one limit was involved." if len(viol) <= 1 else (
        f"{len(viol)} limits were independently observed ({', '.join(types)}). They are reported separately; the "
        "evidence shows each one happened, but it does not by itself prove that one caused another.")
    if "PROCESS_LIMIT" in types and "TIME_LIMIT" in types:
        rel += " The program kept running after process creation was refused until the runtime deadline."
    if "MEMORY_LIMIT" in types and "TIME_LIMIT" in types:
        rel += " Memory pressure and the deadline were both observed; whether memory pressure slowed the program is not determined."
    peaks = (f"peak CPU {_fmt(job.get('cpu_usage'))}% (100% = 1 core), peak memory {_fmt(job.get('peak_memory'))} MB, "
             f"peak processes {_fmt(job.get('process_count'))}, runtime {_fmt(rt)} s")
    tel = job.get("telemetry") or {}
    rel_note = ("Kernel cgroup counters were available (high reliability)." if tel.get("probe_available")
                else "Only Docker statistics (1 Hz) were available, so short spikes may be missed.")
    facts = [f"Status: {job.get('status_label') or status}.", f"Configured limits: {limits}.", f"Measured: {peaks}."]
    facts += [f"{v['type']} - {v.get('evidence')}" for v in viol if v.get("confidence") == "CONFIRMED"]
    probable = [f"{v['type']} (inferred): {v.get('evidence')}" for v in viol if v.get("confidence") != "CONFIRMED"]
    if status == "RUNTIME_ERROR" and "PROCESS_LIMIT" in types:
        probable.append("The non-zero exit is probably the program reacting to refused process creation, but the exit "
                        "reason was not recorded by the sandbox.")
    if status == "COMPLETED" and (job.get("cpu_usage") or 0) < 5 and rt and rt > 1:
        probable.append("Low CPU use over a long run suggests the program mostly waited (sleep or blocking I/O).")
    if status == "TERMINATED" and primary == "TIME_LIMIT" and (job.get("cpu_usage") or 0) >= 80 * (cfg.get("cpu") or 1):
        probable.append("CPU stayed near its quota until the deadline, which is typical of a busy loop or very heavy computation.")
    if first_err and status == "COMPILATION_FAILED":
        probable.append("The first compiler error is the most likely root cause; later errors are often follow-ons.")
    sug = []
    for t in dict.fromkeys(types):
        sug += SUGGEST.get(t, [])
    if status == "COMPILATION_FAILED":
        sug.append("Fix the first reported compiler error and re-run; check that all needed source files and headers were uploaded.")
    if status == "RUNTIME_ERROR" and not viol:
        sug.append("Inspect stderr and the exit status; re-run with additional logging.")
    if status == "MONITOR_FAILURE":
        sug.append("Re-run the job. If it repeats, check the Docker daemon health and the backend logs.")
    if not sug:
        sug.append("No action needed.")
    mech = [f"{t}: {MECHANISM[t]}" for t in dict.fromkeys(types) if t in MECHANISM]
    if status == "MONITOR_FAILURE":
        mech.append("Independent watchdog: a separate thread supervises the stats monitor via a heartbeat; when the "
                    "monitor is lost and cannot be restarted it kills the container (SIGKILL to init -> PID namespace teardown) "
                    "and the worker's finally-block removes it.")
    if not mech:
        mech.append("Isolation: Linux namespaces (PID, mount, network, IPC) plus cgroup v2 limits, dropped capabilities, "
                    "no-new-privileges and seccomp confine the process.")
    return dict(summary=what, why_ended=why, violations=vlist,
                limits_and_metrics=f"Configured: {limits}. Observed: {peaks}. {rel_note}",
                observed_facts=facts, probable_causes=probable, os_mechanism=mech, outcome=outcome,
                relationships=rel, suggestions=list(dict.fromkeys(sug)))


# ---- AI providers --------------------------------------------------------------------------------------------------
SYSTEM = (
    "You explain the outcome of a program run inside an operating-system resource sandbox to a student or developer. "
    "You receive ONLY structured execution metadata (no source code). Rules: separate observed facts from probable causes; "
    "never claim a measurement that is null/absent; resource-budget violations are not proof of malicious behaviour; "
    "explain each violation individually and describe relationships without asserting unsupported causal chains; "
    "name the Linux mechanism (cgroups v2, OOM killer, pids controller, CFS bandwidth, seccomp, namespaces) responsible. "
    "Reply with ONE JSON object only, with keys: summary (string), why_ended (string), violations (array of "
    "{type, explanation}), limits_and_metrics (string), observed_facts (array of strings), probable_causes (array of "
    "strings), os_mechanism (array of strings), outcome (COMPLETED|FAILED|TERMINATED|REJECTED), relationships (string), "
    "suggestions (array of strings). Keep every string under 600 characters.")


def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=C.AI_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _call_provider(payload):
    user = "Execution metadata (JSON):\n" + json.dumps(payload, ensure_ascii=True)
    p = C.AI_PROVIDER
    if p == "anthropic":
        r = _post((C.AI_BASE_URL or "https://api.anthropic.com") + "/v1/messages",
                  {"x-api-key": C.AI_API_KEY, "anthropic-version": "2023-06-01"},
                  dict(model=C.AI_MODEL, max_tokens=1500, system=SYSTEM, messages=[dict(role="user", content=user)]))
        return "".join(b.get("text", "") for b in r.get("content", []))
    if p in ("openai", "openai_compatible"):
        base = (C.AI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        r = _post(base + "/chat/completions", {"Authorization": f"Bearer {C.AI_API_KEY}"},
                  dict(model=C.AI_MODEL, temperature=0.2, messages=[dict(role="system", content=SYSTEM),
                                                                     dict(role="user", content=user)]))
        return r["choices"][0]["message"]["content"]
    if p == "gemini":
        base = (C.AI_BASE_URL or "https://generativelanguage.googleapis.com").rstrip("/")
        r = _post(f"{base}/v1beta/models/{C.AI_MODEL}:generateContent", {"x-goog-api-key": C.AI_API_KEY},
                  dict(systemInstruction=dict(parts=[dict(text=SYSTEM)]),
                       contents=[dict(role="user", parts=[dict(text=user)])]))
        return "".join(x.get("text", "") for x in r["candidates"][0]["content"]["parts"])
    raise ValueError(f"unknown AI_PROVIDER '{p}'")


def _clip(s, n=700):
    return str(s)[:n]


def _validate(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    d = json.loads(m.group(0))
    out = {}
    for k in KEYS:
        if k not in d:
            raise ValueError(f"missing key {k}")
    out["summary"], out["why_ended"] = _clip(d["summary"]), _clip(d["why_ended"])
    out["limits_and_metrics"], out["relationships"] = _clip(d["limits_and_metrics"]), _clip(d["relationships"])
    out["outcome"] = _clip(d["outcome"], 40)
    for k in ("observed_facts", "probable_causes", "os_mechanism", "suggestions"):
        if not isinstance(d[k], list):
            raise ValueError(f"{k} must be a list")
        out[k] = [_clip(x) for x in d[k][:12]]
    if not isinstance(d["violations"], list):
        raise ValueError("violations must be a list")
    out["violations"] = [dict(type=_clip(x.get("type"), 40), explanation=_clip(x.get("explanation")))
                         for x in d["violations"][:12] if isinstance(x, dict)]
    return out


def explain(job, use_ai=True):
    """Return the stored explanation record. Never raises."""
    rb = rule_based(job)
    rec = dict(source="RULE_BASED", generated_at=db.now_iso(), provider=None, model=None, content=rb, ai_attempted=False,
               ai_error=None, retry_available=configured())
    if not use_ai:
        return rec
    if not configured():
        rec["ai_error"] = "AI is not configured (set AI_PROVIDER, AI_API_KEY and AI_MODEL)"
        return rec
    rec["ai_attempted"] = True
    try:
        content = _validate(_call_provider(build_payload(job)))
        rec.update(source="AI", provider=C.AI_PROVIDER, model=C.AI_MODEL, content=content, ai_error=None,
                   fallback_content=rb)
    except Exception as ex:
        msg = f"{type(ex).__name__}: {ex}"
        if C.AI_API_KEY:
            msg = msg.replace(C.AI_API_KEY, "[REDACTED]")
        rec["ai_error"] = sanitize(msg, 300)
    return rec


def store(job_id, rec):
    db.update(job_id, explanation=rec)


def explain_job(job_id, use_ai=True):
    from . import service
    job = service.job_view(job_id)
    if not job:
        return None
    rec = explain(job, use_ai)
    store(job_id, rec)
    return rec


def auto_explain(job_id):
    """Called after a job finishes: always store the offline explanation first, then upgrade with AI in the background."""
    explain_job(job_id, use_ai=False)
    if configured() and C.AI_AUTO:
        threading.Thread(target=explain_job, args=(job_id, True), daemon=True, name=f"ai-{job_id}").start()
