"""KernelGuard HTTP API. Thin layer: validation errors are structured, everything else lives in service.py."""
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from . import ai, challenges, db, entrypoints, limits, report, runner, service
from . import config as C
from . import violations as V
from .scheduler import CapacityFull
from .validate import Rejection, build_project

VERSION = "2.0.0"


@asynccontextmanager
async def lifespan(app):
    service.init()
    yield


app = FastAPI(title="KernelGuard - Hostile-Code Resource Sandbox", version=VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Rejection)
async def _rejection(_, ex: Rejection):
    return JSONResponse(ex.as_dict(), status_code=ex.status)


@app.exception_handler(CapacityFull)
async def _capacity(_, ex: CapacityFull):
    body = {"error": {"code": ex.reason, "message": ex.message, "details": [],
                      "capacity": ex.snapshot, "job_id": getattr(ex, "job_id", None)}}
    return JSONResponse(body, status_code=429 if ex.reason == "CAPACITY_FULL" else 422, headers={"Retry-After": "5"})


def _not_found(what="Job"):
    return Rejection("NOT_FOUND", f"{what} not found", status=404)


async def _read_uploads(files):
    if len(files) > C.MAX_FILES + 5:
        raise Rejection("TOO_MANY_FILES", f"{len(files)} files submitted; limit is {C.MAX_FILES}")
    out, total = [], 0
    for f in files:
        name = f.filename or "file"
        cap = (C.MAX_ARCHIVE_BYTES if name.lower().endswith(".zip") else C.MAX_FILE_BYTES) + 1
        data = await f.read(cap)
        total += len(data)
        if total > 3 * C.MAX_PROJECT_BYTES:
            raise Rejection("UPLOAD_TOO_LARGE", "Request body is too large")
        out.append((name, data))
    return out


# ---- meta ----------------------------------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    snap = service.scheduler().snapshot() if service.scheduler() else {}
    ok = runner.docker_ok()
    return dict(status="ok" if ok else "degraded", docker=ok, version=VERSION, ai=ai.public_status(),
                scheduler={k: snap.get(k) for k in ("active", "pending", "max_active", "max_pending")},
                challenges_enabled=C.CHALLENGES_ENABLED)


@app.get("/api/config")
def get_config():
    return dict(profiles=C.PROFILES, platform_max=C.PLATFORM_MAX, platform_min=C.PLATFORM_MIN,
                limits=dict(max_files=C.MAX_FILES, max_file_bytes=C.MAX_FILE_BYTES, max_project_bytes=C.MAX_PROJECT_BYTES),
                languages={k: dict(label=v["label"], extensions=sorted(v["exts"] | C.DATA_EXTS)) for k, v in C.LANGS.items()},
                presets=limits.all_presets(), preset_categories=C.LANG_CATEGORY,
                admission=dict(max_active=C.MAX_ACTIVE_JOBS, max_pending=C.MAX_PENDING_JOBS), ai=ai.public_status())


@app.get("/api/scheduler")
def get_scheduler():
    s = service.scheduler().snapshot()
    s["timeline"] = [list(t) for t in list(service.scheduler().timeline)[-40:]]
    return s


# ---- presets -------------------------------------------------------------------------------------------------------
@app.get("/api/presets")
def get_presets():
    return limits.all_presets()


@app.put("/api/presets/{category}")
async def put_preset(category: str, body: dict):
    v = limits.save_preset(category, body.get("cpu"), body.get("memory_mb"), body.get("time", body.get("time_limit")), body.get("pids"))
    return dict(category=category, **v, saved=True)


# ---- projects / jobs -----------------------------------------------------------------------------------------------
@app.post("/api/projects/validate")
async def validate_project(files: list[UploadFile] = File(...), language: str = Form(...)):
    uploads = await _read_uploads(files)
    proj = await run_in_threadpool(build_project, uploads, language)
    info = entrypoints.detect(language, proj.files)
    return dict(ok=True, language=language, files=proj.manifest(), total_bytes=proj.total_bytes, file_count=len(proj.files),
                ignored=proj.ignored, entry=info)


@app.post("/api/jobs", status_code=202)
async def create_job(files: list[UploadFile] = File(...), language: str = Form(...), entry_point: str | None = Form(None),
                     profile: str = Form("standard"), cpu: float | None = Form(None), memory_mb: int | None = Form(None),
                     time_limit: int | None = Form(None), pids: int | None = Form(None)):
    uploads = await _read_uploads(files)

    def go():
        proj = build_project(uploads, language)
        cfg = limits.resolve(language, profile, cpu, memory_mb, time_limit, pids)
        jid, state, pos = service.submit_job(proj, language, entry_point or None, cfg)
        return jid

    jid = await run_in_threadpool(go)
    return service.job_view(jid)


@app.get("/api/jobs")
def list_jobs(limit: int = 50):
    return service.history(max(1, min(limit, 200)))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = service.job_view(job_id)
    if not j:
        raise _not_found()
    return j


@app.get("/api/jobs/{job_id}/logs")
def logs(job_id: str):
    j = service.job_view(job_id)
    if not j:
        raise _not_found()
    return dict(stdout=j["stdout"], stderr=j["stderr"], compile_output=j["compile_output"])


@app.post("/api/jobs/{job_id}/cancel")
@app.post("/api/jobs/{job_id}/stop")
def cancel(job_id: str):
    return dict(result=service.cancel_job(job_id))


