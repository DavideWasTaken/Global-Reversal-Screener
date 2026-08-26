from stock_finder.metrics import (
    FundamentalMetrics,
    TechnicalMetrics,
    ValuationMetrics,
)
from stock_finder.scoring import Candidate, WEIGHTS, score_candidates
from stock_finder.universe import UniverseMember


def _candidate(
    symbol: str,
    *,
    size_pass: bool = True,
    valuation_pass: bool = True,
    momentum_pass: bool = True,
):
    technical = TechnicalMetrics(
        observations=150,
        last_price=10.0,
        return_3m=-0.20,
        return_6m=-0.30,
        trend_slope_annualized=-0.25,
        price_slope_3d_z=-0.10,
        obv_slope_3d_z=0.10,
        swing_price_lower_low=None,
        swing_obv_delta=None,
        obv_swing_setup=False,
        obv_swing_3d_pass=False,
        atr20=1.0,
        drawdown_3m=-0.25,
        support_level=9.5,
        support_distance_pct=0.0526,
        support_distance_atr=0.5,
        support_touches=3,
        support_kind="established",
        support_signal=0.80,
        correction_pass=True,
        support_pass=True,
        ema10=9.8,
        roc5=0.05,
        rsi14=45.0,
        macd_line=-0.10,
        macd_signal_line=-0.20,
        macd_histogram=0.10,
        prior_high_5d=9.8,
        price_breakout_5d=0.02,
        volume_ratio20=1.2,
        close_location=0.9,
        rsi_failure_swing_low1=29.0,
        rsi_failure_swing_peak=36.0,
        rsi_failure_swing_low2=32.0,
        momentum_price_breakout=True,
        momentum_rsi_failure_swing=True,
        momentum_macd_inflection=True,
        momentum_confirmations=2,
        momentum_signal_age=0,
        momentum_state="bullish_reversal_confirmed",
        momentum_signal=1.20,
        momentum_pass=momentum_pass,
        bullish_regime_pass=False,
        downtrend_signal=0.20,
        obv_divergence_signal=0.20,
        trend_pass=True,
        obv_pass=True,
    )
    fundamental = FundamentalMetrics(
        fcf_ttm=100.0,
        fcf_previous_ttm=80.0,
        fcf_growth=0.25,
        fcf_turnaround=False,
        net_income_ttm=50.0,
        revenue_ttm=500.0,
        net_margin_ttm=0.10,
        profitable_quarters=4,
        fundamental_period_end="2026-06-30",
        fcf_signal=0.25,
        profit_signal=0.50,
        fcf_pass=True,
        profit_pass=True,
    )
    valuation = ValuationMetrics(
        market_cap_local=20_000_000_000.0,
        market_cap_currency="USD",
        market_cap_usd=20_000_000_000.0,
        market_cap_as_of="2026-08-21",
        dcf_fair_value=15.0,
        peter_lynch_fair_value=13.0,
        ev_sales_fair_value=16.0,
        average_fair_value=14.67,
        consensus_fair_value=15.0,
        margin_of_safety=1 / 3,
        upside_to_fair_value=0.50,
        valuation_methods=3,
        undervalued_methods=3,
        acceptable_methods=3,
        size_pass=size_pass,
        valuation_pass=valuation_pass,
        valuation_signal=0.40,
    )
    return Candidate(
        UniverseMember(symbol=symbol, ticker=symbol, name=symbol),
        technical,
        fundamental,
        valuation,
    )


def test_size_and_valuation_are_hard_scoring_gates():
    valid = _candidate("VALID")
    too_small = _candidate("SMALL", size_pass=False)
    expensive = _candidate("EXPENSIVE", valuation_pass=False)
    no_momentum = _candidate("NO_MOMENTUM", momentum_pass=False)
    scored = score_candidates([valid, too_small, expensive, no_momentum])
    assert list(scored["symbol"]) == ["VALID"]
    assert scored.iloc[0]["total_score"] == 100.0
    assert sum(WEIGHTS.values()) == 1.0
