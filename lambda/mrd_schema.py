"""Pydantic mirror of Market Regime Detector's latest-prediction output.

Source of truth is MRD's `server/schemas.py` (`LatestPredictionResponse`),
served by `GET /v1/models/{model_name}/predictions/latest`. It is kept as a
separate module from `state.py` because it describes an external contract,
not graph plumbing. The full response is mirrored, including the `data`
(raw lake series) and `features` (model-transformed feature values) input
diagnostics nested under `latest`.

Unknown extra fields are ignored rather than rejected, so MRD adding a
field never breaks the Lambda; fields MRD *removes or retypes* will fail
validation loudly in `data_fetcher`, which then fails open.
"""

import datetime as dt

from pydantic import BaseModel, Field


class MRDQuadrantProbabilities(BaseModel):
    GROWTH_UP_INFLATION_DOWN: float
    GROWTH_UP_INFLATION_UP: float
    GROWTH_DOWN_INFLATION_UP: float
    GROWTH_DOWN_INFLATION_DOWN: float


class MRDDataPoint(BaseModel):
    """One row of `latest.data` (a raw data-lake series) or `latest.features`
    (a model's transformed feature value) — MRD uses the same shape for both.
    """

    name: str
    description: str
    value: float | None = None
    # `dt.date`, not bare `date`: a field named `date` would shadow the type for
    # every annotation after it in this class body.
    realtime_start: dt.date | None = None
    date: dt.date | None = None
    lag_days: int | None = None


class MRDLatestSnapshot(BaseModel):
    p_growth_up: float
    p_inflation_up: float
    quadrants: MRDQuadrantProbabilities
    regime: str
    confidence: float
    data: list[MRDDataPoint] = Field(default_factory=list)
    features: list[MRDDataPoint] = Field(default_factory=list)


class MarketRegimeDetectorData(BaseModel):
    """Full `GET /v1/models/{model_name}/predictions/latest` response."""

    model_id: str
    model_name: str
    dataset_id: str
    asof: dt.date
    generated_at: dt.datetime
    observation_date: dt.date | None = None
    is_stale: bool
    latest: MRDLatestSnapshot | None = None
    warnings: list[str] = Field(default_factory=list)
