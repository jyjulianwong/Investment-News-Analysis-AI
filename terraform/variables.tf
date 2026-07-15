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

variable "openrouter_api_key" {
  description = "OpenRouter API key — injected via GitHub Actions secret, never stored in state plaintext"
  type        = string
  sensitive   = true
}

variable "tavily_api_key" {
  description = "Tavily Search API key — injected via GitHub Actions secret"
  type        = string
  sensitive   = true
}

variable "client_github_pages_origin" {
  description = "The GitHub Pages origin allowed for CORS on the Render API, e.g. https://jyjulianwong.github.io"
  type        = string
}
