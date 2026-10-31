# Architecture

## Agent Harness

The Lambda runs an 8-node LangGraph pipeline (`_build_graph` in `lambda/agent.py`). Only 3 nodes call an LLM, and only 1 of those has tools; the rest are deterministic Python.

Each box below shows the node's **objective**, the **state keys it reads** (`in`), the **state keys it writes** (`out`), and its **tools**. Blue = LLM node, grey = deterministic node. Nodes with a tool-calling LLM are noted explicitly.

```mermaid
flowchart TD
    START([handler: initial state<br/>all keys empty / 0 / False])

    N0["<b>0. email_newsletter_search</b> · no LLM<br/>
    <b>Objective:</b> fetch recent newsletters; save as snippets and as citable sources<br/>
    <b>State in:</b> search_results<br/>
    <b>State out:</b> search_results (+ mailto: results)<br/>
    <b>Tools:</b> none (EmailProvider IMAP fetch, S3 put_object)<br/>
    <b>Side effect:</b> uploads snippets to sources/DATE/"]

    N1["<b>1. news_snippet_getter</b> · no LLM<br/>
    <b>Objective:</b> load all of today's snippets from S3 (single source of truth)<br/>
    <b>State in:</b> none<br/>
    <b>State out:</b> snippets (overwritten)<br/>
    <b>Tools:</b> none (S3 list + get)"]

    N2["<b>2. query_generation</b> · LLM, no tools<br/>
    <b>Objective:</b> turn snippets into 6-12 web search queries<br/>
    <b>Prompt data:</b> today, snippets_text<br/>
    <b>State in:</b> snippets<br/>
    <b>State out:</b> queries<br/>
    <b>Tools:</b> none"]

    N3["<b>3. web_search</b> · no LLM<br/>
    <b>Objective:</b> run all queries on the current provider; filter stale and future results<br/>
    <b>State in:</b> queries, search_provider_index, search_attempt, search_results<br/>
    <b>State out:</b> search_results (appended), search_attempt (+1)<br/>
    <b>Tools:</b> none (WebSearchProvider.search in a thread pool;<br/>attempt 0 = 1-day window, later = 7-day)"]

    N4{"<b>4. search_evaluator</b> · no LLM<br/>
    <b>Objective:</b> judge result quantity and domain diversity; advance provider if exhausted<br/>
    <b>State in:</b> search_results, search_provider_index, search_attempt<br/>
    <b>State out:</b> search_sufficient, search_provider_index, search_attempt<br/>
    <b>Tools:</b> none (ignores mailto: results)"}

    N5["<b>5. market_analyst</b> · LLM, no tools<br/>
    <b>Objective:</b> write the cited Markdown report<br/>
    <b>Prompt data:</b> today, snippets_text, context_text<br/>(each result: query, URL, age label via _age_label, content)<br/>
    <b>State in:</b> snippets, search_results<br/>
    <b>State out:</b> report<br/>
    <b>Tools:</b> none"]

    N6["<b>6. market_data_search</b> · LLM + tools<br/>
    <b>Objective:</b> add verified price and technical evidence next to asset claims<br/>
    <b>Prompt data:</b> today, report_md<br/>
    <b>State in:</b> report<br/>
    <b>State out:</b> report (evidence spans inserted at anchors)<br/>
    <b>Tools:</b> get_market_data(symbol)<br/>(yfinance + stockstats, up to MARKET_DATA_MAX_TOOL_CALLS loops)<br/>
    <b>Fails open:</b> bad JSON / anchor / symbol leaves report unchanged"]

    N7["<b>7. report_verifier</b> · no LLM<br/>
    <b>Objective:</b> deterministic backstop on citations, dates and priced-in checks<br/>
    <b>State in:</b> report, search_results<br/>
    <b>State out:</b> report (repaired / flagged)<br/>
    <b>Tools:</b> none"]

    END_NODE([END: handler renders PDF via WeasyPrint<br/>and uploads md + pdf to reports bucket])

    START --> N0 --> N1 --> N2 --> N3 --> N4
    N4 -- "insufficient and attempt less than 2<br/>(retry wide pass, or next provider)" --> N3
    N4 -- "sufficient, or attempt at least 2" --> N5
    N5 --> N6 --> N7 --> END_NODE

    classDef llm fill:#dbeafe,stroke:#2563eb,color:#111;
    classDef det fill:#f3f4f6,stroke:#6b7280,color:#111;
    classDef edge fill:#fff,stroke:#111,color:#111;
    class N2,N5,N6 llm;
    class N0,N1,N3,N4,N7 det;
    class START,END_NODE edge;
```

### Central state (`lambda/state.py`)

