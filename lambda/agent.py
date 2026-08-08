import os
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import TypedDict

import boto3
import jinja2
import markdown
from weasyprint import HTML

# LangGraph instantiates JsonPlusSerializer without allowed_objects; suppress
# the pending deprecation until LangGraph fixes it upstream.
warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects`")

INA_VERSION = os.environ.get("INA_VERSION", "unknown")

_PROMPTS_ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(Path(__file__).parent / "prompts"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def _today_utc() -> str:
    override = os.environ.get("INA_DATETIME_OVERRIDE")
    if override:
        return override[:10]
    return datetime.now(tz=timezone.utc).date().isoformat()


def _parse_source_date(published_date: str) -> date | None:
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


def _within_days(published_date: str, today: str, max_age_days: int) -> bool:
    """Best-effort check that a source's reported publish date is recent enough.

    A date we can't parse, or that's missing entirely, is treated as
    unverifiable rather than stale — Tavily doesn't reliably attach a publish
    date to every result, and dropping every undated result starves the
    report of content. Unverifiable results are instead flagged in the
    context handed to the analyst (see `_age_label`) so the model can't treat
    them as confirmed-current evidence. `abs()` absorbs the odd result whose
    metadata timezone makes it look 1 day in the future relative to our UTC
    `today`.
    """
    published = _parse_source_date(published_date)
    if published is None:
        return True
    return abs((date.fromisoformat(today) - published).days) <= max_age_days


# Age past which a source is treated as STALE — both in the WEB CONTEXT
# annotation the analyst reads and in the visible citation flag added to
# the rendered report (see `_repair_citation_dates`). Shared so the two
# never drift apart.
_STALE_AGE_DAYS = 30


def _age_label(published_date: str, today: str) -> str:
    """Human-readable recency annotation for a WEB CONTEXT entry.

    Doing the day-count arithmetic here — rather than leaving it to the
    model — closes off the failure mode where a report cites a source's
    actual publish date but gets the "how stale is this" judgment wrong (or,
    worse, doesn't attempt it and just presents the source as current).
    """
    published = _parse_source_date(published_date)
    if published is None:
        return (
            "unknown — Tavily did not return a verifiable publish date for "
            "this source; do not attribute a specific date to it, and do "
            "not treat it as confirmed-current evidence"
        )
    age_days = (date.fromisoformat(today) - published).days
    formatted = published.strftime("%b %-d, %Y")
    if age_days <= 7:
        return f"{formatted} ({age_days}d ago)"
    if age_days <= _STALE_AGE_DAYS:
        return f"{formatted} ({age_days}d ago — not last-week news; confirm it's still new information, not a restated fact)"
    if age_days <= 365:
        return f"{formatted} ({age_days}d ago — STALE; do not present as current evidence for a live or fast-moving claim)"
    years = age_days / 365
    return f"{formatted} ({years:.1f} years ago — STALE; background/historical only, must not be cited as current)"


# ---------------------------------------------------------------------------
# AWS clients — reused across warm invocations
# ---------------------------------------------------------------------------

_ssm = boto3.client("ssm", region_name=os.environ["AWS_REGION_NAME"])
_s3 = boto3.client("s3", region_name=os.environ["AWS_REGION_NAME"])

INPUT_BUCKET = os.environ["AWS_S3_INPUT_BUCKET_NAME"]
OUTPUT_BUCKET = os.environ["AWS_S3_OUTPUT_BUCKET_NAME"]
SSM_OPENROUTER_PARAM = os.environ["SSM_OPENROUTER_PARAM"]
SSM_TAVILY_PARAM = os.environ["SSM_TAVILY_PARAM"]


def _get_secret(param_name: str) -> str:
    resp = _ssm.get_parameter(Name=param_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


def _list_snippets(day: str) -> list[str]:
    """Return the text content of every snippet file for the given day (YYYY-MM-DD)."""
    prefix = f"input/{day}/"
    paginator = _s3.get_paginator("list_objects_v2")
    keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=INPUT_BUCKET, Prefix=prefix)
        for obj in page.get("Contents", [])
    ]
    texts = []
    for key in keys:
        body = _s3.get_object(Bucket=INPUT_BUCKET, Key=key)["Body"].read().decode("utf-8")
        texts.append(body.strip())
    return texts


def _upload_report(day: str, md_text: str, pdf_bytes: bytes) -> None:
    _s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=f"output/{day}/report.md",
        Body=md_text.encode("utf-8"),
        ContentType="text/markdown",
    )
    _s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=f"output/{day}/report.pdf",
        Body=pdf_bytes,
        ContentType="application/pdf",
    )


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

_PDF_CSS = """
body { font-family: Georgia, serif; max-width: 800px; margin: 40px auto; color: #222; }
h1 { color: #1a3a5c; border-bottom: 2px solid #1a3a5c; padding-bottom: 6px; }
h2 { color: #2c5f8a; margin-top: 2em; }
h3 { color: #3a7ab8; }
code { background: #f4f4f4; padding: 2px 4px; border-radius: 3px; }
blockquote { border-left: 4px solid #ccc; margin-left: 0; padding-left: 16px; color: #555; }
"""


def _md_to_pdf(md_text: str) -> bytes:
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    full_html = f"<html><head><style>{_PDF_CSS}</style></head><body>{html_body}</body></html>"
    return HTML(string=full_html).write_pdf()


# ---------------------------------------------------------------------------
# Citation verification
# ---------------------------------------------------------------------------

# A link with a bracket instead of a closing paren, e.g. `[Source](https://
# example.com/page]` — the exact shape of a real malformed citation this
# pipeline has produced.
_MALFORMED_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^()\]]+)\]")
_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(([^()\s]+)\)")

