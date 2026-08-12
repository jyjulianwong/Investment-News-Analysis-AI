import os
import warnings

import boto3
import markdown
from helpers import PROMPTS_ENV, today_utc
from nodes.market_analyst import build_market_analyst_node
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
# LangGraph pipeline
# ---------------------------------------------------------------------------


def _build_graph(openrouter_key: str, tavily_key: str):
    """Build and return the compiled LangGraph graph. Called once per warm start."""
    from langchain_openai import ChatOpenAI
    from langchain_tavily import TavilySearch
    from langgraph.graph import END, StateGraph

    llm = ChatOpenAI(
        model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.4-nano"),
        openai_api_key=openrouter_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.4,
    )

    system_prompt = PROMPTS_ENV.get_template("system.j2").render()

    # time_range is deliberately NOT baked into these kwargs: it varies by
    # search attempt (see nodes/web_search.py), so it's passed per-call instead.
    tavily_kwargs = {
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
    include_domains_raw = os.environ.get(
        "TAVILY_INCLUDE_DOMAINS",
        "reuters.com,apnews.com,cnbc.com,marketwatch.com,investing.com,"
        "tradingeconomics.com,federalreserve.gov,bis.org,imf.org,"
        "finance.yahoo.com,theguardian.com",
    )
    include_domains = [d.strip() for d in include_domains_raw.split(",") if d.strip()]
    search_tool_filtered = (
        TavilySearch(**tavily_kwargs, include_domains=include_domains) if include_domains else None
    )
    search_tool_open = TavilySearch(**tavily_kwargs)
    search_workers = int(os.environ.get("TAVILY_SEARCH_WORKERS", "5"))
    min_results = int(os.environ.get("EVALUATOR_MIN_RESULTS", "5"))
    min_domains = int(os.environ.get("EVALUATOR_MIN_DOMAINS", "3"))

    graph = StateGraph(AgentState)
    graph.add_node("query_generation", build_query_generation_node(llm, system_prompt))
    graph.add_node(
        "web_search", build_web_search_node(search_tool_filtered, search_tool_open, search_workers)
    )
    graph.add_node("search_evaluator", build_search_evaluator_node(min_results, min_domains))
    graph.add_node("market_analyst", build_market_analyst_node(llm, system_prompt))
    graph.add_node("report_verifier", report_verifier_node)

    graph.set_entry_point("query_generation")
    graph.add_edge("query_generation", "web_search")
    graph.add_edge("web_search", "search_evaluator")
    graph.add_conditional_edges(
        "search_evaluator",
        route_after_evaluation,
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
    today = today_utc()
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
