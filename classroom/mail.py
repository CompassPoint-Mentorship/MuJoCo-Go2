"""Email drafts: the console writes them, the mentor sends them.

The console never sends mail. When mentor A emails the class, the message
goes out from A's own Gmail, lands in A's Sent folder, and replies come back
to A -- because A pressed Send. The console's whole job is to write a good
draft and open it in the right mailbox.

That was a choice, and the alternative was considered: Gmail's send-only
API permission would let the console send in one click, but the server would
then hold a send-as-you credential for every mentor, and a compromised class
server could email parents as their child's mentor. A draft costs one click
and stores nothing. It also means a mentor reads every message to a student
before it leaves, which for a class that includes under-13s is the right
default anyway.

**Which mailbox.** People commonly have several Google accounts open in one
browser, and a bare Gmail link opens in whichever is the default -- so "send
through A" would quietly become "send through A's personal account". Every
draft carries `authuser=<A's address>`, the account A signed into the console
with. A `mailto:` link is offered too, for a mentor whose address is not a
Gmail mailbox.

**Only addresses the class already has.** Every function takes a student or
a mentor, never a free-form address.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

from .settings import Settings

GMAIL_COMPOSE = "https://mail.google.com/mail/"


# ----------------------------------------------------------------- content
#
# Plain text: what Gmail's compose window takes, what every client shows, and
# what a mentor can edit before sending without fighting formatting.

def student_invite(settings: Settings, student, mentor_name: str) -> tuple[str, str]:
    site = settings.base_url.rstrip("/")
    subject = "Your Robo Dog class: how to start"
    body = f"""Hi {student.display},

You have been added to the Robo Dog class at CompassPoint Mentorship, where you
will program a robot dog in Python.

YOUR JOIN CODE:  {student.join_code}

Keep it to yourself -- it signs you in, like a password.

1. Open your student page and sign in with the code:
   {site}/student

2. Put the dog on your own laptop. Open Terminal (Mac or Ubuntu) and paste
   this one line:

   curl -fsSL {settings.install_url} | bash

   It installs nothing system-wide and never asks for your password. Your
   student page has the same line with a copy button.

3. Open "Go2 Lab" from your Applications folder. In the lab, press "sign in"
   at the top of the simulator so you can hand in homework.

See you in class!
{mentor_name}
"""
    return subject, body


def mentor_invite(settings: Settings, email: str, mentor_name: str) -> tuple[str, str]:
    site = settings.base_url.rstrip("/")
    subject = "You are a mentor on the Robo Dog class console"
    body = f"""Hello,

I have added you as a mentor for the Robo Dog class at CompassPoint Mentorship.

Sign in to the console with this Google account ({email}):
   {site}/console

From there you can add students, start their labs, hand out course material,
read handed-in homework and email the class.

{mentor_name}
"""
    return subject, body


def announcement(settings: Settings, subject: str, message: str,
                 mentor_name: str) -> tuple[str, str]:
    """One message to the whole class, addressed to nobody in particular.

    Sent as BCC, so it cannot greet anyone by name -- and so no student sees
    another student's address.
    """
    site = settings.base_url.rstrip("/")
    body = f"""Hi everyone,

{message.strip()}

{mentor_name}
Robo Dog class -- {site}/student
"""
    return subject.strip(), body


def feedback(settings: Settings, student, handin_name: str, comment: str,
             mentor_name: str) -> tuple[str, str]:
    site = settings.base_url.rstrip("/")
    subject = f"Feedback on {handin_name}"
    quoted = "\n".join(f"  | {line}" for line in comment.strip().splitlines())
    body = f"""Hi {student.display},

I looked at {handin_name}, which you handed in:

{quoted}

All your feedback is on your student page:
   {site}/student

{mentor_name}
"""
    return subject, body


# ----------------------------------------------------------------- links

def gmail_draft(*, sender: str, to: list[str] | str, subject: str, body: str,
                bcc: list[str] | None = None) -> str:
    """A link that opens this message, ready to send, in `sender`'s Gmail."""
    to_list = [to] if isinstance(to, str) else list(to)
    params = {"authuser": sender, "view": "cm", "fs": "1", "tf": "1",
              "to": _join(to_list), "su": subject, "body": body}
    if bcc:
        params["bcc"] = _join(bcc)
    return GMAIL_COMPOSE + "?" + urlencode(params, quote_via=quote)


def mailto(*, to: list[str] | str, subject: str, body: str,
           bcc: list[str] | None = None) -> str:
    """The same message for whatever mail app this computer uses."""
    to_list = [to] if isinstance(to, str) else list(to)
    query = {"subject": subject, "body": body}
    if bcc:
        query["bcc"] = _join(bcc)
    return ("mailto:" + quote(_join(to_list), safe="@,")
            + "?" + urlencode(query, quote_via=quote))


def _join(addresses) -> str:
    """Comma-separated, with anything that is not exactly one address dropped."""
    return ",".join(a for a in (_clean(x) for x in addresses) if a)


def _clean(addr: str) -> str:
    """One address, or nothing: no header injection through a stored email."""
    addr = (addr or "").strip()
    if "@" not in addr or any(c in addr for c in "\r\n,;<>\"'"):
        return ""
    return addr
