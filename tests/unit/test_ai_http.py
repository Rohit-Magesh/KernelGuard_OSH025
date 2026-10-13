"""Exercises the real HTTP code path of the AI client against local mock providers (no external service)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import ai, config as C

GOOD = dict(summary="Mock summary", why_ended="Mock why", violations=[dict(type="TIME_LIMIT", explanation="mock")],
            limits_and_metrics="m", observed_facts=["f"], probable_causes=["p"], os_mechanism=["o"], outcome="TERMINATED",
            relationships="r", suggestions=["s"])
JOB = dict(id="x", status="TERMINATED", status_label="TERMINATED - TIME LIMIT", primary_reason="TIME_LIMIT", exit_code=137,
           language="c", entry_point="a.c", resource_profile="safe", runtime=10, cpu_usage=50, peak_memory=3, process_count=3,
           config=dict(cpu=0.5, memory_mb=256, time=10, pids=20), violations=[], stdout="", stderr="", compile_output="",
           telemetry={}, monitor={})


class H(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        H.seen.append((self.path, dict(self.headers), body))
        if self.path.endswith("/messages"):
            out = {"content": [{"type": "text", "text": json.dumps(GOOD)}]}
        elif self.path.endswith("/chat/completions"):
            out = {"choices": [{"message": {"content": "```json\n" + json.dumps(GOOD) + "\n```"}}]}
        elif "generateContent" in self.path:
            out = {"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}}]}
        else:
            self.send_response(500)
            self.end_headers()
            return
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("provider,path", [("anthropic", "/v1/messages"), ("openai", "/chat/completions"),
                                           ("openai_compatible", "/chat/completions"), ("gemini", "generateContent")])
def test_each_provider_roundtrip(monkeypatch, server, provider, path):
    base = server + ("/v1" if provider in ("openai", "openai_compatible") else "")
    monkeypatch.setattr(C, "AI_PROVIDER", provider)
    monkeypatch.setattr(C, "AI_API_KEY", "test-key-123")
    monkeypatch.setattr(C, "AI_MODEL", "model-x")
    monkeypatch.setattr(C, "AI_BASE_URL", base)
    H.seen.clear()
    rec = ai.explain(JOB)
    assert rec["source"] == "AI" and rec["content"]["summary"] == "Mock summary"
    p, headers, body = H.seen[-1]
    assert path in p
    assert "test-key-123" not in json.dumps(body)                      # key only in the auth header, never the payload
    assert "test-key-123" in json.dumps(headers) and "key=" not in p   # and never in the URL


def test_unreachable_provider_falls_back_quickly(monkeypatch):
    monkeypatch.setattr(C, "AI_PROVIDER", "openai_compatible")
    monkeypatch.setattr(C, "AI_API_KEY", "k")
    monkeypatch.setattr(C, "AI_MODEL", "m")
    monkeypatch.setattr(C, "AI_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(C, "AI_TIMEOUT", 3)
    rec = ai.explain(JOB)
    assert rec["source"] == "RULE_BASED" and rec["ai_attempted"] and rec["ai_error"]