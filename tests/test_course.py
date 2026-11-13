"""Tests for course packs: finding one, reading it, handing it out.

The seeding rules themselves are covered in test_desktop.py, which is where
they were written; these cover the part that is new -- that a pack is a thing
the engine finds rather than a folder it is born knowing about.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

from go2 import course


def _pack(tmp_path: pathlib.Path, *, version: str = "2026.01.01",
          title: str = "Test Course") -> pathlib.Path:
    pack = tmp_path / "pack"
    (pack / course.UNITS).mkdir(parents=True)
    (pack / course.UNITS / "01_lesson.py").write_text("print('one')\n")
    (pack / course.MANIFEST).write_text(
        '[course]\n'
        'id = "test-course"\n'
        f'title = "{title}"\n'
        f'version = "{version}"\n\n'
        '[[unit]]\n'
        'id = "01"\n'
        'title = "Lesson one"\n'
        'files = ["01_lesson.py"]\n'
    )
    return pack


def test_the_bundled_pack_is_a_valid_pack():
    assert course.units_dir(course.BUNDLED).is_dir()
    meta = course.manifest(course.BUNDLED)
    assert meta["course"]["id"], "the bundled pack needs an id"
    assert meta["course"]["version"]


def test_every_file_the_manifest_names_actually_exists():
    """A typo here hands a student a syllabus pointing at nothing."""
    units = course.units_dir(course.BUNDLED)
    listed = {name for unit in course.manifest(course.BUNDLED).get("unit", [])
              for name in unit.get("files", [])}
    missing = sorted(n for n in listed if not (units / n).is_file())
    assert not missing, f"course.toml names files that are not there: {missing}"

    # ...and the other way round, so new material cannot go unlisted.
    on_disk = {p.name for p in units.iterdir()
               if p.is_file() and not p.name.startswith(".")}
    assert not sorted(on_disk - listed), "files in units/ that course.toml never mentions"


def test_resolution_order(tmp_path, monkeypatch):
    explicit = _pack(tmp_path / "explicit", title="Explicit")
    env = _pack(tmp_path / "env", title="From env")
    installed = _pack(tmp_path / "installed_src", title="Installed")

    home = tmp_path / "home"
    (home / "courses").mkdir(parents=True)
    shutil.copytree(installed, home / "courses" / "test-course")
    monkeypatch.setenv("GO2_HOME", str(home))

    monkeypatch.setenv("GO2_COURSE", str(env))
    assert course.resolve(explicit) == explicit          # argument wins
    assert course.resolve() == env                       # then the environment

    monkeypatch.delenv("GO2_COURSE")
    assert course.resolve() == home / "courses" / "test-course"

    shutil.rmtree(home / "courses")
    assert course.resolve() == course.BUNDLED            # finally, bundled


def test_resolve_complains_about_a_path_that_is_not_a_pack(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        course.resolve(tmp_path / "nowhere")
    assert "course.toml" in str(exc.value)


def test_a_pack_without_a_manifest_still_hands_out_files(tmp_path):
    """Metadata is a convenience; losing it must not stop a class."""
    pack = tmp_path / "flat"
    pack.mkdir()
    (pack / "lesson.py").write_text("print('hi')\n")

    assert course.resolve(pack) == pack
    assert course.manifest(pack) == {}
    assert course.sync(tmp_path / "work", pack) == ["added lesson.py"]
    assert (tmp_path / "work" / "lesson.py").exists()


def test_describe_names_the_course_for_a_banner(tmp_path):
    pack = _pack(tmp_path, title="Robot Dog", version="2026.11.12")
    assert course.describe(pack) == "Robot Dog 2026.11.12"
    assert course.course_id(pack) == "test-course"


def test_a_newer_pack_updates_an_untouched_file(tmp_path):
    """The reason packs exist: content ships without shipping the engine."""
    pack = _pack(tmp_path / "v1")
    work = tmp_path / "work"
    course.sync(work, pack)

    newer = _pack(tmp_path / "v2", version="2026.02.02")
    (newer / course.UNITS / "01_lesson.py").write_text("print('two')\n")
    notes = course.sync(work, newer)

    assert (work / "01_lesson.py").read_text() == "print('two')\n"
    assert any("updated" in n for n in notes)


def test_dry_run_writes_nothing(tmp_path):
    pack = _pack(tmp_path)
    work = tmp_path / "work"
    notes = course.sync(work, pack, dry_run=True)
    assert notes == ["added 01_lesson.py"]
    assert not work.exists()


def test_the_cli_is_what_the_server_script_calls(tmp_path):
    """go2-student.sh shells out to this; keep it working from the module."""
    pack = _pack(tmp_path)
    work = tmp_path / "work"
    out = subprocess.run(
        [sys.executable, "-m", "go2.course", "--course", str(pack),
         "sync", "--into", str(work)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "added 01_lesson.py" in out.stdout
    assert (work / "01_lesson.py").exists()


def test_handing_out_coursework_needs_no_simulator(tmp_path):
    """`go2-course sync` runs where mujoco and numpy are not installed.

    The classroom container is the real case: it copies notebooks and never
    simulates anything. Importing the package used to pull in go2.api, so
    seeding a new student failed there with `No module named 'numpy'`.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent("""
        import sys
        class Blocked:
            def find_module(self, name, path=None): return None
            def find_spec(self, name, target=None, path=None):
                if name.split('.')[0] in ('numpy', 'mujoco'):
                    raise ImportError(f'{name} is not installed here')
                return None
        sys.meta_path.insert(0, Blocked())

        import go2
        from go2 import course
        print(course.units_dir(course.BUNDLED).is_dir())
        print('numpy' not in sys.modules, 'mujoco' not in sys.modules)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["True", "True", "True"], out.stdout


def test_a_laptop_is_told_when_the_class_has_a_newer_simulator(monkeypatch):
    """Announced, never auto-installed: 250 MB at the start of a lesson is
    the worst possible moment."""
    from go2 import build, course

    monkeypatch.setattr(build, "build_info",
                        lambda: {"commit": "aaaaaaa1111111111111111111111111111111aa"})
    assert course.engine_is_behind("bbbbbbb2222222222222222222222222222222bb")
    assert not course.engine_is_behind("aaaaaaa1111111111111111111111111111111aa")
    assert not course.engine_is_behind(""), "no answer from the site is not 'behind'"

    monkeypatch.setattr(build, "build_info", lambda: {"commit": ""})
    assert not course.engine_is_behind("bbbbbbb"), "a source checkout is never nagged"
