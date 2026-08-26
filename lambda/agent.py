import os
import warnings

import boto3
import markdown
from helpers import PROMPTS_ENV, today_utc
from nodes.email_newsletter_search import build_email_newsletter_search_node
from nodes.market_analyst import build_market_analyst_node
from nodes.market_data_search import build_market_data_search_node
from nodes.news_snippet_getter import build_news_snippet_getter_node
from nodes.query_generation import build_query_generation_node
from nodes.report_verifier import report_verifier_node
from nodes.search_evaluator import build_search_evaluator_node, route_after_evaluation
from nodes.web_search import build_web_search_node
from state import AgentState
from weasyprint import HTML

# LangGraph instantiates JsonPlusSerializer without allowed_objects; suppress
# the pending deprecation until LangGraph fixes it upstream.
warnings.filterwarnings("ignore", message=r"The default value of `allowed_objects`")

# ---------------------------------------------------------------------------
# AWS clients — reused across warm invocations
# ---------------------------------------------------------------------------

_ssm = boto3.client("ssm", region_name=os.environ["AWS_REGION_NAME"])
_s3 = boto3.client("s3", region_name=os.environ["AWS_REGION_NAME"])

INPUT_BUCKET = os.environ["AWS_S3_INPUT_BUCKET_NAME"]
OUTPUT_BUCKET = os.environ["AWS_S3_OUTPUT_BUCKET_NAME"]
SSM_OPENROUTER_PARAM = os.environ["SSM_OPENROUTER_PARAM"]
SSM_TAVILY_PARAM = os.environ["SSM_TAVILY_PARAM"]
SSM_EMAIL_IMAP_USERNAME_PARAM = os.environ["SSM_EMAIL_IMAP_USERNAME_PARAM"]
SSM_EMAIL_IMAP_PASSWORD_PARAM = os.environ["SSM_EMAIL_IMAP_PASSWORD_PARAM"]


