"""The classroom site: who can see what, and what a button is allowed to run.

Two things here are worth more than the rest. A console button shells out to
`go2-student.sh` with a name that came from a browser, so the name has to be
checked before it ever reaches a command line. And a student page shows a lab
token, which is a password -- so it must come from the session's own row and
nothing else, whatever the request asks for.

Provisioning is off in every test (`provision=False`): the tests check what
the app *asks* for, with a recording stand-in, rather than building docker
images on a CI box that has no docker.
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

fastapi = pytest.importorskip("fastapi", reason="the classroom extra is not installed")
from fastapi.testclient import TestClient            # noqa: E402

from classroom import app as app_module              # noqa: E402
from classroom import db, labs                       # noqa: E402
from classroom.settings import Settings              # noqa: E402

TEACHER = "mentor@example.org"
SESSION_CSRF = "a-token-this-session-really-has"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        base_url="https://robo-dog.example.org",
        db_path=tmp_path / "classroom.db",
        session_secret="test-secret-not-a-real-one",
        teachers=frozenset({TEACHER}),
        provision=False,
        # Its own pack, or the tests edit the class's real course material --
        # which they did, and which removed a live notebook before anyone
        # noticed. A test that writes outside tmp_path is a bug in the test.
        course_pack=tmp_path / "pack",
    )


@pytest.fixture
def client(settings, monkeypatch):
    calls: list[tuple[str, str]] = []

    def record(self, command, slug, *, check=True):
        if not db.valid_slug(slug):
            raise labs.LabError(f"{slug!r} is not a valid student name")
        calls.append((command, slug))
        return ""

    monkeypatch.setattr(labs.Labs, "_run", record)
    # Over https, because the session cookie is Secure whenever the public
    # name is -- which it is in production, and a test that quietly drops
    # the cookie would be testing a configuration nobody runs.
    client = TestClient(app_module.create_app(settings),
                        base_url=settings.base_url)
    client.calls = calls
    client.settings = settings
    return client


def sign_in_as(client, *, kind, email=None, name="Someone", slug=None):
    """Forge the session cookie the app would have set.

    Going through Google in a test would mean mocking Google; going through
    the app's own signing means the test proves the app trusts a session it
    signed, which is the thing that matters.
    """
    from itsdangerous import TimestampSigner

    # Clear first: setting a cookie that is already in the jar can leave both,
    # and "the old session won" is the opposite of what these tests check.
    client.cookies.clear()
    # The form token lives in the session too, so a forged session carries one
    # -- otherwise a test cannot tell "blocked because they are a student"
    # from "blocked because the token was stale".
    data = {"who": {"kind": kind, "email": email, "name": name, "slug": slug},
            "csrf": SESSION_CSRF}
    raw = base64.b64encode(json.dumps(data).encode())
    signed = TimestampSigner(client.settings.session_secret).sign(raw)
    client.cookies.set("session", signed.decode())


# --------------------------------------------------------------- open pages

def test_the_front_page_needs_no_login(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "robot dog" in r.text.lower()


def test_the_installer_is_mirrored_off_this_domain(client):
    """A student types the address on the board, not a GitHub URL."""
    r = client.get("/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh")
    assert r.status_code == 200
    assert r.text.startswith("#!/bin/bash")
    assert "Go2 simulator installer" in r.text
    # Byte-for-byte what this checkout holds, or the mirror is a fork.
    source = pathlib.Path(__file__).resolve().parents[1] / "install" / "mac.sh"
    assert r.text == source.read_text()


def test_the_mirrored_installer_is_never_cached(client):
    """The CDN window on raw.githubusercontent is the bug this must not repeat."""
    r = client.get("/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh")
    assert "no-store" in r.headers.get("cache-control", "")


# ------------------------------------------------------------------ console

def test_the_console_is_closed_to_anonymous_visitors(client):
    r = client.get("/console")
    assert r.status_code == 200
    assert "Mentor console" in r.text
    assert "Add a student" not in r.text


def test_a_signed_in_student_cannot_open_the_console(client):
    sign_in_as(client, kind="student", name="Ada", slug="ada")
    r = client.get("/console")
    assert "Add a student" not in r.text
    assert "not a mentor" in r.text


def test_a_teacher_adds_a_student_and_gets_a_lab_started(client):
    sign_in_as(client, kind="teacher", email=TEACHER, name="Mentor")
    csrf = _csrf(client, "/console")

    r = client.post("/console/add",
                    data={"name": "Ada Lovelace", "email": "ada@example.org",
                          "lab": "1", "csrf": csrf}, follow_redirects=True)

    assert r.status_code == 200
    student = db.get(client.settings.db_path, "ada-lovelace")
    assert student is not None
    assert student.display == "Ada Lovelace"
    assert student.email == "ada@example.org"
    assert client.calls == [("create", "ada-lovelace"), ("up", "ada-lovelace")]


def test_adding_a_student_without_a_lab_runs_nothing(client):
    sign_in_as(client, kind="teacher", email=TEACHER)
    csrf = _csrf(client, "/console")
    client.post("/console/add", data={"name": "Roster Only", "csrf": csrf},
                follow_redirects=True)
    assert db.get(client.settings.db_path, "roster-only") is not None
    assert client.calls == []


def test_a_name_that_is_not_a_name_never_reaches_a_command_line(client):
    """The console turns typing into a container name. It is the boundary."""
    sign_in_as(client, kind="teacher", email=TEACHER)
    csrf = _csrf(client, "/console")

    for hostile in ("; rm -rf /", "../../etc/passwd", "$(whoami)", "a b; docker ps"):
        client.post("/console/add",
                    data={"name": hostile, "lab": "1", "csrf": csrf},
                    follow_redirects=True)

    for _command, slug in client.calls:
        assert db.valid_slug(slug), f"{slug!r} reached go2-student.sh"
    for student in db.all_students(client.settings.db_path):
        assert db.valid_slug(student.slug)


def test_a_post_without_the_session_token_does_nothing(client):
    sign_in_as(client, kind="teacher", email=TEACHER)
    client.get("/console")                       # establishes a session token
    client.post("/console/add", data={"name": "No Token"},
                follow_redirects=True)
    assert db.get(client.settings.db_path, "no-token") is None


def test_a_student_cannot_drive_the_console_buttons(client):
    sign_in_as(client, kind="teacher", email=TEACHER)
    csrf = _csrf(client, "/console")
    client.post("/console/add", data={"name": "Ada", "csrf": csrf},
                follow_redirects=True)
    client.calls.clear()

    # Their own session, their own valid token: so what stops them is being
    # a student, not a stale form.
    sign_in_as(client, kind="student", name="Ada", slug="ada")
    client.post("/console/lab/ada/start", data={"csrf": SESSION_CSRF},
                follow_redirects=True)

    assert client.calls == []


# ------------------------------------------------------------------ student

def test_a_join_code_signs_a_student_in(client):
    student = db.add(client.settings.db_path, slug="ada", display="Ada",
                     email=None, created_by="test")
    csrf = _csrf(client, "/student")

    r = client.post("/student/code",
                    data={"code": student.join_code.lower().replace("-", " "),
                          "csrf": csrf}, follow_redirects=True)

    assert r.status_code == 200
    assert "Hello, " in r.text and "Ada" in r.text
    assert "curl -fsSL https://robo-dog.example.org" in r.text


def test_a_wrong_code_says_so_and_signs_nobody_in(client):
    db.add(client.settings.db_path, slug="ada", display="Ada", email=None,
           created_by="test")
    csrf = _csrf(client, "/student")
    r = client.post("/student/code", data={"code": "XXXX-XXXX", "csrf": csrf},
                    follow_redirects=True)
    assert "does not match anyone" in r.text
    assert "Hello, " not in r.text


def test_a_student_sees_their_own_lab_and_not_another_one(client, monkeypatch):
    """A lab link is a password. It comes from the session's row, only."""
    path = client.settings.db_path
    db.add(path, slug="ada", display="Ada", email=None, created_by="t")
    db.add(path, slug="bob", display="Bob", email=None, created_by="t")

    def fake_info(self, slug):
        return labs.Lab(slug=slug, port=8400 if slug == "ada" else 8401,
                        token=f"token-for-{slug}", work_dir="/tmp",
                        status="Up 2 minutes")

    monkeypatch.setattr(labs.Labs, "info", fake_info)
    monkeypatch.setattr(labs.Labs, "links", lambda self, slug: [
        ("lan", f"https://10.0.0.1:8400/proxy/8399/split?token=token-for-{slug}")])

    sign_in_as(client, kind="student", name="Ada", slug="ada")
    r = client.get("/student")

    assert "token-for-ada" in r.text
    assert "token-for-bob" not in r.text


