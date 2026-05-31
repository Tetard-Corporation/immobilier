"""Relève des emails (newsletters d'agences) via IMAP.

Stdlib uniquement (`imaplib` + `email`). Le parsing d'un message est isolé dans
`parse_email_message` pour être testable sans serveur.
"""

from __future__ import annotations

import email
import imaplib
import logging
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message

from ..config import Settings, get_settings

logger = logging.getLogger("immobilier.email")


@dataclass
class IncomingEmail:
    subject: str
    body: str
    is_html: bool
    sender: str
    message_id: str


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _extract_body(msg: Message) -> tuple[str, bool]:
    """Renvoie (corps, is_html). Préfère le HTML, sinon le texte brut."""
    html_part = None
    text_part = None
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace") if payload else ""
            except Exception:
                continue
            if ctype == "text/html" and html_part is None:
                html_part = decoded
            elif ctype == "text/plain" and text_part is None:
                text_part = decoded
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        decoded = payload.decode(charset, errors="replace") if payload else ""
        if msg.get_content_type() == "text/html":
            html_part = decoded
        else:
            text_part = decoded

    if html_part:
        return html_part, True
    return text_part or "", False


def parse_email_message(msg: Message) -> IncomingEmail:
    body, is_html = _extract_body(msg)
    return IncomingEmail(
        subject=_decode(msg.get("Subject")),
        body=body,
        is_html=is_html,
        sender=_decode(msg.get("From")),
        message_id=(msg.get("Message-ID") or "").strip(),
    )


def fetch_unseen(settings: Settings | None = None, *, mark_seen: bool = True) -> list[IncomingEmail]:
    """Récupère les emails non lus de la boîte configurée."""
    settings = settings or get_settings()
    if not settings.imap_configured:
        return []
    conn_cls = imaplib.IMAP4_SSL if settings.imap_use_ssl else imaplib.IMAP4
    emails: list[IncomingEmail] = []
    conn = conn_cls(settings.imap_host)
    try:
        conn.login(settings.imap_user, settings.imap_password)
        conn.select(settings.imap_folder)
        _, data = conn.search(None, "UNSEEN")
        ids = data[0].split() if data and data[0] else []
        for num in ids:
            flag = "(RFC822)" if mark_seen else "(BODY.PEEK[])"
            _, msg_data = conn.fetch(num, flag)
            if not msg_data or not isinstance(msg_data[0], tuple):
                continue
            emails.append(parse_email_message(email.message_from_bytes(msg_data[0][1])))
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    logger.info("IMAP : %s email(s) non lu(s) relevé(s).", len(emails))
    return emails