def _get_secret(param_name: str) -> str:
    resp = _ssm.get_parameter(Name=param_name, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _parse_newsletter_senders(raw: str, default_count: int) -> list[tuple[str, int]]:
    """Parse `EMAIL_NEWSLETTER_SENDERS` into `(address, count)` pairs.

    Each comma-separated entry is either a bare address (fetches
    `default_count` messages) or `address:count` (e.g. `foo@bar.com:3`) to
    override how many of that sender's latest messages to pull — a
    high-volume mailbox like FT's news alerts wants more than a
    single-issue-per-day newsletter, without a code change to support it.
    """
    senders = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        address, _, count_str = entry.partition(":")
        count = int(count_str) if count_str else default_count
        senders.append((address.strip(), count))
    return senders


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------


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
.ina-market-data { color: #999; }
"""


def _md_to_pdf(md_text: str) -> bytes:
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    full_html = f"<html><head><style>{_PDF_CSS}</style></head><body>{html_body}</body></html>"
    return HTML(string=full_html).write_pdf()


# ---------------------------------------------------------------------------
# LangGraph pipeline
# ---------------------------------------------------------------------------


def _build_graph(
    openrouter_key: str, tavily_key: str, email_imap_username: str, email_imap_password: str
):
    """Build and return the compiled LangGraph graph. Called once per warm start."""
    from email_providers.factory import build_email_provider
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, StateGraph
    from search_providers.factory import build_web_search_provider

    email_provider = build_email_provider(
        os.environ.get("EMAIL_PROVIDER", "imap"),
        host=os.environ.get("EMAIL_IMAP_HOST", "imap.gmail.com"),
        port=int(os.environ.get("EMAIL_IMAP_PORT", "993")),
        username=email_imap_username,
        password=email_imap_password,
    )
    email_newsletter_senders = _parse_newsletter_senders(
        os.environ.get(
            "EMAIL_NEWSLETTER_SENDERS",
            "brewmarkets@morningbrew.com,"
            "FT@news-alerts.ft.com:3,"
            "noreply@newsletter.thetimes.co.uk:3",
        ),
        default_count=int(os.environ.get("EMAIL_NEWSLETTER_DEFAULT_FETCH_COUNT", "1")),
    )

    llm = ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", "google/gemini-3-flash-preview"),
        openai_api_key=openrouter_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.4,
        default_headers={
            "HTTP-Referer": "https://github.com/jyjulianwong/Investment-News-Analysis-AI",
            "X-Title": "Investment News Analysis AI",
        },
    )

    system_prompt = PROMPTS_ENV.get_template("system.j2").render()

    include_domains_raw = os.environ.get(
        "TAVILY_INCLUDE_DOMAINS",
        "reuters.com,apnews.com,cnbc.com,marketwatch.com,investing.com,"
        "tradingeconomics.com,federalreserve.gov,bis.org,imf.org,"
        "finance.yahoo.com,theguardian.com",
    )
    include_domains = [d.strip() for d in include_domains_raw.split(",") if d.strip()]

    # Construction kwargs per search provider. Each provider needs different
    # inputs (Tavily needs the SSM-fetched key + a curated domain list, DDGS
    # needs neither) — keyed by the same name used in WEB_SEARCH_PROVIDERS
    # and search_providers.factory._PROVIDERS. Adding a new provider means
    # adding one entry here plus one entry in that registry — no other code
    # changes.
    provider_kwargs = {
        "tavily": {
            "api_key": tavily_key,
            "max_results": int(os.environ.get("TAVILY_MAX_RESULTS", "10")),
            "search_depth": os.environ.get("TAVILY_SEARCH_DEPTH", "advanced"),
            # "news" is the only Tavily topic that reliably attaches
            # published_date metadata to results — "finance" and "general"
            # omit it on most results, which is why staleness/date checks
            # need it. The curated include_domains list already does the
            # work of keeping results finance-relevant, so this doesn't
            # sacrifice much.
            "topic": os.environ.get("TAVILY_TOPIC", "news"),
            "include_domains": include_domains,
        },
        "ddgs": {
            "max_results": int(os.environ.get("DDGS_MAX_RESULTS", "10")),
        },
    }
    provider_priority = [
        p.strip()
        for p in os.environ.get("WEB_SEARCH_PROVIDERS", "tavily,ddgs").split(",")
        if p.strip()
    ]
    search_providers = [
        build_web_search_provider(name, **provider_kwargs[name]) for name in provider_priority
    ]
    search_workers = int(os.environ.get("TAVILY_SEARCH_WORKERS", "5"))
    min_results = int(os.environ.get("EVALUATOR_MIN_RESULTS", "5"))
    min_domains = int(os.environ.get("EVALUATOR_MIN_DOMAINS", "3"))

    graph = StateGraph(AgentState)
    graph.add_node(
        "email_newsletter_search",
        build_email_newsletter_search_node(
            email_provider, email_newsletter_senders, _s3, INPUT_BUCKET
        ),
    )
    graph.add_node("news_snippet_getter", build_news_snippet_getter_node(_s3, INPUT_BUCKET))
    graph.add_node("query_generation", build_query_generation_node(llm, system_prompt))
    graph.add_node("web_search", build_web_search_node(search_providers, search_workers))
    graph.add_node(
        "search_evaluator",
        build_search_evaluator_node(min_results, min_domains, search_providers),
    )
    graph.add_node("market_analyst", build_market_analyst_node(llm, system_prompt))
    graph.add_node("market_data_search", build_market_data_search_node(llm, system_prompt))
    graph.add_node("report_verifier", report_verifier_node)

    graph.set_entry_point("email_newsletter_search")
    graph.add_edge("email_newsletter_search", "news_snippet_getter")
    graph.add_edge("news_snippet_getter", "query_generation")
    graph.add_edge("query_generation", "web_search")
    graph.add_edge("web_search", "search_evaluator")
    graph.add_conditional_edges(
        "search_evaluator",
        route_after_evaluation,
        {"market_analyst": "market_analyst", "web_search": "web_search"},
    )
    graph.add_edge("market_analyst", "market_data_search")
    graph.add_edge("market_data_search", "report_verifier")
    graph.add_edge("report_verifier", END)

    return graph.compile()


# Cache the compiled graph across warm Lambda invocations
_graph = None


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def handler(event, context):
    today = today_utc()
    print(f"[agent] Starting run for {today}")

    openrouter_key = _get_secret(SSM_OPENROUTER_PARAM)
    tavily_key = _get_secret(SSM_TAVILY_PARAM)
    email_imap_username = _get_secret(SSM_EMAIL_IMAP_USERNAME_PARAM)
    email_imap_password = _get_secret(SSM_EMAIL_IMAP_PASSWORD_PARAM)

    global _graph
    if _graph is None:
        _graph = _build_graph(openrouter_key, tavily_key, email_imap_username, email_imap_password)

    # snippets starts empty — email_newsletter_search and news_snippet_getter
    # populate it from S3 as the first two graph nodes (see _build_graph),
    # rather than this being a pre-processing step outside the graph.
    initial_state = {
        "snippets": [],
        "queries": [],
        "search_results": [],
        "search_attempt": 0,
        "search_provider_index": 0,
        "search_sufficient": False,
        "report": "",
    }
    final_state = _graph.invoke(initial_state)
    md_text = final_state["report"]

    pdf_bytes = _md_to_pdf(md_text)
    _upload_report(today, md_text, pdf_bytes)
    print(f"[agent] Report uploaded to s3://{OUTPUT_BUCKET}/output/{today}/")

    return {"statusCode": 200, "date": today}