@app.get("/api/jobs/{job_id}/explanation")
def get_explanation(job_id: str):
    j = service.job_view(job_id)
    if not j:
        raise _not_found()
    if not j["is_final"]:
        raise Rejection("NOT_FINISHED", "The job has not finished yet", status=409)
    return j.get("explanation") or ai.explain_job(job_id, use_ai=False)


@app.post("/api/jobs/{job_id}/explanation")
async def regenerate_explanation(job_id: str, body: dict | None = None):
    j = service.job_view(job_id)
    if not j:
        raise _not_found()
    if not j["is_final"]:
        raise Rejection("NOT_FINISHED", "The job has not finished yet", status=409)
    use_ai = (body or {}).get("use_ai", True)
    return await run_in_threadpool(ai.explain_job, job_id, use_ai)


@app.get("/api/jobs/{job_id}/report")
def get_report(job_id: str):
    j = service.job_view(job_id)
    if not j:
        raise _not_found()
    if not j["is_final"]:
        raise Rejection("NOT_FINISHED", "The report is available after the job has finished", status=409)
    if not j.get("explanation"):
        j["explanation"] = ai.explain_job(job_id, use_ai=False)
    pdf = report.build_pdf(j)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="kernelguard-{job_id}.pdf"'})


# ---- challenges ----------------------------------------------------------------------------------------------------
@app.get("/api/challenges")
def list_challenges():
    return dict(enabled=C.CHALLENGES_ENABLED, challenges=challenges.catalog())


@app.post("/api/challenges/{cid}/run", status_code=202)
def run_challenge(cid: str):
    return dict(run_id=challenges.start_run(cid.upper()))


@app.get("/api/challenges/runs/{run_id}")
def get_run(run_id: str):
    r = challenges.run_view(run_id)
    if not r:
        raise _not_found("Challenge run")
    return r


# ---- security / demos ----------------------------------------------------------------------------------------------
@app.get("/api/security")
def security():
    ver = db.meta_get("security_verified")
    p = (ver or {}).get("probe") or {}
    rl = (ver or {}).get("run_limits") or {}
    sec_name = (ver or {}).get("seccomp_profile")
    custom = C.SECCOMP_PATH and __import__("os").path.exists(C.SECCOMP_PATH)

    def item(key, name, desc, applied, check=None):
        verified = None if not p else bool(check(p)) if check else None
        return dict(key=key, name=name, description=desc, configured=applied, verified=verified)

    controls = [
        item("namespaces", "Process isolation", "Own PID, mount, network and IPC namespaces (one disposable container per job). Verified by the probe seeing itself as a low PID in a private PID namespace.", True,
             lambda p: 0 < p.get("pid", 0) < 200),
        item("nonroot", "Non-root execution", "Workload runs as uid 1000; telemetry probe as uid 1001 so the workload cannot signal it.",
             True, lambda p: p.get("uid") not in (0, None)),
        item("caps", "Dropped capabilities", "cap_drop=ALL: no CAP_* in the effective set.", True, lambda p: p.get("cap_eff") == 0),
        item("nnp", "no-new-privileges", "setuid/file-capability escalation is disabled.", True, lambda p: p.get("no_new_privs") == 1),
        item("seccomp", "Seccomp syscall filter",
             f"Custom deny-list profile {'loaded' if custom else 'NOT found - Docker default profile in force'} (ptrace, mount, unshare, bpf, ...).",
             bool(custom), lambda p: p.get("seccomp") == 2),
        item("rofs", "Read-only root filesystem", "Only the size-capped tmpfs mounts /workspace and /tmp are writable.", True,
             lambda p: p.get("rootfs_read_only") == 1),
        item("net", "Network isolation", "network_mode=none: only the loopback interface exists.", True, lambda p: p.get("net_ifaces") == 0),
        item("nosock", "No Docker socket / host mounts", "The socket is mounted only into the trusted backend.", True,
             lambda p: p.get("docker_sock") == 0),
        item("cgroups", "cgroup v2 limits", "CPU quota, memory (no swap), PIDs and ulimits applied by the kernel "
             "(values read back from the run-phase cgroup).", True,
             lambda p: (rl.get("pids_max") or -1) > 0 and (rl.get("memory_max") or -1) > 0),
    ]
    return dict(controls=controls, verified_at=(ver or {}).get("at"), verified_job=(ver or {}).get("job_id"),
                seccomp_profile=sec_name, probe=p or None,
                limitations=["Containers share the Docker Desktop VM's Linux kernel; this is not a microVM boundary.",
                             "CPU is throttled by quota, never killed; memory and runtime peaks are sampled at 250 ms.",
                             "Verification values are read from inside a real job container at start-up; run any job to refresh."])


@app.get("/api/demos")
def demos():
    p = C.DEMOS_DIR / "catalog.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


@app.get("/api/demos/{demo_id}")
def demo(demo_id: str):
    cat = {d["id"]: d for d in demos()}
    d = cat.get(demo_id)
    if not d:
        raise _not_found("Demo")
    files = []
    for rel in d["files"]:
        path = (C.DEMOS_DIR / d.get("dir", "") / rel).resolve()
        if C.DEMOS_DIR.resolve() not in path.parents:
            continue
        files.append(dict(name=rel.split("/")[-1] if not d.get("keep_paths") else rel, content=path.read_text(encoding="utf-8")))
    return dict(d, files=files)
