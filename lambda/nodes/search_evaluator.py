from urllib.parse import urlparse

from search_providers.base import WebSearchProvider
from state import AgentState


def _is_newsletter_result(result: dict) -> bool:
    # email_newsletter_search.py's `_as_search_result` stands in a `mailto:`
    # URI for a URL. Newsletter results are excluded from this node's
    # sufficiency/diversity check (see search_evaluator_node) — they're
    # fetched unconditionally from a fixed, small sender list rather than
    # queried for, so counting them here would let a healthy-looking mailbox
    # mask a web search provider that returned nothing (e.g. an exhausted
    # API quota never falling through to the next provider). They still
    # reach the analyst as WEB CONTEXT via state["search_results"] — only
    # this evaluation is scoped to actual web search results.
    return result.get("url", "").startswith("mailto:")


def _domain(url: str) -> str:
    # Only ever called on the web-search-only subset of results, so
    # http(s) netloc extraction is all that's needed here.
    return urlparse(url).netloc or url


def build_search_evaluator_node(
    min_results: int, min_domains: int, providers: list[WebSearchProvider]
):
    def search_evaluator_node(state: AgentState) -> AgentState:
        results = [r for r in state["search_results"] if not _is_newsletter_result(r)]
        unique_domains = {_domain(r["url"]) for r in results if r.get("url")}
        sufficient = len(results) >= min_results and len(unique_domains) >= min_domains
        print(
            f"[agent] Search evaluator: {len(results)} results, {len(unique_domains)} domains — {'sufficient' if sufficient else 'insufficient'}"
        )
        for d in sorted(unique_domains):
            print(f"[agent]   {d}")

        provider_index = state["search_provider_index"]
        attempt = state["search_attempt"]
        # Each provider gets its own narrow/wide (day/week) retry pair
        # (see nodes/web_search.py). Once both attempts are exhausted and
        # the result is still insufficient, fall through to the next
        # provider in priority order, resetting attempts for it.
        provider_exhausted = attempt >= 2
        if not sufficient and provider_exhausted and provider_index + 1 < len(providers):
            print(
                f"[agent] Search evaluator: {providers[provider_index].name} exhausted — "
                f"falling through to {providers[provider_index + 1].name}"
            )
            provider_index += 1
            attempt = 0

        return {
            **state,
            "search_sufficient": sufficient,
            "search_provider_index": provider_index,
            "search_attempt": attempt,
        }

    return search_evaluator_node


def route_after_evaluation(state: AgentState) -> str:
    if state["search_sufficient"] or state["search_attempt"] >= 2:
        return "market_analyst"
    return "web_search"
