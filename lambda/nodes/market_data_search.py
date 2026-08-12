import json
import os
import re
from datetime import date, timedelta

from helpers import PROMPTS_ENV, today_utc
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from state import AgentState

# Bounds the tool-calling loop below — each iteration is one LLM round-trip,
# so this also bounds how much of the Lambda's 15-minute timeout this node
# can consume.
MAX_TOOL_ITERATIONS = int(os.environ.get("MARKET_DATA_MAX_TOOL_CALLS", "8"))

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# (stockstats column name, display label) for the indicator set fetched on
# every call — a fixed set so tool output has a consistent shape.
_INDICATOR_COLUMNS = (
    ("close_50_sma", "50-day SMA"),
    ("close_200_sma", "200-day SMA"),
    ("rsi", "RSI (14)"),
    ("macd", "MACD"),
    ("macds", "MACD signal"),
    ("macdh", "MACD histogram"),
    ("boll", "Bollinger mid"),
    ("boll_ub", "Bollinger upper"),
    ("boll_lb", "Bollinger lower"),
)


def _classify_trend(latest_close: float, ind: dict[str, float | None]) -> str:
    """Deterministic plain-language trend read from indicator values.

    The overbought/oversold, golden/death-cross, and Bollinger-extreme
    thresholds are compared here rather than left to the model — the same
    reasoning as market_analyst._age_label: doing the numeric comparison in
    code closes off the failure mode where the model eyeballs the numbers in
    the tool output and gets the direction wrong.
    """
    sma50, sma200 = ind.get("close_50_sma"), ind.get("close_200_sma")
    rsi = ind.get("rsi")
    macd, macds = ind.get("macd"), ind.get("macds")
    boll_ub, boll_lb = ind.get("boll_ub"), ind.get("boll_lb")

    notes = []
    if sma50 is not None and sma200 is not None:
        if sma50 > sma200:
            notes.append(
                "50-day SMA above 200-day SMA (golden-cross regime, bullish trend structure)"
            )
        else:
            notes.append(
                "50-day SMA below 200-day SMA (death-cross regime, bearish trend structure)"
            )
    if sma200 is not None:
        notes.append(f"price is {'above' if latest_close > sma200 else 'below'} its 200-day SMA")
    if rsi is not None:
        if rsi >= 70:
            notes.append(f"RSI {rsi:.1f} — overbought, may be due a pullback")
        elif rsi <= 30:
            notes.append(f"RSI {rsi:.1f} — oversold, may be due a bounce")
        else:
            notes.append(f"RSI {rsi:.1f} — neutral")
    if macd is not None and macds is not None:
        direction = "bullish" if macd > macds else "bearish"
        notes.append(
            f"MACD {'above' if macd > macds else 'below'} its signal line — {direction} momentum"
        )
    if boll_ub is not None and boll_lb is not None:
        if latest_close >= boll_ub:
            notes.append(
                "price at/above the upper Bollinger Band — stretched to the upside, possible reversal risk"
            )
        elif latest_close <= boll_lb:
            notes.append(
                "price at/below the lower Bollinger Band — stretched to the downside, possible reversal risk"
            )
    return "; ".join(notes) if notes else "insufficient history for a technical read"


_CHANGE_WINDOWS = ((1, "1-day"), (30, "~30-trading-day"), (90, "~90-trading-day"))


def _fetch_snapshot(symbol: str, today: str) -> dict | None:
    """Fetch price history and compute technical indicators for `symbol`,
    as of `today` (the pipeline's notion of "today" — respects
    INA_DATETIME_OVERRIDE, not the real wall-clock date).

    Returns a structured snapshot, or None if Yahoo Finance has no data for
    it (unknown/delisted ticker). Network/lookup errors propagate to the
    caller, which turns them into the tool's 'no data' message.
    """
    import pandas as pd
    import yfinance as yf
    from stockstats import wrap

    end_date = date.fromisoformat(today)
    # yfinance's `end` is exclusive, so request one day past `today` to
    # keep today's own bar in range if the market has already closed for
    # it — the same pattern web_search.py uses for its date bounds. Without
    # pinning `end` here at all, this would default to the real wall-clock
    # date and leak future data into a backtest run under
    # INA_DATETIME_OVERRIDE, the same look-ahead failure mode the "future"
    # guard in web_search.py exists to prevent for search results.
    hist = yf.Ticker(symbol).history(
        start=(end_date - timedelta(days=365)).isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),
        interval="1d",
    )
    if hist.empty:
        return None

    closes = hist["Close"]
    latest_close = round(float(closes.iloc[-1]), 4)
    latest_date = hist.index[-1].strftime("%Y-%m-%d")

    def _pct_change(days_back: int) -> float | None:
        if days_back <= 0 or len(closes) <= days_back:
            return None
        reference = float(closes.iloc[-1 - days_back])
        if reference == 0:
            return None
        return round((latest_close - reference) / reference * 100, 2)

    changes = {
        label: _pct_change(min(days_back, len(closes) - 1)) for days_back, label in _CHANGE_WINDOWS
    }

    # A year of daily bars is enough history for a 200-day SMA on an
    # established instrument; a newly-listed one just yields N/A below —
    # stockstats leaves those cells NaN rather than raising.
    stock_df = wrap(hist.reset_index().copy())
    indicators: dict[str, float | None] = {}
    for column, _label in _INDICATOR_COLUMNS:
        try:
            stock_df[column]  # triggers stockstats calculation
            value = stock_df.iloc[-1][column]
            indicators[column] = None if pd.isna(value) else round(float(value), 4)
        except Exception:  # noqa: BLE001 — one bad indicator shouldn't sink the snapshot
            indicators[column] = None

    return {
        "symbol": symbol.upper(),
        "latest_close": latest_close,
        "latest_date": latest_date,
        "changes": changes,
        "indicators": indicators,
        "technical_read": _classify_trend(latest_close, indicators),
    }


