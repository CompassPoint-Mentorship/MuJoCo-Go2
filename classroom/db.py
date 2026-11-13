"""The roster: one SQLite file, one table, no ORM.

A class is thirty rows. SQLite handles that on a laptop, needs no service to
keep running next to the one we are already supervising, and is a single
file a mentor can copy before trying something. Every function opens its own
connection: the cost is irrelevant at this size, and it keeps threads --
there is a provisioning thread -- from sharing a handle.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import pathlib
import re
import secrets
import sqlite3
from dataclasses import dataclass

from .settings import SLUG_PATTERN

# No 0/O/1/I/L: a join code gets read aloud across a classroom and typed by
# someone who has never seen it written down.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

STATES = ("none", "provisioning", "running", "stopped", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    slug        TEXT PRIMARY KEY,
    display     TEXT NOT NULL,
    email       TEXT,
    join_code   TEXT NOT NULL UNIQUE,
    lab_state   TEXT NOT NULL DEFAULT 'none',
    lab_note    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL DEFAULT '',
    -- 'student', or 'mentor' for a mentor's own lab: the same machinery,
    -- kept off the class roster so a mentor testing next week's exercise is
    -- not something students see or sync affects differently.
    kind        TEXT NOT NULL DEFAULT 'student'
);
CREATE INDEX IF NOT EXISTS students_email ON students (email);

-- Mentors added through the console, on top of the ones GO2_TEACHERS names.
-- `added_by` is not decoration: opening the console means running docker on
-- the class server, so who granted that, and when, is worth being able to
-- answer later.
-- A laptop connected to one student, by the device-sign-in flow. Only the
-- key's hash is stored: the key itself exists on the laptop and nowhere else,
-- so a copied database cannot be used to hand in as anyone.
CREATE TABLE IF NOT EXISTS devices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key_hash    TEXT NOT NULL UNIQUE,
    slug        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL DEFAULT '',
    revoked     INTEGER NOT NULL DEFAULT 0
);

-- A laptop asking to be connected. `user_code` is short and appears in the
-- browser; `device_code` is long and only the laptop knows it, so knowing the
-- short code -- it is in a URL -- is not enough to collect the key.
CREATE TABLE IF NOT EXISTS connect_requests (
    device_code TEXT PRIMARY KEY,
    user_code   TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending/approved/denied/collected
    slug        TEXT,
    key         TEXT                              -- held only until collected
);

-- Homework handed in from a laptop. Copies, never synced: two hand-ins of
-- the same file are two rows, newest first.
CREATE TABLE IF NOT EXISTS handins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    stored      TEXT NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    device_id   INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS handins_slug ON handins (slug, created_at);

CREATE TABLE IF NOT EXISTS mentors (
    email       TEXT PRIMARY KEY,
    display     TEXT NOT NULL DEFAULT '',
    added_by    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
"""


