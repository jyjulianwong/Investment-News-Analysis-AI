from state import AgentState


def build_search_evaluator_node(min_results: int, min_domains: int):
    def search_evaluator_node(state: AgentState) -> AgentState:
        results = state["search_results"]
        unique_domains = {r["url"].split("/")[2] for r in results if r.get("url")}
        sufficient = len(results) >= min_results and len(unique_domains) >= min_domains
        print(
            f"[agent] Search evaluator: {len(results)} results, {len(unique_domains)} domains — {'sufficient' if sufficient else 'insufficient'}"
        )
        for d in sorted(unique_domains):
            print(f"[agent]   {d}")
        return {**state, "search_sufficient": sufficient}

    return search_evaluator_node


def route_after_evaluation(state: AgentState) -> str:
    if state["search_sufficient"] or state["search_attempt"] >= 2:
        return "market_analyst"
    return "web_search"
