import re

from helpers import PROMPTS_ENV, today_utc
from langchain_core.messages import HumanMessage, SystemMessage
from state import AgentState

_VERDICT_RE = re.compile(r"(?i)VERDICT:\s*(PASS|FAIL)")
_FEEDBACK_RE = re.compile(r"(?is)FEEDBACK:\s*(.+)")


def build_report_evaluator_node(llm, system_prompt: str, max_retries: int):
    def report_evaluator_node(state: AgentState) -> AgentState:
        prompt = PROMPTS_ENV.get_template("report_evaluator.j2").render(
            today=today_utc(), report_md=state.report
        )
        response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=prompt)])
        content = response.content.strip()

        verdict_match = _VERDICT_RE.search(content)
        # An unparseable response fails open as PASS — this node is a
        # consistency check on top of an already-validated pipeline, not a
        # required gate, so a malformed judge response should not be able to
        # loop the graph forever.
        verdict = verdict_match.group(1).upper() if verdict_match else "PASS"
        feedback_match = _FEEDBACK_RE.search(content)
        feedback = feedback_match.group(1).strip() if feedback_match else ""

        revision = state.report_revision
        if verdict == "FAIL" and feedback and revision < max_retries:
            print(
                f"[agent] Report evaluator: major contradiction(s) found "
                f"(revision {revision + 1}/{max_retries}) -> {feedback}"
            )
            return {
                "report_approved": False,
                "report_feedback": feedback,
                "report_revision": revision + 1,
            }
        if verdict == "FAIL":
            print(
                f"[agent] Report evaluator: contradiction(s) still flagged after "
                f"{max_retries} revision(s) — proceeding anyway: {feedback}"
            )
        else:
            print("[agent] Report evaluator: no major contradictions found")
        return {"report_approved": True, "report_feedback": ""}

    return report_evaluator_node


def route_after_report_evaluation(state: AgentState) -> str:
    return "report_validator" if state.report_approved else "market_analyst"
