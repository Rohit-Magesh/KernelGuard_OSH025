"""Test bootstrap: makes the backend package importable both in the repo and inside the backend image."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
for cand in (os.path.join(HERE, "..", "backend"), os.path.join(HERE, "..")):
    if os.path.isdir(os.path.join(cand, "app")):
        sys.path.insert(0, os.path.abspath(cand))
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="kgtest-"), "test.db"))
for cand in (os.path.join(HERE, "..", "demos"), "/app/demos"):
    if os.path.isdir(cand):
        os.environ.setdefault("DEMOS_DIR", os.path.abspath(cand))