def test_a_student_removed_from_the_roster_is_signed_out(client):
    sign_in_as(client, kind="student", name="Ghost", slug="ghost")
    r = client.get("/student")
    assert "no longer on the list" in r.text


def test_the_join_code_is_readable_out_loud(client):
    """It gets read across a classroom and typed by someone who cannot see it."""
    for _ in range(50):
        code = db.new_code()
        assert "-" in code and len(code) == 9
        assert not set(code) & set("01OIL"), f"{code} has a character to misread"


# -------------------------------------------------------------------- misc

def test_google_sign_in_degrades_to_an_explanation(client):
    """An unconfigured Google must not be a 500 in front of a class."""
    r = client.get("/login/google?next=/console", follow_redirects=False)
    assert r.status_code == 200
    assert "not set up yet" in r.text


def test_health_check(client):
    assert client.get("/healthz").text.strip() == "ok"


def _csrf(client, path: str) -> str:
    """Read the session token out of a rendered form, like a browser would."""
    import re

    page = client.get(path).text
    match = re.search(r'name="csrf" value="([^"]+)"', page)
    assert match, f"no csrf field on {path}"
    return match.group(1)


def test_a_monitor_can_use_head(client):
    """Gateways and uptime checks send HEAD, and a 405 looks like an outage."""
    assert client.head("/healthz").status_code == 200
    r = client.head("/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/x-shellscript")


