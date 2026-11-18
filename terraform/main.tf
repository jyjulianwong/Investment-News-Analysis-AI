terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
}

locals {
  global_prefix = "${var.aws_account_id}-${var.project}" # S3 buckets (globally scoped)
  scoped_prefix = var.project                            # all other resources (account-scoped)
}

# ---------------------------------------------------------------------------
# S3 — Sources bucket (private, the server Lambda writes here)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "sources" {
  bucket = "${local.global_prefix}-news-sources"
}

resource "aws_s3_bucket_public_access_block" "sources" {
  bucket                  = aws_s3_bucket.sources.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "sources" {
  bucket = aws_s3_bucket.sources.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ---------------------------------------------------------------------------
# S3 — Reports bucket (public read, UI lists and links to reports here)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "reports" {
  bucket = "${local.global_prefix}-news-reports"
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_policy" "reports_public_read" {
  bucket = aws_s3_bucket.reports.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.reports.arn,
          "${aws_s3_bucket.reports.arn}/*"
        ]
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.reports]
}

resource "aws_s3_bucket_cors_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = [var.client_github_pages_origin, "http://localhost:3000"]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

# ---------------------------------------------------------------------------
# SSM — API keys (values injected manually / via CI, Terraform manages slots)
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "openrouter_api_key" {
  name        = "/${local.scoped_prefix}/openrouter_api_key"
  type        = "SecureString"
  value       = var.openrouter_api_key
  description = "OpenRouter API key for the Lambda LangGraph agent"
}

resource "aws_ssm_parameter" "openai_api_key" {
  name        = "/${local.scoped_prefix}/openai_api_key"
  type        = "SecureString"
  value       = var.openai_api_key
  description = "OpenAI API key for the Lambda LangGraph agent"
}

resource "aws_ssm_parameter" "tavily_api_key" {
  name        = "/${local.scoped_prefix}/tavily_api_key"
  type        = "SecureString"
  value       = var.tavily_api_key
  description = "Tavily Search API key for the Lambda web search step"
}

resource "aws_ssm_parameter" "email_imap_username" {
  name        = "/${local.scoped_prefix}/email_imap_username"
  type        = "SecureString"
  value       = var.email_imap_username
  description = "Gmail address for the Lambda email newsletter search step"
}

resource "aws_ssm_parameter" "email_imap_app_password" {
  name        = "/${local.scoped_prefix}/email_imap_app_password"
  type        = "SecureString"
  value       = var.email_imap_app_password
  description = "Gmail App Password for the Lambda email newsletter search step"
}

