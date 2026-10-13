import json

import pytest

from app import ai, config as C, db, limits, report
from app.validate import Rejection


@pytest.fixture(autouse=True, scope="module")
def _db():
    db.init()


def test_profiles_resolve_to_immutable_configs():
    c = limits.resolve("c", "safe")
    assert (c["cpu"], c["memory_mb"], c["time"], c["pids"]) == (0.5, 256, 10, 20)
    assert limits.resolve("c", "high")["memory_mb"] == 2048
    assert c["compile"]["pids"] >= 128 and c["network"] == "none"


@pytest.mark.parametrize("kw", [dict(cpu=5, memory_mb=512, time_limit=10, pids=20), dict(cpu=1, memory_mb=8192, time_limit=10, pids=20),
                                dict(cpu=1, memory_mb=512, time_limit=500, pids=20), dict(cpu=1, memory_mb=512, time_limit=10, pids=900),
                                dict(cpu="abc", memory_mb=512, time_limit=10, pids=20)])
def test_custom_limits_validated(kw):
    with pytest.raises(Rejection):
        limits.resolve("c", "custom", **kw)


def test_custom_ok_and_unknown_profile():
    assert limits.resolve("c", "custom", cpu=4, memory_mb=4096, time_limit=120, pids=200)["pids"] == 200
    with pytest.raises(Rejection):
        limits.resolve("c", "turbo")


def test_presets_per_language_category_persist():
    limits.save_preset("python", 2, 1024, 45, 80)
    c = limits.resolve("python", "preset")
    assert (c["cpu"], c["memory_mb"], c["time"], c["pids"]) == (2.0, 1024, 45, 80) and c["source"] == "preset:python"
    assert limits.resolve("c", "preset")["source"] == "preset:c_cpp"          # other categories untouched
    assert limits.all_presets()["python"]["saved"] is True


JOB = dict(id="abc123", status="TERMINATED", status_label="TERMINATED - TIME LIMIT", primary_reason="TIME_LIMIT", exit_code=137,
           language="c", entry_point="loop.c", resource_profile="safe", runtime=10.1, cpu_usage=50, peak_memory=3, process_count=20,
           config=dict(cpu=0.5, memory_mb=256, time=10, pids=20, profile="safe", source="profile:safe", compile=dict(time=60, memory_mb=1024, pids=128)),
           violations=[dict(type="TIME_LIMIT", confidence="CONFIRMED", role="PRIMARY", action="TERMINATED", limit=10, observed=10.1,
                            unit="s", evidence="deadline", t_offset=10.1, source="watchdog"),
                       dict(type="MEMORY_LIMIT", confidence="PROBABLE", role="CONCURRENT", action="OBSERVED", limit=256, observed=250,
                            unit="MB", evidence="oom text", t_offset=None, source="program-output")],
           stdout="hi\n", stderr="", compile_output="", files=[dict(path="loop.c", size=10)], plan={}, telemetry={}, monitor={}, created_at="t")


def test_rule_based_explains_every_violation_and_separates_fact_from_inference():
    c = ai.rule_based(JOB)
    assert [v["type"] for v in c["violations"]] == ["TIME_LIMIT", "MEMORY_LIMIT"]
    assert any("MEMORY_LIMIT (inferred)" in p for p in c["probable_causes"])
    assert not any("MEMORY_LIMIT" in f for f in c["observed_facts"])
    assert "malicious" not in json.dumps(c).lower().replace("not that it is malicious", "")


def test_explain_without_provider_is_rule_based(monkeypatch):
    monkeypatch.setattr(C, "AI_PROVIDER", "")
    monkeypatch.setattr(C, "AI_API_KEY", "")
    rec = ai.explain(JOB)
    assert rec["source"] == "RULE_BASED" and rec["ai_attempted"] is False and "not configured" in rec["ai_error"]


def _configure(monkeypatch):
    monkeypatch.setattr(C, "AI_PROVIDER", "openai_compatible")
    monkeypatch.setattr(C, "AI_API_KEY", "sk-secret-123456")
    monkeypatch.setattr(C, "AI_MODEL", "m")
    monkeypatch.setattr(C, "AI_BASE_URL", "http://127.0.0.1:9")


def test_ai_success_is_labelled_ai(monkeypatch):
    _configure(monkeypatch)
    good = dict(summary="s", why_ended="w", violations=[dict(type="TIME_LIMIT", explanation="e")], limits_and_metrics="l",
                observed_facts=["f"], probable_causes=[], os_mechanism=["m"], outcome="TERMINATED", relationships="r", suggestions=["x"])
    monkeypatch.setattr(ai, "_call_provider", lambda payload: "Sure!\n" + json.dumps(good))
    rec = ai.explain(JOB)
    assert rec["source"] == "AI" and rec["content"]["summary"] == "s" and rec["model"] == "m"


@pytest.mark.parametrize("reply", ["not json at all", '{"summary": "only"}', '{"summary":"a","why_ended":"b","violations":"x"}'])
def test_ai_garbage_falls_back_and_hides_key(monkeypatch, reply):
    _configure(monkeypatch)
    monkeypatch.setattr(ai, "_call_provider", lambda payload: reply)
    rec = ai.explain(JOB)
    assert rec["source"] == "RULE_BASED" and rec["ai_attempted"] and rec["retry_available"]
    assert "sk-secret" not in json.dumps(rec)


def test_ai_exception_falls_back(monkeypatch):
    _configure(monkeypatch)

    def boom(payload):
        raise TimeoutError("timed out calling sk-secret-123456")
    monkeypatch.setattr(ai, "_call_provider", boom)
    rec = ai.explain(JOB)
    assert rec["source"] == "RULE_BASED" and "sk-secret-123456" not in json.dumps(rec)


def test_payload_never_contains_source_or_secrets():
    job = dict(JOB, stderr="token=abcd1234 Authorization: Bearer xyz C:\\Users\\alice\\proj", compile_output="x" * 5000)
    p = ai.build_payload(job)
    blob = json.dumps(p)
    assert "alice" not in blob and "abcd1234" not in blob and len(p["compiler_output_tail"]) < 900
    assert "files" not in p and "source" not in p


def test_pdf_builds_with_and_without_ai_explanation():
    pdf = report.build_pdf(dict(JOB))
    assert pdf[:5] == b"%PDF-" and len(pdf) > 3000
    job2 = dict(JOB, explanation=ai.explain(JOB), status="COMPLETED", violations=[], status_label="COMPLETED", stdout="caf\u00e9 \u2713\n")
    assert report.build_pdf(job2)[:5] == b"%PDF-"