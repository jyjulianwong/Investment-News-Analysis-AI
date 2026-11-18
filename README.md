# Investment News Analysis AI

A GitHub Pages web app + AWS serverless stack that aggregates daily news snippets and produces AI-generated investment analysis reports.

## Who is this for?

I make a habit of reading the news on my way to work every morning... but I know nothing about economics. Instead of manually bookmarking news articles, waiting until I am back home, spending time slowly doing the research, and making poorly-educated guesses on what implications it might have to my investment portfolio, I decided to let AI and automation in the cloud do all that for me. In the morning, I "bookmark" important news articles into my system, AI reads them and does its analysis at noon whilst I'm at work, and the summary report is all ready for me to read by the time I leave. Stuck in the same situation as me? (Probably not.) Then, this project is fit for you.

## Architecture

```
GitHub Pages Client
  └─ POST /snippets ──► Server Lambda (FastAPI, via API Gateway)
                              └─► S3 sources bucket  (sources/YYYY-MM-DD/<content-hash>.txt)  ◄──┐
                                        │                                           │ writes newsletter
                              EventBridge (10:00 UTC, Mon–Fri)                      │ snippet, same format
                                        │                                           │
                              Lambda container (LangGraph)                          │
                                ├─ Email Newsletter Search (IMAP) ──────────────────┘
                                ├─ News Snippet Getter (reads all of sources/YYYY-MM-DD/ back)
                                ├─ Query Generation    (OpenRouter)
                                ├─ Web Search          (Tavily first, DDGS fallback — priority list)
                                ├─ Search Evaluator    (retries wider, then falls through providers)
                                └─ Market Analyst      (OpenRouter)
                                        │
                                S3 reports bucket  (reports/YYYY-MM-DD/report.{pdf,md})
                                        │
                              GitHub Pages Client  ◄─ public S3 links
```

## Repository Layout

```
.
├── client/              # Static GitHub Pages site
├── server/              # FastAPI service, deployed as a Lambda (Mangum) behind API Gateway
├── lambda/              # Lambda container image + LangGraph agent
├── terraform/
│   ├── bootstrap/       # State bucket, lock table and CI OIDC role (scripts/bootstrap.sh)
│   └── *.tf             # Main infrastructure (deployed via CI/CD)
└── .github/workflows/   # CI/CD pipelines
```

---

## Prerequisites

### AWS CLI

Install the AWS CLI before running any of the setup steps below.

### Configure credentials

Sign the CLI in to the account you're deploying into — IAM Identity Center is
recommended, so nothing on your machine is a long-lived key. All commands below use
whatever profile is active.

```bash
aws configure sso --profile ina   # once
aws sso login --profile ina
export AWS_PROFILE=ina            # or pass --profile ina to each command
```

---

## Initial Setup

Complete all the following steps on your local development machine first.

### 1. Bootstrap the account

Requires `jyjulianwong/Infrastructure-GitHub`'s bootstrap to have run in the same
account first — it owns the account-wide GitHub Actions OIDC provider. Then, with
admin credentials for the target account and an authenticated `gh` CLI:

```bash
scripts/bootstrap.sh
```

It shows the plan and asks before applying anything, and is safe to re-run. It:

1. applies `terraform/bootstrap` — the Terraform state bucket
   (`<ACCOUNT_ID>-jyjulianwong-ina-terraform-state`), lock table
   (`jyjulianwong-ina-terraform-lock`), and the IAM role CI assumes through
   GitHub's OIDC provider (trusted for this repo's `main` branch and pull requests
   only — there are no static AWS keys);
2. moves the bootstrap's own state into the state bucket, so later runs work from
   any clean clone;
3. sets the `AWS_ACCOUNT_ID` and `AWS_ROLE_ARN` Actions secrets, prompts for any of
   the following the repo doesn't have yet, and deletes any leftover
   `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` secrets:

