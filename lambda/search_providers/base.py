from abc import ABC, abstractmethod


class WebSearchProvider(ABC):
    """Adapter interface for a web search backend.

    Node and graph code depend only on `WebSearchProvider`, never on a
    concrete search service — add support for a new one (a different search
    API, a scraper, ...) by implementing this interface and registering it
    in `search_providers.factory`, without touching `nodes/web_search.py`.
    """

    name: str

    @abstractmethod
    def search(self, query: str, time_range: str) -> dict:
        """Run one search call for `query` over `time_range` ("day" or
        "week").

        Returns `{"results": [{"url", "content", "published_date"}, ...]}`
        on success — an empty `results` list is a legitimate "nothing
        found", not a failure. Returns `{"error": Exception}` on a hard
        provider failure (auth, quota, rate limit, network, ...); callers
        must treat that differently from a legitimate zero-result search,
        since it means the provider couldn't be trusted for this call at
        all, not that there was no news.
        """
