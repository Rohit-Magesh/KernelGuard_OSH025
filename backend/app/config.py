"""Central configuration. Everything tunable lives here; environment variables override defaults."""
import os
from pathlib import Path

MB = 1024 * 1024


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _flag(name, default=False):
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


# ---- resource profiles (immutable budgets resolved before a job starts) -----------------------------------------
PROFILES = {
    "safe":     dict(cpu=0.5, memory_mb=256,  time=10, pids=20),
    "standard": dict(cpu=1.0, memory_mb=512,  time=30, pids=50),
    "high":     dict(cpu=2.0, memory_mb=2048, time=60, pids=100),
}
PLATFORM_MAX = dict(cpu=4.0, memory_mb=4096, time=120, pids=200)   # absolute ceilings
PLATFORM_MIN = dict(cpu=0.25, memory_mb=32, time=1, pids=5)

# Per-language-category saved presets (user can edit/save them; these are the first-run defaults)
LANG_CATEGORY = {"c": "c_cpp", "cpp": "c_cpp", "python": "python", "java": "java"}
PRESET_DEFAULTS = {
    "c_cpp":  dict(cpu=1.0, memory_mb=512,  time=30, pids=50),
    "python": dict(cpu=1.0, memory_mb=512,  time=30, pids=50),
    "java":   dict(cpu=1.0, memory_mb=1024, time=30, pids=100),   # the JVM needs many threads (each one is a PID)
}

# Hackathon challenge configurations (trusted fixtures only)
CHALLENGE_CONFIG = {
    "C1_OK":   dict(cpu=0.5, memory_mb=128, time=10, pids=20),
    "C1_BUSY": dict(cpu=0.5, memory_mb=128, time=5,  pids=20),
    "C3":      dict(cpu=0.5, memory_mb=128, time=20, pids=20),    # PID limit 20, 20 s outer watchdog
    "C4":      dict(cpu=0.5, memory_mb=128, time=25, pids=20),
    "C5":      dict(cpu=1.0, memory_mb=128, time=30, pids=20),
}

# ---- upload limits ----------------------------------------------------------------------------------------------
MAX_FILES = 20
MAX_FILE_BYTES = 2 * MB
MAX_PROJECT_BYTES = 10 * MB
MAX_ARCHIVE_BYTES = 10 * MB
MAX_PATH_DEPTH = 6
MAX_NAME_LEN = 64
MAX_COMPRESSION_RATIO = 200          # zip-bomb guard (uncompressed / compressed, per entry)
DATA_EXTS = {".txt", ".csv", ".json", ".dat", ".in"}
IGNORED_ARCHIVE_NAMES = {".ds_store", "thumbs.db"}   # harmless OS junk, silently skipped (and reported)

LANGS = {
    "c":      dict(image="hcs-sandbox-c",      exts={".c", ".h"},                                   label="C"),
    "cpp":    dict(image="hcs-sandbox-cpp",    exts={".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh"},   label="C++"),
    "python": dict(image="hcs-sandbox-python", exts={".py"},                                        label="Python"),
    "java":   dict(image="hcs-sandbox-java",   exts={".java"},                                      label="Java"),
}

# ---- execution --------------------------------------------------------------------------------------------------
MAX_OUTPUT = 256 * 1024              # stdout+stderr bytes tolerated before the job is terminated (OUTPUT_LIMIT)
STORED_OUTPUT = 64 * 1024            # bytes of each stream persisted / displayed
COMPILE_TIMEOUT = _int("COMPILE_TIMEOUT", 60)
COMPILE_LIMITS = dict(memory_mb=768, pids=128)      # toolchain budget, applied to the compile phase only
HARD_DEADLINE_GRACE = 15             # seconds added to compile+run budgets for the absolute watchdog deadline
MONITOR_STALE_SECONDS = float(os.getenv("MONITOR_STALE_SECONDS", "4"))
MONITOR_RESTARTS = _int("MONITOR_RESTARTS", 1)
PROBE_INTERVAL_MS = 250

# ---- admission control (C5) -------------------------------------------------------------------------------------
MAX_ACTIVE_JOBS = _int("MAX_ACTIVE_JOBS", 2)
MAX_PENDING_JOBS = _int("MAX_PENDING_JOBS", 4)
HOST_CPU_BUDGET = os.getenv("HOST_CPU_BUDGET")          # None -> auto (min(docker CPUs, 4))
HOST_MEM_BUDGET_MB = os.getenv("HOST_MEM_BUDGET_MB")    # None -> auto (min(75% of docker memory, 6144))

# ---- challenges / fault injection -------------------------------------------------------------------------------
CHALLENGES_ENABLED = _flag("CHALLENGES_ENABLED", True)
# Fault injection can only be requested by the server-side challenge runner (never by /api/jobs uploads).
# This switch additionally lets an operator disable it entirely.
FAULT_INJECTION_ALLOWED = _flag("FAULT_INJECTION_ALLOWED", True)

# ---- paths ------------------------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = os.getenv("DB_PATH", "sandbox.db")
_default_sc = ROOT / "sandbox" / "seccomp" / "sandbox-seccomp.json"
SECCOMP_PATH = os.getenv("SECCOMP_PATH", str(_default_sc))
DEMOS_DIR = Path(os.getenv("DEMOS_DIR", str(ROOT / "demos")))
if not DEMOS_DIR.exists() and (ROOT / "demos").exists():
    DEMOS_DIR = ROOT / "demos"

# ---- AI explanation (provider agnostic, optional) ---------------------------------------------------------------
AI_PROVIDER = os.getenv("AI_PROVIDER", "").strip().lower()   # anthropic | openai | openai_compatible | gemini | ""
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()
AI_TIMEOUT = _int("AI_TIMEOUT_SECONDS", 25)
AI_AUTO = _flag("AI_AUTO_EXPLAIN", True)                     # call the AI automatically when a job finishes
