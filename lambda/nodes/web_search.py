from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from helpers import parse_source_date, today_utc
from state import AgentState


def _within_days(published_date: str, today: str, max_age_days: int) -> bool:
    """Best-effort check that a source's reported publish date is recent enough
    and not from the future.

    A date we can't parse, or that's missing entirely, is treated as
    unverifiable rather than stale — Tavily doesn't reliably attach a publish
    date to every result, and dropping every undated result starves the
    report of content. Unverifiable results are instead flagged in the
    context handed to the analyst (see `nodes/market_analyst._age_label`) so
    the model can't treat them as confirmed-current evidence.

    The lower bound of -1 absorbs the odd result whose metadata timezone makes
    it look 1 day in the future relative to our UTC `today`. Anything more
    than 1 day ahead is rejected — this matters most in backtesting mode
    (INA_DATETIME_OVERRIDE), where Tavily may otherwise return articles
    published after the overridden date.
    """
    published = parse_source_date(published_date)
    if published is None:
        return True
    days_old = (date.fromisoformat(today) - published).days
    return -1 <= days_old <= max_age_days


def build_web_search_node(search_tool_filtered, search_tool_open, search_workers: int):
    def web_search_node(state: AgentState) -> AgentState:
        today = today_utc()
        attempt = state["search_attempt"]
        tool = search_tool_filtered if attempt == 0 and search_tool_filtered else search_tool_open
        # Same-day coverage can be genuinely thin (early in the day, a quiet
        # news cycle, a narrow query) — pinning every attempt to an exact
        # calendar-day match risks a starved, fact-free report. Start strict
        # and widen the recency window on retry, the same way the domain
        # filter already opens up on retry.
        time_range = "day" if attempt == 0 else "week"
        max_age_days = 1 if attempt == 0 else 7
        new_results = []
        stale_count = 0
        future_count = 0
        error_count = 0
        with ThreadPoolExecutor(max_workers=search_workers) as pool:
            futures = {
                pool.submit(tool.invoke, {"query": q, "time_range": time_range}): q
                for q in state["queries"]
            }
            for future in as_completed(futures):
                query = futures[future]
                try:
                    result = future.result()
                    for r in result.get("results", []):
                        published_date = r.get("published_date", "")
                        parsed = parse_source_date(published_date)
                        if parsed is not None and (date.fromisoformat(today) - parsed).days < -1:
                            future_count += 1
                            continue
                        if not _within_days(published_date, today, max_age_days):
                            stale_count += 1
                            continue
                        new_results.append(
                            {
                                "query": query,
                                "content": r.get("content", ""),
                                "url": r.get("url", ""),
                                "published_date": published_date,
                            }
                        )
                except Exception as exc:
                    # Single-query failures should not abort the entire run, but
                    # they must be visible — a silently-swallowed error on every
                    # query looks identical to "no news today" otherwise.
                    error_count += 1
                    print(
                        f"[agent] Web search: query {query!r} failed — {type(exc).__name__}: {exc}"
                    )
        if future_count:
            print(
                f"[agent] Web search: dropped {future_count} result(s) dated after {today} (future — backtesting guard)"
            )
        if stale_count:
            print(f"[agent] Web search: dropped {stale_count} result(s) older than {max_age_days}d")
        if error_count:
            print(
                f"[agent] Web search: {error_count}/{len(state['queries'])} quer{'y' if error_count == 1 else 'ies'} failed"
            )
        return {
            **state,
            "search_results": state["search_results"] + new_results,
            "search_attempt": attempt + 1,
        }

    return web_search_node
