from search_providers.base import WebSearchProvider


class DDGSSearchProvider(WebSearchProvider):
    """DDGS-backed search provider — a free, keyless fallback for when the
    primary (paid/quota-limited) provider(s) come up short.

    DDGS is an unofficial scraper with no SLA and known rate-limiting/
    blocking behavior, especially from shared cloud IPs — it's meant to
    supplement a curated primary source, not replace it.
    """

    name = "ddgs"

    _TIME_RANGE = {"day": "d", "week": "w"}

    def __init__(self, max_results: int):
        self._max_results = max_results

    def search(self, query: str, time_range: str) -> dict:
        from ddgs import DDGS

        try:
            # A fresh DDGS() per call, not shared across threads — matches
            # the library's own usage pattern and avoids cross-thread state
            # issues under web_search.py's ThreadPoolExecutor.
            with DDGS() as ddgs:
                raw = ddgs.news(
                    query,
                    timelimit=self._TIME_RANGE.get(time_range),
                    max_results=self._max_results,
                )
        except Exception as exc:
            return {"error": exc}
        return {
            "results": [
                {
                    "url": r.get("url", ""),
                    "content": r.get("body", ""),
                    "published_date": r.get("date", ""),
                }
                for r in raw
            ]
        }
