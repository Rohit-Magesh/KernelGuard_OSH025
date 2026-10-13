"""SQLite persistence. JSON-valued columns are (de)serialised here so the rest of the code sees plain dicts."""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DB_PATH

JSON_COLS = {"violations", "files", "config", "plan", "telemetry", "monitor", "explanation", "queue"}
# column -> SQL type/default. New columns are added automatically to older databases (see init()).
COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "created_at": "TEXT", "started_at": "TEXT", "finished_at": "TEXT",
    "language": "TEXT", "filename": "TEXT", "entry_point": "TEXT",
    "resource_profile": "TEXT",
    "cpu_limit": "REAL", "memory_limit": "INTEGER", "pid_limit": "INTEGER", "time_limit": "INTEGER",
    "status": "TEXT", "primary_reason": "TEXT DEFAULT 'NONE'", "exit_code": "INTEGER",
    "cpu_usage": "REAL DEFAULT 0", "peak_memory": "REAL DEFAULT 0", "process_count": "INTEGER DEFAULT 0",
    "runtime": "REAL DEFAULT 0",
    "stdout": "TEXT DEFAULT ''", "stderr": "TEXT DEFAULT ''", "compile_output": "TEXT DEFAULT ''",
    "violation_reason": "TEXT DEFAULT 'NONE'",      # legacy single-string summary (kept for compatibility)
    "violations": "TEXT DEFAULT '[]'", "files": "TEXT DEFAULT '[]'", "config": "TEXT DEFAULT '{}'",
    "plan": "TEXT DEFAULT '{}'", "telemetry": "TEXT DEFAULT '{}'", "monitor": "TEXT DEFAULT '{}'",
    "explanation": "TEXT", "queue": "TEXT DEFAULT '{}'",
    "queue_position": "INTEGER", "challenge_id": "TEXT", "simulated": "INTEGER DEFAULT 0",
    "error": "TEXT",
}
LIST_COLS = [c for c in COLUMNS if c not in ("stdout", "stderr", "compile_output", "telemetry", "plan", "config", "explanation")]
_local = threading.local()


def _conn():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def init():
    with _conn() as c:
        try:
            c.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        cols = ", ".join(f"{k} {v}" for k, v in COLUMNS.items())
        c.execute(f"CREATE TABLE IF NOT EXISTS jobs({cols})")
        have = {r["name"] for r in c.execute("PRAGMA table_info(jobs)")}
        for k, v in COLUMNS.items():
            if k not in have:
                c.execute(f"ALTER TABLE jobs ADD COLUMN {k} {v.replace('PRIMARY KEY', '')}")
        c.execute("CREATE TABLE IF NOT EXISTS job_files(job_id TEXT, path TEXT, data BLOB, PRIMARY KEY(job_id, path))")
        c.execute("""CREATE TABLE IF NOT EXISTS presets(category TEXT PRIMARY KEY, cpu REAL, memory_mb INTEGER,
                     time_limit INTEGER, pids INTEGER, updated_at TEXT)""")
        c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS challenge_runs(id TEXT PRIMARY KEY, challenge_id TEXT, created_at TEXT,
                     finished_at TEXT, status TEXT, result TEXT)""")


def _enc(k, v):
    return json.dumps(v) if k in JSON_COLS and v is not None and not isinstance(v, str) else v


def _dec(row):
    d = dict(row)
    for k in JSON_COLS:
        if k in d and isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


def insert(d):
    d = {k: _enc(k, v) for k, v in d.items()}
    with _conn() as c:
        c.execute(f"INSERT INTO jobs({','.join(d)}) VALUES({','.join('?' * len(d))})", list(d.values()))


def update(job_id, **kw):
    assert set(kw) <= set(COLUMNS), set(kw) - set(COLUMNS)
    kw = {k: _enc(k, v) for k, v in kw.items()}
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {','.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), job_id])


def get(job_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _dec(r) if r else None


def recent(n=50, challenge=False):
    with _conn() as c:
        rows = c.execute(f"SELECT {','.join(LIST_COLS)} FROM jobs ORDER BY created_at DESC LIMIT ?", (n,)).fetchall()
    return [_dec(r) for r in rows]


def jobs_in_states(states):
    q = ",".join("?" * len(states))
    with _conn() as c:
        rows = c.execute(f"SELECT id, status, created_at FROM jobs WHERE status IN ({q}) ORDER BY created_at", list(states)).fetchall()
    return [dict(r) for r in rows]


# ---- project files for queued/active jobs (so queued work survives a backend restart) -------------------------------
def save_files(job_id, files):
    with _conn() as c:
        c.executemany("INSERT OR REPLACE INTO job_files VALUES(?,?,?)", [(job_id, p, d) for p, d in files.items()])


def load_files(job_id):
    with _conn() as c:
        rows = c.execute("SELECT path, data FROM job_files WHERE job_id=?", (job_id,)).fetchall()
    return {r["path"]: bytes(r["data"]) for r in rows}


def drop_files(job_id):
    with _conn() as c:
        c.execute("DELETE FROM job_files WHERE job_id=?", (job_id,))


# ---- presets -------------------------------------------------------------------------------------------------------
def get_presets():
    with _conn() as c:
        rows = c.execute("SELECT * FROM presets").fetchall()
    return {r["category"]: dict(cpu=r["cpu"], memory_mb=r["memory_mb"], time=r["time_limit"], pids=r["pids"],
                                updated_at=r["updated_at"]) for r in rows}


def put_preset(category, cpu, memory_mb, time, pids):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO presets VALUES(?,?,?,?,?,?)", (category, cpu, memory_mb, time, pids, now_iso()))


# ---- meta / challenge runs -----------------------------------------------------------------------------------------
def meta_get(key, default=None):
    with _conn() as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r["value"])
    except ValueError:
        return default


def meta_put(key, value):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))


def save_challenge_run(run_id, challenge_id, created_at, status, result, finished_at=None):
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO challenge_runs VALUES(?,?,?,?,?,?)",
                  (run_id, challenge_id, created_at, finished_at, status, json.dumps(result)))


def get_challenge_run(run_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM challenge_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["result"] = json.loads(d["result"] or "{}")
    return d


def latest_challenge_runs():
    with _conn() as c:
        rows = c.execute("SELECT * FROM challenge_runs WHERE status IN ('PASSED','FAILED','ERROR') ORDER BY created_at DESC").fetchall()
    out = {}
    for r in rows:
        if r["challenge_id"] not in out:
            d = dict(r)
            d["result"] = json.loads(d["result"] or "{}")
            out[r["challenge_id"]] = d
    return out
