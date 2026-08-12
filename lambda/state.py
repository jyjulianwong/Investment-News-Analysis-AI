from typing import TypedDict


class AgentState(TypedDict):
    snippets: list[str]
    queries: list[str]
    search_results: list[dict]
    search_attempt: int
    search_sufficient: bool
    report: str
