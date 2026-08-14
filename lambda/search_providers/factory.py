from search_providers.base import WebSearchProvider
from search_providers.ddgs_provider import DDGSSearchProvider
from search_providers.tavily_provider import TavilySearchProvider

# Registry of search adapters keyed by the WEB_SEARCH_PROVIDERS priority
# list. Add support for a new provider by implementing `WebSearchProvider`
# (see search_providers/base.py) and registering its constructor here — node
# and graph code never change.
_PROVIDERS = {
    "tavily": TavilySearchProvider,
    "ddgs": DDGSSearchProvider,
}


def build_web_search_provider(provider: str, **kwargs) -> WebSearchProvider:
    try:
        provider_cls = _PROVIDERS[provider]
    except KeyError:
        raise ValueError(
            f"Unknown search provider {provider!r} — supported: {sorted(_PROVIDERS)}"
        ) from None
    return provider_cls(**kwargs)
