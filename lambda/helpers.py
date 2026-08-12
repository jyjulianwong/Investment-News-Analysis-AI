import os
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import jinja2

PROMPTS_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent / "prompts"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)

# Age past which a source is treated as STALE — both in the WEB CONTEXT
# annotation the analyst reads (nodes/market_analyst.py) and in the visible
# citation flag added to the rendered report (nodes/report_verifier.py).
# Shared here so the two never drift apart.
STALE_AGE_DAYS = 7


def today_utc() -> str:
    override = os.environ.get("INA_DATETIME_OVERRIDE")
    if override:
        return override[:10]
    return datetime.now(tz=timezone.utc).date().isoformat()


def parse_source_date(published_date: str) -> date | None:
    """Parse a Tavily-reported publish date into a `date`, or None if missing
    or unparseable. Tavily reports dates either as ISO (`2026-08-03`,
    optionally with a time component) or RFC 2822 (`Mon, 03 Aug 2026
    05:00:00 GMT`)."""
    if not published_date:
        return None
    try:
        return datetime.fromisoformat(published_date).date()
    except ValueError:
        try:
            return parsedate_to_datetime(published_date).date()
        except (TypeError, ValueError):
            return None
