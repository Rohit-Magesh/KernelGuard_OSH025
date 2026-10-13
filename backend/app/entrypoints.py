"""Entry-point detection and build planning.

A *project* may be one program built from many files, or several independent programs that happen to be uploaded
together. We never glue unrelated `main()` files together: if more than one entry point exists the caller must pick one,
and only that program's dependency closure is compiled."""
import os
import posixpath
import re

from . import config as C
from .validate import Rejection

C_SRC = {".c"}
CPP_SRC = {".cpp", ".cc", ".cxx"}
HEADERS = {".h", ".hpp", ".hh"}

_STRIP = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
_C_MAIN = re.compile(r'(?<![\w.>:])(?:(?:int|void)\s+)?main\s*\([^;{}]*\)\s*\{')
_JAVA_MAIN = re.compile(r'public\s+static\s+void\s+main\s*\(\s*(?:final\s+)?String\s*(?:\[\s*\]\s*\w+|\.\.\.\s*\w+|\w+\s*\[\s*\])')
_JAVA_CLASS = re.compile(r'\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)')
_JAVA_PKG = re.compile(r'^\s*package\s+([\w.]+)\s*;', re.M)
_PY_MAIN = re.compile(r'if\s+__name__\s*==\s*["\']__main__["\']')
_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.M)


def _text(b):
    return b.decode("utf-8", "replace")


def _strip(src):
    """Blank out comments and string/char literals so `main(` inside them cannot be mistaken for a definition."""
    return _STRIP.sub(lambda m: m.group(0)[0] * 2 if m.group(0)[0] in "\"'" else " ", src)


def has_c_main(data):
    return bool(_C_MAIN.search(_strip(_text(data))))


def java_main_class(path, data):
    """Fully-qualified main class if this file declares a `public static void main(String[])`, else None."""
    src = _strip(_text(data))
    m = _JAVA_MAIN.search(src)
    if not m:
        return None
    classes = [c for c in _JAVA_CLASS.finditer(src[:m.start()])]
    stem = os.path.splitext(posixpath.basename(path))[0]
    name = classes[-1].group(1) if classes else stem
    pkg = _JAVA_PKG.search(src)
    return (pkg.group(1) + "." if pkg else "") + name


def _ext(p):
    return os.path.splitext(p)[1].lower()


def detect(language, files):
    """Return {candidates:[{path,label}], default, needs_selection, kind}. Pure function over {path: bytes}."""
    cands, default = [], None
    if language in ("c", "cpp"):
        exts = C_SRC if language == "c" else CPP_SRC
        srcs = sorted(p for p in files if _ext(p) in exts)
        mains = [p for p in srcs if has_c_main(files[p])]
        cands = [dict(path=p, label=p) for p in mains]
        default = mains[0] if len(mains) == 1 else None
        support = [p for p in srcs if p not in mains]
        kind = "single-program" if len(mains) == 1 else ("multiple-programs" if len(mains) > 1 else "no-entry-point")
        return dict(candidates=cands, default=default, needs_selection=len(mains) > 1, kind=kind, support=support)
    if language == "python":
        pys = sorted(p for p in files if _ext(p) == ".py")
        guarded = [p for p in pys if _PY_MAIN.search(_strip_py(files[p]))]
        if "main.py" in pys:
            default, cands = "main.py", [dict(path=p, label=p) for p in dict.fromkeys(["main.py"] + guarded)]
        elif len(guarded) == 1:
            default, cands = guarded[0], [dict(path=guarded[0], label=guarded[0])]
        elif len(guarded) > 1:
            cands = [dict(path=p, label=p) for p in guarded]
        elif len(pys) == 1:
            default, cands = pys[0], [dict(path=pys[0], label=pys[0])]
        else:
            cands = [dict(path=p, label=p) for p in pys]
        kind = "single-program" if default else ("multiple-programs" if len(cands) > 1 else "no-entry-point")
        return dict(candidates=cands, default=default, needs_selection=default is None and len(cands) > 1, kind=kind, support=[])
    if language == "java":
        found = []
        for p in sorted(files):
            if _ext(p) == ".java":
                cls = java_main_class(p, files[p])
                if cls:
                    found.append((p, cls))
        cands = [dict(path=p, label=cls, **{"class": cls}) for p, cls in found]
        main = next((c for c in cands if c["class"] == "Main"), None)
        default = main["path"] if main else (cands[0]["path"] if len(cands) == 1 else None)
        kind = "single-program" if default else ("multiple-programs" if len(cands) > 1 else "no-entry-point")
        return dict(candidates=cands, default=default, needs_selection=default is None and len(cands) > 1, kind=kind, support=[])
    return dict(candidates=[], default=None, needs_selection=False, kind="no-entry-point", support=[])


def _strip_py(b):
    return re.sub(r'#[^\n]*', '', _text(b))


def _resolve_include(from_path, inc, files):
    for base in (posixpath.dirname(from_path), ""):
        cand = posixpath.normpath(posixpath.join(base, inc))
        if cand in files:
            return cand
    return None


