from urllib.parse import urlparse

from search_providers.base import WebSearchProvider
from state import AgentState


def _domain(url: str) -> str:
    # Web search results are usually http(s), where netloc is the domain.
    # Sources like email_newsletter_search's `mailto:` URLs have no netloc —
    # fall back to path (a bare `.split("/")[2]` would IndexError on those).
    # Using `.path` rather than the full url matters now that a single
    # newsletter sender can contribute several messages (up to its
    # configured count — see EMAIL_NEWSLETTER_SENDERS in agent.py), each
    # with a distinct fragment identifying that message (see
    # `_as_search_result` in nodes/email_newsletter_search.py): `.path` is
    # just the mailbox address, shared across those messages, so they still
    # count as one source for diversity purposes rather than one per email.
    parsed = urlparse(url)
    return parsed.netloc or parsed.path or url


def build_search_evaluator_node(
    min_results: int, min_domains: int, providers: list[WebSearchProvider]
):
    def search_evaluator_node(state: AgentState) -> AgentState:
        results = state["search_results"]
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
