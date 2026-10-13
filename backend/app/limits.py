"""Resource-profile resolution. A request is turned into one immutable execution config *before* the job is queued;
nothing downstream mutates it."""
from . import config as C
from . import db
from .validate import Rejection

FIELD_LABEL = dict(cpu="CPU", memory_mb="memory (MB)", time="runtime (s)", pids="PIDs")


def _number(name, raw, cast):
    try:
        v = cast(float(raw)) if cast is int else float(raw)
        if cast is int and float(raw) != v:
            raise ValueError
        return v
    except (TypeError, ValueError):
        raise Rejection("INVALID_LIMIT", f"{FIELD_LABEL[name]} must be a {'whole ' if cast is int else ''}number",
                        [dict(path=name, code="INVALID_LIMIT", reason="not a valid number")])


def validate_limits(cpu, memory_mb, time, pids):
    vals = dict(cpu=_number("cpu", cpu, float), memory_mb=_number("memory_mb", memory_mb, int),
                time=_number("time", time, int), pids=_number("pids", pids, int))
    bad = []
    for k, v in vals.items():
        if not (C.PLATFORM_MIN[k] <= v <= C.PLATFORM_MAX[k]):
            bad.append(dict(path=k, code="LIMIT_OUT_OF_RANGE",
                            reason=f"{FIELD_LABEL[k]} must be between {C.PLATFORM_MIN[k]} and {C.PLATFORM_MAX[k]}"))
    if bad:
        raise Rejection("LIMIT_OUT_OF_RANGE", bad[0]["reason"], bad)
    return vals


def preset_for(language):
    cat = C.LANG_CATEGORY[language]
    saved = db.get_presets().get(cat)
    base = C.PRESET_DEFAULTS[cat]
    src = saved or base
    return cat, dict(cpu=src["cpu"], memory_mb=src["memory_mb"], time=src["time"], pids=src["pids"])


def resolve(language, profile="standard", cpu=None, memory_mb=None, time_limit=None, pids=None, label=None):
    """Return the immutable execution config dict stored with the job."""
    profile = (profile or "standard").lower()
    if profile in C.PROFILES:
        vals, source = dict(C.PROFILES[profile]), f"profile:{profile}"
    elif profile == "preset":
        cat, vals = preset_for(language)
        source = f"preset:{cat}"
    elif profile == "custom":
        if None in (cpu, memory_mb, time_limit, pids):
            raise Rejection("INVALID_LIMIT", "Custom profile needs cpu, memory_mb, time_limit and pids")
        vals, source = validate_limits(cpu, memory_mb, time_limit, pids), "custom"
    else:
        raise Rejection("UNKNOWN_PROFILE", f"Unknown resource profile '{profile}'")
    cfg = dict(profile=label or profile, source=source, cpu=float(vals["cpu"]), memory_mb=int(vals["memory_mb"]),
               time=int(vals["time"]), pids=int(vals["pids"]),
               compile=dict(memory_mb=max(int(vals["memory_mb"]), C.COMPILE_LIMITS["memory_mb"]),
                            pids=max(int(vals["pids"]), C.COMPILE_LIMITS["pids"]), time=C.COMPILE_TIMEOUT),
               network="none")
    return cfg


def save_preset(category, cpu, memory_mb, time, pids):
    if category not in C.PRESET_DEFAULTS:
        raise Rejection("UNKNOWN_PRESET", f"Unknown preset '{category}'", status=404)
    v = validate_limits(cpu, memory_mb, time, pids)
    db.put_preset(category, v["cpu"], v["memory_mb"], v["time"], v["pids"])
    return v


def all_presets():
    saved = db.get_presets()
    out = {}
    for cat, base in C.PRESET_DEFAULTS.items():
        out[cat] = dict(saved.get(cat) or base, saved=cat in saved)
    return out
