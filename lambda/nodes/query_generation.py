from helpers import PROMPTS_ENV, today_utc
from langchain_core.messages import HumanMessage, SystemMessage
from state import AgentState


def build_query_generation_node(llm, system_prompt: str):
    def query_generation_node(state: AgentState) -> AgentState:
        snippets_text = "\n\n---\n\n".join(state["snippets"])
        prompt = PROMPTS_ENV.get_template("query_generation.j2").render(
            today=today_utc(), snippets_text=snippets_text
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
        return {**state, "queries": lines}

    return query_generation_node
