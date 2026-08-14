from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class MailMessage:
    sender: str
    subject: str
    received_at: datetime
    body: str


class MailProvider(ABC):
    """Adapter interface for fetching mail from a mailbox.

    Node and graph code depend only on `MailProvider`/`MailMessage`, never
    on a concrete mail service — add support for a new one (a different
    IMAP host, Gmail API, Outlook Graph, ...) by implementing this
    interface and registering it in `mail.factory`, without touching
    `nodes/email_newsletter_search.py`.
    """

    @abstractmethod
    def fetch_latest_from(self, sender: str, before: date | None = None) -> MailMessage | None:
        """Return the most recently received message from `sender` whose
        received date (UTC calendar date) is on or before `before`, or the
        truly latest message if `before` is None.

        `before` is how the pipeline's simulated "today"
        (`INA_DATETIME_OVERRIDE`) reaches this adapter — without it, a
        backtest run would leak whatever the mailbox's actual latest email
        is, rather than the one that would genuinely have been the latest
        as of the simulated date. Mirrors the future-date guard in
        `nodes/web_search.py` and the `end` pinning in
        `nodes/market_data_search.py`.

        Returns None if no message from `sender` satisfies that."""