def _format_snapshot(snapshot: dict) -> str:
    """Render a snapshot as the plain-text the LLM reads back as tool output."""
    lines = [
        f"Symbol: {snapshot['symbol']}",
        f"Latest close: {snapshot['latest_close']} on {snapshot['latest_date']}",
    ]
    for _days_back, label in _CHANGE_WINDOWS:
        change = snapshot["changes"][label]
        if change is not None:
            lines.append(f"{label} change: {change:+.2f}%")
    for column, label in _INDICATOR_COLUMNS:
        value = snapshot["indicators"][column]
        lines.append(f"{label}: {value if value is not None else 'N/A'}")
    lines.append(f"Technical read: {snapshot['technical_read']}")
    return "\n".join(lines)


def _compose_evidence(snapshot: dict, commentary: str) -> str:
    """Deterministically state the verified price/technical facts, then
    append the model's interpretive commentary.

    The model only supplies `commentary` — how this relates to the report's
    claim — never the price or the SMA/RSI/MACD/Bollinger comparisons
    themselves. Those come straight from `snapshot`, exactly as computed by
    `_classify_trend`/`_fetch_snapshot`, so the model can no longer invert a
    comparison (e.g. claim "above" when the data says "below") while
    paraphrasing it into prose — the failure mode observed in practice even
    when the tool output already stated the comparison in plain English.
    """
    day_change = snapshot["changes"]["1-day"]
    change_clause = f" ({day_change:+.2f}% on the day)" if day_change is not None else ""
    fact = (
        f"{snapshot['symbol']} closed at {snapshot['latest_close']} on {snapshot['latest_date']}"
        f"{change_clause}. {snapshot['technical_read']}."
    )
    return f"{fact} {commentary.strip()}"


def _build_market_data_tool():
    from langchain_core.tools import tool

    # Populated as a side effect of each successful tool call, keyed by the
    # resolved (uppercased) symbol — lets _apply_insertions later compose the
    # deterministic factual clause without re-parsing the tool's text output.
    snapshots: dict[str, dict] = {}
    # Mutable box for "today", set by the node before each invocation (see
    # build_market_data_search_node) — the tool itself takes no date
    # argument, so this is how it learns the pipeline's current date rather
    # than defaulting to the real wall-clock date.
    cutoff: dict[str, str] = {}

    @tool
    def get_market_data(symbol: str) -> str:
        """Look up price history and technical indicators for a Yahoo Finance
        ticker symbol — the source of truth for any claim about an asset's
        price level or trend, and for judging whether it looks
        overbought/oversold or near a bullish/bearish reversal. Use standard
        Yahoo Finance tickers, e.g. GC=F (gold futures), CL=F (WTI crude),
        BTC-USD (Bitcoin), ^GSPC (S&P 500), ^TNX (US 10-year Treasury yield),
        a plain ticker for individual equities (e.g. AAPL), or EURUSD=X for FX
        pairs. Returns the latest close, 1-day/30-day/90-day % change, 50-day
        and 200-day SMAs, RSI, MACD, Bollinger Bands, and a plain-language
        technical read, or an explicit 'no data' message if the symbol is
        invalid or unrecognised.
        """
        try:
            snapshot = _fetch_snapshot(symbol, cutoff["today"])
        except Exception as exc:
            return f"No market data available for '{symbol}' ({type(exc).__name__}: {exc})"
        if snapshot is None:
            return f"No market data available for '{symbol}' — unknown or delisted ticker."

        snapshots[snapshot["symbol"]] = snapshot
        return _format_snapshot(snapshot)

    return get_market_data, snapshots, cutoff


