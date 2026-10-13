"""Integration tests against a RUNNING backend with Docker (docker compose up). Skipped when the API is unreachable.

    docker compose exec backend python -m pytest /app/tests/integration -v
"""
import io
import os
import time
import zipfile
from pathlib import Path

import pytest
import requests

BASE = os.environ.get("KG_BASE_URL", "http://localhost:8000")
DEMOS = next(p for p in (Path(__file__).resolve().parents[2] / "demos", Path("/app/demos")) if p.exists())

try:
    _h = requests.get(BASE + "/api/health", timeout=3).json()
    UP = _h.get("docker") is True
except Exception:
    UP = False
pytestmark = pytest.mark.skipif(not UP, reason="KernelGuard backend with Docker is not reachable")


def post_job(files, language="c", **form):
    fs = [("files", (n, d)) for n, d in files]
    data = {"language": language, **{k: str(v) for k, v in form.items()}}
    return requests.post(BASE + "/api/jobs", files=fs, data=data, timeout=60)


def finish(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = requests.get(f"{BASE}/api/jobs/{job_id}", timeout=10).json()
        if j["is_final"]:
            return j
        time.sleep(0.4)
    raise AssertionError("job did not finish: " + str(j["status"]))


def run(files, language="c", **form):
    r = post_job(files, language, **form)
    assert r.status_code == 202, r.text
    return finish(r.json()["id"])


def demo(rel):
    p = DEMOS / rel
    return p.name, p.read_bytes()


def container_exists(job_id):
    import docker
    try:
        docker.from_env().containers.get(f"hcs-{job_id}")
        return True
    except Exception:
        return False


def test_health_and_config():
    assert requests.get(BASE + "/api/health").json()["scheduler"]["max_active"] == 2
    cfg = requests.get(BASE + "/api/config").json()
    assert cfg["profiles"]["safe"]["pids"] == 20 and cfg["platform_max"]["memory_mb"] == 4096


def test_single_file_completes_and_cleans_up():
    j = run([demo("c/normal_success.c")], profile="standard")
    assert j["status"] == "COMPLETED" and "500500" in j["stdout"] and j["violations"] == []
    assert not container_exists(j["id"])


def test_multiple_files_uploaded_together_are_all_used():
    files = [demo("c/project_multi_file/main.c"), demo("c/project_multi_file/mathutil.c"), demo("c/project_multi_file/mathutil.h")]
    j = run(files)
    assert j["status"] == "COMPLETED" and "gcd(84, 36) = 12" in j["stdout"]
    assert sorted(f["path"] for f in j["files"]) == ["main.c", "mathutil.c", "mathutil.h"]


def test_zip_project_upload():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in ("main.c", "mathutil.c", "mathutil.h"):
            z.writestr("proj/" + n, (DEMOS / "c/project_multi_file" / n).read_bytes())
    j = run([("proj.zip", buf.getvalue())])
    assert j["status"] == "COMPLETED" and j["entry_point"] == "proj/main.c"


def test_independent_programs_need_selection_then_run_only_the_selected_one():
    names = ["hello_tool.c", "sum_tool.c", "greet.c", "greet.h"]
    files = [(n, (DEMOS / "c/project_two_programs" / n).read_bytes()) for n in names]
    r = post_job(files)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ENTRY_POINT_SELECTION_REQUIRED"
    v = requests.post(BASE + "/api/projects/validate", files=[("files", f) for f in files], data={"language": "c"}).json()
    assert v["entry"]["needs_selection"] and len(v["entry"]["candidates"]) == 2
    j = run(files, entry_point="sum_tool.c")
    assert j["status"] == "COMPLETED" and "5050" in j["stdout"] and j["plan"]["ignored_programs"] == ["hello_tool.c"]


def test_invalid_uploads_are_rejected_without_a_job():
    before = len(requests.get(BASE + "/api/jobs?limit=200").json())
    r = post_job([("../evil.c", b"int main(){}")])
    assert r.status_code == 422 and r.json()["error"]["code"] == "PATH_TRAVERSAL"
    r = post_job([("run.sh", b"echo hi")])
    assert r.status_code == 422 and r.json()["error"]["code"] == "UNSUPPORTED_EXTENSION"
    assert len(requests.get(BASE + "/api/jobs?limit=200").json()) == before


def test_compile_error():
    j = run([demo("c/compile_error.c")])
    assert j["status"] == "COMPILATION_FAILED" and "error" in j["compile_output"] and j["exit_code"] not in (None, 0)


def test_runtime_error_is_not_a_limit_violation():
    j = run([demo("c/runtime_error.c")])
    assert j["status"] == "RUNTIME_ERROR" and j["exit_code"] == 139 and j["violations"] == []


def test_timeout_is_terminated_with_confirmed_time_violation():
    j = run([demo("c/timeout_test.c")], profile="custom", cpu=0.5, memory_mb=128, time_limit=3, pids=20)
    assert j["status"] == "TERMINATED" and j["primary_reason"] == "TIME_LIMIT"
    assert j["violations"][0]["confidence"] == "CONFIRMED" and 3 <= j["runtime"] < 8
    assert not container_exists(j["id"])


def test_memory_limit():
    j = run([demo("c/memory_test.c")], profile="safe")
    assert j["status"] == "TERMINATED" and j["primary_reason"] == "MEMORY_LIMIT"
    v = [x for x in j["violations"] if x["type"] == "MEMORY_LIMIT"][0]
    assert v["action"] == "KILLED_BY_KERNEL" and v["confidence"] == "CONFIRMED"


def test_pid_limit_is_denied_not_terminated():
    j = run([demo("challenges/c3_pid_abuse.c")], profile="custom", cpu=0.5, memory_mb=128, time_limit=20, pids=20)
    v = [x for x in j["violations"] if x["type"] == "PROCESS_LIMIT"][0]
    assert j["status"] == "COMPLETED" and v["action"] == "DENIED" and j["process_count"] <= 20


def test_multiple_simultaneous_violations_are_all_reported():
    j = run([demo("c/multi_violation.c")], profile="safe")
    kinds = {v["type"] for v in j["violations"]}
    assert {"TIME_LIMIT", "PROCESS_LIMIT", "CPU_LIMIT"} <= kinds and j["primary_reason"] == "TIME_LIMIT"
    assert j["violation_reason"].count("+") >= 2


def test_seccomp_and_output_limit():
    j = run([demo("c/seccomp_test.c")])
    assert j["status"] == "TERMINATED" and j["primary_reason"] == "SECURITY_POLICY_VIOLATION"
    j = run([demo("c/output_flood.c")])
    assert j["primary_reason"] == "OUTPUT_LIMIT" and len(j["stdout"]) <= 70000


def test_python_and_java_and_cpp():
    assert run([demo("python/hello.py")], "python")["status"] == "COMPLETED"
    assert run([demo("cpp/hello.cpp")], "cpp")["status"] == "COMPLETED"
    jf = [demo("java/project/Main.java"), demo("java/project/Greeter.java")]
    assert run(jf, "java")["status"] == "COMPLETED"


def test_security_controls_are_verified_from_inside_the_container():
    j = run([demo("python/hello.py")], "python")
    sv = j["telemetry"]["security_verified"]
    assert sv["cap_eff"] == 0 and sv["no_new_privs"] == 1 and sv["seccomp"] == 2
    assert sv["rootfs_read_only"] == 1 and sv["net_ifaces"] == 0 and sv["docker_sock"] == 0
    assert j["telemetry"]["limits_verified"]["pids_max"] == j["config"]["pids"]


def test_presets_roundtrip_and_apply():
    r = requests.put(BASE + "/api/presets/java", json=dict(cpu=1.5, memory_mb=768, time=40, pids=90))
    assert r.status_code == 200
    assert requests.get(BASE + "/api/presets").json()["java"]["memory_mb"] == 768
    j = run([demo("java/Main.java")], "java", profile="preset")
    assert j["config"]["memory_mb"] == 768 and j["config"]["source"] == "preset:java"
    assert requests.put(BASE + "/api/presets/java", json=dict(cpu=9, memory_mb=768, time=40, pids=90)).status_code == 422


def test_explanation_report_and_history():
    j = run([demo("c/timeout_test.c")], profile="custom", cpu=0.5, memory_mb=128, time_limit=2, pids=20)
    for _ in range(20):
        ex = requests.get(f"{BASE}/api/jobs/{j['id']}/explanation").json()
        if ex:
            break
    assert ex["source"] in ("RULE_BASED", "AI") and ex["content"]["why_ended"]
    pdf = requests.get(f"{BASE}/api/jobs/{j['id']}/report")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and pdf.headers["content-type"] == "application/pdf"
    hist = requests.get(BASE + "/api/jobs?limit=20").json()
    assert j["id"] in [h["id"] for h in hist]


def test_cancel_running_job():
    r = post_job([demo("c/timeout_test.c")], profile="standard")
    jid = r.json()["id"]
    for _ in range(60):
        if requests.get(f"{BASE}/api/jobs/{jid}").json()["status"] == "RUNNING":
            break
        time.sleep(0.3)
    assert requests.post(f"{BASE}/api/jobs/{jid}/cancel").json()["result"] == "stopping"
    j = finish(jid, 30)
    assert j["status"] == "TERMINATED" and j["primary_reason"] == "USER_CANCELLED" and not container_exists(jid)


def test_unknown_job_is_404_with_structured_error():
    r = requests.get(BASE + "/api/jobs/doesnotexist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


def _challenge(cid, timeout=240):
    run_id = requests.post(f"{BASE}/api/challenges/{cid}/run").json()["run_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE}/api/challenges/runs/{run_id}").json()
        if r["status"] != "RUNNING":
            return r
        time.sleep(1)
    raise AssertionError("challenge timed out")


@pytest.mark.parametrize("cid", ["C1", "C2", "C3", "C4", "C5"])
def test_official_challenge(cid):
    r = _challenge(cid)
    failed = [c for c in r["result"]["checks"] if not c["passed"]]
    assert r["status"] == "PASSED", failed