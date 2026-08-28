output "s3_sources_bucket_name" {
  description = "S3 bucket where the Render server writes daily news snippets"
  value       = aws_s3_bucket.sources.bucket
}

output "s3_reports_bucket_name" {
  description = "Public S3 bucket where Lambda writes daily analysis reports"
  value       = aws_s3_bucket.reports.bucket
}

output "s3_reports_bucket_url" {
  description = "Base URL for the public reports bucket"
  value       = "https://${aws_s3_bucket.reports.bucket}.s3.${var.aws_region}.amazonaws.com"
}

output "ecr_repository_url" {
  description = "ECR repository URL for the Lambda container image"
  value       = aws_ecr_repository.lambda.repository_url
}

output "lambda_function_name" {
  description = "Lambda function name for CI/CD update commands"
  value       = aws_lambda_function.agent.function_name
}

output "server_access_key_id" {
  description = "AWS access key ID for the Render server IAM user — set as RENDER env var AWS_ACCESS_KEY_ID"
  value       = aws_iam_access_key.server.id
}

output "server_secret_access_key" {
  description = "AWS secret access key for the Render server IAM user — set as RENDER env var AWS_SECRET_ACCESS_KEY"
  value       = aws_iam_access_key.server.secret
  sensitive   = true
}

output "github_actions_access_key_id" {
  description = "AWS access key ID for GitHub Actions — store as GitHub secret AWS_ACCESS_KEY_ID"
  value       = aws_iam_access_key.github_actions.id
}

output "github_actions_secret_access_key" {
  description = "AWS secret for GitHub Actions — store as GitHub secret AWS_SECRET_ACCESS_KEY"
  value       = aws_iam_access_key.github_actions.secret
  sensitive   = true
}
