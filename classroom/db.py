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
    created_by  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS students_email ON students (email);
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
        email: str | None, created_by: str) -> Student:
    if not valid_slug(slug):
        raise ValueError(
            f"{slug!r} is not a usable name: letters, digits and hyphens, "
            "starting with a letter.")
    with connect(path) as conn:
        try:
            conn.execute(
                "INSERT INTO students (slug, display, email, join_code,"
                " lab_state, lab_note, created_at, created_by)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (slug, display.strip() or slug,
                 (email or "").strip().lower() or None,
                 new_code(), "none", "", _now(), created_by))
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


def all_students(path: pathlib.Path) -> list[Student]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT * FROM students ORDER BY created_at, slug").fetchall()
    return [Student.from_row(r) for r in rows]


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


def remove(path: pathlib.Path, slug: str) -> None:
    """Take a student off the roster. Their work dir and lab are untouched.

    Deleting a student's files is a decision for a person with a terminal,
    not a button on a web page -- see the rules in deploy/go2-student.sh.
    """
    with connect(path) as conn:
        conn.execute("DELETE FROM students WHERE slug = ?", (slug,))