def _closure(entry, srcs, mains, files):
    """Source files needed to link `entry`: follow local #includes, then pair each header with same-stem sources."""
    by_stem = {}
    for s in srcs:
        by_stem.setdefault(os.path.splitext(posixpath.basename(s))[0], []).append(s)
    todo, seen_files, used = [entry], {entry}, [entry]
    while todo:
        cur = todo.pop()
        for inc in _INCLUDE.findall(_text(files[cur])):
            h = _resolve_include(cur, inc, files)
            if not h or h in seen_files:
                continue
            seen_files.add(h)
            if _ext(h) in HEADERS:
                stem = os.path.splitext(posixpath.basename(h))[0]
                for s in by_stem.get(stem, []):
                    if s not in mains and s not in seen_files:
                        seen_files.add(s)
                        used.append(s)
                        todo.append(s)
                todo.append(h)
            elif h in srcs and h not in mains:
                used.append(h)
                todo.append(h)
    return used


def plan(language, files, entry_point=None):
    """Build the compile/run plan. Raises Rejection (422/409) when the entry point is missing or ambiguous."""
    info = detect(language, files)
    cand_paths = [c["path"] for c in info["candidates"]]
    entry = entry_point or info["default"]
    if entry_point and entry_point not in cand_paths:
        # permit Java class names and python/c paths that exist but were not auto-detected only if they are real sources
        match = next((c for c in info["candidates"] if c.get("class") == entry_point), None)
        if match:
            entry = match["path"]
        else:
            raise Rejection("INVALID_ENTRY_POINT", f"'{entry_point}' is not a detected entry point",
                            [dict(path=p, code="CANDIDATE", reason="available entry point") for p in cand_paths])
    if language in ("c", "cpp"):
        if info["kind"] == "no-entry-point":
            raise Rejection("NO_ENTRY_POINT", "No main() function was found in the uploaded source files")
        if entry is None:
            raise Rejection("ENTRY_POINT_SELECTION_REQUIRED",
                            f"{len(cand_paths)} independent programs found; select the one to run",
                            [dict(path=p, code="CANDIDATE", reason="contains main()") for p in cand_paths], status=409)
        exts = C_SRC if language == "c" else CPP_SRC
        srcs = sorted(p for p in files if _ext(p) in exts)
        mains = set(cand_paths)
        if len(mains) == 1:
            used, fallback = srcs, None
        else:
            used = _closure(entry, set(srcs), mains, files)
            others = [s for s in srcs if s not in mains]
            full = [entry] + [s for s in others]
            fallback = full if sorted(full) != sorted(used) else None
        incdirs = sorted({posixpath.dirname(p) for p in files if posixpath.dirname(p)})
        cc = ["gcc", "-O2", "-pipe"] if language == "c" else ["g++", "-O2", "-pipe", "-std=c++17"]
        build = lambda srcs_: cc + ["-I."] + [f"-I{d}" for d in incdirs] + ["-o", "program", *srcs_] + (["-lm"] if language == "c" else [])
        attempts = [build(used)] + ([build(fallback)] if fallback else [])
        return dict(language=language, entry=entry, sources=used, fallback_sources=fallback, compile=attempts,
                    run=["./program"], kind=info["kind"],
                    ignored_programs=sorted(m for m in mains if m != entry))
    if language == "python":
        if entry is None:
            if info["kind"] == "no-entry-point":
                raise Rejection("NO_ENTRY_POINT", "No Python source file was found")
            raise Rejection("ENTRY_POINT_SELECTION_REQUIRED", f"{len(cand_paths)} runnable scripts found; select the one to run",
                            [dict(path=p, code="CANDIDATE", reason="script") for p in cand_paths], status=409)
        return dict(language=language, entry=entry, sources=sorted(p for p in files if _ext(p) == ".py"), compile=[],
                    run=["python", "-u", entry], kind=info["kind"], ignored_programs=[])
    if language == "java":
        if info["kind"] == "no-entry-point":
            raise Rejection("NO_ENTRY_POINT", "No class with public static void main(String[]) was found")
        if entry is None:
            raise Rejection("ENTRY_POINT_SELECTION_REQUIRED", f"{len(cand_paths)} classes with main() found; select the one to run",
                            [dict(path=c["path"], code="CANDIDATE", reason=c["class"]) for c in info["candidates"]], status=409)
        cls = next(c["class"] for c in info["candidates"] if c["path"] == entry)
        srcs = sorted(p for p in files if _ext(p) == ".java")
        jvm = ["-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1", "-XX:-UsePerfData", "-Xss512k", "-XX:MaxRAMPercentage=70"]
        return dict(language=language, entry=entry, main_class=cls, sources=srcs,
                    compile=[["javac", "-J-XX:+UseSerialGC", "-J-Xss512k", "-d", "out", *srcs]],
                    run=["java", *jvm, "-cp", "out", cls], kind=info["kind"],
                    ignored_programs=sorted(c["path"] for c in info["candidates"] if c["path"] != entry))
    raise Rejection("UNSUPPORTED_LANGUAGE", language)
