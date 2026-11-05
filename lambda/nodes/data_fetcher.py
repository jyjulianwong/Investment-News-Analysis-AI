import json
import urllib.error
import urllib.parse
import urllib.request

from helpers import today_utc
from mrd_schema import MarketRegimeDetectorData
from pydantic import ValidationError
from state import AgentState


def fetch_market_regime_detector_data(
    base_url: str, model_name: str, timeout_seconds: float
) -> MarketRegimeDetectorData:
    """GET MRD's latest production prediction and validate it against the schema."""
    url = (
        f"{base_url.rstrip('/')}/v1/models/"
        f"{urllib.parse.quote(model_name, safe='')}/predictions/latest"
    )
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "investment-news-analysis-ai"}
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read())
    return MarketRegimeDetectorData.model_validate(payload)


def build_data_fetcher_node(base_url: str, model_name: str, timeout_seconds: float = 15.0):
    """Fetches external, non-news data sets into the graph state ahead of
    Query Generation. Today that is only Market Regime Detector; more sources
    would each get their own state field and be fetched here.

    Fails open: an unreachable or schema-drifted MRD leaves
    `market_regime_detector_data` as `None` rather than failing the whole
    daily run.
    """

    def data_fetcher_node(state: AgentState) -> AgentState:
        data = None
        try:
            data = fetch_market_regime_detector_data(base_url, model_name, timeout_seconds)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValidationError) as exc:
            print(f"[agent] Data fetcher: Market Regime Detector unavailable ({exc!r}); continuing")
        else:
            latest = data.latest
            print(
                f"[agent] Data fetcher: MRD {data.model_id} asof {data.asof}, "
                f"regime={latest.regime if latest else 'n/a'}, "
                f"{len(latest.data) if latest else 0} data / "
                f"{len(latest.features) if latest else 0} feature points"
            )
            # MRD only serves its *latest* prediction, so under a backtest date
            # override this is not point-in-time.
            if data.asof.isoformat() != today_utc():
                print(
                    f"[agent] Data fetcher: note MRD asof {data.asof} differs from "
                    f"pipeline date {today_utc()}"
                )
        return {"market_regime_detector_data": data}

    return data_fetcher_node
