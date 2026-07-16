# Investment News Analysis AI

A GitHub Pages web app + AWS serverless stack that aggregates daily news snippets and produces AI-generated investment analysis reports.

## Architecture

```
GitHub Pages Client
  └─ POST /snippets ──► Render Server (FastAPI)
                              └─► S3 input bucket  (input/YYYY-MM-DD/<uuid>.txt)
                                        │
                              EventBridge (12:00 UTC daily)
                                        │
                              Lambda container (LangGraph)
                                ├─ Query Generation Agent  (OpenRouter)
                                ├─ Web Search Agent        (Tavily)
                                └─ Market Analyst Agent    (OpenRouter)
                                        │
                                S3 output bucket  (output/YYYY-MM-DD/report.{pdf,md})
                                        │
                              GitHub Pages Client  ◄─ public S3 links
```

## Repository Layout

```
.
├── client/              # Static GitHub Pages site
├── server/              # Render-hosted FastAPI service
├── lambda/              # Lambda container image + LangGraph agent
├── terraform/
│   ├── bootstrap/       # One-time state infrastructure (run manually once)
│   └── *.tf             # Main infrastructure (deployed via CI/CD)
└── .github/workflows/   # CI/CD pipelines
```

---

## Prerequisites

### AWS CLI

Install the AWS CLI before running any of the setup steps below.

### Configure credentials

Configure the CLI with your AWS credentials once. All commands below use whatever profile is active.

```bash
aws configure
# AWS Access Key ID:     <your key>
# AWS Secret Access Key: <your secret>
# Default region name:   eu-west-2
# Default output format: json
```

To use a named profile instead of the default:
```bash
aws configure --profile ina
export AWS_PROFILE=ina   # or pass --profile ina to each command
```

---

## Initial Setup

Complete all the following steps on your local development machine first.

### 1. Find your AWS account ID

All Terraform configs require your account ID so they can refuse to apply against the wrong account.

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo $ACCOUNT_ID
```

### 2. Bootstrap Terraform state infrastructure

These two resources must exist before any CI/CD run can initialise the S3 backend.

```bash
cd terraform/bootstrap
terraform init
terraform apply -var="aws_account_id=$ACCOUNT_ID"
# Creates: ${ACCOUNT_ID}-jyjulianwong-ina-terraform-state (S3)
#          jyjulianwong-ina-terraform-lock (DynamoDB)
```

Verify with the AWS CLI:
```bash
aws s3 ls | grep "${ACCOUNT_ID}-jyjulianwong-ina-terraform-state"
aws dynamodb describe-table --table-name "${ACCOUNT_ID}-jyjulianwong-ina-terraform-lock" --query "Table.TableStatus"
```

### 2. First-time Terraform apply

The Lambda function requires a container image to already exist in ECR before it can be created.
This means the initial apply must be done in two phases.

**Phase 1 — create ECR only:**

```bash
cd terraform
terraform init \
  -backend-config="bucket=${ACCOUNT_ID}-jyjulianwong-ina-terraform-state" \
  -backend-config="dynamodb_table=jyjulianwong-ina-terraform-lock"
terraform apply \
  -target=aws_ecr_repository.lambda \
  -var="aws_account_id=$ACCOUNT_ID" \
  -var="openrouter_api_key=YOUR_KEY" \
  -var="tavily_api_key=YOUR_KEY" \
  -var="client_github_pages_origin=https://YOUR_USERNAME.github.io"
```

**Phase 2 — build and push the initial Lambda image:**

```bash
ECR_URL=$(terraform output -raw ecr_repository_url)

aws ecr get-login-password --region eu-west-2 | \
  docker login --username AWS --password-stdin "$ECR_URL"

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --push \
  -t "$ECR_URL:latest" \
  ../lambda/