| Secret name                    | Value                                         |
|--------------------------------|-----------------------------------------------|
| `OPENROUTER_API_KEY`           | Your OpenRouter API key                       |
| `OPENAI_API_KEY`               | Your OpenAI API key                           |
| `TAVILY_API_KEY`               | Your Tavily API key (free tier at tavily.com) |
| `EMAIL_IMAP_USERNAME`          | Gmail address the Lambda logs into via IMAP   |
| `EMAIL_IMAP_APP_PASSWORD`      | [Gmail App Password](https://myaccount.google.com/apppasswords) for that address |
| `CLIENT_GITHUB_PAGES_ORIGIN`   | e.g. `https://jyjulianwong.github.io`         |
| `GH_ADMIN_TOKEN`               | Admin-role PAT the version-bump job pushes tags with |

The CI role's permissions live in `terraform/bootstrap`, not the main stack, so CI
can never widen its own access — change them by re-running the script.

### 2. Deploy

Push to `main` (or re-run the latest `Deploy Infrastructure & Lambda` workflow). CI
builds everything from an empty account in one run: it creates the ECR repository
on its own first (AWS requires a container Lambda's image to exist when the
function is created), pushes the agent Lambda image, applies the whole main stack
— buckets, SSM parameters, both Lambdas, the server's API Gateway HTTP API and
the EventBridge schedule — then deploys the real code to both Lambdas.

The server (`server/`) runs as a Lambda behind API Gateway, wrapped with Mangum.
Its execution role grants `s3:PutObject` on the sources bucket's `sources/`
prefix only — nothing holds a static AWS key.

### 3. Update client placeholders

In `client/index.html`, set `API_BASE` to the server's API Gateway URL:
```bash
terraform -chdir=terraform output -raw server_api_url
```

In `client/reports.html`, set `S3_BASE` to the reports bucket base URL:
```bash
terraform -chdir=terraform output -raw s3_reports_bucket_url
```

Then, in the server's CORS settings, make sure the `CLIENT_GITHUB_PAGES_ORIGIN`
secret matches the origin the client is served from.

### 4. Enable GitHub Pages

In GitHub → **Settings → Pages → Source**, set branch to `gh-pages` and directory to `/ (root)`.

---

## Useful AWS CLI Commands

### Check S3 bucket contents

```bash
SOURCES_BUCKET=$(terraform -chdir=terraform output -raw s3_sources_bucket_name)
REPORTS_BUCKET=$(terraform -chdir=terraform output -raw s3_reports_bucket_name)

# List today's source snippets
aws s3 ls "s3://$SOURCES_BUCKET/sources/$(date -u +%Y-%m-%d)/"

# List all available reports
aws s3 ls "s3://$REPORTS_BUCKET/reports/"

# Download a specific report locally
aws s3 cp "s3://$REPORTS_BUCKET/reports/YYYY-MM-DD/report.pdf" ./report.pdf
```

### Manually trigger the Lambda (for testing)

```bash
aws lambda invoke \
  --function-name jyjulianwong-ina-agent \
  --region eu-west-2 \
  response.json && cat response.json && echo
```

### Update SSM secrets manually

Use this to rotate API keys without re-running Terraform:

```bash
aws ssm put-parameter \
  --name "/jyjulianwong-ina/openrouter_api_key" \
  --value "sk-or-..." \
  --type SecureString \
  --overwrite \
  --region eu-west-2

aws ssm put-parameter \
  --name "/jyjulianwong-ina/tavily_api_key" \
  --value "tvly-..." \
  --type SecureString \
  --overwrite \
  --region eu-west-2

aws ssm put-parameter \
  --name "/jyjulianwong-ina/email_imap_username" \
  --value "you@gmail.com" \
  --type SecureString \
  --overwrite \
  --region eu-west-2

aws ssm put-parameter \
  --name "/jyjulianwong-ina/email_imap_app_password" \
  --value "your Gmail App Password" \
  --type SecureString \
  --overwrite \
  --region eu-west-2
```

### Verify SSM parameters exist

```bash
aws ssm get-parameters-by-path \
  --path "/jyjulianwong-ina/" \
  --with-decryption \
  --region eu-west-2 \
  --query "Parameters[].{Name:Name,Value:Value}"
```

### Log in to ECR (for manual Docker push)

```bash
aws ecr get-login-password --region eu-west-2 | \
  docker login --username AWS --password-stdin \
  $(aws ecr describe-repositories \
      --repository-names jyjulianwong-ina-lambda-agent \
      --region eu-west-2 \
      --query "repositories[0].repositoryUri" \
      --output text | sed 's|/.*||')
```

### Tail Lambda logs

```bash
aws logs tail /aws/lambda/jyjulianwong-ina-agent --follow --region eu-west-2
```

### Check Lambda function status

```bash
aws lambda get-function \
  --function-name jyjulianwong-ina-agent \
  --region eu-west-2 \
  --query "Configuration.{State:State,LastUpdateStatus:LastUpdateStatus,ImageUri:Code.ImageUri}"
```

---

## Daily Flow

| Time (UTC)  | Event |
|-------------|-------|
| Any time    | User pastes a news snippet on the Submit page; it is stored in `s3://<sources-bucket>/sources/YYYY-MM-DD/<content-hash>.txt` |
| 12:00       | EventBridge triggers the Lambda |
| 12:00–12:15 | Lambda fetches each configured newsletter sender's latest message(s) (IMAP) and writes them to `s3://<sources-bucket>/sources/YYYY-MM-DD/<content-hash>.txt` like any other snippet, reads all of that day's snippets back from S3, then runs the rest of the LangGraph pipeline |
| After 12:15 | User visits the Reports page; today's PDF and Markdown report are available |

---

## CI/CD

### `deploy-terraform.yml`
- **On PR** (touching `terraform/`, `lambda/` or `server/`): runs `terraform plan` and posts the output as a PR comment.
- **On merge to `main`**: ensures the ECR repository exists, builds and pushes the agent Lambda's Docker image to it, runs `terraform apply`, calls `aws lambda update-function-code` to activate the new image, then packages the server Lambda (`scripts/package_lambda.sh`) and deploys it the same way.

Both jobs authenticate to AWS by assuming `AWS_ROLE_ARN` through GitHub's OIDC provider (`id-token: write`) — no AWS access keys are stored anywhere.

### `deploy-client.yml`
- **On merge to `main`** (touching `client/`): publishes the `client/` directory to the `gh-pages` branch via `peaceiris/actions-gh-pages`.

---

## Local Development

### Server

```bash
# Uses your active AWS credentials (e.g. `aws sso login`)
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cd server
uv sync
AWS_S3_SOURCES_BUCKET_NAME="${ACCOUNT_ID}-jyjulianwong-ina-news-sources" \
  CLIENT_GITHUB_PAGES_ORIGIN=http://localhost:3000 uv run uvicorn main:app --reload --port 8000
```

### Lambda

For end-to-end integration testing, use `./scripts/run_local_lambda.sh` — it builds the image and runs the handler in one step with credentials and env vars pre-configured.

To test the Lambda runtime interface emulator (RIE) manually instead:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
cd lambda
docker buildx build --platform linux/amd64 --provenance=false --load -t ina-lambda-agent-test .
docker run -p 9000:8080 \
  -e AWS_REGION_NAME=eu-west-2 \
  -e "AWS_S3_SOURCES_BUCKET_NAME=${ACCOUNT_ID}-jyjulianwong-ina-news-sources" \
  -e "AWS_S3_REPORTS_BUCKET_NAME=${ACCOUNT_ID}-jyjulianwong-ina-news-reports" \
  -e "SSM_OPENROUTER_PARAM=/jyjulianwong-ina/openrouter_api_key" \
  -e "SSM_OPENAI_PARAM=/jyjulianwong-ina/openai_api_key" \
  -e "LLM_PROVIDER=openrouter" \
  -e "SSM_TAVILY_PARAM=/jyjulianwong-ina/tavily_api_key" \
  -e "SSM_EMAIL_IMAP_USERNAME_PARAM=/jyjulianwong-ina/email_imap_username" \
  -e "SSM_EMAIL_IMAP_PASSWORD_PARAM=/jyjulianwong-ina/email_imap_app_password" \
  -v "$HOME/.aws:/root/.aws:ro" \
  -e AWS_PROFILE="${AWS_PROFILE:-default}" \
  ina-lambda-agent-test

# In a second terminal:
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{}'
```