# ---------------------------------------------------------------------------
# ECR — Lambda container image repository
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "lambda" {
  name                 = "${local.scoped_prefix}-lambda-agent"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "lambda" {
  repository = aws_ecr_repository.lambda.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep last 5 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}

# ---------------------------------------------------------------------------
# IAM — Lambda execution role
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${local.scoped_prefix}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_permissions" {
  statement {
    sid    = "ReadWriteSourcesBucket"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.sources.arn,
      "${aws_s3_bucket.sources.arn}/*",
    ]
  }

  statement {
    sid    = "WriteReportsBucket"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.reports.arn}/*"]
  }

  statement {
    sid    = "ReadSSMParams"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
    ]
    resources = [
      aws_ssm_parameter.openrouter_api_key.arn,
      aws_ssm_parameter.openai_api_key.arn,
      aws_ssm_parameter.tavily_api_key.arn,
      aws_ssm_parameter.email_imap_username.arn,
      aws_ssm_parameter.email_imap_app_password.arn,
    ]
  }

  statement {
    sid    = "DecryptSSMParams"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "lambda_permissions" {
  name   = "${local.scoped_prefix}-lambda-permissions"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

# ---------------------------------------------------------------------------
# Lambda — container image function
# Placeholder image_uri; CI/CD updates this after each ECR push.
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "agent" {
  function_name = "${local.scoped_prefix}-agent"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.lambda.repository_url}:latest"
  timeout       = 900 # 15 minutes — LLM calls can be slow
  memory_size   = 1024

  environment {
    variables = {
      AWS_S3_SOURCES_BUCKET_NAME = aws_s3_bucket.sources.bucket
      AWS_S3_REPORTS_BUCKET_NAME = aws_s3_bucket.reports.bucket
      AWS_REGION_NAME            = var.aws_region

      SSM_OPENROUTER_PARAM          = aws_ssm_parameter.openrouter_api_key.name
      SSM_OPENAI_PARAM              = aws_ssm_parameter.openai_api_key.name
      SSM_TAVILY_PARAM              = aws_ssm_parameter.tavily_api_key.name
      SSM_EMAIL_IMAP_USERNAME_PARAM = aws_ssm_parameter.email_imap_username.name
      SSM_EMAIL_IMAP_PASSWORD_PARAM = aws_ssm_parameter.email_imap_app_password.name

      LLM_PROVIDER            = var.llm_provider
      OPENROUTER_MODEL        = var.openrouter_model
      OPENAI_MODEL            = var.openai_model
      OPENAI_REASONING_EFFORT = var.openai_reasoning_effort

      EMAIL_PROVIDER                       = var.email_provider
      EMAIL_IMAP_HOST                      = var.email_imap_host
      EMAIL_IMAP_PORT                      = var.email_imap_port
      EMAIL_IMAP_FOLDER                    = var.email_imap_folder
      EMAIL_NEWSLETTER_SENDERS             = var.email_newsletter_senders
      EMAIL_NEWSLETTER_DEFAULT_FETCH_COUNT = var.email_newsletter_default_fetch_count

      WEB_SEARCH_PROVIDERS   = var.web_search_providers
      TAVILY_MAX_RESULTS     = var.tavily_max_results
      TAVILY_SEARCH_DEPTH    = var.tavily_search_depth
      TAVILY_TOPIC           = var.tavily_topic
      TAVILY_INCLUDE_DOMAINS = var.tavily_include_domains
      TAVILY_SEARCH_WORKERS  = var.tavily_search_workers
      DDGS_MAX_RESULTS       = var.ddgs_max_results

      SEARCH_EVALUATOR_MIN_RESULTS = var.evaluator_min_results
      SEARCH_EVALUATOR_MIN_DOMAINS = var.evaluator_min_domains

      MARKET_DATA_MAX_TOOL_CALLS = var.market_data_max_tool_calls

      MRD_API_BASE_URL            = var.mrd_api_base_url
      MRD_MODEL_NAME              = var.mrd_model_name
      MRD_REQUEST_TIMEOUT_SECONDS = var.mrd_request_timeout_seconds
    }
  }

  lifecycle {
    # CI/CD updates the image; Terraform should not revert it on next apply
    ignore_changes = [image_uri]
  }
}

# ---------------------------------------------------------------------------
# EventBridge — daily trigger at 10:00 UTC
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "agent_daily_noon" {
  name                = "${local.scoped_prefix}-agent-daily-noon"
  description         = "Trigger the '${local.scoped_prefix}-agent' Lambda function at 10:00 UTC on weekdays"
  schedule_expression = "cron(0 10 ? * MON-FRI *)"
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.agent_daily_noon.name
  target_id = "${local.scoped_prefix}-lambda-agent"
  arn       = aws_lambda_function.agent.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.agent_daily_noon.arn
}

# ---------------------------------------------------------------------------
# Lambda — server (zip): the FastAPI snippet-ingest API (server/), wrapped
# with Mangum. Its execution role replaces the static access key it used to
# need while it ran on Render.
#
# Terraform's zip only contains server/'s own .py files, no dependencies —
# enough for the first `terraform apply` to succeed, same placeholder pattern
# as Obsidian-Server and Market-Regime-Detector. CI/CD packages the real
# dependency-inclusive zip (scripts/package_lambda.sh) and pushes it via
# `update-function-code`.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "server_lambda" {
  name               = "${local.scoped_prefix}-server-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "server_lambda_basic" {
  role       = aws_iam_role.server_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "server_lambda_permissions" {
  statement {
    sid    = "WriteSourcesBucket"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.sources.arn}/sources/*"]
  }
}

resource "aws_iam_role_policy" "server_lambda_permissions" {
  name   = "${local.scoped_prefix}-server-lambda-permissions"
  role   = aws_iam_role.server_lambda.id
  policy = data.aws_iam_policy_document.server_lambda_permissions.json
}

data "archive_file" "server_src" {
  type        = "zip"
  source_dir  = "${path.module}/../server"
  excludes    = ["build", ".venv", "__pycache__"]
  output_path = "${path.module}/.placeholder-server-lambda.zip"
}

resource "aws_lambda_function" "server" {
  function_name    = "${local.scoped_prefix}-server"
  role             = aws_iam_role.server_lambda.arn
  runtime          = "python3.12"
  handler          = "main.handler"
  filename         = data.archive_file.server_src.output_path
  source_code_hash = data.archive_file.server_src.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      AWS_S3_SOURCES_BUCKET_NAME = aws_s3_bucket.sources.bucket
      CLIENT_GITHUB_PAGES_ORIGIN = var.client_github_pages_origin
    }
  }

  lifecycle {
    # CI/CD updates the code with real dependencies bundled in; Terraform
    # should not revert it to the dependency-less placeholder on next apply.
    ignore_changes = [filename, source_code_hash]
  }
}

resource "aws_cloudwatch_log_group" "server" {
  name              = "/aws/lambda/${aws_lambda_function.server.function_name}"
  retention_in_days = 30
}

# ---------------------------------------------------------------------------
# API Gateway — public HTTP API in front of the server Lambda.
#
# Not a Lambda Function URL: Market-Regime-Detector found its AWS
# Organization's SCP denies anonymous lambda:InvokeFunctionUrl calls, while API
# Gateway invokes through lambda:InvokeFunction as the apigateway.amazonaws.com
# service principal, which that guardrail doesn't cover. Same AWS_PROXY HTTP
# API pattern as Market-Regime-Detector's server API. CORS is handled by the
# app's own CORSMiddleware, so the API has no cors_configuration of its own.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "server" {
  name          = "${local.scoped_prefix}-server-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "server" {
  api_id                 = aws_apigatewayv2_api.server.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.server.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "server_default" {
  api_id             = aws_apigatewayv2_api.server.id
  route_key          = "$default"
  target             = "integrations/${aws_apigatewayv2_integration.server.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_stage" "server_default" {
  api_id      = aws_apigatewayv2_api.server.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigateway_server" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.server.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.server.execution_arn}/*/*"
}
