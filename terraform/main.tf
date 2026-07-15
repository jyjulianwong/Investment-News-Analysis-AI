terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region             = var.aws_region
  allowed_account_ids = [var.aws_account_id]
}

locals {
  prefix = var.project
}

# ---------------------------------------------------------------------------
# S3 — Input bucket (private, Render API writes here)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "input" {
  bucket = "${local.prefix}-news-input"
}

resource "aws_s3_bucket_public_access_block" "input" {
  bucket                  = aws_s3_bucket.input.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "input" {
  bucket = aws_s3_bucket.input.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ---------------------------------------------------------------------------
# S3 — Output bucket (public read, UI lists and links to reports here)
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "output" {
  bucket = "${local.prefix}-news-output"
}

resource "aws_s3_bucket_public_access_block" "output" {
  bucket                  = aws_s3_bucket.output.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "output" {
  bucket = aws_s3_bucket.output.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_policy" "output_public_read" {
  bucket = aws_s3_bucket.output.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.output.arn,
          "${aws_s3_bucket.output.arn}/*"
        ]
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.output]
}

resource "aws_s3_bucket_cors_configuration" "output" {
  bucket = aws_s3_bucket.output.id
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = [var.client_github_pages_origin]
    expose_headers  = ["ETag"]
    max_age_seconds = 3000
  }
}

# ---------------------------------------------------------------------------
# SSM — API keys (values injected manually / via CI, Terraform manages slots)
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "openrouter_api_key" {
  name        = "/${local.prefix}/openrouter_api_key"
  type        = "SecureString"
  value       = var.openrouter_api_key
  description = "OpenRouter API key for the Lambda LangGraph agent"
}

resource "aws_ssm_parameter" "tavily_api_key" {
  name        = "/${local.prefix}/tavily_api_key"
  type        = "SecureString"
  value       = var.tavily_api_key
  description = "Tavily Search API key for the Lambda web search step"
}

# ---------------------------------------------------------------------------
# ECR — Lambda container image repository
# ---------------------------------------------------------------------------

resource "aws_ecr_repository" "lambda" {
  name                 = "${local.prefix}-lambda"
  image_tag_mutability = "MUTABLE"

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
        countNumber = 5
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
  name               = "${local.prefix}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda_permissions" {
  statement {
    sid    = "ReadInputBucket"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:ListBucket",
    ]
    resources = [
      aws_s3_bucket.input.arn,
      "${aws_s3_bucket.input.arn}/*",
    ]
  }

  statement {
    sid    = "WriteOutputBucket"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.output.arn}/*"]
  }

  statement {
    sid    = "ReadSSMParams"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
    ]
    resources = [
      aws_ssm_parameter.openrouter_api_key.arn,
      aws_ssm_parameter.tavily_api_key.arn,
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
  name   = "${local.prefix}-lambda-permissions"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda_permissions.json
}

# ---------------------------------------------------------------------------
# Lambda — container image function
# Placeholder image_uri; CI/CD updates this after each ECR push.
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "agent" {
  function_name = "${local.prefix}-agent"
  role          = aws_iam_role.lambda.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.lambda.repository_url}:latest"
  timeout       = 900 # 15 minutes — LLM calls can be slow
  memory_size   = 1024

  environment {
    variables = {
      AWS_S3_INPUT_BUCKET_NAME  = aws_s3_bucket.input.bucket
      S3_OUTPUT_BUCKET     = aws_s3_bucket.output.bucket
      SSM_OPENROUTER_PARAM = aws_ssm_parameter.openrouter_api_key.name
      SSM_TAVILY_PARAM     = aws_ssm_parameter.tavily_api_key.name
      AWS_REGION_NAME      = var.aws_region
    }
  }

  lifecycle {
    # CI/CD updates the image; Terraform should not revert it on next apply
    ignore_changes = [image_uri]
  }
}

# ---------------------------------------------------------------------------
# EventBridge — daily trigger at 12:00 UTC
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "daily_noon" {
  name                = "${local.prefix}-daily-noon"
  description         = "Trigger the news analysis Lambda at 12:00 UTC daily"
  schedule_expression = "cron(0 12 * * ? *)"
}

resource "aws_cloudwatch_event_target" "lambda" {
  rule      = aws_cloudwatch_event_rule.daily_noon.name
  target_id = "${local.prefix}-lambda"
  arn       = aws_lambda_function.agent.arn
}

resource "aws_lambda_permission" "eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.agent.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_noon.arn
}

# ---------------------------------------------------------------------------
# IAM — Render API user (write-only to input bucket)
# ---------------------------------------------------------------------------

resource "aws_iam_user" "server" {
  name = "${local.prefix}-server-user"
}

data "aws_iam_policy_document" "server" {
  statement {
    sid    = "WriteInputBucket"
    effect = "Allow"
    actions = [
      "s3:PutObject",
    ]
    resources = ["${aws_s3_bucket.input.arn}/input/*"]
  }
}

resource "aws_iam_user_policy" "server" {
  name   = "${local.prefix}-server-user-policy"
  user   = aws_iam_user.server.name
  policy = data.aws_iam_policy_document.server.json
}

resource "aws_iam_access_key" "server" {
  user = aws_iam_user.server.name
}

# ---------------------------------------------------------------------------
# IAM — GitHub Actions user (Terraform apply + ECR push + Lambda update)
# ---------------------------------------------------------------------------

resource "aws_iam_user" "github_actions" {
  name = "${local.prefix}-github-actions-user"
}

data "aws_iam_policy_document" "github_actions" {
  statement {
    sid    = "ECRAuth"
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ECRPush"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
      "ecr:DescribeRepositories",
    ]
    resources = [aws_ecr_repository.lambda.arn]
  }

  statement {
    sid    = "LambdaUpdate"
    effect = "Allow"
    actions = [
      "lambda:UpdateFunctionCode",
      "lambda:GetFunction",
    ]
    resources = [aws_lambda_function.agent.arn]
  }

  statement {
    sid    = "TerraformState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:ListBucket",
    ]
    resources = [
      "arn:aws:s3:::jyjulianwong-ina-terraform-state",
      "arn:aws:s3:::jyjulianwong-ina-terraform-state/*",
    ]
  }

  statement {
    sid    = "TerraformLock"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
    ]
    resources = ["arn:aws:dynamodb:${var.aws_region}:*:table/jyjulianwong-ina-terraform-lock"]
  }

  # Regional Terraform apply permissions
  statement {
    sid    = "TerraformApplyRegional"
    effect = "Allow"
    actions = [
      "s3:*",
      "lambda:*",
      "ecr:*",
      "events:*",
      "ssm:*",
      "kms:Describe*",
      "kms:List*",
      "logs:*",
    ]
    resources = ["*"]
    condition {
      test     = "StringLike"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # IAM is a global service and does not populate aws:RequestedRegion,
  # so it must be in its own statement without that condition.
  statement {
    sid    = "TerraformApplyIAM"
    effect = "Allow"
    actions = [
      "iam:*",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_user_policy" "github_actions" {
  name   = "${local.prefix}-github-actions-user-policy"
  user   = aws_iam_user.github_actions.name
  policy = data.aws_iam_policy_document.github_actions.json
}

resource "aws_iam_access_key" "github_actions" {
  user = aws_iam_user.github_actions.name
}
