# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

GitHub Pages static site (`client/`) → Render-hosted FastAPI server (`server/`) → S3 sources bucket → EventBridge trigger at 10:00 UTC, Monday–Friday → Lambda container (`lambda/`) → S3 reports bucket (private) → Render server (lists reports, presigns downloads, Obsidian-gates reports older than 3 days) → GitHub Pages Reports page.

The Lambda runs a LangGraph pipeline (`_build_graph` in `lambda/agent.py`) with an optimizer-evaluator search loop, a second optimizer-evaluator loop over the drafted report, and a final deterministic validation pass. Graph state is a Pydantic model, `AgentState` (`lambda/state.py`) — not a `TypedDict` — so LangGraph validates/coerces it on every node transition, catching a malformed update at the point it's produced rather than letting it propagate silently; `search_results` is typed `list[SearchResult]` (also `lambda/state.py`), enforcing the `{query, content, url, published_date}` shape on every web-search and newsletter result the same way. Snippet collection is entirely inside the graph — the Lambda's `initial_state["snippets"]` always starts empty; the first two nodes populate it from S3, which is the single source of truth for both user-submitted and newsletter snippets:
0. **Email Newsletter Search** — logs into a mailbox via an `EmailProvider` adapter (`lambda/email_providers/`; `ImapEmailProvider` today, IMAP + a Gmail App Password) and, having selected the IMAP folder named by `EMAIL_IMAP_FOLDER` (default `Newsletters` in Terraform/production — the `run_local_lambda.sh` script falls back to `INBOX` if the env var isn't set locally; a Gmail label maps 1:1 to an IMAP folder of the same name — set this to scope the search to a folder/label containing only the configured senders' mail instead of the whole inbox), fetches, per address in `EMAIL_NEWSLETTER_SENDERS` (default `brewmarkets@morningbrew.com,FT@news-alerts.ft.com:3,noreply@newsletter.thetimes.com:2,noreply@newsletter.thetimes.co.uk:2`), that sender's most recent messages — up to a per-sender count parsed from an optional `address:count` suffix (`_parse_newsletter_senders` in `lambda/agent.py`; bare addresses fall back to `EMAIL_NEWSLETTER_DEFAULT_FETCH_COUNT`, default `1`), so a high-volume sender like FT's news alerts can pull more than one message without a code change. Any fetched message older than `STALE_AGE_DAYS` (7, `lambda/helpers.py`) relative to the pipeline's "today" is skipped and logged rather than uploaded — a sign of a misconfigured sender or lapsed subscription rather than real news. Each remaining message does double duty: (1) formatted as a snippet — sender, subject, UTC received datetime, body — and uploaded to `s3://<sources-bucket>/sources/YYYY-MM-DD/<content-hash>.txt`, the same key format and content type the Render server (`server/main.py`) uses for user-submitted snippets, so News Snippet Getter picks it up like any other; (2) also appended directly to `state.search_results` as a `SearchResult`, shaped exactly like a Web Search result (`query`/`content`/`url`/`published_date`) with `mailto:<sender>#<received-timestamp>` standing in for the URL — the fragment keeps multiple messages from the same sender distinctly citable and correctly dated (Report Validator's `published_by_url` lookup is keyed by URL) while `_domain` in `nodes/search_evaluator.py` still folds them into a single source for diversity counting — this makes the newsletter a legitimate, citable WEB CONTEXT source for Market Analyst (e.g. `[brewmarkets@morningbrew.com, Dec 31, 2025](mailto:brewmarkets@morningbrew.com#2025-12-31T21:20:21+00:00)`), subject to the exact same citation and staleness checks as any web-searched source. Adding an email service means implementing `EmailProvider` (`lambda/email_providers/base.py`) and registering it in `lambda/email_providers/factory.py`; adding a sender (and its fetch count) means editing `EMAIL_NEWSLETTER_SENDERS`, no code change needed
1. **News Snippet Getter** — lists and reads every object under `sources/YYYY-MM-DD/` in the sources bucket (user-submitted snippets plus whatever Email Newsletter Search just uploaded) and sets `state.snippets` from that listing
2. **Data Fetcher** (`lambda/nodes/data_fetcher.py`) — fetches non-news data sets into graph state before Query Generation. Today that is only Market Regime Detector (MRD): a plain `GET {MRD_API_BASE_URL}/v1/models/{MRD_MODEL_NAME}/predictions/latest` (defaults: MRD's public API Gateway URL, `allweather-hmm2naive-us`; 15s timeout), validated into `MarketRegimeDetectorData` (`lambda/mrd_schema.py`, a full mirror of MRD's `LatestPredictionResponse` including the `latest.data` raw-series and `latest.features` transformed-feature diagnostics) and written to `state.market_regime_detector_data`. It fails open (`None`) if MRD is unreachable or its response no longer matches the schema. Nothing downstream reads the field yet. MRD only serves its *latest* prediction, so under an `INA_DATETIME_OVERRIDE` backtest this data is not point-in-time. When MRD's output contract changes, update `mrd_schema.py` to match. A new data source gets its own state field fetched in this same node
3. **Query Generation** — LLM reads the snippets from step 1 plus whatever `state.search_results` already holds at this point (newsletter entries from step 0 — this node runs before Web Search, so that's all there is so far) and produces 6–12 web search queries aimed at what isn't covered yet: gaps the snippets/newsletters raise but don't answer, or a theme worth dedicated, deeper coverage. The prompt (`lambda/prompts/query_generation.j2`) explicitly lists those existing results under an ALREADY KNOWN section and instructs the model not to write a query that would just resurface information already sitting there — either drop it or sharpen it into a genuine follow-up angle. The `ChatOpenAI` construction site (`_build_graph` in `lambda/agent.py`) builds two LLM instances per warm start — `main_llm` (Query Generation, Market Analyst, Report Evaluator) and `tool_llm` (Market Data Search's tool-calling loop) — and switches vendor based on `LLM_PROVIDER` (`openai` or `openrouter`, default `openrouter`): `openrouter` points both at `https://openrouter.ai/api/v1` with the OpenRouter key and `OPENROUTER_MODEL` (default `google/gemini-3-flash-preview`), with `tool_llm` just aliased to `main_llm`; `openai` points both at the default OpenAI endpoint with the OpenAI key and `OPENAI_MODEL` (default `gpt-6-luna`, a plain chat model that rejects any non-default `temperature`, with or without `reasoning_effort` — only `1`, the API's own default, is accepted — so `temperature=1` is always passed explicitly for it rather than left out, since the pinned `langchain-openai` version would otherwise default to `0.7`). If `OPENAI_REASONING_EFFORT` is also set (`minimal`/`low`/`medium`/`high`, for reasoning-capable OpenAI models), `main_llm` passes it as `reasoning_effort`; `tool_llm` always passes the literal string `"none"` instead (never Python `None`, which would just omit the param and leave the model's own default reasoning active) because Chat Completions rejects function tools combined with a non-`"none"` `reasoning_effort`, and a reasoning-capable model like `gpt-6-luna` reasons by default even when the param is omitted entirely. When `OPENAI_REASONING_EFFORT` is unset, both instances explicitly pass `reasoning_effort="none"`. Adding a vendor means extending that if/else, no graph changes
4. **Web Search** — runs against a priority-ordered list of `WebSearchProvider` adapters (`lambda/search_providers/`; `WEB_SEARCH_PROVIDERS`, default `tavily,ddgs`), one at a time: for the current provider, all queries are fetched in parallel via `ThreadPoolExecutor`, first pass narrow (`time_range="day"`, `max_age_days=1`; Tavily also applies its curated `TAVILY_INCLUDE_DOMAINS` filter on this pass), second pass wide (`time_range="week"`, `max_age_days=7`, no domain filter). Results dated more than 1 day in the future relative to `today` are always dropped (guards against a provider returning post-cutoff articles when `INA_DATETIME_OVERRIDE` is used for backtesting). `TavilySearchProvider` (`langchain_tavily.TavilySearch`, `topic="news"` by default — the only Tavily topic that reliably attaches `published_date`) is the default primary provider; `DDGSSearchProvider` (the free, keyless `ddgs` package) is the default fallback — unofficial and rate-limit-prone, so it's meant to supplement Tavily, not replace it. Adding a new provider means implementing `WebSearchProvider` (`lambda/search_providers/base.py`), registering it in `lambda/search_providers/factory.py`, and adding its construction kwargs in `agent.py`'s `provider_kwargs`; no changes to `nodes/web_search.py` or the graph
5. **Search Evaluator** — pure Python; checks whether the accumulated search results meet quantity (`SEARCH_EVALUATOR_MIN_RESULTS`, default `5`) and diversity (`SEARCH_EVALUATOR_MIN_DOMAINS`, default `3`) thresholds; if not met, retries the current provider's wide pass if it hasn't run yet, then falls through to the next provider in `WEB_SEARCH_PROVIDERS` (resetting to its narrow pass) once the current one has had both attempts, until providers are exhausted
6. **Market Analyst** — LLM synthesises snippets + search results into a Markdown report, with each source's recency called out (`_age_label`; sources older than `STALE_AGE_DAYS` (7, `lambda/helpers.py`) are explicitly flagged STALE so the model can't present them as current). On a retry from Report Evaluator (step 8) below, `state.report_feedback` is non-empty and the prompt (`lambda/prompts/market_analyst.j2`) prepends a REVISION REQUIRED block quoting that feedback, instructing the model to write a fresh draft from scratch that resolves it rather than patching the old text
7. **Market Data Search** (`lambda/nodes/market_data_search.py`) — a tool-calling LLM pass (`tool_llm`) that re-reads the Market Analyst's report and, for up to `MARKET_DATA_MAX_TOOL_CALLS` round-trips (default `8`), calls a `get_market_data(symbol)` tool for any asset-price claim it wants to verify — the tool fetches a year of daily history from Yahoo Finance (`yfinance`) and computes SMAs/RSI/MACD/Bollinger Bands (`stockstats`), with the overbought/oversold, golden/death-cross, and Bollinger-extreme comparisons classified deterministically in code (`_classify_trend`), not left for the model to eyeball. The model's final output is a JSON array of `{anchor, symbol, commentary}` objects; `_apply_insertions` locates each `anchor` verbatim (or via a markdown-bold/whitespace-tolerant fallback match, `_find_anchor_end`) in the report and appends a `<span class="ina-market-data">` evidence block immediately after it, composed of the deterministic price/technical facts plus the model's own interpretive `commentary` (`_compose_evidence`) — the model never supplies the price or indicator comparisons itself, only the narrative tying them to the report's claim. An anchor that can't be matched, a symbol with no fetched snapshot, or unparseable JSON all fail open, leaving the report untouched rather than raising. This node re-runs every time Market Analyst re-runs (see step 8's retry loop), each time against whatever the latest draft is
8. **Report Evaluator** (`lambda/nodes/report_evaluator.py`) — an LLM pass (`main_llm`) whose only job is to scan the drafted report for *internal contradictions* — e.g. a bullish Opportunities pick resting on an assumption a different section's Key Themes explicitly contradicts — not to re-derive the analysis or re-check citations/sources (those are Report Validator's job, step 9). It responds with `VERDICT: PASS` or `VERDICT: FAIL` plus a `FEEDBACK:` paragraph (prompt: `lambda/prompts/report_evaluator.j2`); on `FAIL`, `state.report_approved` is set `False`, `state.report_feedback` is set to that paragraph, `state.report_revision` is incremented, and the graph routes back to Market Analyst (step 6) for a fresh draft informed by the feedback. `REPORT_EVALUATOR_MAX_RETRIES` (default `2`) bounds the loop — once hit, a `FAIL` is logged but the graph proceeds anyway rather than looping forever; an unparseable LLM response fails open as `PASS` for the same reason. On `PASS` (or once retries are exhausted), `state.report_approved` is set `True` and the graph proceeds to Report Validator
9. **Report Validator** (`lambda/nodes/report_validator.py`, renamed from `report_verifier.py`) — pure Python, deterministic backstop over the LLM's output before PDF rendering, run only once Report Evaluator has signed off: strips/repairs any citation link whose URL wasn't actually returned by search (`_verify_citations`), corrects or adds citation dates and STALE flags from real source metadata regardless of what the model wrote (`_repair_citation_dates`), and flags any "Opportunities"/"If I had 6 months..." bullet that makes a priced-in claim without citing a concrete number (`_flag_unquantified_priced_in_checks`)

