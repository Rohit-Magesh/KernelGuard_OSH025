import io
import warnings
import zipfile

import pytest

from app import config as C
from app.validate import Rejection, build_project, normalize_path


def make_zip(entries):
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data, *kind in entries:
                zi = zipfile.ZipInfo(name)
                zi.external_attr = (0o120777 << 16) if kind and kind[0] == "symlink" else (0o100644 << 16)
                z.writestr(zi, data)
    return buf.getvalue()


SRC = b"int main(void){return 0;}\n"


@pytest.mark.parametrize("raw,code", [
    ("../escape.txt", "PATH_TRAVERSAL"), ("a/b/../../../x.c", "PATH_TRAVERSAL"), ("..\\x.c", "PATH_TRAVERSAL"),
    ("/etc/passwd", "ABSOLUTE_PATH"), ("C:\\Windows\\x.c", "WINDOWS_ABSOLUTE_PATH"), ("c:/x.c", "WINDOWS_ABSOLUTE_PATH"),
    ("\\\\srv\\share\\x.c", "WINDOWS_ABSOLUTE_PATH"), ("", "EMPTY_PATH"), ("a\x00.c", "INVALID_CHARACTERS"),
    ("sp ace.c", "UNSAFE_NAME"), (".hidden.c", "UNSAFE_NAME"),
])
def test_normalize_rejects(raw, code):
    path, bad = normalize_path(raw)
    assert path is None and bad[0] == code


def test_normalize_accepts_and_cleans():
    assert normalize_path("src\\util.c") == ("src/util.c", None)
    assert normalize_path("./main.c") == ("main.c", None)


def test_multiple_plain_files_are_all_kept():
    p = build_project([("main.c", SRC), ("util.c", b"int u;\n"), ("util.h", b"extern int u;\n")], "c")
    assert sorted(p.files) == ["main.c", "util.c", "util.h"]


def test_zip_accepted_with_folders():
    p = build_project([("p.zip", make_zip([("src/main.c", SRC), ("src/util.h", b"#pragma once\n")]))], "c")
    assert sorted(p.files) == ["src/main.c", "src/util.h"]


def test_zip_and_files_merge_but_duplicates_rejected():
    with pytest.raises(Rejection) as e:
        build_project([("main.c", SRC), ("p.zip", make_zip([("main.c", SRC)]))], "c")
    assert e.value.code == "DUPLICATE_PATH"


@pytest.mark.parametrize("entries,code", [
    ([("../escape.txt", b"x")], "PATH_TRAVERSAL"),
    ([("docs/../../e.c", b"x")], "PATH_TRAVERSAL"),
    ([("/abs.c", b"x")], "ABSOLUTE_PATH"),
    ([("C:\\w\\e.c", b"x")], "WINDOWS_ABSOLUTE_PATH"),
    ([("l.c", b"/etc/passwd", "symlink")], "SYMLINK"),
    ([("a.c", b"1"), ("a.c", b"2")], "DUPLICATE_PATH"),
    ([("a.c", b"1"), ("A.C", b"2")], "CASE_CONFLICT"),
    ([("a.exe", b"MZ")], "UNSUPPORTED_EXTENSION"),
    ([(f"f{i}.c", b"x") for i in range(21)], "TOO_MANY_FILES"),
    ([("big.txt", b"\0" * (C.MAX_FILE_BYTES + 1))], "FILE_TOO_LARGE"),
])
def test_zip_rejections(entries, code):
    with pytest.raises(Rejection) as e:
        build_project([("p.zip", make_zip(entries))], "c")
    assert code in {d["code"] for d in e.value.details} | {e.value.code}


def test_zip_junk_is_ignored_not_executed():
    p = build_project([("p.zip", make_zip([("main.c", SRC), ("__MACOSX/._main.c", b"x"), (".DS_Store", b"x")]))], "c")
    assert list(p.files) == ["main.c"] and len(p.ignored) == 2


def test_archive_size_cap():
    with pytest.raises(Rejection) as e:
        build_project([("big.zip", b"PK" + b"\0" * (C.MAX_ARCHIVE_BYTES + 10))], "c")
    assert e.value.code == "ARCHIVE_TOO_LARGE" or "ARCHIVE_TOO_LARGE" in {d["code"] for d in e.value.details}


def test_not_a_zip():
    with pytest.raises(Rejection) as e:
        build_project([("x.zip", b"hello")], "c")
    assert e.value.code == "INVALID_ARCHIVE"


def test_oversize_plain_file_and_wrong_language_hint():
    with pytest.raises(Rejection) as e:
        build_project([("a.py", b"x" * (C.MAX_FILE_BYTES + 1))], "python")
    assert e.value.code == "FILE_TOO_LARGE"
    with pytest.raises(Rejection) as e:
        build_project([("a.py", b"print(1)")], "c")
    assert "Python" in e.value.details[0]["reason"]


def test_traversal_in_plain_upload_name_is_rejected_not_basenamed():
    with pytest.raises(Rejection) as e:
        build_project([("../evil.c", SRC)], "c")
    assert e.value.code == "PATH_TRAVERSAL"