def test_the_gateway_check_reports_the_scheme_it_really_saw(client):
    """The check a reverse proxy is verified with, so it must not lie.

    The session cookie cannot do this job: it is Secure because the public
    name is https, even for a request that arrived over plain http. This
    endpoint reports what the request actually carried.
    """
    r = client.get("/healthz.json")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["scheme"] == "https"          # the TestClient speaks https here
    assert body["base_url"] == "https://robo-dog.example.org"
    assert body["google"] is False


def test_the_secure_cookie_is_not_evidence_of_forwarding(settings, monkeypatch):
    """Written down because the gateway document used to claim it was."""
    monkeypatch.setattr(labs.Labs, "_run", lambda *a, **k: "")
    plain = TestClient(app_module.create_app(settings),
                       base_url="http://not-https.example")
    cookie = plain.get("/student").headers.get("set-cookie", "")
    assert "secure" in cookie.lower(), (
        "the Secure flag follows the configured public name, not the request")


def test_privacy_and_terms_are_public_and_say_who_to_ask(client):
    """Google will not publish an OAuth app without them, and they are linked
    from every page -- so they must render for someone not signed in."""
    for path in ("/privacy", "/terms"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "tech@cpmentorship.org" in r.text, f"{path} names nobody to ask"
    assert "/privacy" in client.get("/").text


def test_every_page_carries_the_organisation_identity(client):
    """The logo and icons are the same files cpmentorship.org serves.

    A sister site that merely looks similar is what families notice and
    distrust, so this asserts the assets are referenced and present rather
    than that someone remembered to style a header.
    """
    import pathlib

    brand = pathlib.Path(__file__).resolve().parents[1] / "classroom" / "static" / "brand"
    for name in ("logo.webp", "favicon.ico", "icon.png", "apple-icon.png"):
        assert (brand / name).is_file(), f"missing brand asset {name}"

    for path in ("/", "/student", "/console", "/privacy", "/terms"):
        page = client.get(path).text
        assert "/static/brand/logo.webp" in page, path
        assert "/static/brand/favicon.ico" in page, path


def test_the_pages_make_no_third_party_requests(client):
    """The privacy page says so, so it has to stay true.

    Fonts are self-hosted for this reason; an external stylesheet or script
    would quietly make that sentence false.
    """
    for path in ("/", "/student", "/console", "/privacy", "/terms"):
        page = client.get(path)
        for marker in ("fonts.googleapis.com", "fonts.gstatic.com",
                       "cdn.jsdelivr.net", "googletagmanager", "google-analytics"):
            assert marker not in page.text, f"{path} reaches out to {marker}"


def test_assets_are_versioned_so_a_cdn_cannot_pin_them(client):
    """Cloudflare cached the old stylesheet for four hours against new HTML.

    The page is dynamic and the assets are not, so the version has to live in
    the asset URL; nothing else gets a change past an edge cache reliably.
    """
    import re

    page = client.get("/").text
    for asset in ("style.css", "logo.webp", "favicon.ico"):
        assert re.search(rf"{re.escape(asset)}\?v=[0-9a-f]{{6,}}", page), \
            f"{asset} is referenced without a version"

    versions = set(re.findall(r"\?v=([0-9a-f]+)", page))
    assert len(versions) == 1, f"one build, one version: {versions}"

    r = client.get(f"/static/style.css?v={versions.pop()}")
    assert r.status_code == 200
    assert "max-age" in r.headers.get("cache-control", "")


def test_private_network_addresses_never_reach_a_browser(client, monkeypatch):
    """A link on the private overlay is for a mentor at a terminal.

    `go2-student.sh link` still prints it; this app must not put it on a page
    a student opens, and the repository this code lives in is public.
    """
    from classroom import db

    db.add(client.settings.db_path, slug="ada", display="Ada", email=None,
           created_by="t")
    monkeypatch.setattr(labs.Labs, "info", lambda self, slug: labs.Lab(
        slug=slug, port=8400, token="t", work_dir="/tmp", status="Up 1 minute"))
    monkeypatch.setattr(labs.Labs, "_run", lambda self, cmd, slug, check=True: (
        "  (tailnet) https://100.64.1.2:8400/proxy/8399/split?token=t\n"
        "  (lan)     https://192.168.1.9:8400/proxy/8399/split?token=t\n"))

    sign_in_as(client, kind="student", name="Ada", slug="ada")
    page = client.get("/student").text

    assert "192.168.1.9" in page, "the usable link should still be offered"
    assert "100.64.1.2" not in page
    assert "tailnet" not in page.lower()


def test_a_mentor_can_add_another_mentor_by_gmail(client):
    sign_in_as(client, kind="teacher", email=TEACHER, name="Mentor")
    csrf = _csrf(client, "/console")

    client.post("/console/mentors/add",
                data={"email": "Co.Mentor@Gmail.com ", "csrf": csrf},
                follow_redirects=True)

    assert db.is_mentor(client.settings.db_path, "co.mentor@gmail.com"), \
        "stored lower-cased and trimmed, because that is how Google sends it"


def test_an_added_mentor_really_gets_the_console(client):
    """The allowlist is the environment's list plus the console's, not either."""
    from classroom import auth

    db.add_mentor(client.settings.db_path, "new@gmail.com", added_by=TEACHER)
    assert auth.is_mentor(client.settings, "new@gmail.com")

    sign_in_as(client, kind="teacher", email="new@gmail.com", name="New")
    assert "Add a student" in client.get("/console").text


def test_removing_a_mentor_takes_effect_before_their_cookie_expires(client):
    """A signed session is not authority; the lists are, on every request."""
    db.add_mentor(client.settings.db_path, "temp@gmail.com", added_by=TEACHER)
    sign_in_as(client, kind="teacher", email="temp@gmail.com", name="Temp")
    assert "Add a student" in client.get("/console").text

    db.remove_mentor(client.settings.db_path, "temp@gmail.com")

    page = client.get("/console").text
    assert "Add a student" not in page, "a two-week cookie outlived the removal"


def test_the_environments_mentors_cannot_be_removed_from_the_console(client):
    """Otherwise one click can lock the owners out of their own server."""
    sign_in_as(client, kind="teacher", email=TEACHER)
    csrf = _csrf(client, "/console")

    r = client.post("/console/mentors/remove",
                    data={"email": TEACHER, "csrf": csrf}, follow_redirects=True)

    assert "cannot be removed from here" in r.text
    from classroom import auth
    assert auth.is_mentor(client.settings, TEACHER)


def test_a_student_cannot_make_themselves_a_mentor(client):
    db.add(client.settings.db_path, slug="ada", display="Ada", email=None,
           created_by="t")
    sign_in_as(client, kind="student", name="Ada", slug="ada")

    client.post("/console/mentors/add",
                data={"email": "ada@gmail.com", "csrf": SESSION_CSRF},
                follow_redirects=True)

    assert not db.is_mentor(client.settings.db_path, "ada@gmail.com")


# ------------------------------------------------- course material, labs, work

def test_the_class_pack_is_seeded_from_the_bundled_one_and_is_separate(settings):
    """A mentor's edit must never reach a student's laptop install.

    The pack inside the go2 package is what `curl ... | bash` downloads. The
    class works from a copy, so changing the course does not change what the
    installer hands out.
    """
    from classroom.course import Pack

    bundled = pathlib.Path(__file__).resolve().parents[1] / "go2" / "course_pack"
    pack = Pack(settings.course_pack, bundled=bundled).ensure()

    names = {f.name for f in pack.files()}
    assert "01_hello_dog.ipynb" in names
    assert pack.path != bundled
    pack.remove("01_hello_dog.ipynb")
    assert (bundled / "units" / "01_hello_dog.ipynb").is_file(), \
        "removing from the class pack touched the shipped one"


def test_only_course_shaped_files_can_be_uploaded(client):
    from classroom.course import CourseError, Pack

    pack = Pack(client.settings.course_pack)
    for name in ("lesson.sh", "../../etc/passwd", "evil.ipynb.exe", "",
                 "note.py\x00.sh"):
        with pytest.raises(CourseError):
            pack.write(name, b"x")

    # A name with a path in it keeps only its last component -- browsers do
    # send them, and the file belongs in units/ either way.
    landed = pack.write("../../a/b.py", b"x")
    assert landed.name == "b.py"
    assert landed.path.parent == pack.units.resolve()

    unit = pack.write("extra_notes.md", b"# notes\n")
    assert unit.name == "extra_notes.md"


def test_a_broken_notebook_is_refused_before_a_class_gets_it(client):
    from classroom.course import CourseError, Pack

    pack = Pack(client.settings.course_pack)
    with pytest.raises(CourseError, match="readable notebook|no cells"):
        pack.write("broken.ipynb", b"{oh no")
    with pytest.raises(CourseError, match="no cells"):
        pack.write("empty.ipynb", b'{"metadata": {}}')
    pack.write("fine.ipynb", b'{"cells": [], "nbformat": 4}')


def test_uploading_and_removing_course_files_through_the_console(client):
    sign_in_as(client, kind="teacher", email=TEACHER)
    csrf = _csrf(client, "/console")

    client.post("/console/course/upload",
                data={"csrf": csrf},
                files={"upload": ("exercise.py", b"dog.move(vx=0.2)\n", "text/x-python")},
                follow_redirects=True)
    page = client.get("/console/course").text
    assert "exercise.py" in page

    client.post("/console/course/remove",
                data={"name": "exercise.py", "csrf": csrf}, follow_redirects=True)
    assert "exercise.py" not in client.get("/console/course").text


def test_a_student_cannot_touch_the_course_material(client):
    db.add(client.settings.db_path, slug="ada", display="Ada", email=None,
           created_by="t")
    sign_in_as(client, kind="student", name="Ada", slug="ada")

    r = client.get("/console/course", follow_redirects=False)
    assert r.status_code == 303

    client.post("/console/course/upload", data={"csrf": SESSION_CSRF},
                files={"upload": ("mine.py", b"x", "text/x-python")},
                follow_redirects=True)
    from classroom.course import Pack
    assert "mine.py" not in {f.name for f in Pack(client.settings.course_pack).files()}


def test_a_mentor_lab_is_created_but_stays_off_the_class_roster(client):
    sign_in_as(client, kind="teacher", email=TEACHER, name="Mentor")
    csrf = _csrf(client, "/console")

    client.post("/console/mylab", data={"csrf": csrf}, follow_redirects=True)

    mine = db.lab_for_mentor(client.settings.db_path, TEACHER)
    assert mine is not None and mine.kind == "mentor"
    assert mine.slug not in [s.slug for s in db.all_students(client.settings.db_path)]
    assert ("create", mine.slug) in client.calls


def test_a_mentor_can_read_a_students_work_and_nobody_else_can(client, tmp_path):
    work = tmp_path / "ada"
    work.mkdir()
    (work / "my_dog.ipynb").write_text(
        '{"cells": [{"cell_type": "code", "source": ["dog.backflip()\\n"]}]}')
    db.add(client.settings.db_path, slug="ada", display="Ada", email=None,
           created_by="t")
    monkey = lambda self, slug: work if slug == "ada" else None   # noqa: E731
    labs.Labs.work_dir = monkey

    sign_in_as(client, kind="teacher", email=TEACHER)
    page = client.get("/console/work/ada?open=my_dog.ipynb").text
    assert "dog.backflip()" in page, "the mentor should see what was written"

    sign_in_as(client, kind="student", name="Ada", slug="ada")
    assert client.get("/console/work/ada", follow_redirects=False).status_code == 303


def test_a_students_work_cannot_be_read_outside_their_folder(client, tmp_path):
    """The one place a path from a URL meets the file system."""
    work = tmp_path / "ada"
    work.mkdir()
    (tmp_path / "secret.txt").write_text("not theirs")
    db.add(client.settings.db_path, slug="ada", display="Ada", email=None,
           created_by="t")
    labs.Labs.work_dir = lambda self, slug: work

    sign_in_as(client, kind="teacher", email=TEACHER)
    for attempt in ("../secret.txt", "..%2Fsecret.txt", "/etc/hostname"):
        page = client.get(f"/console/work/ada?open={attempt}").text
        assert "not theirs" not in page
        r = client.get(f"/console/work/ada/download?name={attempt}")
        assert r.status_code in (400, 404), attempt
        assert "not theirs" not in r.text


# ------------------------------------------- laptops pull from the class site

def test_the_site_publishes_the_class_pack_with_hashes(client):
    import hashlib

    from classroom.course import Pack

    pack = Pack(client.settings.course_pack)
    pack.write("extra.md", b"# extra\n")

    m = client.get("/course/manifest.json")
    assert m.status_code == 200
    assert "no-store" in m.headers["cache-control"]
    body = m.json()
    entry = next(f for f in body["files"] if f["name"] == "extra.md")
    assert entry["sha256"] == hashlib.sha256(b"# extra\n").hexdigest()

    f = client.get("/course/files/extra.md")
    assert f.content == b"# extra\n"
    assert client.get("/course/files/..%2F..%2Fsettings.py").status_code in (400, 404)


def test_a_laptop_mirrors_the_class_pack_and_follows_changes(client, tmp_path, monkeypatch):
    """The whole point: a mentor's change reaches a laptop with no reinstall."""
    from classroom.course import Pack
    from go2 import course as engine

    pack = Pack(client.settings.course_pack)
    pack.write("lesson.md", b"version one\n")

    def fetch(url, timeout):            # the laptop talks to the test client
        path = url.split("://", 1)[1].split("/", 1)[1]
        r = client.get("/" + path)
        if r.status_code != 200:
            raise OSError(f"HTTP {r.status_code}")
        return r.content

    monkeypatch.setattr(engine, "_fetch", fetch)
    root = tmp_path / "courses"

    first = engine.pull("https://robo-dog.example.org/course", root=root)
    assert first.online
    mirror = first.pack / "units"
    assert (mirror / "lesson.md").read_bytes() == b"version one\n"

    second = engine.pull("https://robo-dog.example.org/course", root=root)
    assert not any("new or changed" in n for n in second.notes), "re-downloaded unchanged files"

    pack.write("lesson.md", b"version two\n")
    pack.remove("01_hello_dog.py")
    third = engine.pull("https://robo-dog.example.org/course", root=root)
    assert (mirror / "lesson.md").read_bytes() == b"version two\n"
    assert not (mirror / "01_hello_dog.py").exists(), "a withdrawn file stayed"
    assert any("1 new or changed" in n and "1 withdrawn" in n for n in third.notes)


def test_a_laptop_with_no_network_still_starts(tmp_path, monkeypatch):
    """Offline is a normal state for a laptop, not an error."""
    import urllib.error

    from go2 import course as engine

    def down(url, timeout):
        raise urllib.error.URLError("Name or service not known")

    monkeypatch.setattr(engine, "_fetch", down)
    result = engine.pull("https://robo-dog.example.org/course", root=tmp_path)

    assert not result.online
    assert result.pack is None
    assert any("offline" in n for n in result.notes)


def test_a_damaged_download_never_replaces_a_good_file(client, tmp_path, monkeypatch):
    from classroom.course import Pack
    from go2 import course as engine

    Pack(client.settings.course_pack).write("lesson.md", b"good\n")
    real = {}

    def fetch(url, timeout):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        body = client.get(path).content
        return body if "manifest" in url else b"corrupted in transit"

    monkeypatch.setattr(engine, "_fetch", fetch)
    root = tmp_path / "courses"
    (root / "go2-intro" / "units").mkdir(parents=True)
    keep = root / "go2-intro" / "units" / "lesson.md"
    keep.write_bytes(b"what the laptop already had\n")

    result = engine.pull("https://robo-dog.example.org/course", root=root)

    assert keep.read_bytes() == b"what the laptop already had\n"
    assert any("damaged" in n for n in result.notes)


def test_pulling_can_be_turned_off_for_a_laptop(tmp_path, monkeypatch):
    from go2 import course as engine

    monkeypatch.setenv("GO2_COURSE_URL", "off")
    called = []
    monkeypatch.setattr(engine, "_fetch", lambda *a: called.append(a))
    result = engine.pull(root=tmp_path)
    assert not called and not result.online