```mermaid
flowchart LR
    subgraph State["AgentState"]
        snippets["snippets<br/>list of str"]
        queries["queries<br/>list of str"]
        search_results["search_results<br/>list of dict<br/>query, content, url, published_date"]
        search_attempt["search_attempt<br/>int"]
        search_provider_index["search_provider_index<br/>int"]
        search_sufficient["search_sufficient<br/>bool"]
        report["report<br/>str (Markdown)"]
    end

    N0["0 email_newsletter_search"] -- writes --> search_results
    N1["1 news_snippet_getter"] -- writes --> snippets
    snippets -- read by --> N2["2 query_generation"]
    N2 -- writes --> queries
    queries -- read by --> N3["3 web_search"]
    N3 -- writes --> search_results
    N3 -- writes --> search_attempt
    search_results -- read by --> N4["4 search_evaluator"]
    N4 -- writes --> search_sufficient
    N4 -- writes --> search_provider_index
    N4 -- writes --> search_attempt
    snippets -- read by --> N5["5 market_analyst"]
    search_results -- read by --> N5
    N5 -- writes --> report
    report -- read and rewritten by --> N6["6 market_data_search"]
    report -- read and rewritten by --> N7["7 report_verifier"]
    search_results -- read by --> N7
```

### State item types and schemas

`AgentState` is a `TypedDict` (`lambda/state.py`) used as the `StateGraph` schema. A `TypedDict` is a static type hint only. LangGraph does not validate values at runtime, and the state has no reducers (no `Annotated[..., operator.add]`), so every key is last-write-wins. Nodes return `{**state, ...}`, so appends such as `search_results` are done manually by each node. No node is restricted to the keys it declares.

| Key | Declared type | Fixed schema? | Actual shape and who enforces it |
|---|---|---|---|
| `snippets` | `list[str]` | **No.** Free text. | Each item is the raw text of one S3 object, stripped. User-submitted snippets are validated only by the server's `SnippetRequest` (non-empty, max length). Newsletter snippets follow a de facto template from `_format_snippet`: `Source:`, `Subject:`, `Received:` lines, a blank line, then the body. Downstream nodes only join them with `---`, so neither format is parsed. |
| `queries` | `list[str]` | **No.** | The LLM's output split on lines, with leading numbering stripped. The prompt asks for 6-12 queries, but the count and content are not checked. |
| `search_results` | `list[dict]` | **De facto only**, via convention. The dict keys are not declared anywhere. | Web results are built in `web_search.py` and newsletter results in `email_newsletter_search._as_search_result`. Both produce `{query: str, content: str, url: str, published_date: str}`. See the field notes below. |
| `search_attempt` | `int` | Yes (a primitive). | 0 or more. Incremented by `web_search`, reset to 0 by `search_evaluator` on provider fall-through. Routing treats `>= 2` as exhausted. |
| `search_provider_index` | `int` | Yes (a primitive). | Index into the provider list. Only `search_evaluator` advances it. It is never bounds-checked beyond that node's `provider_index + 1 < len(providers)` guard. |
| `search_sufficient` | `bool` | Yes (a primitive). | Set by `search_evaluator` and read only by `route_after_evaluation`. |
| `report` | `str` | **No.** Markdown prose. | Structure is requested by the `market_analyst.j2` prompt (seven numbered `##` sections). It is not validated. `market_data_search` appends raw HTML `<span class="ina-market-data">` blocks into it. `report_verifier` regex-matches headings and fails open if they are missing. |

**`search_results` field notes**

- `url`: an `http(s)` URL for web results, or `mailto:<sender>#<ISO timestamp>` for newsletters. The `mailto:` prefix is how nodes tell the two apart (`_is_newsletter_result`).
- `published_date`: a string in one of three forms: ISO 8601 (web results with a date, and newsletters), RFC 2822 (some providers), or an empty string when unknown. `parse_source_date` handles all three. Unknown dates are kept and flagged, not dropped.
- `content`: free text, either a provider's article extract or the newsletter body.
- `query`: the originating search query, or `Email newsletter: <subject>` for newsletters.
- Results are read with a mix of `r["url"]`, `r["query"]`, `r["content"]` (assumed present) and `r.get("published_date", "")`, so a missing key would raise a `KeyError` in some places and be tolerated in others.

**Schemas that do exist at the edges.** There are typed objects around the graph, but none of them are the state itself:

- `EmailMessage` dataclass (`sender`, `subject`, `received_at`, `body`) in `email_providers/base.py`.
- `SnippetRequest` Pydantic model on the server's `POST /snippets`.
- `WebSearchProvider.search` contract, documented as `{"results": [{url, content, published_date}]}` or `{"error": Exception}`, in `search_providers/base.py` (docstring only, not enforced).
- The `{anchor, symbol, commentary}` JSON the Market Data Search LLM must return, filtered by `_extract_insertions` (checks the keys exist, not their types).

### Shared context for all LLM nodes

- **System prompt** (`lambda/prompts/system.j2`): rendered once with no variables. It sets the team role, the asset classes in scope, and a "fully automated, never ask for clarification" instruction. It is the `SystemMessage` for every LLM call.
- **Date**: `today_utc()` in `lambda/helpers.py`, which respects `INA_DATETIME_OVERRIDE`. Nodes read it directly rather than from state.
- **LLM instances**: `main_llm` for Query Generation and Market Analyst; `tool_llm` for Market Data Search (the same object as `main_llm` on OpenRouter).
- **Memory**: none across nodes or runs. Each call is a fresh system + human message pair. The only accumulated conversation is Market Data Search's tool loop, within that node.
