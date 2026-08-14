from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class EmailMessage:
    sender: str
    subject: str
    received_at: datetime
    body: str


class EmailProvider(ABC):
    """Adapter interface for fetching email from a mailbox.

    Node and graph code depend only on `EmailProvider`/`EmailMessage`, never
    on a concrete email service — add support for a new one (a different
    IMAP host, Gmail API, Outlook Graph, ...) by implementing this
    interface and registering it in `email_providers.factory`, without
    touching `nodes/email_newsletter_search.py`.
    """

    @abstractmethod
    def fetch_latest_from(
        self, sender: str, before: date | None = None, count: int = 1
    ) -> list[EmailMessage]:
        """Return up to `count` most recently received messages from
        `sender` whose received date (UTC calendar date) is on or before
        `before`, newest first — or the truly latest messages if `before`
        is None.

        `before` is how the pipeline's simulated "today"
        (`INA_DATETIME_OVERRIDE`) reaches this adapter — without it, a
        backtest run would leak whatever the mailbox's actual latest emails
        are, rather than the ones that would genuinely have been latest as
        of the simulated date. Mirrors the future-date guard in
        `nodes/web_search.py` and the `end` pinning in
        `nodes/market_data_search.py`.

        Returns an empty list if no message from `sender` satisfies that."""