# A month name (optionally trailing "?", the model's own uncertainty marker,
# e.g. "Jun? 2026") followed by an optional day and a 4-digit year, e.g.
# "June 19, 2023", "Feb 21, 2025", "Jan 2026".
_LABEL_DATE_RE = re.compile(
    r"(?i)\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\??\.?"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?,?)?\s*\d{4}\b"
)


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
    - any citation older than `_STALE_AGE_DAYS` gets an explicit "· STALE"
      flag appended to the label, even if the model got the date right —
      so a reader sees the staleness without needing to do the date math
      themselves against today's report date.
    """

    def _canonical_suffix(published_date: str) -> str:
        real_date = _parse_source_date(published_date)
        if real_date is None:
            return "date unknown"
        formatted = real_date.strftime("%b %-d, %Y")
        age_days = abs((date.fromisoformat(today) - real_date).days)
        return f"{formatted} · STALE" if age_days > _STALE_AGE_DAYS else formatted

    def _fix(match: re.Match) -> str:
        text, url = match.group(1), match.group(2)
        if url not in published_by_url:
            return match.group(0)
        canonical = _canonical_suffix(published_by_url[url])
        real_date = _parse_source_date(published_by_url[url])
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


# A '%' or '$' sign, "bps", or a multiple like "18x" — the minimum bar for a
# priced-in check to count as citing an actual number rather than narrative.
_QUANT_EVIDENCE_RE = re.compile(r"%|\$|\bbps\b|\b\d+(?:\.\d+)?x\b", re.IGNORECASE)
_NOT_AVAILABLE_RE = re.compile(
    r"(?i)\bnot available\b|\bno (?:reliable |current )?(?:price|valuation|pricing) data\b|\bunavailable\b"
)
_HEADING_RE = re.compile(r"(?m)^#{2,3}\s")
_BULLET_LINE_RE = re.compile(r"(?m)^- \*\*.+$")


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
        for s in (_find_section(report_md, p) for p in ("Opportunities", "If I had 6 months.*"))
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


# ---------------------------------------------------------------------------
# LangGraph pipeline
# ---------------------------------------------------------------------------


def _build_graph(openrouter_key: str, tavily_key: str):
    """Build and return the compiled LangGraph graph. Called once per warm start."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI
    from langchain_tavily import TavilySearch
    from langgraph.graph import END, StateGraph

    llm = ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.4-nano"),
        openai_api_key=openrouter_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.4,
    )

    _SYSTEM_PROMPT = _PROMPTS_ENV.get_template("system.j2").render()

    # time_range is deliberately NOT baked into these kwargs: it varies by
    # search attempt (see web_search_node), so it's passed per-call instead.
    _tavily_kwargs = {
        "tavily_api_key": tavily_key,
        "max_results": int(os.environ.get("TAVILY_MAX_RESULTS", "10")),
        "search_depth": os.environ.get("TAVILY_SEARCH_DEPTH", "advanced"),
        # "news" is the only Tavily topic that reliably attaches
        # published_date metadata to results — "finance" and "general" omit
        # it on most results, which is why staleness/date checks need it.
        # The curated include_domains list below already does the work of
        # keeping results finance-relevant, so this doesn't sacrifice much.
        "topic": os.environ.get("TAVILY_TOPIC", "news"),
    }
    _include_domains_raw = os.environ.get(
        "TAVILY_INCLUDE_DOMAINS",
        "reuters.com,apnews.com,cnbc.com,marketwatch.com,investing.com,"
        "tradingeconomics.com,federalreserve.gov,bis.org,imf.org,"
        "finance.yahoo.com,theguardian.com",
    )
    include_domains = [d.strip() for d in _include_domains_raw.split(",") if d.strip()]
    search_tool_filtered = (
        TavilySearch(**_tavily_kwargs, include_domains=include_domains) if include_domains else None
    )
    search_tool_open = TavilySearch(**_tavily_kwargs)
    search_workers = int(os.environ.get("TAVILY_SEARCH_WORKERS", "5"))
    min_results = int(os.environ.get("EVALUATOR_MIN_RESULTS", "5"))
    min_domains = int(os.environ.get("EVALUATOR_MIN_DOMAINS", "3"))

    class AgentState(TypedDict):
        snippets: list[str]
        queries: list[str]
        search_results: list[dict]
        search_attempt: int
        search_sufficient: bool
        report: str

    # --- Node: Query Generation ---
    def query_generation_node(state: AgentState) -> AgentState:
        snippets_text = "\n\n---\n\n".join(state["snippets"])
        prompt = _PROMPTS_ENV.get_template("query_generation.j2").render(
            today=_today_utc(), snippets_text=snippets_text
        )
        response = llm.invoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)])
        lines = [
            line.lstrip("0123456789. ").strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        return {**state, "queries": lines}

    # --- Node: Web Search ---
    def web_search_node(state: AgentState) -> AgentState:
        today = _today_utc()
        attempt = state["search_attempt"]
        tool = search_tool_filtered if attempt == 0 and search_tool_filtered else search_tool_open
        # Same-day coverage can be genuinely thin (early in the day, a quiet
        # news cycle, a narrow query) — pinning every attempt to an exact
        # calendar-day match risks a starved, fact-free report. Start strict
        # and widen the recency window on retry, the same way the domain
        # filter already opens up on retry.
        time_range = "day" if attempt == 0 else "week"
        max_age_days = 1 if attempt == 0 else 7
        new_results = []
        stale_count = 0
        error_count = 0
        with ThreadPoolExecutor(max_workers=search_workers) as pool:
            futures = {
                pool.submit(tool.invoke, {"query": q, "time_range": time_range}): q
                for q in state["queries"]
            }
            for future in as_completed(futures):
                query = futures[future]
                try:
                    result = future.result()
                    for r in result.get("results", []):
                        published_date = r.get("published_date", "")
                        if not _within_days(published_date, today, max_age_days):
                            stale_count += 1
                            continue
                        new_results.append(
                            {
                                "query": query,
                                "content": r.get("content", ""),
                                "url": r.get("url", ""),
                                "published_date": published_date,
                            }
                        )
                except Exception as exc:
                    # Single-query failures should not abort the entire run, but
                    # they must be visible — a silently-swallowed error on every
                    # query looks identical to "no news today" otherwise.
                    error_count += 1
                    print(
                        f"[agent] Web search: query {query!r} failed — {type(exc).__name__}: {exc}"
                    )
        if stale_count:
            print(f"[agent] Web search: dropped {stale_count} result(s) older than {max_age_days}d")
        if error_count:
            print(
                f"[agent] Web search: {error_count}/{len(state['queries'])} quer{'y' if error_count == 1 else 'ies'} failed"
            )
        return {
            **state,
            "search_results": state["search_results"] + new_results,
            "search_attempt": attempt + 1,
        }

    # --- Node: Search Evaluator ---
    def search_evaluator_node(state: AgentState) -> AgentState:
        results = state["search_results"]
        unique_domains = {r["url"].split("/")[2] for r in results if r.get("url")}
        sufficient = len(results) >= min_results and len(unique_domains) >= min_domains
        print(
            f"[agent] Search evaluator: {len(results)} results, {len(unique_domains)} domains — {'sufficient' if sufficient else 'insufficient'}"
        )
        for d in sorted(unique_domains):
            print(f"[agent]   {d}")
        return {**state, "search_sufficient": sufficient}

    def _route_after_evaluation(state: AgentState) -> str:
        if state["search_sufficient"] or state["search_attempt"] >= 2:
            return "market_analyst"
        return "web_search"

    # --- Node: Market Analyst ---
    def market_analyst_node(state: AgentState) -> AgentState:
        today = _today_utc()
        snippets_text = "\n\n---\n\n".join(state["snippets"])
        context_parts = [
            f"**Query:** {r['query']}\n**Source:** {r['url']}\n"
            f"**Published:** {_age_label(r.get('published_date', ''), today)}\n{r['content']}"
            for r in state["search_results"]
        ]
        context_text = "\n\n---\n\n".join(context_parts)
        prompt = _PROMPTS_ENV.get_template("market_analyst.j2").render(
            today=today,
            snippets_text=snippets_text,
            context_text=context_text,
        )
        response = llm.invoke([SystemMessage(content=_SYSTEM_PROMPT), HumanMessage(content=prompt)])
        report_md = (
            f"# Investment News Analysis AI — {today}\n\n"
            f"{response.content.strip()}\n\n"
            f"---\n*Generated by Investment News Analysis AI v{INA_VERSION}*"
        )
        return {**state, "report": report_md}

    # --- Node: Report Verifier ---
    def report_verifier_node(state: AgentState) -> AgentState:
        valid_urls = {r["url"] for r in state["search_results"] if r.get("url")}
        published_by_url = {
            r["url"]: r.get("published_date", "") for r in state["search_results"] if r.get("url")
        }
        report_md = _verify_citations(state["report"], valid_urls)
        report_md = _repair_citation_dates(report_md, published_by_url, _today_utc())
        report_md = _flag_unquantified_priced_in_checks(report_md)
        return {**state, "report": report_md}

    # --- Graph assembly ---
    graph = StateGraph(AgentState)
    graph.add_node("query_generation", query_generation_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("search_evaluator", search_evaluator_node)
    graph.add_node("market_analyst", market_analyst_node)
    graph.add_node("report_verifier", report_verifier_node)

    graph.set_entry_point("query_generation")
    graph.add_edge("query_generation", "web_search")
    graph.add_edge("web_search", "search_evaluator")
    graph.add_conditional_edges(
        "search_evaluator",
        _route_after_evaluation,
        {"market_analyst": "market_analyst", "web_search": "web_search"},
    )
    graph.add_edge("market_analyst", "report_verifier")
    graph.add_edge("report_verifier", END)

    return graph.compile()


# Cache the compiled graph across warm Lambda invocations
_graph = None


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event, context):
    today = _today_utc()
    print(f"[agent] Starting run for {today}")

    snippets = _list_snippets(today)
    print(f"[agent] Found {len(snippets)} snippet(s) — running LangGraph pipeline")

    openrouter_key = _get_secret(SSM_OPENROUTER_PARAM)
    tavily_key = _get_secret(SSM_TAVILY_PARAM)

    global _graph
    if _graph is None:
        _graph = _build_graph(openrouter_key, tavily_key)

    initial_state = {
        "snippets": snippets,
        "queries": [],
        "search_results": [],
        "search_attempt": 0,
        "search_sufficient": False,
        "report": "",
    }
    final_state = _graph.invoke(initial_state)
    md_text = final_state["report"]

    pdf_bytes = _md_to_pdf(md_text)
    _upload_report(today, md_text, pdf_bytes)
    print(f"[agent] Report uploaded to s3://{OUTPUT_BUCKET}/output/{today}/")

    return {"statusCode": 200, "date": today}
