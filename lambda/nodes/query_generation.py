from helpers import PROMPTS_ENV, today_utc
from langchain_core.messages import HumanMessage, SystemMessage
from state import AgentState, SearchResult


def _format_known_results(results: list[SearchResult]) -> str:
    return "\n\n---\n\n".join(
        f"**Source:** {r.url or 'n/a'}\n**Published:** {r.published_date or 'unknown'}\n{r.content}"
        for r in results
    )


def build_query_generation_node(llm, system_prompt: str):
    def query_generation_node(state: AgentState) -> AgentState:
        snippets_text = "\n\n---\n\n".join(state.snippets)
        # At this point in the graph, search_results only ever holds
        # newsletter entries (email_newsletter_search runs before this node;
        # web_search hasn't run yet) — but injecting it generically, rather
        # than hardcoding "newsletters", keeps this node correct if a future
        # retry loop ever re-enters query_generation with real web results
        # already accumulated.
        known_results_text = _format_known_results(state.search_results)
        prompt = PROMPTS_ENV.get_template("query_generation.j2").render(
            today=today_utc(), snippets_text=snippets_text, known_results_text=known_results_text
        )
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)])
        lines = [
            line.lstrip("0123456789. ").strip()
            for line in response.content.strip().splitlines()
            if line.strip()
        ]
        print(
            f"[agent] Query generation: {len(lines)} quer{'y' if len(lines) == 1 else 'ies'} generated"
        )
        for q in lines:
            print(f"[agent]   {q}")
        return {"queries": lines}

    return query_generation_node
