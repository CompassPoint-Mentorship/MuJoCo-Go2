"""Everything the classroom app needs to know, read from the environment.

Nothing here has a default that could surprise someone in production: no
teacher is hard-coded, no secret is invented and silently reused, and the
app refuses to pretend Google sign-in works when it has no client id. A
missing setting shows up as a page saying what to set, not as a 500 at the
moment a teacher is standing in front of a class.
"""

from __future__ import annotations

import os
import pathlib
import secrets
from dataclasses import dataclass, field

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# A student name becomes a container name, a directory name and part of a
# file path, so it is checked against this before it reaches any of them.
SLUG_PATTERN = r"^[a-z][a-z0-9-]{1,30}$"


def load_dotenv(path: pathlib.Path | None = None) -> None:
    """Read KEY=VALUE lines from .env, without overriding the real environment.

    The deployment uses systemd's EnvironmentFile; this is for running the
    app by hand on this box, where the same values already live in .env.
    """
    path = path or (REPO_ROOT / ".env")
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    # Where the site is served from, as the browser sees it. Google checks the
    # redirect URI against this exactly, so a wrong value is the single most
    # common reason sign-in fails -- it is read from one place for that reason.
    base_url: str = "http://127.0.0.1:8500"
    db_path: pathlib.Path = REPO_ROOT / "classroom" / "classroom.db"
    session_secret: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    teachers: frozenset[str] = field(default_factory=frozenset)
    repo_root: pathlib.Path = REPO_ROOT
    # Off in tests and in a dry run: with it off, the console manages the
    # roster and never shells out to docker.
    provision: bool = True
    # The public name students use for the install line. It is a mirror of
    # the same script GitHub serves, so the path deliberately matches.
    install_path: str = "/CompassPoint-Mentorship/MuJoCo-Go2/main/install/mac.sh"
    # Whose X-Forwarded-* headers to believe. Anyone who can open a socket to
    # this port can claim any client address and any scheme, so this is the
    # gateway's address and nobody else's. "*" trusts every caller, which is
    # only safe when nothing but the proxy can reach the port at all.
    trusted_proxy: str = "127.0.0.1,::1"
    # The class's own course pack: what the console edits and what every
    # sync hands out. Separate from the pack bundled in the go2 package,
    # which is what a laptop install gets and must stay pristine.
    course_pack: pathlib.Path = REPO_ROOT / "courses" / "live"
    # Homework handed in from laptops: small files, kept per student.
    handin_dir: pathlib.Path = REPO_ROOT / "handins"
    # Invitations are the one email the platform sends itself, from the
    # organisation's address. Announcements and feedback are not: those open
    # as drafts in the mentor's own Gmail. Plain SMTP, so the mailbox is a
    # setting -- tech@ today, a dedicated sending address tomorrow.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""
    mail_daily_limit: int = 300


    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host and self.mail_from
                    and (self.smtp_password or not self.smtp_user))

    @property
    def install_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.install_path}"

    def is_teacher(self, email: str | None) -> bool:
        return bool(email) and email.strip().lower() in self.teachers

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        teachers = {e.strip().lower()
                    for e in os.environ.get("GO2_TEACHERS", "").split(",")
                    if e.strip()}
        secret = os.environ.get("GO2_SESSION_SECRET", "")
        if not secret:
            # A new secret per start means everyone is logged out by a
            # restart. That is survivable and loud; a hard-coded fallback
            # would be neither.
            secret = secrets.token_urlsafe(32)
        return cls(
            base_url=os.environ.get("GO2_BASE_URL", "http://127.0.0.1:8500"),
            db_path=pathlib.Path(os.environ.get(
                "GO2_CLASSROOM_DB", str(REPO_ROOT / "classroom" / "classroom.db"))),
            session_secret=secret,
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            teachers=frozenset(teachers),
            provision=os.environ.get("GO2_PROVISION", "1") != "0",
            trusted_proxy=os.environ.get("GO2_TRUSTED_PROXY", "127.0.0.1,::1"),
            course_pack=pathlib.Path(os.environ.get(
                "GO2_COURSE_PACK", str(REPO_ROOT / "courses" / "live"))),
            handin_dir=pathlib.Path(os.environ.get(
                "GO2_HANDIN_DIR", str(REPO_ROOT / "handins"))),
            smtp_host=os.environ.get("SMTP_HOST", ""),
            smtp_port=int(os.environ.get("SMTP_PORT", "587") or 587),
            smtp_user=os.environ.get("SMTP_USER", ""),
            smtp_password=os.environ.get("SMTP_PASSWORD", "").replace(" ", ""),
            mail_from=os.environ.get("MAIL_FROM", ""),
            mail_daily_limit=int(os.environ.get("MAIL_DAILY_LIMIT", "300") or 300),
        )
