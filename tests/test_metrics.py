import numpy as np
import pandas as pd
from pathlib import Path

from reversal_screener.metrics import (
    ValuationInputs,
    calculate_fundamental_metrics,
    calculate_technical_metrics,
    calculate_valuation_metrics,
    fair_value_within_tolerance,
    on_balance_volume,
    passes_preliminary_downtrend,
    three_calendar_day_bars,
    three_session_bars,
)
from reversal_screener.universe import to_yahoo_symbol


def test_country_symbol_mapping():
    assert to_yahoo_symbol("700", "HK") == "0700.HK"
    assert to_yahoo_symbol("BRK/B", "US") == "BRK-B"
    assert to_yahoo_symbol("7203", "JP") == "7203.T"
    assert to_yahoo_symbol("600519", "CN") == "600519.SS"
    assert to_yahoo_symbol("000001", "CN") == "000001.SZ"


def test_three_session_bars_uses_trading_rows():
    index = pd.bdate_range("2026-01-01", periods=7)
    frame = pd.DataFrame(
        {"Close": np.arange(1, 8, dtype=float), "Volume": np.ones(7)}, index=index
    )
    bars = three_session_bars(frame)
    assert list(bars["Close"]) == [4.0, 7.0]
    assert list(bars["Volume"]) == [3.0, 3.0]


def test_obv_and_price_diverge():
    # Six months down gently. Three-session upticks carry much more volume.
    bar_count = 60
    bar_close = np.array(
        [120 - 0.5 * index + (1.2 if index % 2 else 0) for index in range(bar_count)]
    )
    bar_volume = np.array([12.0 if index % 2 else 1.0 for index in range(bar_count)])
    n = bar_count * 3
    index = pd.bdate_range("2025-01-01", periods=n)
    close = np.repeat(bar_close, 3) + np.tile([-0.1, 0.0, 0.1], bar_count)
    volume = np.repeat(bar_volume * 1_000_000 / 3, 3)
    frame = pd.DataFrame({"Close": close, "Volume": volume}, index=index)
    metrics = calculate_technical_metrics(frame)
    assert metrics is not None
    assert metrics.trend_pass
    assert metrics.obv_pass
    assert metrics.price_slope_3d_z < 0
    assert metrics.obv_slope_3d_z > 0
    assert passes_preliminary_downtrend(frame[["Close"]])


def test_fundamentals_use_overlapping_ttm_windows():
    dates = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
    cashflow = pd.DataFrame(
        [[40.0, 30.0, 25.0, 20.0, 10.0]], index=["Free Cash Flow"], columns=dates
    )
    income = pd.DataFrame(
        [
            [20.0, 18.0, 16.0, 14.0, 12.0],
            [100.0, 100.0, 100.0, 100.0, 100.0],
        ],
        index=["Net Income", "Total Revenue"],
        columns=dates,
    )
    metrics = calculate_fundamental_metrics(cashflow, income)
    assert metrics is not None
    assert metrics.fcf_ttm == 115.0
    assert metrics.fcf_previous_ttm == 85.0
    assert metrics.fcf_pass
    assert metrics.profit_pass
    assert metrics.profitable_quarters == 4
    assert round(metrics.net_margin_ttm, 4) == 0.17


def test_calendar_3d_swing_divergence_matches_crm_reference():
    fixture = Path(__file__).parent / "fixtures" / "crm_2025-12-01_2026-06-26.csv"
    frame = pd.read_csv(fixture, parse_dates=["Date"]).set_index("Date")
    frame.attrs["exchange_timezone"] = "America/New_York"

    # The signal must not appear before the June 26 confirmation bar.
    for cutoff in ["2026-06-23", "2026-06-24", "2026-06-25"]:
        earlier = frame.loc[:cutoff].copy()
        earlier.attrs.update(frame.attrs)
        earlier_metrics = calculate_technical_metrics(earlier)
        assert earlier_metrics is not None
        assert not earlier_metrics.momentum_pass

    bars = three_calendar_day_bars(frame)
    assert list(bars.tail(3).index.strftime("%Y-%m-%d")) == [
        "2026-06-18",
        "2026-06-23",
        "2026-06-26",
    ]
    metrics = calculate_technical_metrics(frame)
    assert metrics is not None
    assert metrics.trend_pass
    assert metrics.correction_pass
    assert metrics.support_pass
    assert metrics.support_kind == "emerging_reclaim"
    assert metrics.support_touches == 5
    assert round(metrics.support_level, 2) == 149.80
    assert round(metrics.support_distance_atr, 2) == 0.99
    assert round(metrics.roc5, 4) == 0.0434
    assert round(metrics.rsi14, 2) == 41.25
    assert round(metrics.macd_histogram, 2) == -1.59
    assert round(metrics.rsi_failure_swing_low1, 3) == 29.851
    assert round(metrics.rsi_failure_swing_peak, 3) == 33.733
    assert round(metrics.rsi_failure_swing_low2, 3) == 31.767
    assert round(metrics.prior_high_5d, 2) == 157.06
    assert round(metrics.price_breakout_5d, 4) == 0.0083
    assert round(metrics.volume_ratio20, 2) == 1.34
    assert round(metrics.close_location, 3) == 0.987
    assert metrics.momentum_price_breakout
    assert metrics.momentum_rsi_failure_swing
    assert metrics.momentum_macd_inflection
    assert metrics.momentum_confirmations == 2
    assert metrics.momentum_signal_age == 0
    assert metrics.momentum_state == "bullish_reversal_confirmed"
    assert metrics.momentum_pass
    assert not metrics.bullish_regime_pass
    assert metrics.obv_swing_3d_pass
    assert metrics.obv_pass
    assert round(metrics.swing_price_lower_low, 4) == -0.0232
    assert round(metrics.return_3m, 4) == -0.1469


