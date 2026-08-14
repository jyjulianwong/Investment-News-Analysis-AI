import hashlib
from datetime import date

from email_adapter.base import MailMessage, MailProvider
from helpers import today_utc
from state import AgentState


def _format_snippet(message: MailMessage) -> str:
    received_label = message.received_at.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Source: Email newsletter from {message.sender}\n"
        f"Subject: {message.subject}\n"
        f"Received: {received_label}\n\n"
        f"{message.body.strip()}"
    )


def _as_search_result(message: MailMessage) -> dict:
    """Shape a fetched newsletter exactly like a Tavily search result, so
    market_analyst.py's WEB CONTEXT rendering and report_verifier.py's
    citation checks treat it identically to any other source — no
    special-casing needed downstream. A `mailto:` URI stands in for a URL,
    since the message has no public web address, but it's still a distinct,
    verifiable **Source:** line the model can cite per the CITATION RULES,
    e.g. `[brewmarkets@morningbrew.com, Dec 31, 2025](mailto:brewmarkets@morningbrew.com)`.
    """
    return {
        "query": f"Email newsletter: {message.subject}",
        "content": message.body.strip(),
        "url": f"mailto:{message.sender}",
        "published_date": message.received_at.isoformat(),
    }


def build_email_newsletter_search_node(
    mail_provider: MailProvider, senders: list[str], s3_client, input_bucket: str
):
    def email_newsletter_search_node(state: AgentState) -> AgentState:
        today = today_utc()
        # Respects INA_DATETIME_OVERRIDE — a backtest run must see the
        # newsletter email that was actually latest as of the simulated
        # date, not whatever is truly latest in the mailbox right now.
        cutoff = date.fromisoformat(today)
        uploaded = 0
        newsletter_results = []
        for sender in senders:
            try:
                message = mail_provider.fetch_latest_from(sender, before=cutoff)
            except Exception as exc:
                print(
                    f"[agent] Email newsletter search: failed to fetch from {sender!r} — "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if message is None:
                print(f"[agent] Email newsletter search: no message found from {sender!r}")
                continue
            body_preview = " ".join(message.body.split())[:200]
            print(
                f"[agent] Email newsletter search: fetched {sender!r} — "
                f"subject={message.subject!r}, received={message.received_at.isoformat()}, "
                f"body_chars={len(message.body)}, preview={body_preview!r}"
            )

            # Uploaded to the same input/ prefix and format the server writes
            # user-submitted snippets to (see server/main.py) — this makes
            # the newsletter just another snippet for `news_snippet_getter`
            # to pick up, rather than a separate path into `state`.
            #
            # Keyed by a content hash rather than a random uuid: re-running
            # the pipeline for the same day (e.g. repeated backtests against
            # INA_DATETIME_OVERRIDE, or a warm-start retry) fetches the same
            # historical email — its `_format_snippet` text is byte-identical
            # each time since `received_at` comes from the email's own Date:
            # header, not the fetch time — so the upload overwrites the same
            # key instead of accumulating duplicate snippets that would each
            # get counted separately by `news_snippet_getter`.
            snippet_bytes = _format_snippet(message).encode("utf-8")
            content_hash = hashlib.sha256(snippet_bytes).hexdigest()
            key = f"input/{today}/{content_hash}.txt"
            s3_client.put_object(
                Bucket=input_bucket,
                Key=key,
                Body=snippet_bytes,
                ContentType="text/plain",
            )
            uploaded += 1
            newsletter_results.append(_as_search_result(message))

        if uploaded:
            print(
                f"[agent] Email newsletter search: uploaded {uploaded} newsletter snippet(s) "
                f"to s3://{input_bucket}/input/{today}/"
            )
        if not newsletter_results:
            return state
        return {**state, "search_results": state["search_results"] + newsletter_results}

    return email_newsletter_search_node
