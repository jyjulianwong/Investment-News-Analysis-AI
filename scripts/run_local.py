"""
Local test runner for the Lambda agent.

Patches out all AWS I/O so the agent can be exercised end-to-end without
any cloud connectivity:
  - boto3.client is mocked before the agent module is imported, so the
    module-level SSM and S3 client objects never make real connections.
  - _list_snippets is replaced with a function returning hardcoded snippets.
  - _upload_report is replaced with a function writing files to OUTPUT_DIR.
  - _get_secret reads OPENROUTER_API_KEY / TAVILY_API_KEY from the environment.

Run via scripts/run_local.sh (which sets cwd to lambda/ and invokes uv).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# run_local.sh sets cwd to lambda/, so agent.py is importable directly.
# This explicit insert guards against running the script from another directory.
sys.path.insert(0, str(Path(__file__).parent.parent / "lambda"))

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/tmp/ina-local"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Dummy snippets — realistic enough to exercise the full LangGraph pipeline
# ---------------------------------------------------------------------------

DUMMY_SNIPPETS = [
    (
        "Federal Reserve signals potential rate cuts in H1 2025 as inflation continues "
        "to cool. The latest CPI print came in at 2.8% year-over-year, below consensus "
        "estimates of 3.1%, the fourth consecutive month of downside surprises."
    ),
    (
        "NVIDIA reports record quarterly revenue of $35.1 billion, up 94% year-over-year, "
        "driven by surging demand for H100 and H200 AI accelerators from hyperscalers "
        "including Microsoft Azure, Google Cloud, and AWS. Data centre revenue now "
        "accounts for 87% of total sales."
    ),
    (
        "Oil prices slide below $70 per barrel as OPEC+ delays planned production cuts "
        "by three months citing weaker-than-expected demand from China. Manufacturing PMI "
        "in China fell to 49.1 in November, signalling continued contraction."
    ),
    (
        "Bitcoin surpasses $100,000 for the first time following the SEC's approval of "
        "spot Bitcoin ETFs. Institutional inflows exceeded $2 billion in the first week "
        "of trading, led by BlackRock's iShares Bitcoin Trust."
    ),
]

# ---------------------------------------------------------------------------
# Stub module-level env vars that agent.py reads at import time
# ---------------------------------------------------------------------------

os.environ.setdefault("AWS_REGION_NAME",           "eu-west-2")
os.environ.setdefault("AWS_S3_INPUT_BUCKET_NAME",  "local-dummy")
os.environ.setdefault("S3_OUTPUT_BUCKET",           "local-dummy")
os.environ.setdefault("SSM_OPENROUTER_PARAM",       "/jyjulianwong-ina/openrouter_api_key")
os.environ.setdefault("SSM_TAVILY_PARAM",           "/jyjulianwong-ina/tavily_api_key")

# ---------------------------------------------------------------------------
# Import agent with boto3.client mocked so no real AWS calls are made
# during module initialisation
# ---------------------------------------------------------------------------

with patch("boto3.client", return_value=MagicMock()):
    import agent

# ---------------------------------------------------------------------------
# Patch: return dummy snippets instead of reading from S3
# ---------------------------------------------------------------------------

agent._list_snippets = lambda day: DUMMY_SNIPPETS

# ---------------------------------------------------------------------------
# Patch: write output locally instead of uploading to S3
# ---------------------------------------------------------------------------

def _local_save(day: str, md_text: str, pdf_bytes: bytes) -> None:
    md_path = OUTPUT_DIR / f"{day}_report.md"
    pdf_path = OUTPUT_DIR / f"{day}_report.pdf"

    md_path.write_text(md_text, encoding="utf-8")
    print(f"[local] Markdown → {md_path}")

    try:
        pdf_path.write_bytes(pdf_bytes)
        print(f"[local] PDF      → {pdf_path}")
    except Exception as exc:
        # WeasyPrint requires system libs (pango, cairo). If they are absent
        # on the host, the Markdown output is still saved successfully.
        print(f"[local] PDF skipped — WeasyPrint error: {exc}")
        print("[local] Install pango/cairo via Homebrew to enable PDF output locally.")

agent._upload_report = _local_save

# ---------------------------------------------------------------------------
# Patch: read API keys from environment instead of SSM
# ---------------------------------------------------------------------------

def _env_secret(param: str) -> str:
    if "openrouter" in param:
        return os.environ["OPENROUTER_API_KEY"]
    if "tavily" in param:
        return os.environ["TAVILY_API_KEY"]
    raise KeyError(f"Unknown param name: {param!r}")

agent._get_secret = _env_secret

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

result = agent.handler({}, None)
print(f"\n[local] Handler returned: {result}")
