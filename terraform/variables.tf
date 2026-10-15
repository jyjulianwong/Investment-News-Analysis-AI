variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-west-2"
}

variable "aws_account_id" {
  description = "AWS account ID to deploy into — Terraform will refuse to apply if the active credentials resolve to a different account"
  type        = string
}

variable "project" {
  description = "Short project identifier used to namespace resource names"
  type        = string
  default     = "jyjulianwong-ina"
}

# ---------------------------------------------------------------------------
# Secrets — injected via GitHub Actions secrets, never stored in state plaintext
# ---------------------------------------------------------------------------

variable "openrouter_api_key" {
  description = "OpenRouter API key"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  type        = string
  sensitive   = true
}

variable "tavily_api_key" {
  description = "Tavily Search API key"
  type        = string
  sensitive   = true
}

variable "email_imap_username" {
  description = "Gmail address the Lambda logs into via IMAP to fetch newsletter snippets"
  type        = string
  sensitive   = true
}

variable "email_imap_app_password" {
  description = "Gmail App Password for email_imap_username"
  type        = string
  sensitive   = true
}

variable "client_github_pages_origin" {
  description = "The GitHub Pages origin allowed for CORS on the Render API, e.g. https://jyjulianwong.github.io"
  type        = string
}

# ---------------------------------------------------------------------------
# LLM vendor config — injected via GitHub Actions variables (non-sensitive)
# ---------------------------------------------------------------------------

variable "llm_provider" {
  description = "Which LLM vendor the Lambda's ChatOpenAI call site uses: \"openai\" or \"openrouter\""
  type        = string
  default     = "openrouter"
}

variable "openrouter_model" {
  description = "OpenRouter model id used when llm_provider is \"openrouter\""
  type        = string
  default     = "google/gemini-3-flash-preview"
}

variable "openai_model" {
  description = "OpenAI model id used when llm_provider is \"openai\""
  type        = string
  default     = "gpt-6-luna"
}

variable "openai_reasoning_effort" {
  description = "Reasoning effort for OpenAI reasoning models (\"minimal\", \"low\", \"medium\", \"high\") when llm_provider is \"openai\"; empty disables reasoning_effort entirely (plain chat models like gpt-6-luna don't support it)"
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Email newsletter search config
# ---------------------------------------------------------------------------

variable "email_provider" {
  description = "EmailProvider adapter name registered in lambda/email_providers/factory.py"
  type        = string
  default     = "imap"
}

variable "email_imap_host" {
  description = "IMAP host for the email provider"
  type        = string
  default     = "imap.gmail.com"
}

variable "email_imap_port" {
  description = "IMAP port for the email provider"
  type        = string
  default     = "993"
}

variable "email_imap_folder" {
  description = "IMAP folder/Gmail label to scope newsletter search to"
  type        = string
  default     = "Newsletters"
}

variable "email_newsletter_senders" {
  description = "Comma-separated sender addresses to fetch newsletters from, each optionally suffixed with :count (e.g. foo@bar.com:3) to override how many of that sender's latest messages to pull"
  type        = string
  default     = "brewmarkets@morningbrew.com,FT@news-alerts.ft.com:3,noreply@newsletter.thetimes.com:2,noreply@newsletter.thetimes.co.uk:2"
}

variable "email_newsletter_default_fetch_count" {
  description = "Default message count for senders in email_newsletter_senders without an explicit :count suffix"
  type        = string
  default     = "1"
}

# ---------------------------------------------------------------------------
# Web search config
# ---------------------------------------------------------------------------

variable "web_search_providers" {
  description = "Comma-separated, priority-ordered WebSearchProvider adapter names registered in lambda/search_providers/factory.py"
  type        = string
  default     = "tavily,ddgs"
}

variable "tavily_max_results" {
  description = "Max results Tavily returns per query"
  type        = string
  default     = "10"
}

variable "tavily_search_depth" {
  description = "Tavily search depth (\"basic\" or \"advanced\")"
  type        = string
  default     = "advanced"
}

variable "tavily_topic" {
  description = "Tavily search topic — \"news\" is the only topic that reliably attaches published_date metadata"
  type        = string
  default     = "news"
}

variable "tavily_include_domains" {
  description = "Comma-separated curated domain allowlist applied on Tavily's narrow (first) search pass"
  type        = string
  default     = "reuters.com,apnews.com,cnbc.com,marketwatch.com,investing.com,tradingeconomics.com,federalreserve.gov,bis.org,imf.org,finance.yahoo.com,theguardian.com"
}

variable "tavily_search_workers" {
  description = "Thread pool size used to fetch all queries in parallel per search provider pass"
  type        = string
  default     = "5"
}

variable "ddgs_max_results" {
  description = "Max results the DDGS (DuckDuckGo) fallback provider returns per query"
  type        = string
  default     = "10"
}

# ---------------------------------------------------------------------------
# Search evaluator config
# ---------------------------------------------------------------------------

variable "evaluator_min_results" {
  description = "Minimum accumulated search result count before Search Evaluator considers results sufficient"
  type        = string
  default     = "5"
}

variable "evaluator_min_domains" {
  description = "Minimum distinct source domains before Search Evaluator considers results sufficiently diverse"
  type        = string
  default     = "3"
}

# ---------------------------------------------------------------------------
# Market data search config
# ---------------------------------------------------------------------------

variable "market_data_max_tool_calls" {
  description = "Max tool-calling loop iterations in the Market Data Search node — bounds LLM round-trips against the Lambda's 15-minute timeout"
  type        = string
  default     = "8"
}