@dataclass
class Student:
    slug: str
    display: str
    email: str | None
    join_code: str
    lab_state: str
    lab_note: str
    created_at: str
    created_by: str
    kind: str = "student"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Student":
        return cls(**{k: row[k] for k in row.keys()})


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def connect(path: pathlib.Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(path: pathlib.Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        # Work dirs created before `kind` existed: adding it here rather than
        # in a migration tool, because one column is not worth a dependency.
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(students)")}
        if "kind" not in columns:
            conn.execute("ALTER TABLE students ADD COLUMN kind TEXT NOT NULL"
                         " DEFAULT 'student'")


def new_code(length: int = 8) -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
    return f"{raw[:4]}-{raw[4:]}"


def slug_for(name: str) -> str:
    """A container-safe name from whatever the teacher typed.

    Teachers type "Ada Lovelace"; docker, directories and ports want
    "ada-lovelace". Doing it here rather than asking the teacher to type a
    slug keeps one validated shape in the database, whatever was typed.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:31]
    if slug and not slug[0].isalpha():
        slug = f"s-{slug}"[:31]
    return slug.rstrip("-")


def valid_slug(slug: str) -> bool:
    return bool(re.match(SLUG_PATTERN, slug))


def add(path: pathlib.Path, *, slug: str, display: str,
        email: str | None, created_by: str, kind: str = "student") -> Student:
    if not valid_slug(slug):
        raise ValueError(
            f"{slug!r} is not a usable name: letters, digits and hyphens, "
            "starting with a letter.")
    with connect(path) as conn:
        try:
            conn.execute(
                "INSERT INTO students (slug, display, email, join_code,"
                " lab_state, lab_note, created_at, created_by, kind)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (slug, display.strip() or slug,
                 (email or "").strip().lower() or None,
                 new_code(), "none", "", _now(), created_by, kind))
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"{slug} is already on the list") from exc
    found = get(path, slug)
    assert found is not None
    return found


def get(path: pathlib.Path, slug: str) -> Student | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM students WHERE slug = ?",
                           (slug,)).fetchone()
    return Student.from_row(row) if row else None


def by_email(path: pathlib.Path, email: str) -> Student | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM students WHERE email = ?",
                           (email.strip().lower(),)).fetchone()
    return Student.from_row(row) if row else None


def by_code(path: pathlib.Path, code: str) -> Student | None:
    """Look a join code up, forgiving how it was typed.

    Students type codes with the hyphen missing, in lower case, with a space
    in the middle. None of that is worth a failed login in front of a class.
    """
    tidy = re.sub(r"[^A-Z0-9]", "", code.upper())
    if not tidy:
        return None
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM students").fetchall()
    for row in rows:
        if re.sub(r"[^A-Z0-9]", "", row["join_code"]) == tidy:
            return Student.from_row(row)
    return None


def all_students(path: pathlib.Path, kind: str = "student") -> list[Student]:
    """The class roster. Mentor labs are `kind='mentor'` and asked for by name."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM students WHERE kind = ? ORDER BY created_at, slug",
            (kind,)).fetchall()
    return [Student.from_row(r) for r in rows]


def lab_for_mentor(path: pathlib.Path, email: str) -> Student | None:
    """A mentor's own lab, if they have made one."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT * FROM students WHERE kind = 'mentor' AND email = ?",
            ((email or "").strip().lower(),)).fetchone()
    return Student.from_row(row) if row else None


def set_state(path: pathlib.Path, slug: str, state: str, note: str = "") -> None:
    if state not in STATES:
        raise ValueError(f"unknown lab state {state!r}")
    with connect(path) as conn:
        conn.execute("UPDATE students SET lab_state = ?, lab_note = ?"
                     " WHERE slug = ?", (state, note[:500], slug))


def set_email(path: pathlib.Path, slug: str, email: str | None) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE students SET email = ? WHERE slug = ?",
                     ((email or "").strip().lower() or None, slug))


# ------------------------------------------------------------ laptops

CONNECT_SECONDS = 600            # ten minutes to click "yes" in the browser


def _hash_key(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()


def start_connect(path: pathlib.Path, label: str) -> dict:
    """A laptop asks to be connected. Returns both codes."""
    import time as _t

    now = _t.time()
    with connect(path) as conn:
        conn.execute("DELETE FROM connect_requests WHERE expires_at < ?", (now,))
        pending = conn.execute("SELECT COUNT(*) FROM connect_requests").fetchone()[0]
        if pending > 500:
            # Unauthenticated, so bounded: nobody needs five hundred laptops
            # waiting for approval at once.
            raise ValueError("too many laptops are waiting to connect; try again shortly")
        device_code = secrets.token_urlsafe(32)
        user_code = new_code()
        conn.execute(
            "INSERT INTO connect_requests (device_code, user_code, label,"
            " created_at, expires_at) VALUES (?,?,?,?,?)",
            (device_code, user_code, label.strip()[:80], now, now + CONNECT_SECONDS))
    return {"device_code": device_code, "user_code": user_code,
            "expires_in": CONNECT_SECONDS}


def connect_request(path: pathlib.Path, user_code: str) -> dict | None:
    """A pending request, by the short code a browser carries."""
    import time as _t

    tidy = re.sub(r"[^A-Z0-9]", "", (user_code or "").upper())
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM connect_requests WHERE status = 'pending'"
            " AND expires_at > ?", (_t.time(),)).fetchall()
    for row in rows:
        if re.sub(r"[^A-Z0-9]", "", row["user_code"]) == tidy:
            return dict(row)
    return None


def decide_connect(path: pathlib.Path, user_code: str, slug: str | None) -> bool:
    """A signed-in student says yes (slug) or no (None). True if it applied."""
    req = connect_request(path, user_code)
    if not req:
        return False
    with connect(path) as conn:
        if slug is None:
            conn.execute("UPDATE connect_requests SET status = 'denied'"
                         " WHERE device_code = ?", (req["device_code"],))
            return True
        key = "go2_" + secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO devices (key_hash, slug, label, created_at)"
            " VALUES (?,?,?,?)", (_hash_key(key), slug, req["label"], _now()))
        conn.execute(
            "UPDATE connect_requests SET status = 'approved', slug = ?, key = ?"
            " WHERE device_code = ?", (slug, key, req["device_code"]))
    return True


def poll_connect(path: pathlib.Path, device_code: str) -> dict:
    """What a laptop learns when it asks. The key is handed over exactly once."""
    import time as _t

    with connect(path) as conn:
        row = conn.execute("SELECT * FROM connect_requests WHERE device_code = ?",
                           (device_code or "",)).fetchone()
        if not row:
            return {"status": "unknown"}
        if row["status"] == "pending":
            return {"status": "expired" if row["expires_at"] < _t.time() else "pending"}
        if row["status"] == "approved":
            # Collected once, then forgotten here: from now on only its hash
            # exists on this side.
            conn.execute("UPDATE connect_requests SET status = 'collected', key = NULL"
                         " WHERE device_code = ?", (device_code,))
            student = conn.execute("SELECT display FROM students WHERE slug = ?",
                                   (row["slug"],)).fetchone()
            return {"status": "approved", "key": row["key"], "slug": row["slug"],
                    "name": student["display"] if student else row["slug"]}
        return {"status": row["status"]}


def device_for_key(path: pathlib.Path, key: str) -> dict | None:
    if not key:
        return None
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM devices WHERE key_hash = ? AND revoked = 0",
                           (_hash_key(key),)).fetchone()
        if row:
            conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?",
                         (_now(), row["id"]))
    return dict(row) if row else None


def devices_for(path: pathlib.Path, slug: str) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM devices WHERE slug = ? AND revoked = 0"
                            " ORDER BY created_at DESC", (slug,)).fetchall()
    return [dict(r) for r in rows]


def revoke_device(path: pathlib.Path, device_id: int, slug: str) -> None:
    with connect(path) as conn:
        conn.execute("UPDATE devices SET revoked = 1 WHERE id = ? AND slug = ?",
                     (device_id, slug))


# ------------------------------------------------------------ hand-ins

def add_handin(path: pathlib.Path, *, slug: str, name: str, stored: str,
               size: int, sha256: str, device_id: int | None) -> dict:
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO handins (slug, name, stored, size, sha256, device_id,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (slug, name, stored, size, sha256, device_id, _now()))
        row = conn.execute("SELECT * FROM handins WHERE id = ?",
                           (cur.lastrowid,)).fetchone()
    return dict(row)


def handins_for(path: pathlib.Path, slug: str) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute("SELECT * FROM handins WHERE slug = ?"
                            " ORDER BY created_at DESC, id DESC", (slug,)).fetchall()
    return [dict(r) for r in rows]


def handin(path: pathlib.Path, handin_id: int) -> dict | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM handins WHERE id = ?",
                           (handin_id,)).fetchone()
    return dict(row) if row else None


# ------------------------------------------------------------------ mentors

def add_mentor(path: pathlib.Path, email: str, added_by: str,
               display: str = "") -> str:
    """Let one more Google address open the console. Returns the address.

    No validation beyond "looks like an address": the gate is that only a
    mentor can call this, and Google decides whether the address is real.
    """
    email = (email or "").strip().lower()
    if "@" not in email or " " in email or len(email) < 5:
        raise ValueError(f"{email or '(nothing)'} is not an email address")
    with connect(path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mentors (email, display, added_by, created_at)"
            " VALUES (?,?,?,?)", (email, display.strip(), added_by, _now()))
    return email


def mentors(path: pathlib.Path) -> list[dict]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM mentors ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def is_mentor(path: pathlib.Path, email: str) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    with connect(path) as conn:
        return conn.execute("SELECT 1 FROM mentors WHERE email = ?",
                            (email,)).fetchone() is not None


def remove_mentor(path: pathlib.Path, email: str) -> None:
    with connect(path) as conn:
        conn.execute("DELETE FROM mentors WHERE email = ?",
                     ((email or "").strip().lower(),))


def remove(path: pathlib.Path, slug: str) -> None:
    """Take a student off the roster. Their work dir and lab are untouched.

    Deleting a student's files is a decision for a person with a terminal,
    not a button on a web page -- see the rules in deploy/go2-student.sh.
    """
    with connect(path) as conn:
        conn.execute("DELETE FROM students WHERE slug = ?", (slug,))
