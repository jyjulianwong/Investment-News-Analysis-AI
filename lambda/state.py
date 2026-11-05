from mrd_schema import MarketRegimeDetectorData
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """A single web search / newsletter result flowing through the graph.

    Enforced as a schema (rather than a plain dict) so a provider or node
    that drifts from the `{query, content, url, published_date}} shape fails
    loudly, at the point it's produced, instead of silently propagating a
    malformed entry into the Market Analyst's context or the Report
    Validator's citation checks.
    """

    query: str = ""
    content: str = ""
    url: str = ""
    published_date: str = ""


class AgentState(BaseModel):
    """Pydantic schema for the graph state, validated on every node
    transition (LangGraph coerces the accumulated channel values into this
    model before each node runs) rather than trusting each node to hand the
    next one well-shaped data.
    """

    snippets: list[str] = Field(default_factory=list)
    # Written by data_fetcher; None if MRD was unreachable or failed validation.
    market_regime_detector_data: MarketRegimeDetectorData | None = None
    queries: list[str] = Field(default_factory=list)
    search_results: list[SearchResult] = Field(default_factory=list)
    search_attempt: int = 0
    search_provider_index: int = 0
    search_sufficient: bool = False
    report: str = ""
    # Set by report_evaluator; read by market_analyst on a retry so it knows
    # what to fix, and by route_after_report_evaluation to decide whether to
    # loop back for another draft or proceed to report_validator.
    report_feedback: str = ""
    report_revision: int = 0
    report_approved: bool = False
