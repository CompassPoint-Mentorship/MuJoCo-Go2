"""Who is asking: Google for anyone who has an account, a join code for the rest.

Two ways in, deliberately:

  * **Google** is how a teacher signs in, and the only way the console can be
    reached. A teacher is whoever appears in GO2_TEACHERS -- an allowlist,
    not a role in a database, because the thing it protects is a button that
    runs docker on the class server.
  * **A join code** is how a student signs in when they have no Google
    account the school lets them use, which is most of them. The code is
    issued by the teacher and names exactly one student.

A session says what kind of person this is and, for a student, which row on
the roster they are. Nothing else is trusted from the browser: the student
page looks everything up from that slug, so a student cannot see another
student's lab token by editing anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.requests import Request

from . import db
from .settings import Settings

SESSION_KEY = "who"


@dataclass(frozen=True)
class Who:
    kind: str              # "teacher" or "student"
    email: str | None
    name: str
    slug: str | None = None

    @property
    def is_teacher(self) -> bool:
        return self.kind == "teacher"


def sign_in(request: Request, who: Who) -> None:
    request.session[SESSION_KEY] = {
        "kind": who.kind, "email": who.email,
        "name": who.name, "slug": who.slug,
    }


def sign_out(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def current(request: Request) -> Who | None:
    raw = request.session.get(SESSION_KEY)
    if not isinstance(raw, dict) or raw.get("kind") not in ("teacher", "student"):
        return None
    return Who(kind=raw["kind"], email=raw.get("email"),
               name=raw.get("name") or "", slug=raw.get("slug"))


def identify(settings: Settings, email: str, name: str) -> Who | None:
    """Turn a verified Google address into a teacher, a student, or nobody.

    Order matters: the teacher list wins, so a mentor who is also on the
    roster for testing still gets the console.
    """
    email = (email or "").strip().lower()
    if settings.is_teacher(email):
        return Who(kind="teacher", email=email, name=name or email)
    student = db.by_email(settings.db_path, email) if email else None
    if student:
        return Who(kind="student", email=email,
                   name=student.display, slug=student.slug)
    return None


def oauth_client(settings: Settings):
    """The Google client, or None when it has not been configured yet.

    Returning None rather than raising lets every page keep working with
    join codes while a mentor is still filling in the Google console.
    """
    if not settings.google_configured:
        return None
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
