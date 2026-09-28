from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd

from .metrics import FundamentalMetrics, TechnicalMetrics, ValuationMetrics
from .universe import UniverseMember


WEIGHTS = {
    "correction_score": 0.10,
    "support_score": 0.15,
    "momentum_score": 0.10,
    "fcf_score": 0.25,
    "profit_score": 0.10,
    "obv_score": 0.10,
    "valuation_score": 0.20,
}


@dataclass(frozen=True)
class Candidate:
    member: UniverseMember
    technical: TechnicalMetrics
    fundamental: FundamentalMetrics
    valuation: ValuationMetrics

    def flat(self) -> dict:
        return {
            **asdict(self.member),
            **self.technical.to_dict(),
            **self.fundamental.to_dict(),
            **self.valuation.to_dict(),
        }


def _percentile(series: pd.Series) -> pd.Series:
    if len(series) == 1:
        return pd.Series([100.0], index=series.index)
    return series.rank(method="average", pct=True) * 100.0


def score_candidates(candidates: Iterable[Candidate]) -> pd.DataFrame:
    rows = [candidate.flat() for candidate in candidates]
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    eligible = frame[
        frame["correction_pass"]
        & frame["support_pass"]
        & frame["momentum_pass"]
        & frame["obv_pass"]
        & frame["fcf_pass"]
        & frame["profit_pass"]
        & frame["size_pass"]
        & frame["valuation_pass"]
    ].copy()
    if eligible.empty:
        return eligible

    eligible["correction_score"] = _percentile(eligible["downtrend_signal"])
    eligible["support_score"] = _percentile(eligible["support_signal"])
    eligible["momentum_score"] = _percentile(eligible["momentum_signal"])
    eligible["fcf_score"] = _percentile(eligible["fcf_signal"])
    eligible["profit_score"] = _percentile(eligible["profit_signal"])
    eligible["obv_score"] = _percentile(eligible["obv_divergence_signal"])
    eligible["valuation_score"] = _percentile(eligible["valuation_signal"])
    eligible["total_score"] = sum(
        eligible[column] * weight for column, weight in WEIGHTS.items()
    )
    eligible["rank"] = eligible["total_score"].rank(
        method="first", ascending=False
    ).astype(int)
    ordered = eligible.sort_values(
        ["total_score", "valuation_score", "fcf_score", "obv_score", "symbol"],
        ascending=[False, False, False, False, True],
    )
    return ordered.reset_index(drop=True)