```

**Phase 3 — complete the full apply:**

```bash
terraform apply \
  -var="aws_account_id=$ACCOUNT_ID" \
  -var="openrouter_api_key=YOUR_KEY" \
  -var="tavily_api_key=YOUR_KEY" \
  -var="client_github_pages_origin=https://YOUR_USERNAME.github.io"
```

After this, all subsequent infrastructure and Lambda changes are deployed automatically by GitHub Actions on push to `main`.

### 3. Retrieve generated IAM credentials

```bash
cd terraform

# For GitHub Actions
terraform output github_actions_access_key_id
terraform output -raw github_actions_secret_access_key && echo

# For the Render-hosted server
terraform output server_access_key_id
terraform output -raw server_secret_access_key && echo

# Print the output bucket base URL
terraform output s3_output_bucket_url
```

### 4. Add GitHub Actions secrets

In your GitHub repository → **Settings → Secrets and variables → Actions**, add:

| Secret name                    | Value                                         |
|--------------------------------|-----------------------------------------------|
| `AWS_ACCOUNT_ID`               | Your 12-digit AWS account ID                  |
| `AWS_ACCESS_KEY_ID`            | GitHub Actions IAM user access key ID         |
| `AWS_SECRET_ACCESS_KEY`        | GitHub Actions IAM user secret access key     |
| `OPENROUTER_API_KEY`           | Your OpenRouter API key                       |
| `TAVILY_API_KEY`               | Your Tavily API key (free tier at tavily.com) |
| `CLIENT_GITHUB_PAGES_ORIGIN`   | e.g. `https://jyjulianwong.github.io`         |

### 5a. Create and deploy the Render service

Render deploys the server automatically on every push to `main` — no GitHub Actions required.

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub account and select this repository
3. Configure the service:

| Setting           | Value                                    |
|-------------------|------------------------------------------|
| **Name**          | Choose any name (e.g. `ina-server`)      |
| **Region**        | Match your AWS region (e.g. EU West)     |
| **Branch**        | `main`                                   |
| **Root directory**| `server`                                 |
| **Runtime**       | Python 3                                 |
| **Build command** | `pip install uv && uv sync`              |
| **Start command** | `uvicorn main:app --host 0.0.0.0 --port $PORT` |

4. Click **Create Web Service** — Render will build and deploy the server
5. Copy the service URL (e.g. `https://ina-server.onrender.com`) — you will need it in step 6

After this initial setup, every push to `main` that touches `server/` will trigger an automatic redeploy.

### 5b. Set Render environment variables

In your Render service dashboard → **Environment**, add:

| Variable                       | Value                                             |
|--------------------------------|---------------------------------------------------|
| `AWS_ACCESS_KEY_ID`            | Render server IAM user access key ID              |
| `AWS_SECRET_ACCESS_KEY`        | Render server IAM user secret access key          |
| `AWS_REGION`                   | `eu-west-2`                                       |
| `AWS_S3_INPUT_BUCKET_NAME`     | Output of `terraform output s3_input_bucket_name` |
| `CLIENT_GITHUB_PAGES_ORIGIN`   | e.g. `https://jyjulianwong.github.io`             |

Render **start command:**
```
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Render **root directory:** `server`

### 6. Update client placeholders

In `client/index.html`, replace:
```js
const API_BASE = "https://your-render-app.onrender.com";
```
with your actual Render service URL.

In `client/reports.html`, replace the placeholder `S3_BASE` with the value printed by:
```bash
terraform output s3_output_bucket_url
```

### 7. Enable GitHub Pages

In GitHub → **Settings → Pages → Source**, set branch to `gh-pages` and directory to `/ (root)`.

---

## Useful AWS CLI Commands

### Check S3 bucket contents

```bash
INPUT_BUCKET=$(terraform -chdir=terraform output -raw s3_input_bucket_name)
OUTPUT_BUCKET=$(terraform -chdir=terraform output -raw s3_output_bucket_name)

# List today's input snippets
aws s3 ls "s3://$INPUT_BUCKET/input/$(date -u +%Y-%m-%d)/"

