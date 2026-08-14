import email
import html
import imaplib
import re
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime

from email_providers.base import EmailMessage, EmailProvider

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RUN_RE = re.compile(r"[ \t]+")


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def _html_to_text(markup: str) -> str:
    """Minimal HTML-to-text fallback for newsletters that only send a
    text/html part — strips tags and collapses whitespace. Not a full
    renderer, just enough to hand newsletter prose to an LLM."""
    text = _TAG_RE.sub(" ", markup)
    text = html.unescape(text)
    return _WHITESPACE_RUN_RE.sub(" ", text).strip()


def _extract_body(msg: Message) -> str:
    if not msg.is_multipart():
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        text = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        return text.strip() if msg.get_content_type() == "text/plain" else _html_to_text(text)

    plain_bytes, plain_charset = None, "utf-8"
    html_bytes, html_charset = None, "utf-8"
    for part in msg.walk():
        if part.get_content_disposition() == "attachment":
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain" and plain_bytes is None:
            plain_bytes = part.get_payload(decode=True)
            plain_charset = part.get_content_charset() or plain_charset
        elif content_type == "text/html" and html_bytes is None:
            html_bytes = part.get_payload(decode=True)
            html_charset = part.get_content_charset() or html_charset

    # Prefer the HTML part: bulk-mail platforms (Beehiiv, Mailchimp, ...)
    # commonly design newsletters for HTML rendering only and ship the
    # text/plain alternative as an auto-generated stub — e.g. Morning
    # Brew's plain part is just "Looks like your email provider is
    # scrambling the email :( Click here to read it in full online: ..."
    # with none of the actual content. HTML is the part that reliably
    # carries it.
    if html_bytes is not None:
        return _html_to_text(html_bytes.decode(html_charset, errors="replace"))
    if plain_bytes is not None:
        return plain_bytes.decode(plain_charset, errors="replace").strip()
    return ""


def _parse_message(raw: bytes, sender: str) -> EmailMessage:
    msg = email.message_from_bytes(raw)
    date_header = msg.get("Date")
    received_at = (
        parsedate_to_datetime(date_header) if date_header else datetime.now(tz=timezone.utc)
    )
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    return EmailMessage(
        sender=sender,
        subject=_decode_header_value(msg.get("Subject")),
        received_at=received_at.astimezone(timezone.utc),
        body=_extract_body(msg),
    )


class ImapEmailProvider(EmailProvider):
    """IMAP4 + password adapter.

    Works against Gmail (with an App Password, since Gmail no longer
    accepts plain account passwords over IMAP) and, unchanged, against any
    other IMAP4 mailbox that authenticates the same way.
    """

    def __init__(
        self, host: str, username: str, password: str, port: int = 993, mailbox: str = "INBOX"
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._mailbox = mailbox

    def fetch_latest_from(
        self, sender: str, before: date | None = None, count: int = 1
    ) -> list[EmailMessage]:
        with imaplib.IMAP4_SSL(self._host, self._port) as conn:
            conn.login(self._username, self._password)
            status, _ = conn.select(self._mailbox, readonly=True)
            if status != "OK":
                raise RuntimeError(f"Could not select mailbox {self._mailbox!r}: {status}")

            search_criteria = ["FROM", f'"{sender}"']
            if before is not None:
                # IMAP BEFORE compares INTERNALDATE (when the server received
                # the message), not the Date: header _parse_message treats as
                # authoritative, and excludes the given date itself — search
                # one day past `before` so this stays a coarse pre-filter and
                # never drops a candidate the exact per-message check below
                # would have kept.
                imap_date = (before + timedelta(days=1)).strftime("%d-%b-%Y")
                search_criteria += ["BEFORE", imap_date]

            status, data = conn.search(None, *search_criteria)
            if status != "OK":
                raise RuntimeError(f"IMAP SEARCH for {sender!r} failed: {status}")
            if not data or not data[0]:
                return []

            # IMAP SEARCH returns UIDs in ascending order, so the mailbox's
            # most recently received match is last — walk backwards from
            # there, fetching full messages only as needed, collecting up to
            # `count` whose actual Date: header satisfies `before`.
            messages: list[EmailMessage] = []
            for uid in reversed(data[0].split()):
                if len(messages) >= count:
                    break
                status, msg_data = conn.fetch(uid, "(RFC822)")
                if status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    raise RuntimeError(f"IMAP FETCH for uid {uid!r} failed: {status}")
                message = _parse_message(msg_data[0][1], sender)
                if before is None or message.received_at.date() <= before:
                    messages.append(message)

        return messages
