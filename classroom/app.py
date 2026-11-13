"""The classroom site: a promo page, a teacher console, a student page.

    /                 what this is, for anyone
    /student          a student's own page: how to install, their lab link
    /console          the teacher's roster: add a student, start/stop a lab
    /CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh
                      the installer, mirrored off this domain

The mirror exists so a student types one address, the one on the board,
rather than a GitHub URL with an organisation name in it that they will get
wrong. It serves this checkout's copy of the script, with caching off: the
whole reason the installer prints the commit it resolved is that a cached
installer is a silent way to install the wrong thing.

Provisioning a lab runs docker, which takes minutes the first time, so the
add button starts a background thread and the console polls. A teacher
adding six students before a class should not be watching a spinner.
"""

from __future__ import annotations

import contextlib
import pathlib
import secrets
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, Form, Request
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from fastapi import File, UploadFile

from . import auth, db
from .course import CourseError, Pack, inside, safe_name
from .labs import LabError, Labs
from .settings import Settings

HERE = pathlib.Path(__file__).resolve().parent
HANDIN_MAX = 5 * 1024 * 1024      # a notebook is kilobytes; this is generous


def static_version() -> str:
    """A short token that changes whenever a file under static/ does.

    Every asset URL carries it, which is the only reliable way to ship a
    stylesheet change through a CDN. Cloudflare caches .css, .woff2 and
    images for hours whether or not the origin asked it to -- the first
    rebrand of this site was invisible for exactly that reason, with the edge
    serving the previous stylesheet against the new HTML.
    """
    import hashlib

    digest = hashlib.sha256()
    for path in sorted((HERE / "static").rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.name}{stat.st_mtime_ns}{stat.st_size}".encode())
    return digest.hexdigest()[:10]


def _repo_commit(repo: pathlib.Path) -> str:
    """The commit this checkout is on, read without a git binary.

    The classroom container has no git, and does not need one to read two
    small files. This is what a fresh install would get -- main is pushed
    as it is committed -- so a laptop on an older commit is told so.
    """
    git = repo / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref:"):
            return head if len(head) == 40 else ""
        ref = head.split(" ", 1)[1].strip()
        loose = git / ref
        if loose.is_file():
            return loose.read_text().strip()
        packed = git / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
    except OSError:
        pass
    return ""


