from search_providers.base import WebSearchProvider


class TavilySearchProvider(WebSearchProvider):
    """Tavily-backed search provider.

    `topic="news"` is the only Tavily topic that reliably attaches
    `published_date` metadata to results — "finance" and "general" omit it
    on most results, which is why staleness/date checks downstream need it.
    """

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        max_results: int,
        search_depth: str,
        topic: str,
        include_domains: list[str] | None = None,
    ):
        from langchain_tavily import TavilySearch

        kwargs = {
            "tavily_api_key": api_key,
            "max_results": max_results,
            "search_depth": search_depth,
            "topic": topic,
        }
        # The curated include_domains list keeps results finance-relevant on
        # the narrow ("day") pass; the wide ("week") pass drops it so a
        # thin news day doesn't starve the report of content.
        self._filtered = (
            TavilySearch(**kwargs, include_domains=include_domains) if include_domains else None
        )
        self._open = TavilySearch(**kwargs)

    def search(self, query: str, time_range: str) -> dict:
        tool = self._filtered if (time_range == "day" and self._filtered) else self._open
        try:
            return tool.invoke({"query": query, "time_range": time_range})
        except Exception as exc:
            return {"error": exc}
