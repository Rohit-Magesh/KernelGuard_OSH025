"""Upload / archive validation. Runs entirely in memory *before* any container exists; nothing is extracted to the host.

Every rejection is a structured `Rejection` carrying a stable machine code plus per-entry details."""
import io
import os
import re
import stat
import zipfile

from . import config as C


class Rejection(Exception):
    """Request rejected before execution. `status` is the HTTP status the API should answer with."""

    def __init__(self, code, message, details=None, status=422):
        super().__init__(message)
        self.code, self.message, self.details, self.status = code, message, details or [], status

    def as_dict(self):
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")        # no leading '.', '-', no spaces / shell metacharacters
DRIVE_RE = re.compile(r"^[A-Za-z]:")


def normalize_path(raw):
    """Return (safe_relative_posix_path, None) or (None, (code, reason)). Never raises."""
    if not isinstance(raw, str) or raw == "":
        return None, ("EMPTY_PATH", "empty file name")
    if "\x00" in raw or any(ord(ch) < 32 for ch in raw):
        return None, ("INVALID_CHARACTERS", "control characters in path")
    p = raw.replace("\\", "/")                               # treat Windows separators as separators
    if p.startswith("//"):
        return None, ("WINDOWS_ABSOLUTE_PATH", "UNC / network path")
    if DRIVE_RE.match(p):
        return None, ("WINDOWS_ABSOLUTE_PATH", "drive-prefixed Windows path")
    if p.startswith("/"):
        return None, ("ABSOLUTE_PATH", "absolute path")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts:
        return None, ("EMPTY_PATH", "empty file name")
    if ".." in parts:
        return None, ("PATH_TRAVERSAL", "path escapes the project directory ('..' component)")
    if len(parts) > C.MAX_PATH_DEPTH:
        return None, ("PATH_TOO_DEEP", f"more than {C.MAX_PATH_DEPTH} path components")
    for x in parts:
        if len(x) > C.MAX_NAME_LEN:
            return None, ("NAME_TOO_LONG", f"component longer than {C.MAX_NAME_LEN} characters")
        if not NAME_RE.match(x):
            return None, ("UNSAFE_NAME", f"unsupported characters in '{x}' (allowed: letters, digits, _ . -)")
    return "/".join(parts), None


class Project:
    """A validated set of files held in memory: {relative_path: bytes}."""

    def __init__(self, files, ignored=None):
        self.files = files
        self.ignored = ignored or []

    @property
    def total_bytes(self):
        return sum(len(d) for d in self.files.values())

    def manifest(self):
        return [dict(path=p, size=len(d)) for p, d in sorted(self.files.items())]


def _allowed_exts(language):
    return C.LANGS[language]["exts"] | C.DATA_EXTS


def _check_entry(path, size, language, problems):
    ext = os.path.splitext(path)[1].lower()
    if ext not in _allowed_exts(language):
        hint = [k for k, v in C.LANGS.items() if ext in v["exts"] and k != language]
        msg = f"file type '{ext or '(none)'}' is not allowed for {C.LANGS[language]['label']}"
        if hint:
            msg += f" (looks like {C.LANGS[hint[0]]['label']} - change the language?)"
        problems.append(dict(path=path, code="UNSUPPORTED_EXTENSION", reason=msg))
    if size > C.MAX_FILE_BYTES:
        problems.append(dict(path=path, code="FILE_TOO_LARGE",
                             reason=f"{size} bytes exceeds the {C.MAX_FILE_BYTES // 1024} KB per-file limit"))


