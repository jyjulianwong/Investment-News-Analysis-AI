import os
from datetime import datetime, timezone
from typing import TypedDict

import boto3
import markdown
from weasyprint import HTML


def _today_utc() -> str:
    override = os.environ.get("INA_DATETIME_OVERRIDE")
    if override:
        return override[:10]
    return datetime.now(tz=timezone.utc).date().isoformat()


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
# No-snippets fallback report
# ---------------------------------------------------------------------------


def _no_snippets_report(day: str) -> str:
    return f"""# Investment News Analysis — {day}

## No Snippets Submitted

No news snippets were submitted for {day}. The analysis pipeline was skipped.

Please submit at least one news snippet via the web interface before 12:00 UTC
to receive a market analysis report for that day.
"""


# ---------------------------------------------------------------------------
# LangGraph pipeline
# ---------------------------------------------------------------------------


def _build_graph(openrouter_key: str, tavily_key: str):
    """Build and return the compiled LangGraph graph. Called once per warm start."""
    from langchain_community.tools.tavily_search import TavilySearchResults
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, StateGraph

    os.environ["TAVILY_API_KEY"] = tavily_key

    llm = ChatOpenAI(
        model="openai/gpt-4o-mini",  # cost-efficient via OpenRouter
        openai_api_key=openrouter_key,
        openai_api_base="https://openrouter.ai/api/v1",
        temperature=0.3,
    )

    search_tool = TavilySearchResults(max_results=5)

    class AgentState(TypedDict):
        snippets: list[str]
        queries: list[str]
        search_results: list[dict]
        report: str

    # --- Node: Query Generation ---
    def query_generation_node(state: AgentState) -> AgentState:
        snippets_text = "\n\n---\n\n".join(state["snippets"])
        prompt = (
            "You are a financial research assistant. Given the following news snippets, "
            "generate a list of 5–8 precise web search queries that would help gather "
            "up-to-date context and data needed to analyse their market and investment implications.\n\n"
            f"NEWS SNIPPETS:\n{snippets_text}\n\n"
            "Return ONLY a numbered list of search queries, one per line. No other text."
        )
        response = llm.invoke(prompt)
        lines = [
            line.lstrip("0123456789. ").strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        return {**state, "queries": lines}

    # --- Node: Web Search ---
    def web_search_node(state: AgentState) -> AgentState:
        all_results = []
        for query in state["queries"]:
            try:
                results = search_tool.invoke(query)
                for r in results:
                    all_results.append(
                        {"query": query, "content": r.get("content", ""), "url": r.get("url", "")}
                    )
            except Exception:
                pass  # single-query failures should not abort the entire run
        return {**state, "search_results": all_results}

    # --- Node: Market Analyst ---
    def market_analyst_node(state: AgentState) -> AgentState:
        today = _today_utc()
        snippets_text = "\n\n---\n\n".join(state["snippets"])
        context_parts = [
            f"**Query:** {r['query']}\n**Source:** {r['url']}\n{r['content']}"
            for r in state["search_results"]
        ]
        context_text = "\n\n---\n\n".join(context_parts)

        prompt = (
            f"You are a senior market analyst producing a daily investment intelligence report dated {today}.\n\n"
            "You have been provided with:\n"
            "1. Raw news snippets submitted by the user today.\n"
            "2. Extended web search context gathered from those snippets.\n\n"
            "Based on this information, write a thorough Markdown report that covers:\n"
            "- **Executive Summary**: 3–5 bullet points of the most important insights.\n"
            "- **Key Themes**: Major macroeconomic or sector themes emerging from the news.\n"
            "- **Asset Class Outlook** (equities, bonds, commodities, crypto as relevant): "
            "mid-to-long-term directional views with reasoning.\n"
            "- **Specific Opportunities & Risks**: Concrete assets, sectors, or geographies "
            "to watch, with a brief rationale for each.\n"
            "- **Conclusion**: A short paragraph synthesising the overall market direction.\n\n"
            "Use Markdown formatting (headings, bullet points, bold). Be analytical and specific. "
            "Cite sources where relevant using [Source](url) Markdown links.\n\n"
            f"## NEWS SNIPPETS\n\n{snippets_text}\n\n"
            f"## WEB CONTEXT\n\n{context_text}"
        )
        response = llm.invoke(prompt)
        report_md = f"# Investment News Analysis — {today}\n\n{response.content.strip()}"
        return {**state, "report": report_md}

    # --- Graph assembly ---
    graph = StateGraph(AgentState)
    graph.add_node("query_generation", query_generation_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("market_analyst", market_analyst_node)

    graph.set_entry_point("query_generation")
    graph.add_edge("query_generation", "web_search")
    graph.add_edge("web_search", "market_analyst")
    graph.add_edge("market_analyst", END)

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

    if not snippets:
        print("[agent] No snippets found — generating fallback report")
        md_text = _no_snippets_report(today)
    else:
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
            "report": "",
        }
        final_state = _graph.invoke(initial_state)
        md_text = final_state["report"]

    pdf_bytes = _md_to_pdf(md_text)
    _upload_report(today, md_text, pdf_bytes)
    print(f"[agent] Report uploaded to s3://{OUTPUT_BUCKET}/output/{today}/")

    return {"statusCode": 200, "date": today}
