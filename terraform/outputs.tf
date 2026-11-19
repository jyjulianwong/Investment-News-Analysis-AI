output "s3_sources_bucket_name" {
  description = "S3 bucket where the server Lambda writes daily news snippets"
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

output "server_lambda_function_name" {
  description = "Server Lambda function name — for CI/CD update-function-code"
  value       = aws_lambda_function.server.function_name
}

output "server_api_url" {
  description = "Public API Gateway URL for the snippet-ingest server — client/index.html and scripts/run_local_client.sh should point here"
  value       = aws_apigatewayv2_api.server.api_endpoint
}

output "alerts_topic_arn" {
  description = "SNS topic ARN alert_email is subscribed to"
  value       = aws_sns_topic.alerts.arn
}