# List all available reports
aws s3 ls "s3://$OUTPUT_BUCKET/output/"

# Download a specific report locally
aws s3 cp "s3://$OUTPUT_BUCKET/output/YYYY-MM-DD/report.pdf" ./report.pdf
```

### Manually trigger the Lambda (for testing)

```bash
aws lambda invoke \
  --function-name ina-agent \
  --region eu-west-2 \
  response.json && cat response.json
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
      --repository-names ina-lambda \
      --region eu-west-2 \
      --query "repositories[0].repositoryUri" \
      --output text | sed 's|/.*||')
```

### Tail Lambda logs

```bash
aws logs tail /aws/lambda/ina-agent --follow --region eu-west-2
```

### Check Lambda function status

```bash
aws lambda get-function \
  --function-name ina-agent \
  --region eu-west-2 \
  --query "Configuration.{State:State,LastUpdateStatus:LastUpdateStatus,ImageUri:Code.ImageUri}"
```

---

## Daily Flow

| Time (UTC)  | Event |
|-------------|-------|
| Any time    | User pastes a news snippet on the Submit page; it is stored in `s3://<input-bucket>/input/YYYY-MM-DD/<uuid>.txt` |
| 12:00       | EventBridge triggers the Lambda |
| 12:00–12:15 | Lambda runs the LangGraph pipeline (or generates a no-snippets report if none were submitted) |
| After 12:15 | User visits the Reports page; today's PDF and Markdown report are available |

---

## CI/CD

### `deploy-terraform.yml`
- **On PR** (touching `terraform/` or `lambda/`): runs `terraform plan` and posts the output as a PR comment.
- **On merge to `main`**: runs `terraform apply`, builds and pushes the Lambda Docker image to ECR, then calls `aws lambda update-function-code` to activate the new image.

### `deploy-client.yml`
- **On merge to `main`** (touching `client/`): publishes the `client/` directory to the `gh-pages` branch via `peaceiris/actions-gh-pages`.

---

## Local Development

### Server

```bash
# ACCOUNT_ID must be set — see step 1 of Initial Setup
cd server
uv sync
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  AWS_S3_INPUT_BUCKET_NAME="${ACCOUNT_ID}-jyjulianwong-ina-news-input" \
  CLIENT_GITHUB_PAGES_ORIGIN=http://localhost uv run uvicorn main:app --reload
```

### Lambda (local test, no Docker)

```bash
# ACCOUNT_ID must be set — see step 1 of Initial Setup
cd lambda
uv sync
AWS_REGION_NAME=eu-west-2 \
  AWS_S3_INPUT_BUCKET_NAME="${ACCOUNT_ID}-jyjulianwong-ina-news-input" \
  S3_OUTPUT_BUCKET="${ACCOUNT_ID}-jyjulianwong-ina-news-output" \
  SSM_OPENROUTER_PARAM="/jyjulianwong-ina/openrouter_api_key" \
  SSM_TAVILY_PARAM="/jyjulianwong-ina/tavily_api_key" \
  AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  uv run python -c "import agent; print(agent.handler({}, None))"
```

### Lambda (Docker build test)

```bash
# ACCOUNT_ID must be set — see step 1 of Initial Setup
cd lambda
docker buildx build --platform linux/amd64 --provenance=false --load -t ina-lambda-test .
docker run -p 9000:8080 \
  -e AWS_REGION_NAME=eu-west-2 \
  -e "AWS_S3_INPUT_BUCKET_NAME=${ACCOUNT_ID}-jyjulianwong-ina-news-input" \
  -e "S3_OUTPUT_BUCKET=${ACCOUNT_ID}-jyjulianwong-ina-news-output" \
  -e "SSM_OPENROUTER_PARAM=/jyjulianwong-ina/openrouter_api_key" \
  -e "SSM_TAVILY_PARAM=/jyjulianwong-ina/tavily_api_key" \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  ina-lambda-test

# In a second terminal:
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{}'
```