def test_broken_established_support_does_not_pass():
    index = pd.bdate_range("2025-01-01", periods=150)
    close = np.full(150, 105.0)
    low = np.full(150, 103.0)
    high = np.full(150, 107.0)
    # Two well-separated reactions around 100 create a historical support.
    for position in [55, 90]:
        low[position] = 100.0
        close[position] = 102.0
    # A decisive multi-session break must invalidate it.
    close[-8:] = np.linspace(98.0, 90.0, 8)
    low[-8:] = close[-8:] - 2.0
    high[-8:] = close[-8:] + 2.0
    volume = np.full(150, 1_000_000.0)
    frame = pd.DataFrame(
        {"High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )
    metrics = calculate_technical_metrics(frame)
    assert metrics is not None
    assert metrics.correction_pass
    assert not metrics.support_pass


def test_one_day_bounce_without_broad_confirmation_is_not_bullish_momentum():
    index = pd.bdate_range("2025-01-01", periods=150)
    close = np.linspace(130.0, 100.0, 150)
    close[-1] = 101.0
    frame = pd.DataFrame(
        {
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(150, 1_000_000.0),
        },
        index=index,
    )
    metrics = calculate_technical_metrics(frame)
    assert metrics is not None
    assert not metrics.momentum_pass
    assert not metrics.bullish_regime_pass


def test_multi_method_valuation_and_size_are_hard_gates():
    dates = pd.to_datetime(
        ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
    )
    cashflow = pd.DataFrame(
        [[40.0, 30.0, 25.0, 20.0, 10.0]],
        index=["Free Cash Flow"],
        columns=dates,
    )
    income = pd.DataFrame(
        [[20.0, 18.0, 16.0, 14.0, 12.0], [100.0] * 5],
        index=["Net Income", "Total Revenue"],
        columns=dates,
    )
    fundamental = calculate_fundamental_metrics(cashflow, income)
    assert fundamental is not None
    inputs = ValuationInputs(
        market_cap=500.0,
        market_cap_currency="USD",
        market_cap_as_of="2026-06-30",
        cash=100.0,
        cash_currency="USD",
        total_debt=200.0,
        debt_currency="USD",
        fcf_currency="USD",
        net_income_currency="USD",
        revenue_currency="USD",
        annual_fcf=(("2025-12-31", 100.0), ("2024-12-31", 90.0), ("2023-12-31", 80.0)),
        annual_diluted_eps=(("2025-12-31", 5.0), ("2024-12-31", 4.4), ("2023-12-31", 4.0)),
        annual_enterprise_value=(("2025-12-31", 1_100.0), ("2024-12-31", 1_000.0), ("2023-12-31", 900.0)),
        annual_enterprise_value_currency="USD",
        annual_revenue=(("2025-12-31", 400.0), ("2024-12-31", 380.0), ("2023-12-31", 350.0)),
        annual_revenue_currency="USD",
    )
    valuation = calculate_valuation_metrics(
        10.0,
        fundamental,
        inputs,
        {"USD": 1.0},
        min_market_cap_usd=100.0,
        max_overvaluation=0.15,
    )
    assert valuation.size_pass
    assert valuation.valuation_methods == 3
    assert valuation.undervalued_methods >= 2
    assert valuation.acceptable_methods >= 2
    assert valuation.valuation_pass
    assert valuation.consensus_fair_value is not None

    too_small = calculate_valuation_metrics(
        10.0,
        fundamental,
        inputs,
        {"USD": 1.0},
        min_market_cap_usd=1_000.0,
        max_overvaluation=0.15,
    )
    assert not too_small.size_pass


def test_fair_value_tolerance_accepts_undervaluation_and_caps_overvaluation():
    assert fair_value_within_tolerance(100.0, 120.0, 0.15)
    assert fair_value_within_tolerance(100.0, 100.0, 0.15)
    assert fair_value_within_tolerance(115.0, 100.0, 0.15)
    assert not fair_value_within_tolerance(115.01, 100.0, 0.15)
    assert not fair_value_within_tolerance(100.0, None, 0.15)


def test_dcf_is_unavailable_with_persistently_negative_annual_fcf():
    dates = pd.to_datetime(
        ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"]
    )
    fundamental = calculate_fundamental_metrics(
        pd.DataFrame([[40.0, 30.0, 25.0, 20.0, 10.0]], index=["Free Cash Flow"], columns=dates),
        pd.DataFrame([[20.0] * 5, [100.0] * 5], index=["Net Income", "Total Revenue"], columns=dates),
    )
    assert fundamental is not None
    inputs = ValuationInputs(
        market_cap=500.0,
        market_cap_currency="USD",
        market_cap_as_of="2026-06-30",
        cash=100.0,
        cash_currency="USD",
        total_debt=200.0,
        debt_currency="USD",
        fcf_currency="USD",
        net_income_currency="USD",
        revenue_currency="USD",
        annual_fcf=(("2025-12-31", -30.0), ("2024-12-31", -20.0), ("2023-12-31", 10.0)),
        annual_diluted_eps=(("2025-12-31", 5.0), ("2024-12-31", 4.0), ("2023-12-31", 3.0)),
        annual_enterprise_value=(("2025-12-31", 1_100.0), ("2024-12-31", 1_000.0)),
        annual_enterprise_value_currency="USD",
        annual_revenue=(("2025-12-31", 400.0), ("2024-12-31", 380.0)),
        annual_revenue_currency="USD",
    )
    valuation = calculate_valuation_metrics(
        10.0, fundamental, inputs, {"USD": 1.0}, min_market_cap_usd=100.0
    )
    assert valuation.dcf_fair_value is None
