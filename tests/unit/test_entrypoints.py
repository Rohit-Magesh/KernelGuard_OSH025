import pytest

from app.entrypoints import detect, plan
from app.validate import Rejection

MAIN = b"#include <stdio.h>\nint main(void){puts(\"x\");return 0;}\n"


def test_single_main_compiles_everything_together():
    files = {"main.c": MAIN, "util.c": b"int u(void){return 1;}\n", "util.h": b"int u(void);\n"}
    p = plan("c", files)
    assert p["entry"] == "main.c" and sorted(p["sources"]) == ["main.c", "util.c"]
    assert len(p["compile"]) == 1 and "util.c" in p["compile"][0]


def test_two_independent_programs_require_selection():
    files = {"a.c": MAIN, "b.c": MAIN}
    assert detect("c", files)["needs_selection"] is True
    with pytest.raises(Rejection) as e:
        plan("c", files)
    assert e.value.code == "ENTRY_POINT_SELECTION_REQUIRED" and e.value.status == 409


def test_selected_program_excludes_other_mains_and_unrelated_helpers():
    files = {
        "a.c": b'#include "ha.h"\nint main(void){return fa();}\n', "ha.h": b"int fa(void);\n", "ha.c": b'#include "ha.h"\nint fa(void){return 0;}\n',
        "b.c": b'#include "hb.h"\nint main(void){return fb();}\n', "hb.h": b"int fb(void);\n", "hb.c": b'#include "hb.h"\nint fb(void){return 0;}\n',
    }
    p = plan("c", files, "a.c")
    assert sorted(p["sources"]) == ["a.c", "ha.c"]
    assert "b.c" not in p["compile"][0] and "hb.c" not in p["compile"][0]
    assert p["fallback_sources"] is not None            # link-failure retry set exists


def test_main_in_comment_or_string_is_not_an_entry_point():
    files = {"a.c": b'/* int main(void){} */\nconst char *s = "int main(void){";\nint f(void){return 1;}\n', "m.c": MAIN}
    assert detect("c", files)["candidates"] == [{"path": "m.c", "label": "m.c"}]


def test_no_entry_point():
    with pytest.raises(Rejection) as e:
        plan("c", {"lib.c": b"int f(void){return 1;}\n"})
    assert e.value.code == "NO_ENTRY_POINT"


def test_invalid_entry_point():
    with pytest.raises(Rejection) as e:
        plan("c", {"m.c": MAIN}, "nope.c")
    assert e.value.code == "INVALID_ENTRY_POINT"


def test_python_defaults_to_main_py_and_supports_selection():
    assert plan("python", {"main.py": b"print(1)", "x.py": b"print(2)"})["entry"] == "main.py"
    two = {"a.py": b'if __name__ == "__main__":\n    pass\n', "b.py": b'if __name__ == "__main__":\n    pass\n'}
    with pytest.raises(Rejection):
        plan("python", two)
    assert plan("python", two, "b.py")["run"] == ["python", "-u", "b.py"]


def test_java_defaults_to_Main_class_and_compiles_all_sources():
    files = {"Main.java": b"public class Main { public static void main(String[] a){} }",
             "Other.java": b"public class Other { public static void main(String[] a){} }",
             "Helper.java": b"class Helper {}"}
    p = plan("java", files)
    assert p["main_class"] == "Main" and p["run"][-1] == "Main"
    assert sorted(p["sources"]) == ["Helper.java", "Main.java", "Other.java"]
    assert plan("java", files, "Other.java")["main_class"] == "Other"


def test_java_package_is_part_of_class_name():
    files = {"app/Main.java": b"package app;\npublic class Main { public static void main(String[] a){} }"}
    assert plan("java", files)["main_class"] == "app.Main"


def test_cpp_uses_cpp_sources_only():
    files = {"m.cpp": b"int main(){return 0;}", "x.c": b"int main(void){return 0;}"}
    assert plan("cpp", files)["sources"] == ["m.cpp"]