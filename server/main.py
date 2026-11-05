import hashlib
import os
import re
from datetime import date, datetime, timezone

import boto3
from botocore.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from obsidian_auth import optional_device, require_device
from pydantic import BaseModel, field_validator

app = FastAPI(title="Investment News Analysis API")

# CORS — only the GitHub Pages origin is permitted
_ALLOWED_ORIGIN = os.environ.get("CLIENT_GITHUB_PAGES_ORIGIN", "")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_ALLOWED_ORIGIN] if _ALLOWED_ORIGIN else ["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

_REGION = os.environ.get("AWS_REGION", "eu-west-2")
_s3 = boto3.client(
    "s3",
    region_name=_REGION,
    # Pinned so presigned report URLs use the regional host directly instead
    # of boto's global s3.amazonaws.com, which relies on an S3 redirect.
    endpoint_url=f"https://s3.{_REGION}.amazonaws.com",
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)
_SOURCES_BUCKET = os.environ["AWS_S3_SOURCES_BUCKET_NAME"]
_REPORTS_BUCKET = os.environ["AWS_S3_REPORTS_BUCKET_NAME"]

# Reports whose date is more than this many days before today are readable only
# by an Obsidian-authenticated device. The reports bucket itself is private;
# this server is the only way to read it, so this is enforced here, not in S3.
_OPEN_REPORT_MAX_AGE_DAYS = int(os.environ.get("OPEN_REPORT_MAX_AGE_DAYS", "3"))
_PRESIGNED_URL_TTL_SECONDS = 300
_REPORT_FORMATS = {"pdf": "report.pdf", "md": "report.md"}
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
def submit_snippet(body: SnippetRequest, device_id: str = Depends(require_device)):
    today = _today_utc()
    # Keyed by a content hash rather than a random uuid, so a duplicate
    # submission (e.g. a retried request) overwrites the same object instead
    # of accumulating a second copy of the same snippet.
    snippet_bytes = body.text.encode("utf-8")
    content_hash = hashlib.sha256(snippet_bytes).hexdigest()
    key = f"sources/{today}/{content_hash}.txt"
    try:
        _s3.put_object(
            Bucket=_SOURCES_BUCKET,
            Key=key,
            Body=snippet_bytes,
            ContentType="text/plain",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to store snippet") from exc

    return {"key": key, "date": today}


def _is_locked(day: str) -> bool:
    age_days = (date.fromisoformat(_today_utc()) - date.fromisoformat(day)).days
    return age_days > _OPEN_REPORT_MAX_AGE_DAYS


@app.get("/reports")
def list_reports(device_id: str | None = Depends(optional_device)):
    """Every report date, newest first. Public: older dates are listed but
    marked `locked` unless the caller presents a valid Obsidian token."""
    paginator = _s3.get_paginator("list_objects_v2")
    candidates = (
        prefix["Prefix"].removeprefix("reports/").rstrip("/")
        for page in paginator.paginate(Bucket=_REPORTS_BUCKET, Prefix="reports/", Delimiter="/")
        for prefix in page.get("CommonPrefixes", [])
    )
    days = sorted({d for d in candidates if _DAY_RE.match(d)}, reverse=True)
    return {
        "authenticated": device_id is not None,
        "reports": [{"date": d, "locked": device_id is None and _is_locked(d)} for d in days],
    }


@app.get("/reports/{day}/{fmt}")
def get_report_url(day: str, fmt: str, device_id: str | None = Depends(optional_device)):
    """Short-lived presigned S3 URL for one report file. 401 if the report is
    older than the open window and no valid Obsidian token was presented."""
    if fmt not in _REPORT_FORMATS or not _DAY_RE.match(day):
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        locked = _is_locked(day)
    except ValueError as exc:  # matches the regex but isn't a real date
        raise HTTPException(status_code=404, detail="Report not found") from exc
    if locked and device_id is None:
        raise HTTPException(
            status_code=401,
            detail=f"Reports older than {_OPEN_REPORT_MAX_AGE_DAYS} days require an authorized device",
        )

    key = f"reports/{day}/{_REPORT_FORMATS[fmt]}"
    try:
        _s3.head_object(Bucket=_REPORTS_BUCKET, Key=key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Report not found") from exc

    url = _s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": _REPORTS_BUCKET, "Key": key},
        ExpiresIn=_PRESIGNED_URL_TTL_SECONDS,
    )
    return {"url": url, "expires_in": _PRESIGNED_URL_TTL_SECONDS}


@app.get("/health")
def health():
    return {"status": "ok"}