class VersionedStatic(StaticFiles):
    """Static files that say how long they may be kept.

    Safe to cache hard, because the URL carries a version: a changed file is
    a different URL, so nothing has to expire for a change to arrive.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=604800"
        return response


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db.init(settings.db_path)
    labs = Labs(settings)
    # The class's own pack, seeded from the bundled one the first time.
    pack = Pack(settings.course_pack,
                bundled=settings.repo_root / "go2" / "course_pack").ensure()
    oauth = auth.oauth_client(settings)
    # Two at a time: a docker build is mostly disk and network, and a teacher
    # adding a whole class at once should not start thirty of them.
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="provision")

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Don't wait: a docker build in flight will finish on its own, and a
        # restart must not hang on it.
        pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Robo Dog classroom", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.settings = settings
    app.state.labs = labs
    app.state.pool = pool
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.base_url.startswith("https://"),
        max_age=14 * 24 * 3600,
    )
    app.mount("/static", VersionedStatic(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["settings"] = settings
    templates.env.globals["v"] = static_version()

    # ------------------------------------------------------------- helpers

    def page(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.setdefault("who", auth.current(request))
        ctx.setdefault("csrf", _csrf(request))
        return templates.TemplateResponse(request, name, ctx)

    def _csrf(request: Request) -> str:
        token = request.session.get("csrf")
        if not token:
            token = secrets.token_urlsafe(24)
            request.session["csrf"] = token
        return token

    def _check_csrf(request: Request, given: str) -> bool:
        return bool(given) and secrets.compare_digest(
            given, request.session.get("csrf", ""))

    def teacher_only(request: Request) -> auth.Who | None:
        """The session says mentor; the lists decide whether it still is.

        Re-checked on every request rather than trusted from the cookie, so
        removing a mentor takes effect immediately instead of whenever their
        two-week session happens to expire. The cookie is signed, not
        authoritative.
        """
        who = auth.current(request)
        if not who or not who.is_teacher:
            return None
        if not auth.is_mentor(settings, who.email or ""):
            auth.sign_out(request)
            return None
        return who

    def provision(slug: str) -> None:
        """create + up, in a worker thread, recording what happened."""
        try:
            if not labs.exists(slug):
                labs.create(slug)
            labs.up(slug)
            db.set_state(settings.db_path, slug, "running", "")
        except LabError as exc:
            db.set_state(settings.db_path, slug, "failed", str(exc))

    def mentor_rows() -> list[dict]:
        """Both lists, marked by where they came from.

        The environment's entries cannot be removed here: they are the root
        of the whole arrangement, and a console that can revoke its own
        owners is a console that can be locked by one bad click.
        """
        rows = [{"email": e, "fixed": True, "added_by": "GO2_TEACHERS"}
                for e in sorted(settings.teachers)]
        rows += [{"email": m["email"], "fixed": False,
                  "added_by": m["added_by"], "created_at": m["created_at"]}
                 for m in db.mentors(settings.db_path)
                 if not settings.is_teacher(m["email"])]
        return rows

    def roster() -> list[dict]:
        rows = []
        for student in db.all_students(settings.db_path):
            lab = labs.info(student.slug)
            state = student.lab_state
            if lab and state not in ("provisioning", "failed"):
                state = "running" if lab.running else "stopped"
            rows.append({
                "student": student,
                "lab": lab,
                "state": state,
                "links": labs.links(student.slug) if lab and lab.running else [],
            })
        return rows

    # --------------------------------------------------------------- pages

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page(request, "home.html")

    # --------------------------------------- the pack, for laptops to pull

    @app.api_route("/course/manifest.json", methods=["GET", "HEAD"])
    def course_manifest():
        """What a laptop needs to mirror the class's material.

        Public on purpose, like the installer: it is the same kind of
        content the public repository already ships, and a laptop pulling at
        launch has no session to present. That makes the rule for the pack
        simple and absolute -- nothing goes in it that students may not
        read. Answer keys belong somewhere else.

        Hashes rather than dates, so a laptop downloads exactly the files
        that changed and can tell a damaged download from a real one.
        """
        import hashlib

        files = []
        for unit in pack.files():
            digest = hashlib.sha256(unit.path.read_bytes()).hexdigest()
            files.append({"name": unit.name, "sha256": digest, "size": unit.size})
        toml = pack.path / "course.toml"
        from go2 import course as engine_course
        meta = engine_course.manifest(pack.path).get("course", {})
        body = {
            "id": meta.get("id", "course"),
            "title": meta.get("title", ""),
            "version": hashlib.sha256(
                "".join(f["sha256"] for f in files).encode()).hexdigest()[:12],
            "files": files,
            "course_toml": toml.read_text() if toml.is_file() else "",
            "engine": {"commit": _repo_commit(settings.repo_root)},
        }
        return JSONResponse(body, headers={"Cache-Control": "no-store, max-age=0"})

    @app.api_route("/course/files/{name}", methods=["GET", "HEAD"])
    def course_file(name: str):
        try:
            path = inside(pack.units, name)
        except CourseError as exc:
            return PlainTextResponse(f"{exc}\n", status_code=400)
        if not path.is_file():
            return PlainTextResponse("no such file in the course\n", status_code=404)
        return Response(path.read_bytes(), media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store, max-age=0"})

    # ------------------------------------------------ laptops: sign in

    def safe_next(target: str, default: str = "/student") -> str:
        """Only somewhere on this site. "//evil.example" is a host, not a path."""
        if (not target or not target.startswith("/") or target.startswith("//")
                or "\\" in target or "\n" in target or "\r" in target):
            return default
        return target

    def bearer(request: Request) -> dict | None:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return None
        return db.device_for_key(settings.db_path, header[7:].strip())

    @app.post("/api/connect/start")
    async def api_connect_start(request: Request):
        """A laptop asks to be connected. No identity yet -- that is the point."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        try:
            req = db.start_connect(settings.db_path, str(body.get("label", ""))[:80])
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=429)
        return JSONResponse({
            **req,
            "verify_url": f"{settings.base_url.rstrip('/')}/connect?code={req['user_code']}",
            "interval": 2,
        }, headers={"Cache-Control": "no-store"})

    @app.post("/api/connect/poll")
    async def api_connect_poll(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        result = db.poll_connect(settings.db_path, str(body.get("device_code", "")))
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/connect", response_class=HTMLResponse)
    def connect_page(request: Request, code: str = ""):
        """Where the laptop's "sign in" button lands, in the student's browser.

        Already signed in -- the usual case, since they came here for the
        install line -- this is one click. It is never zero clicks: if it
        were, anyone could send a student a link carrying THEIR OWN laptop's
        code, and the student's browser would quietly connect someone else's
        laptop to the student.
        """
        who = auth.current(request)
        req = db.connect_request(settings.db_path, code)
        if not who:
            return page(request, "student_login.html", error=None,
                        next=f"/connect?code={code}")
        if who.is_teacher:
            return page(request, "connect.html", req=None,
                        error="This connects a student's laptop. You are signed in "
                              "as a mentor -- sign out first, or have the student "
                              "do it.")
        return page(request, "connect.html", req=req, code=code,
                    error=None if req else
                    "That request has expired or was already used. Press "
                    "\u201csign in\u201d on the laptop again.")

    @app.post("/connect/decide")
    def connect_decide(request: Request, code: str = Form(""),
                       decision: str = Form(""), csrf: str = Form("")):
        who = auth.current(request)
        if not who or who.is_teacher or not who.slug or not _check_csrf(request, csrf):
            return RedirectResponse("/student", status_code=303)
        if not db.get(settings.db_path, who.slug):
            return RedirectResponse("/student", status_code=303)
        approve = decision == "yes"
        done = db.decide_connect(settings.db_path, code, who.slug if approve else None)
        return page(request, "connect.html", req=None, done=done, approved=approve)

    @app.get("/api/me")
    def api_me(request: Request):
        device = bearer(request)
        if not device:
            return JSONResponse({"error": "not connected"}, status_code=401)
        student = db.get(settings.db_path, device["slug"])
        if not student:
            return JSONResponse({"error": "not on the class list"}, status_code=401)
        return JSONResponse({"slug": student.slug, "name": student.display})

    # ------------------------------------------------ laptops: hand in

    @app.post("/api/handin")
    async def api_handin(request: Request, file: UploadFile = File(...)):
        """A copy of one file, from a connected laptop, kept for the mentor.

        A hand-in is a snapshot, never a sync: handing the same notebook in
        twice keeps both, so there is no "which version wins" to explain.
        """
        import hashlib
        import time as _t

        device = bearer(request)
        if not device:
            return JSONResponse({"error": "this laptop is not signed in"}, status_code=401)
        if not db.get(settings.db_path, device["slug"]):
            return JSONResponse({"error": "not on the class list"}, status_code=401)
        try:
            name = safe_name(file.filename or "")
        except CourseError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        data = await file.read(HANDIN_MAX + 1)
        if not data:
            return JSONResponse({"error": "that file is empty"}, status_code=400)
        if len(data) > HANDIN_MAX:
            return JSONResponse({"error": f"over the {HANDIN_MAX // 1024 // 1024} MB "
                                          "limit for a hand-in"}, status_code=413)
        folder = settings.handin_dir / device["slug"]
        folder.mkdir(parents=True, exist_ok=True)
        stamp = _t.strftime("%Y%m%d-%H%M%S", _t.gmtime())
        target = inside(folder, f"{stamp}_{name}")
        n = 2
        while target.exists():                      # two in the same second
            target = inside(folder, f"{stamp}-{n}_{name}")
            n += 1
        target.write_bytes(data)
        target.chmod(0o644)
        row = db.add_handin(settings.db_path, slug=device["slug"], name=name,
                            stored=target.name, size=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),
                            device_id=device["id"])
        return JSONResponse({"id": row["id"], "name": name,
                             "at": row["created_at"]})

    @app.get("/api/handins")
    def api_handins(request: Request):
        device = bearer(request)
        if not device:
            return JSONResponse({"error": "not connected"}, status_code=401)
        return JSONResponse({"handins": [
            {"id": h["id"], "name": h["name"], "at": h["created_at"], "size": h["size"]}
            for h in db.handins_for(settings.db_path, device["slug"])]})

    @app.get("/console/handin/{handin_id}")
    def console_handin(request: Request, handin_id: int, download: str = ""):
        if not teacher_only(request):
            return RedirectResponse("/console", status_code=303)
        h = db.handin(settings.db_path, handin_id)
        if not h:
            return PlainTextResponse("no such hand-in\n", status_code=404)
        try:
            path = inside(settings.handin_dir / h["slug"], h["stored"])
        except CourseError:
            return PlainTextResponse("no such hand-in\n", status_code=404)
        if not path.is_file():
            return PlainTextResponse("the file is missing on the server\n", status_code=404)
        if download:
            return Response(path.read_bytes(), media_type="application/octet-stream",
                            headers={"Content-Disposition":
                                     f'attachment; filename="{h["name"]}"'})
        student = db.get(settings.db_path, h["slug"])
        return page(request, "handin.html", h=h, student=student, body=_preview(path))

    @app.post("/console/device/{slug}/{device_id}/revoke")
    def console_revoke(request: Request, slug: str, device_id: int,
                       csrf: str = Form("")):
        if teacher_only(request) and _check_csrf(request, csrf):
            db.revoke_device(settings.db_path, device_id, slug)
        return RedirectResponse(f"/console/work/{slug}", status_code=303)

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy(request: Request):
        """Required to publish the Google sign-in app, and right anyway.

        Google will not let an external OAuth app out of testing without a
        privacy policy and terms. Both pages describe what this server
        actually does -- which is the only kind of privacy policy worth
        serving.
        """
        return page(request, "privacy.html")

    @app.get("/terms", response_class=HTMLResponse)
    def terms(request: Request):
        return page(request, "terms.html")

    @app.get("/student", response_class=HTMLResponse)
    def student_page(request: Request):
        who = auth.current(request)
        if not who:
            return page(request, "student_login.html", error=None)
        if who.is_teacher:
            # A teacher signing in at /student is almost always checking what
            # a student sees. Say so rather than showing an empty page.
            return page(request, "student_login.html",
                        error="You are signed in as a teacher. "
                              "Open the console, or sign out to use a join code.")
        student = db.get(settings.db_path, who.slug or "")
        if not student:
            auth.sign_out(request)
            return page(request, "student_login.html",
                        error="That student is no longer on the list.")
        lab = labs.info(student.slug)
        return page(request, "student.html", student=student, lab=lab,
                    links=labs.links(student.slug) if lab and lab.running else [])

    @app.post("/student/code")
    def student_code(request: Request, code: str = Form(""),
                     csrf: str = Form(""), next: str = Form("")):
        if not _check_csrf(request, csrf):
            return page(request, "student_login.html",
                        error="That page had been open a while. Try again.")
        student = db.by_code(settings.db_path, code)
        if not student:
            return page(request, "student_login.html",
                        error="That code does not match anyone. Check with "
                              "your mentor -- codes look like K7P2-M4QX.")
        auth.sign_in(request, auth.Who(kind="student", email=student.email,
                                       name=student.display, slug=student.slug))
        return RedirectResponse(safe_next(next), status_code=303)

    @app.get("/console", response_class=HTMLResponse)
    def console(request: Request):
        who = auth.current(request)
        if not who:
            return page(request, "console_login.html", error=None)
        if not teacher_only(request):
            return page(request, "console_login.html",
                        error=f"{who.email or who.name} is not a mentor on "
                              "this server. Another mentor can add you from "
                              "the console.")
        return page(request, "console.html", rows=roster(),
                    mentors=mentor_rows())

    @app.post("/console/add")
    async def console_add(request: Request, name: str = Form(""),
                          email: str = Form(""), csrf: str = Form(""),
                          lab: str = Form("")):
        who = teacher_only(request)
        if not who:
            return RedirectResponse("/console", status_code=303)
        if not _check_csrf(request, csrf):
            return page(request, "console.html", rows=roster(),
                        error="That page had been open a while. Try again.")
        slug = db.slug_for(name)
        try:
            student = db.add(settings.db_path, slug=slug, display=name.strip(),
                             email=email, created_by=who.email or who.name)
        except ValueError as exc:
            return page(request, "console.html", rows=roster(), error=str(exc))
        if lab:
            db.set_state(settings.db_path, student.slug, "provisioning",
                         "starting the lab...")
            app.state.pool.submit(provision, student.slug)
        return RedirectResponse("/console", status_code=303)

    @app.post("/console/lab/{slug}/{action}")
    def console_lab(request: Request, slug: str, action: str,
                    csrf: str = Form("")):
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console", status_code=303)
        student = db.get(settings.db_path, slug)
        if not student:
            return RedirectResponse("/console", status_code=303)
        try:
            if action == "start":
                db.set_state(settings.db_path, slug, "provisioning", "starting...")
                app.state.pool.submit(provision, slug)
            elif action == "stop":
                labs.down(slug)
                db.set_state(settings.db_path, slug, "stopped", "")
            elif action == "sync":
                labs.sync(slug)
            elif action == "forget":
                db.remove(settings.db_path, slug)
        except LabError as exc:
            db.set_state(settings.db_path, slug, "failed", str(exc))
        return RedirectResponse("/console", status_code=303)

    # ------------------------------------------------- course material

    @app.get("/console/course", response_class=HTMLResponse)
    def console_course(request: Request, preview: str = ""):
        who = teacher_only(request)
        if not who:
            return RedirectResponse("/console", status_code=303)
        mine = db.lab_for_mentor(settings.db_path, who.email or "")
        return page(request, "course.html", files=pack.files(), pack=pack,
                    my_lab=mine, my_files=_lab_files(mine),
                    preview=preview)

    @app.post("/console/course/upload")
    async def console_course_upload(request: Request, csrf: str = Form(""),
                                    upload: UploadFile = File(...)):
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console/course", status_code=303)
        try:
            data = await upload.read()
            pack.write(upload.filename or "", data)
        except CourseError as exc:
            return page(request, "course.html", files=pack.files(), pack=pack,
                        my_lab=db.lab_for_mentor(settings.db_path, who.email or ""),
                        my_files=_lab_files(db.lab_for_mentor(settings.db_path,
                                                              who.email or "")),
                        error=str(exc))
        return RedirectResponse("/console/course", status_code=303)

    @app.post("/console/course/remove")
    def console_course_remove(request: Request, name: str = Form(""),
                              csrf: str = Form("")):
        who = teacher_only(request)
        if who and _check_csrf(request, csrf):
            try:
                pack.remove(name)
            except CourseError:
                pass
        return RedirectResponse("/console/course", status_code=303)

    @app.post("/console/course/handout")
    def console_course_handout(request: Request, csrf: str = Form(""),
                               dry: str = Form("")):
        """Hand the pack to the class, or show what that would do first."""
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console/course", status_code=303)
        try:
            out = labs.sync_all(dry_run=bool(dry))
        except LabError as exc:
            out = str(exc)
        return page(request, "course.html", files=pack.files(), pack=pack,
                    my_lab=db.lab_for_mentor(settings.db_path, who.email or ""),
                    my_files=_lab_files(db.lab_for_mentor(settings.db_path,
                                                          who.email or "")),
                    preview=out or "(nothing to do -- everyone is up to date)",
                    dry=bool(dry))

    # ------------------------------------------------- a mentor's own lab

    def _lab_files(student) -> list[str]:
        """What a mentor has in their lab, as candidates to publish."""
        if not student:
            return []
        work = labs.work_dir(student.slug)
        if not work:
            return []
        return sorted(p.name for p in work.iterdir()
                      if p.is_file() and not p.name.startswith("."))

    @app.post("/console/mylab")
    def console_my_lab(request: Request, csrf: str = Form("")):
        """Give a mentor a lab of their own, the same way a student gets one."""
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console/course", status_code=303)
        existing = db.lab_for_mentor(settings.db_path, who.email or "")
        if not existing:
            base = db.slug_for((who.email or who.name).split("@")[0]) or "mentor"
            slug = db.slug_for(f"mentor-{base}")
            student = db.add(settings.db_path, slug=slug,
                             display=f"{who.name} (mentor lab)",
                             email=who.email, created_by=who.email or who.name,
                             kind="mentor")
            existing = student
        db.set_state(settings.db_path, existing.slug, "provisioning", "starting...")
        app.state.pool.submit(provision, existing.slug)
        return RedirectResponse("/console/course", status_code=303)

    @app.post("/console/course/publish")
    def console_publish(request: Request, csrf: str = Form(""),
                        names: list[str] = Form(default=[])):
        """Copy files out of a mentor's lab into the class's pack."""
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console/course", status_code=303)
        mine = db.lab_for_mentor(settings.db_path, who.email or "")
        work = labs.work_dir(mine.slug) if mine else None
        errors = []
        if work:
            for name in names:
                try:
                    pack.copy_in(inside(work, name))
                except (CourseError, OSError) as exc:
                    errors.append(f"{name}: {exc}")
        return page(request, "course.html", files=pack.files(), pack=pack,
                    my_lab=mine, my_files=_lab_files(mine),
                    error=" / ".join(errors) or None,
                    preview=("" if errors else
                             f"published {len(names)} file(s) into the pack -- "
                             "hand them out when you are ready"))

    # ------------------------------------------------- a student's work

    @app.get("/console/work/{slug}", response_class=HTMLResponse)
    def console_work(request: Request, slug: str, open: str = ""):
        """What a student has in their folder, for a mentor to look at."""
        if not teacher_only(request):
            return RedirectResponse("/console", status_code=303)
        student = db.get(settings.db_path, slug)
        work = labs.work_dir(slug) if student else None
        listing, shown, body = [], None, None
        if work:
            listing = sorted(
                ({"name": p.name, "size": p.stat().st_size}
                 for p in work.iterdir()
                 if p.is_file() and not p.name.startswith(".")),
                key=lambda f: f["name"])
            if open:
                try:
                    shown = inside(work, open)
                    body = _preview(shown)
                except (CourseError, OSError) as exc:
                    body = f"(cannot show this file: {exc})"
        return page(request, "work.html", student=student, files=listing,
                    work=work, shown=shown.name if shown else None, body=body,
                    handins=db.handins_for(settings.db_path, slug) if student else [],
                    devices=db.devices_for(settings.db_path, slug) if student else [])

    @app.get("/console/work/{slug}/download")
    def console_download(request: Request, slug: str, name: str = ""):
        if not teacher_only(request):
            return RedirectResponse("/console", status_code=303)
        work = labs.work_dir(slug)
        if not work:
            return PlainTextResponse("no such lab\n", status_code=404)
        try:
            path = inside(work, name)
        except CourseError as exc:
            return PlainTextResponse(f"{exc}\n", status_code=400)
        if not path.is_file():
            return PlainTextResponse("no such file\n", status_code=404)
        return Response(path.read_bytes(), media_type="application/octet-stream",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{path.name}"'})

    @app.post("/console/mentors/add")
    def console_add_mentor(request: Request, email: str = Form(""),
                           csrf: str = Form("")):
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console", status_code=303)
        try:
            db.add_mentor(settings.db_path, email, added_by=who.email or who.name)
        except ValueError as exc:
            return page(request, "console.html", rows=roster(),
                        mentors=mentor_rows(), error=str(exc))
        return RedirectResponse("/console", status_code=303)

    @app.post("/console/mentors/remove")
    def console_remove_mentor(request: Request, email: str = Form(""),
                              csrf: str = Form("")):
        who = teacher_only(request)
        if not who or not _check_csrf(request, csrf):
            return RedirectResponse("/console", status_code=303)
        if settings.is_teacher(email):
            # Named in the environment: not ours to revoke, and the error
            # says where to go instead of failing silently.
            return page(request, "console.html", rows=roster(),
                        mentors=mentor_rows(),
                        error=f"{email} is set in GO2_TEACHERS on the server, "
                              "so it cannot be removed from here.")
        db.remove_mentor(settings.db_path, email)
        return RedirectResponse("/console", status_code=303)

    @app.get("/console/status.json")
    def console_status(request: Request):
        if not teacher_only(request):
            return JSONResponse({"error": "not signed in"}, status_code=403)
        return JSONResponse({
            "students": [
                {"slug": r["student"].slug, "state": r["state"],
                 "note": r["student"].lab_note,
                 "port": r["lab"].port if r["lab"] else None}
                for r in roster()
            ]})

    # ---------------------------------------------------------------- auth

    @app.get("/login/google")
    async def login_google(request: Request, next: str = "/student"):
        if oauth is None:
            return page(request, "needs_google.html", next=next)
        request.session["next"] = safe_next(next)
        redirect_uri = f"{settings.base_url.rstrip('/')}/auth/google/callback"
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @app.get("/auth/google/callback")
    async def google_callback(request: Request):
        if oauth is None:
            return RedirectResponse("/", status_code=303)
        try:
            token = await oauth.google.authorize_access_token(request)
        except Exception:  # noqa: BLE001 - every failure here is "try again"
            return page(request, "console_login.html",
                        error="Google sign-in did not complete. Try again.")
        info = token.get("userinfo") or {}
        email = (info.get("email") or "").lower()
        if not info.get("email_verified", True):
            return page(request, "console_login.html",
                        error="That Google account has no verified address.")
        who = auth.identify(settings, email, info.get("name") or email)
        target = request.session.pop("next", "/student")
        if not who:
            return page(request, "console_login.html",
                        error=f"{email} is not on this class's list. A mentor "
                              "can add you, or you can sign in with a join code.")
        auth.sign_in(request, who)
        if who.is_teacher and target == "/student":
            target = "/console"
        return RedirectResponse(target, status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        auth.sign_out(request)
        return RedirectResponse("/", status_code=303)

    # ------------------------------------------------------------ the script

    # HEAD as well as GET: a gateway operator checks with `curl -I`, and an
    # uptime monitor may only ever send HEAD. A 405 there reads as an outage.
    @app.api_route(settings.install_path, methods=["GET", "HEAD"])
    @app.api_route("/install/mac.sh", methods=["GET", "HEAD"])
    def install_script():
        """The installer, from this checkout, uncached.

        Same bytes GitHub serves, from a name a 14-year-old can copy off a
        slide. Caching is off on purpose: the five-minute CDN window on
        raw.githubusercontent is exactly the bug this mirror should not
        reproduce.
        """
        script = settings.repo_root / "install" / "mac.sh"
        if not script.is_file():
            return PlainTextResponse("installer not found on this server\n",
                                     status_code=404)
        return Response(
            script.read_text(),
            media_type="text/x-shellscript; charset=utf-8",
            headers={"Cache-Control": "no-store, max-age=0",
                     "X-Content-Type-Options": "nosniff"})

    def _preview(path: pathlib.Path, limit: int = 60_000) -> str:
        """A readable view of one file, for marking rather than running.

        A notebook is shown as its cells' source: outputs are noise when the
        question is "what did they write", and some of them are images.
        """
        import json

        data = path.read_bytes()[: limit * 2]
        if path.suffix.lower() == ".ipynb":
            try:
                nb = json.loads(data.decode("utf-8"))
            except Exception:  # noqa: BLE001 - fall through to raw text
                nb = None
            if isinstance(nb, dict) and isinstance(nb.get("cells"), list):
                parts = []
                for i, cell in enumerate(nb["cells"], 1):
                    kind = cell.get("cell_type", "?")
                    source = "".join(cell.get("source", []))
                    parts.append(f"--- cell {i} [{kind}] ---\n{source}")
                return "\n\n".join(parts)[:limit]
        try:
            return data.decode("utf-8")[:limit]
        except UnicodeDecodeError:
            return (f"({path.suffix.lstrip('.') or 'binary'} file, "
                    f"{path.stat().st_size // 1024} KB -- download it to open)")

    @app.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        return PlainTextResponse("ok\n")

    @app.api_route("/healthz.json", methods=["GET", "HEAD"])
    def healthz_json(request: Request):
        """What this request looks like by the time it gets here.

        For whoever is configuring the reverse proxy. `scheme` is the one
        worth looking at: it reads https only when X-Forwarded-Proto arrives
        AND the proxy's address is trusted, so it is a real test of the
        forwarding, which the session cookie is not -- that cookie is marked
        Secure because of the configured public name, whatever the request
        did.
        """
        client = request.client.host if request.client else None
        return JSONResponse({
            "ok": True,
            "scheme": request.url.scheme,
            "host": request.headers.get("host"),
            "client": client,
            "forwarded_proto": request.headers.get("x-forwarded-proto"),
            "base_url": settings.base_url,
            "google": settings.google_configured,
        })

    return app


app = None  # built by __main__ or by a test, never at import time
