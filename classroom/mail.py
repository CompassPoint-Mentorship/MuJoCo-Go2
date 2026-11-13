"""Email, all of it from the organisation's address.

Every email this site sends -- invitations, announcements, feedback -- goes
out from MAIL_FROM (tech@cpmentorship.org), sent by the platform, signed with
the name of the mentor who wrote it. One sender means families see one
address they can recognise and filter for, replies land in one mailbox the
organisation controls, and no mentor's personal account is ever involved.

How it got here, because each step was a real option: a single shared SMTP
sender first; then drafts opened in each mentor's own Gmail, so mail came
"from" the person; then the decision that every message should come from
tech@. A mentor's personal Gmail is no longer part of this at all.

**Only addresses the class already has.** Every function takes a student or
a mentor from the roster, never a free-form address, and each address is
checked to be exactly one -- so a stored value cannot add recipients or
headers. A web form that mails anything typed into it, from the
organisation's account, would be a spam relay waiting to be found.

**Every send is recorded** in the outbox with the server's own words on
failure, so "did Ada's invitation go out?" has an answer.

Plain SMTP from the standard library: a Google Workspace mailbox with an app
password today, a relay or a dedicated sending service tomorrow, by
changing settings.
"""

from __future__ import annotations

import html
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from . import db
from .settings import Settings

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


def announcement(settings: Settings, student, subject: str, message: str,
                 mentor_name: str) -> tuple[str, str]:
    """One student's copy of a class announcement.

    One email per student rather than one BCC message: each greets its
    reader, no address can be exposed by a mistake in a header, and an
    address that bounces does not take the rest of the class with it.
    """
    site = settings.base_url.rstrip("/")
    body = f"""Hi {student.display},

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

{mentor_name} looked at {handin_name}, which you handed in:

{quoted}

All your feedback is on your student page:
   {site}/student

{mentor_name}
"""
    return subject, body


def _clean(addr: str) -> str:
    """One address, or nothing: no header injection through a stored email."""
    addr = (addr or "").strip()
    if "@" not in addr or any(c in addr for c in "\r\n,;<>\"'"):
        return ""
    return addr


# ----------------------------------------------------------------- sending
#
# Only invitations come through here. The address is the roster's or the
# mentor list's; it is checked to be exactly one address before a message is
# built, so a stored value cannot add recipients or headers.

class MailError(RuntimeError):
    """Something a mentor should read: not set up, over the limit, refused."""


def _html(body: str) -> str:
    """The same words, escaped line by line, in the organisation's colours."""
    paragraphs, current = [], []
    for line in html.escape(body).splitlines() + [""]:
        if line.strip():
            current.append(line)
        elif current:
            paragraphs.append("<br>".join(current))
            current = []
    inner = "".join(f'<p style="margin:0 0 14px">{p}</p>' for p in paragraphs)
    return ('<!doctype html><html><body style="margin:0;background:#f5f8ef">'
            '<div style="max-width:560px;margin:0 auto;padding:24px 18px;'
            'font:16px/1.55 Georgia,serif;color:#24282b">'
            '<div style="background:#24282b;color:#fff;padding:10px 14px;'
            'border-radius:8px 8px 0 0;font-weight:bold">'
            'CompassPoint Mentorship &middot; Robo Dog</div>'
            '<div style="background:#fff;border:1px solid #e2e6e3;border-top:0;'
            f'padding:18px 16px;border-radius:0 0 8px 8px">{inner}</div>'
            '</div></body></html>')


def build(settings: Settings, to_addr: str, subject: str, body: str) -> EmailMessage:
    to_addr = _clean(to_addr)
    if not to_addr:
        raise MailError("that is not one email address")
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to_addr
    msg["Subject"] = subject
    domain = parseaddr(settings.mail_from)[1].split("@")[-1] or None
    msg["Message-ID"] = make_msgid(domain=domain)
    msg.set_content(body)
    msg.add_alternative(_html(body), subtype="html")
    return msg


def deliver(settings: Settings, msg: EmailMessage) -> None:
    """Hand one message to the SMTP server. MailError, in its words, if refused."""
    if not settings.mail_configured:
        raise MailError("email is not set up on this server yet")
    try:
        with _connect(settings) as server:
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise MailError("the mail server refused the password -- check the app "
                        "password in .env (SMTP_PASSWORD)") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(str(exc) or exc.__class__.__name__) from exc


def _connect(settings: Settings):
    if settings.smtp_port == 465:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20,
                                  context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
        server.starttls(context=ssl.create_default_context())
    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)
    return server


def check_room(settings: Settings, count: int) -> None:
    """Refuse a batch that would cross the daily limit -- before sending any.

    Half an announcement is worse than none: some students get it, some do
    not, and nobody can tell which.
    """
    if not settings.mail_configured:
        raise MailError("email is not set up on this server yet")
    used = db.mail_sent_today(settings.db_path)
    if used + count > settings.mail_daily_limit:
        raise MailError(f"that is {count} emails, and {used} of today's "
                        f"{settings.mail_daily_limit} are already used")


def send_many(settings: Settings, messages: list[dict], *, sent_by: str) -> dict:
    """Send a batch over one connection; record each result. Never raises.

    `messages` are {to_addr, subject, body, kind, slug}. Each is logged
    before it is attempted, so a crash halfway leaves "queued" rows that say
    exactly who was not reached.
    """
    rows = []
    for m in messages:
        mail_id = db.log_mail(settings.db_path, to_addr=m["to_addr"], subject=m["subject"],
                              kind=m["kind"], slug=m.get("slug"), sent_by=sent_by)
        rows.append((mail_id, m))
    sent = failed = 0
    server = None
    try:
        server = _connect(settings)
    except smtplib.SMTPAuthenticationError:
        reason = "the mail server refused the password (SMTP_PASSWORD)"
    except (smtplib.SMTPException, OSError) as exc:
        reason = str(exc) or exc.__class__.__name__
    else:
        reason = ""
    for mail_id, m in rows:
        if server is None:
            db.mark_mail(settings.db_path, mail_id, "failed", reason)
            failed += 1
            continue
        try:
            server.send_message(build(settings, m["to_addr"], m["subject"], m["body"]))
            db.mark_mail(settings.db_path, mail_id, "sent")
            sent += 1
        except (MailError, smtplib.SMTPException, OSError) as exc:
            db.mark_mail(settings.db_path, mail_id, "failed", str(exc) or "refused")
            failed += 1
    if server is not None:
        try:
            server.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return {"sent": sent, "failed": failed}


def send(settings: Settings, *, to_addr: str, subject: str, body: str,
         kind: str, sent_by: str, slug: str | None = None) -> None:
    """Send one invitation now, and record what happened either way.

    Synchronous on purpose: one message takes a second or two, and the
    mentor who clicked should see "sent" or the server's reason on the same
    page -- not a spinner, and not a guess.
    """
    check_room(settings, 1)
    msg = build(settings, to_addr, subject, body)
    mail_id = db.log_mail(settings.db_path, to_addr=to_addr, subject=subject,
                          kind=kind, slug=slug, sent_by=sent_by)
    try:
        deliver(settings, msg)
    except MailError as exc:
        db.mark_mail(settings.db_path, mail_id, "failed", str(exc))
        raise
    db.mark_mail(settings.db_path, mail_id, "sent")
