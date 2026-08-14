import hashlib
from datetime import date

from email_providers.base import EmailMessage, EmailProvider
from helpers import today_utc
from state import AgentState


def _format_snippet(message: EmailMessage) -> str:
    received_label = message.received_at.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Source: Email newsletter from {message.sender}\n"
        f"Subject: {message.subject}\n"
        f"Received: {received_label}\n\n"
        f"{message.body.strip()}"
    )


def _as_search_result(message: EmailMessage) -> dict:
    """Shape a fetched newsletter exactly like a Tavily search result, so
    market_analyst.py's WEB CONTEXT rendering and report_verifier.py's
    citation checks treat it identically to any other source — no
    special-casing needed downstream. A `mailto:` URI stands in for a URL,
    since the message has no public web address, but it's still a distinct,
    verifiable **Source:** line the model can cite per the CITATION RULES,
    e.g. `[brewmarkets@morningbrew.com, Dec 31, 2025](mailto:brewmarkets@morningbrew.com)`.

    The received timestamp is appended as a URL fragment: a sender configured
    to fetch more than one message (see `EMAIL_NEWSLETTER_SENDERS` in
    agent.py) would otherwise produce several search results sharing one
    identical `mailto:` URL, which breaks report_verifier.py's
    `published_by_url` lookup — it's keyed by URL, so distinct messages need
    distinct URLs to each get their own correct citation date. The fragment
    is ignored by `_domain` in nodes/search_evaluator.py, so these still
    count as a single source for diversity purposes.
    """
    return {
        "query": f"Email newsletter: {message.subject}",
        "content": message.body.strip(),
        "url": f"mailto:{message.sender}#{message.received_at.isoformat()}",
        "published_date": message.received_at.isoformat(),
    }


def build_email_newsletter_search_node(
    email_provider: EmailProvider, senders: list[tuple[str, int]], s3_client, input_bucket: str
):
    """`senders` is a list of `(address, count)` pairs — `count` is how many
    of the sender's most recent messages to fetch, set per-sender so a
    high-volume mailbox (e.g. several alerts a day) can be configured to
    pull more than a single-issue-per-day newsletter without a code change
    (see `EMAIL_NEWSLETTER_SENDERS` parsing in `agent.py`)."""

    def email_newsletter_search_node(state: AgentState) -> AgentState:
        today = today_utc()
        # Respects INA_DATETIME_OVERRIDE — a backtest run must see the
        # newsletter emails that were actually latest as of the simulated
        # date, not whatever is truly latest in the mailbox right now.
        cutoff = date.fromisoformat(today)
        uploaded = 0
        newsletter_results = []
        for sender, count in senders:
            try:
                messages = email_provider.fetch_latest_from(sender, before=cutoff, count=count)
            except Exception as exc:
                print(
                    f"[agent] Email newsletter search: failed to fetch from {sender!r} — "
                    f"{type(exc).__name__}: {exc}"
                )
                continue
            if not messages:
                print(f"[agent] Email newsletter search: no message found from {sender!r}")
                continue
            for message in messages:
                body_preview = " ".join(message.body.split())[:200]
                print(
                    f"[agent] Email newsletter search: fetched {sender!r} — "
                    f"subject={message.subject!r}, received={message.received_at.isoformat()}, "
                    f"body_chars={len(message.body)}, preview={body_preview!r}"
                )

                # Uploaded to the same input/ prefix and format the server
                # writes user-submitted snippets to (see server/main.py) —
                # this makes the newsletter just another snippet for
                # `news_snippet_getter` to pick up, rather than a separate
                # path into `state`.
                #
                # Keyed by a content hash rather than a random uuid:
                # re-running the pipeline for the same day (e.g. repeated
                # backtests against INA_DATETIME_OVERRIDE, or a warm-start
                # retry) fetches the same historical email(s) — each
                # message's `_format_snippet` text is byte-identical every
                # time since `received_at` comes from the email's own Date:
                # header, not the fetch time — so the upload overwrites the
                # same key instead of accumulating duplicate snippets that
                # would each get counted separately by `news_snippet_getter`.
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