The validated Markdown gets the mandated regulatory disclaimer appended (`DISCLAIMER_TEXT` in `lambda/helpers.py`, added in `agent.handler` *after* Report Validator so no model output or rewrite can alter it), is converted to PDF via WeasyPrint, and both are uploaded to S3.

Credentials (OpenRouter, OpenAI, and Tavily API keys, the Gmail IMAP username and App Password) are stored in SSM Parameter Store as SecureStrings and fetched at Lambda runtime — never stored in environment variables or code. Both the OpenRouter and OpenAI keys are always fetched regardless of the active `LLM_PROVIDER`, matching the existing eager-fetch pattern for the other credentials.

## Versioning

The Lambda is versioned with CalVer (`YYYY.MM.DD.MINOR`), tracked in `lambda/pyproject.toml`'s `version` field and embedded in every generated report's footer via the `INA_VERSION` env var (baked in at Docker build time via `--build-arg`, see `lambda/Dockerfile`).

`scripts/bump_calver.py` runs once per push to `main` (the `version_bump` job in `deploy-terraform.yml`, gated ahead of `deploy`): it computes the next version, rewrites `lambda/pyproject.toml`, commits directly to `main`, and creates/pushes a `vYYYY.MM.DD.MINOR` git tag. The `deploy` job then checks out that tag before building/pushing the image, so the deployed Lambda always matches a tagged commit.

