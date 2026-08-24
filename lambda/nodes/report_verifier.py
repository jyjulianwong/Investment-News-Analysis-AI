import re
from datetime import date, datetime

from helpers import STALE_AGE_DAYS, parse_source_date, today_utc
from state import AgentState

# A link with a bracket instead of a closing paren, e.g. `[Source](https://
# example.com/page]` — the exact shape of a real malformed citation this
# pipeline has produced.
_MALFORMED_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^()\]]+)\]")
_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^()\s]+)\)")

# A citation link wrapped in a Markdown code span, e.g. `` `[CNBC, Aug
# 2](url)` `` — the model sometimes copies the backtick-quoting used to
# illustrate the citation format in the prompt into its actual citations.
# Markdown never parses inside a code span, so a backtick-wrapped citation
# renders as literal, unclickable text (brackets, parens, and all) instead
# of a hyperlink — the exact "hyperlinks not rendering" failure this guards
# against.
_BACKTICKED_LINK_RE = re.compile(r"`+(\[[^\[\]]+\]\([^()\s]+\))`+")

# A month name (optionally trailing "?", the model's own uncertainty marker,
# e.g. "Jun? 2026") followed by an optional day and a 4-digit year, e.g.
# "June 19, 2023", "Feb 21, 2025", "Jan 2026".
_LABEL_DATE_RE = re.compile(
    r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\??\.?"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?,?)?\s*\d{4}\b"
)

# A '%' or '$' sign, "bps", or a multiple like "18x" — the minimum bar for a
# priced-in check to count as citing an actual number rather than narrative.
_QUANT_EVIDENCE_RE = re.compile(r"%|\$|\bbps\b|\b\d+(?:\.\d+)?x\b", re.IGNORECASE)
_NOT_AVAILABLE_RE = re.compile(
    r"(?i)\bnot available\b|\bno (?:reliable |current )?(?:price|valuation|pricing) data\b|\bunavailable\b"
)
_HEADING_RE = re.compile(r"(?m)^#{2,3}\s")
_BULLET_LINE_RE = re.compile(r"(?m)^- \*\*.+$")


