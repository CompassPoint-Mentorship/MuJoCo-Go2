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

from . import auth, db
from .labs import LabError, Labs
from .settings import Settings

HERE = pathlib.Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db.init(settings.db_path)
    labs = Labs(settings)
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
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.globals["settings"] = settings

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
        who = auth.current(request)
        return who if who and who.is_teacher else None

    def provision(slug: str) -> None:
        """create + up, in a worker thread, recording what happened."""
        try:
            if not labs.exists(slug):
                labs.create(slug)
            labs.up(slug)
            db.set_state(settings.db_path, slug, "running", "")
        except LabError as exc:
            db.set_state(settings.db_path, slug, "failed", str(exc))

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
                     csrf: str = Form("")):
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
        return RedirectResponse("/student", status_code=303)

    @app.get("/console", response_class=HTMLResponse)
    def console(request: Request):
        who = auth.current(request)
        if not who:
            return page(request, "console_login.html", error=None)
        if not who.is_teacher:
            return page(request, "console_login.html",
                        error=f"{who.email or who.name} is not a mentor on "
                              "this server. Ask for your address to be added "
                              "to GO2_TEACHERS.")
        return page(request, "console.html", rows=roster())

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
        request.session["next"] = next if next.startswith("/") else "/student"
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

    @app.get(settings.install_path)
    @app.get("/install/mac.sh")
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

    @app.get("/healthz")
    def healthz():
        return PlainTextResponse("ok\n")

    return app


app = None  # built by __main__ or by a test, never at import time
