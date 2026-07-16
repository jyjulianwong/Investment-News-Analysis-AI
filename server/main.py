import os
import uuid
from datetime import datetime, timezone

import boto3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

app = FastAPI(title="Investment News Analysis API")

# CORS — only the GitHub Pages origin is permitted
_ALLOWED_ORIGIN = os.environ.get("CLIENT_GITHUB_PAGES_ORIGIN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN] if _ALLOWED_ORIGIN else ["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)

_s3 = boto3.client(
    "s3",
    region_name=os.environ.get("AWS_REGION", "eu-west-2"),
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
)
_INPUT_BUCKET = os.environ["AWS_S3_INPUT_BUCKET_NAME"]


def _today_utc() -> str:
    override = os.environ.get("INA_DATETIME_OVERRIDE")
    if override:
        return override[:10]
    return datetime.now(tz=timezone.utc).date().isoformat()

_MAX_SNIPPET_CHARS = 10_000


class SnippetRequest(BaseModel):
    text: str

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text must not be empty")
        if len(v) > _MAX_SNIPPET_CHARS:
            raise ValueError(f"text must not exceed {_MAX_SNIPPET_CHARS} characters")
        return v


@app.post("/snippets", status_code=200)
def submit_snippet(body: SnippetRequest):
    today = _today_utc()
    key = f"input/{today}/{uuid.uuid4()}.txt"
    try:
        _s3.put_object(
            Bucket=_INPUT_BUCKET,
            Key=key,
            Body=body.text.encode("utf-8"),
            ContentType="text/plain",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to store snippet") from exc

    return {"key": key, "date": today}


@app.get("/health")
def health():
    return {"status": "ok"}