def _expand_zip(name, data, problems, language):
    """Validate every archive entry from metadata first, then read contents with a hard size cap
    (declared sizes are never trusted). Returns ([(path, bytes)], ignored_names)."""
    if len(data) > C.MAX_ARCHIVE_BYTES:
        problems.append(dict(path=name, code="ARCHIVE_TOO_LARGE",
                             reason=f"archive is {len(data)} bytes; limit is {C.MAX_ARCHIVE_BYTES // (1024 * 1024)} MB"))
        return [], []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, NotImplementedError, OSError, ValueError) as ex:
        problems.append(dict(path=name, code="INVALID_ARCHIVE", reason=f"not a readable ZIP file ({ex})"))
        return [], []
    entries, ignored, seen = [], [], {}
    file_infos = [i for i in infos if not i.is_dir()]
    if len(file_infos) > C.MAX_FILES:
        problems.append(dict(path=name, code="TOO_MANY_FILES",
                             reason=f"archive contains {len(file_infos)} files; limit is {C.MAX_FILES}"))
    declared = sum(i.file_size for i in file_infos)
    if declared > C.MAX_PROJECT_BYTES:
        problems.append(dict(path=name, code="ARCHIVE_EXPANDS_TOO_LARGE",
                             reason=f"declared uncompressed size {declared} bytes exceeds {C.MAX_PROJECT_BYTES // (1024 * 1024)} MB"))
    for i in infos:
        raw = i.filename
        segs = [s for s in raw.replace("\\", "/").split("/") if s not in ("", ".")]
        if (segs and ".." not in segs and not raw.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", raw)
                and (segs[0] == "__MACOSX" or segs[-1].lower() in C.IGNORED_ARCHIVE_NAMES)):
            if not i.is_dir():
                ignored.append(raw)                     # harmless OS metadata: skipped, never extracted
            continue
        path, bad = normalize_path(raw.rstrip("/") if i.is_dir() else raw)
        if bad:
            problems.append(dict(path=raw, code=bad[0], reason=bad[1]))
            continue
        if i.is_dir():
            continue
        mode = (i.external_attr >> 16) & 0xFFFF
        ftype = stat.S_IFMT(mode)
        if ftype == stat.S_IFLNK:
            problems.append(dict(path=raw, code="SYMLINK", reason="symbolic links are not allowed in archives"))
            continue
        if ftype not in (0, stat.S_IFREG):
            problems.append(dict(path=raw, code="SPECIAL_FILE", reason="device/FIFO/socket entries are not allowed"))
            continue
        if i.flag_bits & 0x1:
            problems.append(dict(path=raw, code="ENCRYPTED_ENTRY", reason="encrypted entries cannot be inspected"))
            continue
        key = path.lower()
        if key in seen:
            code = "DUPLICATE_PATH" if seen[key] == path else "CASE_CONFLICT"
            problems.append(dict(path=raw, code=code, reason=f"conflicts with earlier entry '{seen[key]}'"))
            continue
        seen[key] = path
        if language:
            _check_entry(path, i.file_size, language, problems)
        entries.append((path, i))
    # a path used both as a file and as a directory prefix is a conflict
    names = {p.lower() for p, _ in entries}
    for p, _ in entries:
        parts = p.lower().split("/")
        for k in range(1, len(parts)):
            if "/".join(parts[:k]) in names:
                problems.append(dict(path=p, code="PATH_CONFLICT", reason="file path is also used as a directory"))
                break
    if problems:
        return [], ignored
    out, total = [], 0
    for path, i in entries:
        with zf.open(i) as fh:
            blob = fh.read(C.MAX_FILE_BYTES + 1)             # bounded: a lying header cannot make us inflate more
        if len(blob) > C.MAX_FILE_BYTES:
            problems.append(dict(path=path, code="FILE_TOO_LARGE",
                                 reason=f"expands beyond the {C.MAX_FILE_BYTES // 1024} KB per-file limit"))
            continue
        if i.compress_size and len(blob) / max(i.compress_size, 1) > C.MAX_COMPRESSION_RATIO and len(blob) > 64 * 1024:
            problems.append(dict(path=path, code="COMPRESSION_BOMB", reason="suspicious compression ratio"))
            continue
        total += len(blob)
        if total > C.MAX_PROJECT_BYTES:
            problems.append(dict(path=name, code="ARCHIVE_EXPANDS_TOO_LARGE",
                                 reason=f"uncompressed content exceeds {C.MAX_PROJECT_BYTES // (1024 * 1024)} MB"))
            break
        out.append((path, blob))
    return out, ignored


def build_project(uploads, language):
    """uploads: [(filename, bytes)]. Returns a Project or raises Rejection (nothing is written anywhere)."""
    if language not in C.LANGS:
        raise Rejection("UNSUPPORTED_LANGUAGE", f"Unsupported language '{language}'. Choose one of: {', '.join(C.LANGS)}")
    if not uploads:
        raise Rejection("NO_FILES", "No files were uploaded")
    if len(uploads) > C.MAX_FILES + 5:
        raise Rejection("TOO_MANY_FILES", f"{len(uploads)} files submitted; limit is {C.MAX_FILES}")
    problems, files, ignored, seen = [], {}, [], {}

    def add(path, data):
        key = path.lower()
        if key in seen:
            problems.append(dict(path=path, code="DUPLICATE_PATH", reason=f"duplicate of '{seen[key]}'"))
            return
        seen[key] = path
        files[path] = data

    for name, data in uploads:
        if str(name).lower().endswith(".zip"):
            got, ign = _expand_zip(name, data, problems, language)
            ignored += ign
            for p, d in got:
                add(p, d)
        else:
            path, bad = normalize_path(name)
            if bad:
                problems.append(dict(path=name, code=bad[0], reason=bad[1]))
                continue
            _check_entry(path, len(data), language, problems)
            add(path, data)
    if len(files) > C.MAX_FILES:
        problems.append(dict(path="(project)", code="TOO_MANY_FILES",
                             reason=f"{len(files)} files in project; limit is {C.MAX_FILES}"))
    total = sum(len(d) for d in files.values())
    if total > C.MAX_PROJECT_BYTES:
        problems.append(dict(path="(project)", code="PROJECT_TOO_LARGE",
                             reason=f"{total} bytes in project; limit is {C.MAX_PROJECT_BYTES // (1024 * 1024)} MB"))
    if problems:
        first = problems[0]
        msg = f"{len(problems)} problem(s) found; first: {first['path']} - {first['reason']}"
        raise Rejection(first["code"] if len(problems) == 1 else "UPLOAD_REJECTED", msg, problems)
    if not files:
        raise Rejection("NO_FILES", "The upload contained no usable files")
    return Project(files, ignored)