def _extract_insertions(text: str) -> list[dict]:
    """Parse the model's final JSON array of {anchor, symbol, commentary} objects.

    Any malformed or missing JSON fails open (empty list) — this node is
    additive evidence, not a required step, so a parsing failure should
    leave the report untouched rather than raise.
    """
    match = _JSON_ARRAY_RE.search(text)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [
        item
        for item in parsed
        if isinstance(item, dict)
        and item.get("anchor")
        and item.get("symbol")
        and item.get("commentary")
    ]


def _find_anchor_end(report_md: str, anchor: str) -> int | None:
    """Locate the end of `anchor` in `report_md`, or None if it can't be found.

    Tries an exact substring match first. If that fails, falls back to a
    regex built from the anchor with all `*` characters and whitespace runs
    stripped out, re-inserting an optional 0-2 asterisk run before every
    literal character and a `\\s+` for every whitespace run. This recovers
    the common case of the model dropping or shifting markdown bold markers
    while "copying" text — including dropping a `**` pair entirely, which a
    fixup keyed only on asterisks already present in the anchor would miss —
    without loosening the match on any actual word content: every non-`*`
    character still has to appear, in order, for the match to succeed.
    """
    idx = report_md.find(anchor)
    if idx != -1:
        return idx + len(anchor)

    core = re.sub(r"\s+", " ", anchor.replace("*", "")).strip()
    if not core:
        return None
    pattern = "".join(
        r"\*{0,2}\s+\*{0,2}" if ch == " " else r"\*{0,2}" + re.escape(ch) for ch in core
    )
    pattern = r"\*{0,2}" + pattern + r"\*{0,2}"
    match = re.search(pattern, report_md)
    return match.end() if match else None


def _apply_insertions(
    report_md: str, insertions: list[dict], snapshots: dict[str, dict], today: str
) -> str:
    """Insert evidence right after each anchor's position in the report.

    An anchor the model didn't copy closely enough to be located (see
    `_find_anchor_end`), or a symbol it didn't actually fetch a snapshot for
    (e.g. a typo), is silently skipped — the same fail-open posture as the
    citation verifier's handling of unverifiable URLs. This also guarantees
    the report's original text is never rewritten or paraphrased by this
    node, only appended to.
    """
    for item in insertions:
        anchor = item["anchor"]
        symbol = item["symbol"].strip().upper()
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            print(
                f"[agent] Market data search: no fetched snapshot for symbol, skipping -> {symbol!r}"
            )
            continue
        insert_at = _find_anchor_end(report_md, anchor)
        if insert_at is None:
            print(f"[agent] Market data search: anchor not found, skipping -> {anchor!r}")
            continue
        evidence = _compose_evidence(snapshot, item["commentary"])
        span = (
            f' <span class="ina-market-data">{evidence} '
            f"(Yahoo Finance market data, retrieved {today})</span>"
        )
        report_md = report_md[:insert_at] + span + report_md[insert_at:]
    return report_md


def build_market_data_search_node(llm, system_prompt: str):
    market_data_tool, snapshots, cutoff = _build_market_data_tool()
    llm_with_tools = llm.bind_tools([market_data_tool])

    def market_data_search_node(state: AgentState) -> AgentState:
        today = today_utc()
        # The node builder runs once per warm Lambda start (see _build_graph),
        # so `snapshots` would otherwise carry stale entries across days —
        # clear it before every run so a symbol can only resolve to data
        # actually fetched in this invocation. `cutoff["today"]` similarly
        # has to be refreshed every run so get_market_data respects
        # INA_DATETIME_OVERRIDE instead of a date left over from a prior
        # warm-start invocation.
        snapshots.clear()
        cutoff["today"] = today
        report_md = state["report"]
        prompt = PROMPTS_ENV.get_template("market_data_search.j2").render(
            today=today, report_md=report_md
        )
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]

        tool_calls_made = 0
        response = None
        for _ in range(MAX_TOOL_ITERATIONS):
            response = llm_with_tools.invoke(messages)
            messages.append(response)
            if not response.tool_calls:
                break
            for call in response.tool_calls:
                tool_calls_made += 1
                result = market_data_tool.invoke(call["args"])
                messages.append(ToolMessage(content=result, tool_call_id=call["id"]))
        else:
            print(f"[agent] Market data search: hit MAX_TOOL_ITERATIONS ({MAX_TOOL_ITERATIONS})")

        if tool_calls_made == 0:
            print("[agent] Market data search: no asset-price claims required verification")
            return state

        insertions = _extract_insertions(response.content if response else "")
        if not insertions:
            print("[agent] Market data search: no verbatim-matched evidence to insert")
            return state

        updated_report = _apply_insertions(report_md, insertions, snapshots, today)
        print(f"[agent] Market data search: inserted {len(insertions)} evidence snippet(s)")
        return {**state, "report": updated_report}

    return market_data_search_node
