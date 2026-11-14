"""Replies to the site's email, read out of the sending mailbox.

The mailbox is the organisation's (tech@cpmentorship.org at the time of
writing), and it holds a great deal that has nothing to do with this site.
So the rule is narrow and every line below serves it:

    **Only replies to email this site sent are ever read past their headers,
    and only those are stored.**

How a reply is recognised: every message the site sends gets a Message-ID,
recorded in the outbox. A mail client answering it puts that ID in
`In-Reply-To` and `References`. Each pass fetches only those header lines
(plus From/Date/Subject) for mail that arrived since the last pass, and
downloads a body only when the headers cite one of our IDs.

And it never changes the mailbox. It is opened read-only (EXAMINE, not
SELECT) and every fetch is BODY.PEEK, so nothing is marked read, moved,
labelled or deleted: the people who read tech@ in Gmail see exactly what they
saw before. Attachments are counted, not stored; a reply is cut down to the
new text above the quoted original, and capped.

Standard library only: imaplib and email.
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
import time
from email.utils import getaddresses, parsedate_to_datetime

from . import db
from .settings import Settings

IMAP_HOST = "imap.gmail.com"
FIRST_LOOK_DAYS = 14           # on the very first pass, how far back to look
MAX_REPLY_BYTES = 2_000_000    # bigger than this is mostly attachment
MAX_BODY_CHARS = 20_000
HEADER_FIELDS = "MESSAGE-ID IN-REPLY-TO REFERENCES FROM DATE SUBJECT"

_IDS = re.compile(r"<[^<>\s]+>")


def _ids(value: str | None) -> list[str]:
    return _IDS.findall(value or "")


def strip_quoted(text: str) -> str:
    """The new words of a reply, without the original it quotes.

    Gmail and Apple Mail put "On <date>, <name> wrote:" above the quote --
    sometimes wrapped onto two lines -- and Outlook a "-----Original
    Message-----" rule. Everything from there down is ours already.
    """
    text = text.replace("\r\n", "\n")
    for pattern in (r"\n[^\n]*On [^\n]*(?:\n[^\n]*)?wrote:[ \t]*\n",
                    r"\n-{2,}\s*Original Message\s*-{2,}",
                    r"\n_{8,}\n"):
        parts = re.split(pattern, "\n" + text, maxsplit=1)
        text = parts[0].lstrip("\n")
    lines = text.rstrip().splitlines()
    while lines and (lines[-1].startswith(">") or not lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()[:MAX_BODY_CHARS]


def _plain_text(msg) -> tuple[str, int]:
    """The text/plain body, and how many attachments there were."""
    attachments = 0
    text = ""
    for part in msg.walk():
        if part.is_multipart():
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            attachments += 1
            continue
        if part.get_content_type() == "text/plain" and not text:
            try:
                text = part.get_content()
            except (LookupError, UnicodeDecodeError):
                text = part.get_payload(decode=True).decode("utf-8", "replace")
    if not text:
        text = "(this reply has no plain-text part; read it in the mailbox)"
    return text, attachments


def _since(days: int) -> str:
    return time.strftime("%d-%b-%Y", time.gmtime(time.time() - days * 86400))


def check(settings: Settings, connect=None) -> dict:
    """One pass over new mail. Returns counts; raises nothing it can report."""
    result = {"looked_at": 0, "stored": 0, "error": ""}
    if not settings.mail_configured or not settings.smtp_user:
        result["error"] = "email is not set up"
        return result
    ours = db.sent_message_ids(settings.db_path)
    if not ours:
        return result                      # nothing sent, so nothing to answer
    try:
        box = (connect or _connect)(settings)
    except (imaplib.IMAP4.error, OSError) as exc:
        result["error"] = f"could not open the mailbox: {exc}"
        return result
    try:
        typ, data = box.select("INBOX", readonly=True)    # EXAMINE: read-only
        if typ != "OK":
            result["error"] = "could not open the inbox read-only"
            return result
        validity = (box.response("UIDVALIDITY")[1] or [b""])[0]
        validity = validity.decode() if isinstance(validity, bytes) else str(validity or "")
        last = db.get_meta(settings.db_path, "imap_last_uid", "")
        if last and db.get_meta(settings.db_path, "imap_uidvalidity") == validity:
            typ, data = box.uid("SEARCH", None, f"UID {int(last) + 1}:*")
        else:
            typ, data = box.uid("SEARCH", None, f"SINCE {_since(FIRST_LOOK_DAYS)}")
        uids = [u for u in (data[0] or b"").split() if u]
        if last:
            uids = [u for u in uids if int(u) > int(last)]   # "N:*" always returns one
        if not uids:
            return result
        result["looked_at"] = len(uids)

        # Headers only, a batch at a time, PEEK so nothing is marked read.
        matches = []
        for i in range(0, len(uids), 200):
            chunk = b",".join(uids[i:i + 200]).decode()
            typ, data = box.uid("FETCH", chunk,
                                f"(RFC822.SIZE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})])")
            for item in data or []:
                if not isinstance(item, tuple):
                    continue
                meta, raw = item
                uid = re.search(rb"UID (\d+)", meta)
                size = re.search(rb"RFC822\.SIZE (\d+)", meta)
                headers = email.message_from_bytes(raw, policy=email.policy.default)
                cited = _ids(headers.get("In-Reply-To")) + _ids(headers.get("References"))
                original = next((ours[c] for c in reversed(cited) if c in ours), None)
                if not original or not uid:
                    continue              # not a reply to us: never read further
                mid = (_ids(headers.get("Message-ID")) or [f"<uid-{validity}-{uid.group(1).decode()}>"])[0]
                if db.has_reply(settings.db_path, mid):
                    continue
                matches.append((uid.group(1), int(size.group(1)) if size else 0,
                                headers, original, mid))

        for uid, size, headers, original, mid in matches:
            if size > MAX_REPLY_BYTES:
                body, attachments = (f"(a {size // 1024} KB reply -- mostly attachments; "
                                     "read it in the mailbox)"), 1
            else:
                typ, data = box.uid("FETCH", uid.decode(), "(BODY.PEEK[])")
                raw = next((it[1] for it in data or [] if isinstance(it, tuple)), b"")
                full = email.message_from_bytes(raw, policy=email.policy.default)
                text, attachments = _plain_text(full)
                body = strip_quoted(text)
            sender = (getaddresses([headers.get("From", "")]) or [("", "")])[0][1].lower()
            try:
                received = parsedate_to_datetime(headers.get("Date")).isoformat(timespec="seconds")
            except (TypeError, ValueError):
                received = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
            db.add_reply(settings.db_path, message_id=mid, outbox_id=original["id"],
                         slug=original["slug"], from_addr=sender[:200],
                         subject=str(headers.get("Subject", ""))[:300], body=body,
                         attachments=attachments,
                         matched=int(sender == (original["to_addr"] or "").lower()),
                         received_at=received)
            result["stored"] += 1

        db.set_meta(settings.db_path, "imap_last_uid", max(uids, key=int).decode())
        db.set_meta(settings.db_path, "imap_uidvalidity", validity)
        return result
    except (imaplib.IMAP4.error, OSError) as exc:
        result["error"] = f"reading the mailbox failed: {exc}"
        return result
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass


def _connect(settings: Settings):
    box = imaplib.IMAP4_SSL(IMAP_HOST, 993, timeout=30)
    box.login(settings.smtp_user, settings.smtp_password)
    return box