## Repository layout

```
client/          # Static site (index.html = snippet submit, reports.html = report viewer)
server/          # Render-hosted FastAPI app (POST /snippets, GET /health)
lambda/          # Lambda container image (agent.py + helpers.py + state.py + Dockerfile + nodes/ + email_providers/ + search_providers/ + prompts/*.j2 + pyproject.toml)
scripts/         # Integration test helpers + scripts/bump_calver.py (CalVer bump, used by CI)
terraform/       # AWS infrastructure (S3, Lambda, ECR, IAM, EventBridge, SSM)
terraform/bootstrap/  # One-time state infra (S3 backend + DynamoDB lock table)
.github/workflows/    # deploy-terraform.yml (version bump + Terraform + ECR + Lambda), deploy-client.yml (gh-pages)
```

## Local development

**Integration testing (three separate terminals):**
```bash
# Terminal 1 — client (opens browser at http://localhost:3000)
./scripts/run_local_client.sh

# Terminal 2 — server (port 8000, date frozen to 2000-01-01, connects to real S3)
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... ./scripts/run_local_server.sh

# Terminal 3 — Lambda handler end-to-end (real AWS, date frozen — see script for current override)
./scripts/run_local_lambda.sh
```
All three scripts derive the S3 bucket names automatically via `aws sts get-caller-identity`.
Set `INA_DATETIME_OVERRIDE=YYYY-MM-DD` to override the frozen date (also drives the future-date guard in Web Search — results dated after the override are dropped, not just stale ones — and the `before` cutoff `EmailProvider.fetch_latest_from` uses, so Email Newsletter Search returns the messages that were actually latest as of the simulated date, not the mailbox's true latest).

**Build and test the Lambda Docker image (manual / Lambda RIE):**
```bash
cd lambda
docker buildx build --platform linux/amd64 --provenance=false --load -t ina-lambda-test .
docker run -p 9000:8080 \
  -e AWS_REGION_NAME=eu-west-2 \
  -e AWS_S3_SOURCES_BUCKET_NAME={account_id}-jyjulianwong-ina-news-sources \
  -e AWS_S3_REPORTS_BUCKET_NAME={account_id}-jyjulianwong-ina-news-reports \
  -e SSM_OPENROUTER_PARAM=/jyjulianwong-ina/openrouter_api_key \
  -e SSM_OPENAI_PARAM=/jyjulianwong-ina/openai_api_key \
  -e LLM_PROVIDER=openrouter \
  -e OPENAI_REASONING_EFFORT= \
  -e SSM_TAVILY_PARAM=/jyjulianwong-ina/tavily_api_key \
  -e SSM_EMAIL_IMAP_USERNAME_PARAM=/jyjulianwong-ina/email_imap_username \
  -e SSM_EMAIL_IMAP_PASSWORD_PARAM=/jyjulianwong-ina/email_imap_app_password \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  ina-lambda-test
# Then in a second terminal:
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{}'
```

**Manually invoke the deployed Lambda:**
```bash
aws lambda invoke --function-name jyjulianwong-ina-agent --region eu-west-2 response.json && cat response.json
```

**Tail Lambda logs:**
```bash
aws logs tail /aws/lambda/jyjulianwong-ina-agent --follow --region eu-west-2
```

**Lint / format:**
Ruff config lives in `lambda/pyproject.toml` (`select = ["E", "W", "F", "I", "B", "UP", "C4", "SIM"]`, `E501` ignored). Enforced via pre-commit (`.pre-commit-config.yaml`: `uv-lock`, `uv-sort`, `ruff --fix`, `ruff-format`, plus a Jupyter output-clearing hook).
```bash
pre-commit run --all-files
# or directly:
cd lambda && uv run ruff check --fix . && uv run ruff format .
```
There is no automated test suite in this repo — verification is via the integration scripts above.

## Infrastructure

Region is `eu-west-2`. S3 buckets are globally scoped and named `{account_id}-jyjulianwong-ina-news-sources` and `{account_id}-jyjulianwong-ina-news-reports` (Terraform local: `global_prefix = "${var.aws_account_id}-${var.project}"`). All other resources (Lambda, ECR, SSM, IAM, EventBridge) are prefixed `jyjulianwong-ina-`. Terraform state lives in S3 bucket `{account_id}-jyjulianwong-ina-terraform-state` with DynamoDB lock table `jyjulianwong-ina-terraform-lock` (created once via `terraform/bootstrap/`).

The Lambda `image_uri` is under `lifecycle { ignore_changes }` — CI/CD updates it via `aws lambda update-function-code` after each ECR push, so Terraform will not revert it.

The reports S3 bucket is **private** (all public access blocked). The Render server's IAM user can `ListBucket` and `GetObject` on it; `GET /reports` lists dates (older than 3 days marked `locked` unless a valid Obsidian token is sent) and `GET /reports/{day}/{pdf|md}` returns a 5-minute presigned URL, 401 for a >3-day-old report without a valid token (`OPEN_REPORT_MAX_AGE_DAYS`, default 3). The bucket's CORS config is retained for local testing. The sources bucket is private.

The EventBridge schedule is `cron(0 10 ? * MON-FRI *)` — weekdays only.

## CI/CD

- **`deploy-terraform.yml`**: On PR touching `terraform/` or `lambda/` → `terraform plan` posted as PR comment. On merge to `main` touching those paths → `version_bump` job bumps CalVer and pushes a tag, then `deploy` checks out that tag, runs `terraform apply`, builds and pushes the Docker image to ECR (tagged with both the commit SHA and `latest`, `INA_VERSION` baked in), then `aws lambda update-function-code`.
- **`deploy-client.yml`**: On merge to `main` touching `client/` → publishes `client/` to the `gh-pages` branch.

## Key design constraints

- The Lambda has a 15-minute timeout (`timeout = 900`) and 1024 MB memory to accommodate LLM calls.
- The compiled LangGraph graph is cached in a module-level `_graph` variable for Lambda warm-start reuse.
- CORS on the server allows only the exact `CLIENT_GITHUB_PAGES_ORIGIN` origin (no wildcards).
- `POST /snippets` is gated behind the [Obsidian](https://github.com/jyjulianwong/Obsidian-Server) auth service so only the owner's pre-authorized devices can submit snippets — `server/obsidian_auth.py`'s `require_device` FastAPI dependency verifies a `Bearer` JWT against Obsidian's JWKS (`OBSIDIAN_ISSUER`, default `https://auth.jyjwong.com`) with no per-request call back to Obsidian. The client (`client/index.html`) fetches that JWT by calling Obsidian's `POST /auth/token` with the browser's installed mTLS client certificate before submitting — a device without a provisioned Obsidian certificate simply cannot get a token, which is what enforces "my devices only." `GET /health`, the Reports page, and reports from the last 3 days remain public (`GET /reports*` uses `optional_device`: no token = anonymous, bad token = 401). Obsidian's own `allowed_origins` Terraform var must include the client's origin (GitHub Pages + `http://localhost:3000`) for the browser fetch to Obsidian to succeed.
- WeasyPrint requires `pango`/`cairo` system libraries — these are installed in the Dockerfile. `run_local_lambda.sh` builds and runs the Docker image so this is handled automatically; running `agent.py` outside Docker requires `brew install pango`.
- The client hardcodes `API_BASE` (Render URL) in `index.html` and `S3_BASE` (reports bucket URL) in `reports.html` — these must be updated manually after initial deployment.
- The regulatory disclaimer (`DISCLAIMER_TEXT` in `lambda/helpers.py`) has a mandated exact wording and is duplicated verbatim in the `<footer class="site-disclaimer">` of both `client/index.html` and `client/reports.html` — change all three together or not at all. The report's last two sections are deliberately framed as "Bullish/Bearish Case Studies (Educational Only)", with wording rules in `market_analyst.j2` forbidding directives and return promises (the author is not FCA-authorised to give advice); `report_validator.py`'s heading regexes (`Bullish Case Studies.*`, `Bearish Case Studies.*`) must stay in sync with those headings.
- Citation and date integrity is enforced deterministically, not just by prompting: `_verify_citations`, `_repair_citation_dates`, and `_flag_unquantified_priced_in_checks` in `lambda/nodes/report_validator.py` re-check and, where needed, rewrite the LLM's report before it's rendered to PDF. When touching prompts or the report format, keep the heading text these regexes match on (`Opportunities`, `Bullish Case Studies...`, `Bearish Case Studies...`) in sync with `lambda/prompts/market_analyst.j2`.
- Report-level self-consistency is a separate, earlier check from citation/date integrity: Report Evaluator (`lambda/nodes/report_evaluator.py`) runs right after Market Data Search and before Report Validator, looping back to Market Analyst on a major contradiction (bounded by `REPORT_EVALUATOR_MAX_RETRIES`). Report Validator never sees an unapproved draft.
- `AgentState` (`lambda/state.py`) is a Pydantic model, not a `TypedDict` — LangGraph validates/coerces the accumulated state against it before every node runs. `search_results` is typed `list[SearchResult]` (also in `lambda/state.py`), so any node or provider adapter producing a result must match the `{query, content, url, published_date}` schema; a node can still return a plain `dict` (including nested dicts for `SearchResult` list items) as a partial state update — LangGraph validates it into the Pydantic model on the next node's entry.
- Asset-price claims get a second, independent deterministic backstop: Market Data Search (`lambda/nodes/market_data_search.py`) never lets the model state a price/SMA/RSI/MACD/Bollinger comparison itself — `_fetch_snapshot`/`_classify_trend` compute it from Yahoo Finance data and the model only supplies prose commentary around the inserted fact.
- Email access is behind the `EmailProvider` interface (`lambda/email_providers/base.py`) so nodes and graph code never depend on a concrete email service. `lambda/email_providers/factory.build_email_provider` selects the adapter by the `EMAIL_PROVIDER` env var (default `imap`); `lambda/nodes/email_newsletter_search.py` never imports `ImapEmailProvider` directly.
- Web search access is behind the `WebSearchProvider` interface (`lambda/search_providers/base.py`), same pattern as `EmailProvider`. `lambda/search_providers/factory.build_web_search_provider` selects adapters by the `WEB_SEARCH_PROVIDERS` env var (comma-separated priority list, default `tavily,ddgs`); `lambda/nodes/web_search.py` only depends on the interface. Each provider gets 2 attempts (narrow then wide) before `search_evaluator_node` advances `state["search_provider_index"]` to the next one in priority order — see step 4/5 above.
- S3 is the single source of truth for snippets, not the graph's in-memory state: `email_newsletter_search` only writes to `s3://<sources-bucket>/sources/YYYY-MM-DD/`, and `news_snippet_getter` is the only node that reads snippets into `state["snippets"]`. This is why the Lambda's IAM role needs `s3:PutObject` on the sources bucket (`ReadWriteSourcesBucket` in `terraform/main.tf`), not just read access.