def _parse_label_date(text: str) -> date | None:
    """Parse a date substring matched by `_LABEL_DATE_RE` (e.g. "Jun? 2026",
    "June 19, 2023") into a `date`, trying both abbreviated and full month
    names and with/without a day component."""
    cleaned = re.sub(r"\s+", " ", text.replace("?", "").replace(",", "")).strip()
    for fmt in ("%b %d %Y", "%B %d %Y", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _unwrap_backticked_links(report_md: str) -> str:
    """Strip backticks that wrap a citation link so it renders as a real
    hyperlink instead of literal code text — see `_BACKTICKED_LINK_RE`."""

    def _unwrap(match: re.Match) -> str:
        print(f"[agent] Citation verifier: unwrapped backtick-quoted link -> {match.group(1)}")
        return match.group(1)

    return _BACKTICKED_LINK_RE.sub(_unwrap, report_md)


def _verify_citations(report_md: str, valid_urls: set[str]) -> str:
    """Repair malformed markdown links and strip any link whose URL wasn't
    actually returned by search — the model must not get away with a
    hallucinated or truncated citation."""

    def _fix_malformed(match: re.Match) -> str:
        text, url = match.group(1), match.group(2)
        if url in valid_urls:
            print(f"[agent] Citation verifier: repaired malformed link -> {url}")
            return f"[{text}]({url})"
        print(f"[agent] Citation verifier: dropped malformed, unverifiable link -> {url}")
        return text

    def _check_valid(match: re.Match) -> str:
        text, url = match.group(1), match.group(2)
        if url in valid_urls:
            return match.group(0)
        print(f"[agent] Citation verifier: dropped unverifiable link -> {url}")
        return text

    fixed = _MALFORMED_LINK_RE.sub(_fix_malformed, report_md)
    return _LINK_RE.sub(_check_valid, fixed)


def _repair_citation_dates(report_md: str, published_by_url: dict[str, str], today: str) -> str:
    """Make every citation's date — and staleness — visible in the report
    text itself, never something a reader has to click through to discover.

    A citation with a real, dated, clickable URL can still launder a
    9-month-old article as today's evidence if the date only lives at the
    end of that link. This function is a deterministic backstop, independent
    of whether the model followed the prompt's citation-date rules:
    - a label with no date gets one added, from the source's real Published
      metadata (or an explicit "date unknown" marker);
    - a label with a wrong or unverifiable date gets corrected or replaced
      with "date unknown";
    - any citation older than `STALE_AGE_DAYS` gets an explicit "· STALE"
      flag appended to the label, even if the model got the date right —
      so a reader sees the staleness without needing to do the date math
      themselves against today's report date.
    """

    def _canonical_suffix(published_date: str) -> str:
        real_date = parse_source_date(published_date)
        if real_date is None:
            return "date unknown"
        formatted = real_date.strftime("%b %-d, %Y")
        age_days = abs((date.fromisoformat(today) - real_date).days)
        return f"{formatted} · STALE" if age_days > STALE_AGE_DAYS else formatted

    def _fix(match: re.Match) -> str:
        text, url = match.group(1), match.group(2)
        if url not in published_by_url:
            return match.group(0)
        canonical = _canonical_suffix(published_by_url[url])
        real_date = parse_source_date(published_by_url[url])
        date_match = _LABEL_DATE_RE.search(text)
        already_flagged = "stale" in text.lower() or "date unknown" in text.lower()

        if date_match is None:
            if already_flagged:
                return match.group(0)
            new_text = f"{text}, {canonical}"
            print(f"[agent] Citation verifier: added missing date to label -> {new_text!r}")
            return f"[{new_text}]({url})"

        claimed = _parse_label_date(date_match.group(0))
        matches_real = (
            real_date is not None
            and claimed is not None
            and (claimed.year, claimed.month) == (real_date.year, real_date.month)
        )
        if matches_real and (not canonical.endswith("STALE") or already_flagged):
            return match.group(0)

        new_text = text[: date_match.start()] + canonical + text[date_match.end() :]
        print(
            f"[agent] Citation verifier: set citation date/flag {date_match.group(0)!r} -> {canonical!r}"
        )
        return f"[{new_text}]({url})"

    return _LINK_RE.sub(_fix, report_md)


def _find_section(report_md: str, heading_pattern: str) -> tuple[int, int] | None:
    """Locate the body of a `##`/`###` section by heading text, returning
    (start, end) offsets into `report_md`, or None if the heading isn't
    present (the model is free to deviate from the exact prompted wording,
    in which case this check is skipped rather than raising)."""
    heading_re = re.compile(rf"(?im)^#{{2,3}}\s*(?:\d+\.\s*)?{heading_pattern}\s*$")
    match = heading_re.search(report_md)
    if not match:
        return None
    start = match.end()
    next_match = _HEADING_RE.search(report_md, start)
    return start, (next_match.start() if next_match else len(report_md))


def _flag_unquantified_priced_in_checks(report_md: str) -> str:
    """Deterministic backstop for Standard #3 (priced-in check): flag any
    Opportunities or 6-month-pick bullet that doesn't cite a concrete number
    (a price level, %, bps, or multiple) and doesn't explicitly say pricing
    data wasn't available.

    The model reliably produces narrative that satisfies the section's
    *shape* — "already being framed as sensitive to yields, implying hedge
    demand isn't necessarily exhausted" — without doing the actual pricing
    check the prompt asks for. Rather than trust that prose, scan for the
    presence of an actual number and flag the bullet in the delivered report
    when there isn't one, so the gap is visible to the reader instead of
    silently passing as a completed check.
    """
    spans = [
        s
        for s in (
            _find_section(report_md, p)
            for p in ("Opportunities", "If I had 6 months, I would long.*", "If I had 6 months, I would short.*")
        )
        if s
    ]
    if not spans:
        return report_md

    flagged = 0

    def _check_bullet(match: re.Match) -> str:
        nonlocal flagged
        line = match.group(0)
        if _QUANT_EVIDENCE_RE.search(line) or _NOT_AVAILABLE_RE.search(line):
            return line
        flagged += 1
        return f"{line} *(⚠ priced-in check not quantified — no price/%/bps figure cited)*"

    pieces = []
    last_end = 0
    for start, end in sorted(spans):
        pieces.append(report_md[last_end:start])
        pieces.append(_BULLET_LINE_RE.sub(_check_bullet, report_md[start:end]))
        last_end = end
    pieces.append(report_md[last_end:])
    result = "".join(pieces)
    if flagged:
        print(f"[agent] Priced-in check verifier: flagged {flagged} unquantified bullet(s)")
    return result


def report_verifier_node(state: AgentState) -> AgentState:
    valid_urls = {r["url"] for r in state["search_results"] if r.get("url")}
    published_by_url = {
        r["url"]: r.get("published_date", "") for r in state["search_results"] if r.get("url")
    }
    report_md = _unwrap_backticked_links(state["report"])
    report_md = _verify_citations(report_md, valid_urls)
    report_md = _repair_citation_dates(report_md, published_by_url, today_utc())
    report_md = _flag_unquantified_priced_in_checks(report_md)
    return {**state, "report": report_md}
