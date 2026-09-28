from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from statistics import median

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class TechnicalMetrics:
    observations: int
    last_price: float
    return_3m: float
    return_6m: float
    trend_slope_annualized: float
    price_slope_3d_z: float
    obv_slope_3d_z: float
    swing_price_lower_low: float | None
    swing_obv_delta: float | None
    obv_swing_setup: bool
    obv_swing_3d_pass: bool
    atr20: float
    drawdown_3m: float
    support_level: float | None
    support_distance_pct: float | None
    support_distance_atr: float | None
    support_touches: int
    support_kind: str | None
    support_signal: float
    correction_pass: bool
    support_pass: bool
    ema10: float
    roc5: float
    rsi14: float
    macd_line: float
    macd_signal_line: float
    macd_histogram: float
    prior_high_5d: float
    price_breakout_5d: float
    volume_ratio20: float
    close_location: float
    rsi_failure_swing_low1: float | None
    rsi_failure_swing_peak: float | None
    rsi_failure_swing_low2: float | None
    momentum_price_breakout: bool
    momentum_rsi_failure_swing: bool
    momentum_macd_inflection: bool
    momentum_confirmations: int
    momentum_signal_age: int | None
    momentum_state: str
    momentum_signal: float
    momentum_pass: bool
    bullish_regime_pass: bool
    downtrend_signal: float
    obv_divergence_signal: float
    trend_pass: bool
    obv_pass: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class FundamentalMetrics:
    fcf_ttm: float
    fcf_previous_ttm: float
    fcf_growth: float
    fcf_turnaround: bool
    net_income_ttm: float
    revenue_ttm: float | None
    net_margin_ttm: float | None
    profitable_quarters: int
    fundamental_period_end: str
    fcf_signal: float
    profit_signal: float
    fcf_pass: bool
    profit_pass: bool

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ValuationInputs:
    """Raw values needed by the valuation models.

    Monetary series retain their own currencies because a primary listing and
    the issuer's reporting currency are not always the same (for example, a
    Hong Kong listing can publish accounts in CNY).
    """

    market_cap: float | None
    market_cap_currency: str | None
    market_cap_as_of: str | None
    cash: float | None
    cash_currency: str | None
    total_debt: float | None
    debt_currency: str | None
    fcf_currency: str | None
    net_income_currency: str | None
    revenue_currency: str | None
    annual_fcf: tuple[tuple[str, float], ...]
    annual_diluted_eps: tuple[tuple[str, float], ...]
    annual_enterprise_value: tuple[tuple[str, float], ...]
    annual_enterprise_value_currency: str | None
    annual_revenue: tuple[tuple[str, float], ...]
    annual_revenue_currency: str | None

    def to_dict(self) -> dict:
        return asdict(self)

    def currencies(self) -> set[str]:
        return {
            currency
            for currency in [
                self.market_cap_currency,
                self.cash_currency,
                self.debt_currency,
                self.fcf_currency,
                self.net_income_currency,
                self.revenue_currency,
                self.annual_enterprise_value_currency,
                self.annual_revenue_currency,
            ]
            if currency
        }


@dataclass(frozen=True)
class CompanyMetrics:
    fundamental: FundamentalMetrics
    valuation_inputs: ValuationInputs


@dataclass(frozen=True)
class ValuationMetrics:
    market_cap_local: float | None
    market_cap_currency: str | None
    market_cap_usd: float | None
    market_cap_as_of: str | None
    dcf_fair_value: float | None
    peter_lynch_fair_value: float | None
    ev_sales_fair_value: float | None
    average_fair_value: float | None
    consensus_fair_value: float | None
    margin_of_safety: float | None
    upside_to_fair_value: float | None
    valuation_methods: int
    undervalued_methods: int
    acceptable_methods: int
    size_pass: bool
    valuation_pass: bool
    valuation_signal: float

    def to_dict(self) -> dict:
        return asdict(self)


def _zscore(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    std = float(np.nanstd(values))
    if not np.isfinite(std) or std <= 1e-12:
        return np.zeros_like(values)
    return (values - float(np.nanmean(values))) / std


def _slope(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    x = np.arange(len(values), dtype=float)
    if len(values) < 2 or not np.isfinite(values).all():
        return float("nan")
    return float(np.polyfit(x, values, 1)[0])


def three_session_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily rows into non-overlapping three-trading-session bars."""

    clean = frame[["Close", "Volume"]].dropna().copy()
    complete_rows = (len(clean) // 3) * 3
    if complete_rows < 3:
        return pd.DataFrame(columns=["Close", "Volume"])
    clean = clean.iloc[-complete_rows:]
    groups = np.arange(len(clean)) // 3
    bars = clean.groupby(groups).agg({"Close": "last", "Volume": "sum"})
    bars.index = clean.index[2::3]
    return bars


def three_calendar_day_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate OHLCV into epoch-anchored three-calendar-day bars.

    This matches the common charting interpretation of a 3D timeframe. Empty
    weekend/holiday bins disappear; the current bar may be incomplete.
    """

    if not {"Close", "Volume"}.issubset(frame.columns):
        return pd.DataFrame(columns=["Low", "Close", "Volume"])
    columns = ["Close", "Volume"] + (["Low"] if "Low" in frame else [])
    clean = frame[columns].replace([np.inf, -np.inf], np.nan).dropna(
        subset=["Close", "Volume"]
    ).copy()
    if clean.empty:
        return pd.DataFrame(columns=["Low", "Close", "Volume"])
    if "Low" not in clean:
        clean["Low"] = clean["Close"]

    timezone_name = frame.attrs.get("exchange_timezone")
    index = pd.DatetimeIndex(clean.index)
    if index.tz is not None:
        try:
            local_index = index.tz_convert(timezone_name) if timezone_name else index
        except (TypeError, ValueError):
            local_index = index
        session_dates = local_index.tz_localize(None).normalize()
    else:
        session_dates = index.normalize()
    epoch = pd.Timestamp("1970-01-01")
    clean["_group"] = ((session_dates - epoch).days // 3).astype(int)
    clean["_session_date"] = session_dates
    bars = clean.groupby("_group", sort=True).agg(
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
        SessionDate=("_session_date", "last"),
    )
    bars.index = pd.DatetimeIndex(bars.pop("SessionDate"))
    return bars[["Low", "Close", "Volume"]]


def passes_preliminary_downtrend(
    frame: pd.DataFrame,
    trend_sessions: int = 63,
    price_bars: int = 20,
) -> bool:
    """Cheap, deliberately broad correction gate before OHLCV is requested.

    The exact support test needs lows (and preferably highs), so the close-only
    pre-filter must not require a continuous three-month downtrend.  A symbol is
    retained when it has either lost 5% over 63 sessions or sits at least 12%
    below its 63-session high.
    """

    if "Close" not in frame:
        return False
    close = (
        pd.to_numeric(frame["Close"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if len(close) < max(126, trend_sessions, price_bars * 3) or (close <= 0).any():
        return False
    return_3m = float(close.iloc[-1] / close.iloc[-64] - 1)
    drawdown_3m = float(close.iloc[-1] / close.iloc[-trend_sessions:].max() - 1)
    return bool(return_3m <= -0.05 or drawdown_3m <= -0.12)


def _average_true_range(frame: pd.DataFrame, periods: int = 20) -> float:
    close = frame["Close"].astype(float)
    low = frame.get("Low", close).astype(float)
    high = frame.get("High", pd.concat([close, low], axis=1).max(axis=1)).astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    value = float(true_range.tail(periods).mean())
    return value if np.isfinite(value) and value > 0 else float("nan")


def _support_metrics(
    frame: pd.DataFrame,
    atr20: float,
    pivot_span: int = 5,
) -> tuple[float | None, float | None, float | None, int, str | None, float, bool]:
    """Return the nearest valid historical support or confirmed emerging base.

    Historical supports need two pivot lows at least 15 sessions apart.  The
    emerging-base branch captures a fresh floor without pretending it already
    has a long history: at least three tests in the preceding ten sessions and
    a bullish reclaim are required.  All calculations end at the supplied
    frame, so the function is safe for point-in-time tests.
    """

    if not np.isfinite(atr20) or atr20 <= 0 or len(frame) < 30:
        return None, None, None, 0, None, 0.0, False
    close = frame["Close"].astype(float)
    low = frame.get("Low", close).astype(float)
    high = frame.get("High", pd.concat([close, low], axis=1).max(axis=1)).astype(float)
    current = float(close.iloc[-1])
    band = max(current * 0.02, atr20 * 0.75)

    # A newly formed floor: repeated tests, followed by a close above the zone
    # and above the preceding session's high.  The Salesforce regression
    # fixture in the test suite exercises this branch.
    recent_low = low.iloc[-11:-1]
    if len(recent_low) >= 6:
        floor = float(recent_low.min())
        touched = recent_low[recent_low <= floor + band]
        touch_span = (
            int(recent_low.index.get_loc(touched.index[-1]))
            - int(recent_low.index.get_loc(touched.index[0]))
            if len(touched) >= 2
            else 0
        )
        emerging_level = float(touched.median()) if len(touched) else floor
        emerging_reclaim = bool(
            len(touched) >= 3
            and touch_span >= 3
            and current > floor + band
            and current <= floor + 1.5 * atr20
            and current > float(high.iloc[-2])
            and current > float(close.iloc[-2])
        )
        if emerging_reclaim:
            distance_pct = float(current / emerging_level - 1.0)
            distance_atr = float((current - emerging_level) / atr20)
            signal = float(
                np.clip(1.0 - abs(distance_atr) / 1.5, 0.0, 1.0)
                + 0.5 * min(len(touched) / 4.0, 1.0)
                + 0.75
            )
            return (
                emerging_level,
                distance_pct,
                distance_atr,
                int(len(touched)),
                "emerging_reclaim",
                signal,
                True,
            )

    # Established horizontal levels from confirmed pivot lows.  Current and
    # last five rows are excluded from pivot confirmation to avoid look-ahead.
    pivots: list[tuple[int, float]] = []
    for position in range(pivot_span, len(low) - pivot_span):
        value = float(low.iloc[position])
        if value <= float(low.iloc[position - pivot_span : position + pivot_span + 1].min()):
            pivots.append((position, value))
    clusters: list[list[tuple[int, float]]] = []
    for pivot in pivots:
        matching = next(
            (
                cluster
                for cluster in clusters
                if abs(pivot[1] - float(np.median([value for _, value in cluster]))) <= band
            ),
            None,
        )
        if matching is None:
            clusters.append([pivot])
        else:
            matching.append(pivot)

    candidates: list[tuple[float, list[tuple[int, float]]]] = []
    for cluster in clusters:
        positions = [position for position, _ in cluster]
        if len(cluster) >= 2 and max(positions) - min(positions) >= 15:
            candidates.append((float(np.median([value for _, value in cluster])), cluster))
    candidates.sort(key=lambda item: abs(current - item[0]))
    for level, cluster in candidates:
        distance_atr = float((current - level) / atr20)
        broken_closes = int((close.tail(5) < level - atr20).sum())
        if -0.5 <= distance_atr <= 1.0 and broken_closes < 2:
            distance_pct = float(current / level - 1.0)
            signal = float(
                np.clip(1.0 - abs(distance_atr), 0.0, 1.0)
                + 0.5 * min(len(cluster) / 4.0, 1.0)
            )
            return level, distance_pct, distance_atr, len(cluster), "established", signal, True
    return None, None, None, 0, None, 0.0, False


def _wilder_rsi(close: pd.Series, periods: int = 14) -> pd.Series:
    """Wilder RSI with the original SMA seed and recursive smoothing."""

    delta = close.diff()
    gains = delta.clip(lower=0.0).fillna(0.0)
    losses = (-delta.clip(upper=0.0)).fillna(0.0)
    average_gain = pd.Series(np.nan, index=close.index, dtype=float)
    average_loss = pd.Series(np.nan, index=close.index, dtype=float)
    if len(close) <= periods:
        return average_gain
    average_gain.iloc[periods] = float(gains.iloc[1 : periods + 1].mean())
    average_loss.iloc[periods] = float(losses.iloc[1 : periods + 1].mean())
    for position in range(periods + 1, len(close)):
        average_gain.iloc[position] = (
            average_gain.iloc[position - 1] * (periods - 1) + gains.iloc[position]
        ) / periods
        average_loss.iloc[position] = (
            average_loss.iloc[position - 1] * (periods - 1) + losses.iloc[position]
        ) / periods
    relative_strength = average_gain / average_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + relative_strength)
    rsi = rsi.mask((average_loss == 0.0) & (average_gain > 0.0), 100.0)
    return rsi.mask((average_loss == 0.0) & (average_gain == 0.0), 50.0)


def _rsi_bullish_failure_swing(
    rsi: pd.Series,
    lookback: int = 15,
) -> tuple[bool, float | None, float | None, float | None]:
    """Detect a completed bottom failure swing on the final observation.

    Textbook causal sequence: RSI low at/below 30, rebound above 30, a higher
    second low that remains above 30, then a cross above the rebound peak.
    """

    values = rsi.to_numpy(dtype=float)
    end = len(values) - 1
    start = max(1, end - lookback)
    if end < 5 or not np.isfinite(values[end]):
        return False, None, None, None
    for low2 in range(end - 1, start + 2, -1):
        if not (
            np.isfinite(values[low2 - 1 : low2 + 2]).all()
            and values[low2] <= values[low2 - 1]
            and values[low2] < values[low2 + 1]
            and values[low2] > 30.0
        ):
            continue
        for peak in range(low2 - 1, start + 1, -1):
            if not (
                np.isfinite(values[peak - 1 : peak + 2]).all()
                and values[peak] >= values[peak - 1]
                and values[peak] > values[peak + 1]
                and values[peak] > 30.0
            ):
                continue
            for low1 in range(peak - 1, start - 1, -1):
                if not (
                    np.isfinite(values[low1 - 1 : low1 + 2]).all()
                    and values[low1] <= 30.0
                    and values[low1] <= values[low1 - 1]
                    and values[low1] < values[low1 + 1]
                    and values[low2] > values[low1]
                    and values[end - 1] <= values[peak]
                    and values[end] > values[peak]
                ):
                    continue
                if values[peak] >= float(np.nanmax(values[low1 + 1 : low2])):
                    return (
                        True,
                        float(values[low1]),
                        float(values[peak]),
                        float(values[low2]),
                    )
    return False, None, None, None


def _bullish_momentum_metrics(
    frame: pd.DataFrame,
    atr20: float,
) -> tuple:
    """Detect an early bullish turn without calling it a mature bull regime.

    The gate requires a completed RSI(14) bottom failure swing and a close
    above the previous five-session high. A separate strict regime flag
    requires RSI >= 50, MACD above its signal and a rising EMA20.
    """

    close = frame["Close"].astype(float)
    high = frame.get("High", close).astype(float)
    low = frame.get("Low", close).astype(float)
    volume = frame["Volume"].astype(float)
    ema10_series = close.ewm(span=10, adjust=False).mean()
    ema20_series = close.ewm(span=20, adjust=False).mean()
    roc5_series = close.pct_change(5)
    rsi = _wilder_rsi(close, 14)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal

    ema10 = float(ema10_series.iloc[-1])
    roc5 = float(roc5_series.iloc[-1])
    rsi14 = float(rsi.iloc[-1])
    macd_line = float(macd.iloc[-1])
    macd_signal_line = float(signal.iloc[-1])
    macd_histogram = float(histogram.iloc[-1])

    prior_high_5d = float(high.iloc[-6:-1].max())
    price_breakout_5d = float(close.iloc[-1] / prior_high_5d - 1.0)
    volume_ratio20 = float(
        volume.iloc[-1] / max(float(volume.iloc[-21:-1].median()), 1.0)
    )
    daily_range = float(high.iloc[-1] - low.iloc[-1])
    close_location = float(
        (close.iloc[-1] - low.iloc[-1]) / daily_range if daily_range > 0 else 0.5
    )
    failure_swing = False
    failure_low1 = failure_peak = failure_low2 = None
    price_breakout = False
    signal_age = None
    # Keep a completed signal alive for up to five sessions, provided price
    # remains above its breakout level and RSI above the higher second low.
    # This lets a weekly scan observe a still-valid reversal without look-ahead.
    end = len(close) - 1
    for signal_position in range(end, max(0, end - 5), -1):
        candidate_rsi = rsi.iloc[: signal_position + 1]
        candidate_failure, low1, peak, low2 = _rsi_bullish_failure_swing(candidate_rsi)
        breakout_level = float(high.iloc[signal_position - 5 : signal_position].max())
        candidate_breakout = bool(close.iloc[signal_position] > breakout_level)
        still_valid = bool(
            close.iloc[-1] >= breakout_level
            and low2 is not None
            and rsi.iloc[-1] > low2
        )
        if candidate_failure and candidate_breakout and still_valid:
            failure_swing = True
            failure_low1, failure_peak, failure_low2 = low1, peak, low2
            price_breakout = True
            signal_age = end - signal_position
            prior_high_5d = breakout_level
            price_breakout_5d = float(
                close.iloc[signal_position] / breakout_level - 1.0
            )
            prior_volume = max(
                float(volume.iloc[max(0, signal_position - 20) : signal_position].median()),
                1.0,
            )
            volume_ratio20 = float(volume.iloc[signal_position] / prior_volume)
            signal_range = float(high.iloc[signal_position] - low.iloc[signal_position])
            close_location = float(
                (close.iloc[signal_position] - low.iloc[signal_position]) / signal_range
                if signal_range > 0
                else 0.5
            )
            break
    macd_differences = histogram.diff().tail(3)
    macd_inflection = bool(
        len(macd_differences) == 3
        and macd_differences.notna().all()
        and (macd_differences > 0).all()
    )
    confirmations = int(price_breakout) + int(failure_swing)
    momentum_pass = bool(price_breakout and failure_swing)
    bullish_regime = bool(
        rsi14 >= 50.0
        and macd_line > macd_signal_line
        and close.iloc[-1] > ema20_series.iloc[-1]
        and ema20_series.iloc[-1] > ema20_series.iloc[-2]
    )
    momentum_state = (
        "bullish_trend_established"
        if bullish_regime
        else "bullish_reversal_confirmed"
        if momentum_pass
        else "turning"
        if macd_inflection or (rsi14 > 30.0 and float(rsi.iloc[-6:-1].min()) <= 30.0)
        else "bearish_or_unconfirmed"
    )

    atr_scale = max(atr20 if np.isfinite(atr20) else 0.0, float(close.iloc[-1]) * 0.01)
    histogram_improvement = float(histogram.iloc[-1] - histogram.iloc[-4])
    momentum_signal = float(
        float(failure_swing)
        + np.clip(price_breakout_5d / 0.03, 0.0, 1.0)
        + 0.35 * np.clip(volume_ratio20 / 1.5, 0.0, 1.0)
        + 0.25 * np.clip(close_location, 0.0, 1.0)
        + np.clip(histogram_improvement / atr_scale, 0.0, 1.0)
    )
    return (
        ema10,
        roc5,
        rsi14,
        macd_line,
        macd_signal_line,
        macd_histogram,
        prior_high_5d,
        price_breakout_5d,
        volume_ratio20,
        close_location,
        failure_low1,
        failure_peak,
        failure_low2,
        price_breakout,
        failure_swing,
        macd_inflection,
        confirmations,
        signal_age,
        momentum_state,
        momentum_signal,
        momentum_pass,
        bullish_regime,
    )


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume.fillna(0.0)).cumsum()


def calculate_technical_metrics(
    frame: pd.DataFrame,
    trend_sessions: int = 63,
    obv_bars: int = 20,
) -> TechnicalMetrics | None:
    columns = ["Close", "Volume"] + [
        column for column in ["High", "Low"] if column in frame
    ]
    clean = frame[columns].replace([np.inf, -np.inf], np.nan).dropna(
        subset=["Close", "Volume"]
    )
    clean.attrs.update(frame.attrs)
    min_rows = max(126, trend_sessions, obv_bars * 3)
    if len(clean) < min_rows or (clean["Close"] <= 0).any():
        return None

    close = clean["Close"].astype(float)
    log_window = np.log(close.iloc[-trend_sessions:].to_numpy())
    daily_log_slope = _slope(log_window)
    annualized_slope = float(np.expm1(daily_log_slope * 252))
    return_3m = float(close.iloc[-1] / close.iloc[-64] - 1)
    return_6m = float(close.iloc[-1] / close.iloc[-126] - 1)
    drawdown_3m = float(close.iloc[-1] / close.iloc[-trend_sessions:].max() - 1)
    atr20 = _average_true_range(clean)
    (
        support_level,
        support_distance_pct,
        support_distance_atr,
        support_touches,
        support_kind,
        support_signal,
        support_pass,
    ) = _support_metrics(clean, atr20)
    (
        ema10,
        roc5,
        rsi14,
        macd_line,
        macd_signal_line,
        macd_histogram,
        prior_high_5d,
        price_breakout_5d,
        volume_ratio20,
        close_location,
        rsi_failure_swing_low1,
        rsi_failure_swing_peak,
        rsi_failure_swing_low2,
        momentum_price_breakout,
        momentum_rsi_failure_swing,
        momentum_macd_inflection,
        momentum_confirmations,
        momentum_signal_age,
        momentum_state,
        momentum_signal,
        momentum_pass,
        bullish_regime_pass,
    ) = _bullish_momentum_metrics(clean, atr20)

    bars = three_calendar_day_bars(clean).tail(obv_bars)
    if len(bars) < obv_bars:
        return None
    obv = on_balance_volume(bars["Close"], bars["Volume"])
    price_slope_z = _slope(_zscore(bars["Close"].to_numpy()))
    obv_slope_z = _slope(_zscore(obv.to_numpy()))

    swing_price_lower_low = None
    swing_obv_delta = None
    swing_setup = False
    swing_pass = False
    if len(bars) >= 3:
        prior, lower, confirmation = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
        prior_obv, lower_obv, confirmation_obv = obv.iloc[-3:]
        if prior["Low"] > 0:
            swing_price_lower_low = float(lower["Low"] / prior["Low"] - 1.0)
        volume_scale = max(float(bars["Volume"].tail(10).median()), 1.0)
        swing_obv_delta = float((lower_obv - prior_obv) / volume_scale)
        swing_setup = bool(
            lower_obv > prior_obv
            and confirmation["Close"] > lower["Close"]
            and confirmation_obv > lower_obv
        )
        swing_pass = bool(lower["Low"] < prior["Low"] and swing_setup)

    trend_pass = bool(return_3m < 0 and annualized_slope < 0 and price_slope_z < 0)
    correction_pass = bool(return_3m <= -0.05 or drawdown_3m <= -0.12)
    obv_pass = bool(obv_slope_z > 0 or swing_pass)
    downtrend_signal = float(
        0.65 * np.clip(-annualized_slope, 0, 0.75)
        + 0.35 * np.clip(-return_3m, 0, 0.40)
    )
    obv_divergence_signal = float(
        max(0.0, obv_slope_z) + 0.35 * max(0.0, -price_slope_z)
        + (0.75 + max(0.0, swing_obv_delta or 0.0) if swing_pass else 0.0)
    )
    return TechnicalMetrics(
        observations=len(clean),
        last_price=float(close.iloc[-1]),
        return_3m=return_3m,
        return_6m=return_6m,
        trend_slope_annualized=annualized_slope,
        price_slope_3d_z=price_slope_z,
        obv_slope_3d_z=obv_slope_z,
        swing_price_lower_low=swing_price_lower_low,
        swing_obv_delta=swing_obv_delta,
        obv_swing_setup=swing_setup,
        obv_swing_3d_pass=swing_pass,
        atr20=atr20,
        drawdown_3m=drawdown_3m,
        support_level=support_level,
        support_distance_pct=support_distance_pct,
        support_distance_atr=support_distance_atr,
        support_touches=support_touches,
        support_kind=support_kind,
        support_signal=support_signal,
        correction_pass=correction_pass,
        support_pass=support_pass,
        ema10=ema10,
        roc5=roc5,
        rsi14=rsi14,
        macd_line=macd_line,
        macd_signal_line=macd_signal_line,
        macd_histogram=macd_histogram,
        prior_high_5d=prior_high_5d,
        price_breakout_5d=price_breakout_5d,
        volume_ratio20=volume_ratio20,
        close_location=close_location,
        rsi_failure_swing_low1=rsi_failure_swing_low1,
        rsi_failure_swing_peak=rsi_failure_swing_peak,
        rsi_failure_swing_low2=rsi_failure_swing_low2,
        momentum_price_breakout=momentum_price_breakout,
        momentum_rsi_failure_swing=momentum_rsi_failure_swing,
        momentum_macd_inflection=momentum_macd_inflection,
        momentum_confirmations=momentum_confirmations,
        momentum_signal_age=momentum_signal_age,
        momentum_state=momentum_state,
        momentum_signal=momentum_signal,
        momentum_pass=momentum_pass,
        bullish_regime_pass=bullish_regime_pass,
        downtrend_signal=downtrend_signal,
        obv_divergence_signal=obv_divergence_signal,
        trend_pass=trend_pass,
        obv_pass=obv_pass,
    )


def _find_row(statement: pd.DataFrame, names: list[str]) -> pd.Series | None:
    if statement is None or statement.empty:
        return None
    normalized = {re.sub(r"[^a-z0-9]", "", str(index).lower()): index for index in statement.index}
    for name in names:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        if key in normalized:
            row = statement.loc[normalized[key]]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            return pd.to_numeric(row, errors="coerce")
    return None


def _recent_values(row: pd.Series | None, count: int) -> tuple[list[float], list]:
    if row is None:
        return [], []
    pairs = [(column, row[column]) for column in row.index if pd.notna(row[column])]
    pairs.sort(key=lambda pair: pd.Timestamp(pair[0]), reverse=True)
    values = [float(value) for _, value in pairs[:count]]
    columns = [column for column, _ in pairs[:count]]
    return values, columns


def calculate_fundamental_metrics(
    quarterly_cashflow: pd.DataFrame,
    quarterly_income: pd.DataFrame,
) -> FundamentalMetrics | None:
    free_cash_flow = _find_row(quarterly_cashflow, ["Free Cash Flow", "FreeCashFlow"])
    if free_cash_flow is None:
        operating = _find_row(
            quarterly_cashflow,
            ["Operating Cash Flow", "Total Cash From Operating Activities"],
        )
        capex = _find_row(quarterly_cashflow, ["Capital Expenditure", "Capital Expenditures"])
        if operating is None or capex is None:
            return None
        free_cash_flow = operating.add(capex, fill_value=np.nan)

    fcf_values, fcf_dates = _recent_values(free_cash_flow, 5)
    net_income = _find_row(
        quarterly_income,
        ["Net Income", "Net Income Common Stockholders", "Net Income Applicable To Common Shares"],
    )
    net_values, net_dates = _recent_values(net_income, 4)
    revenue = _find_row(quarterly_income, ["Total Revenue", "Operating Revenue"])
    revenue_values, _ = _recent_values(revenue, 4)

    if len(fcf_values) < 5 or len(net_values) < 4:
        return None
    fcf_ttm = float(sum(fcf_values[:4]))
    fcf_previous_ttm = float(sum(fcf_values[1:5]))
    net_income_ttm = float(sum(net_values[:4]))
    revenue_ttm = float(sum(revenue_values[:4])) if len(revenue_values) >= 4 else None
    net_margin = (
        net_income_ttm / revenue_ttm
        if revenue_ttm is not None and revenue_ttm > 0
        else None
    )
    denominator = max(abs(fcf_previous_ttm), abs(fcf_ttm) * 0.01, 1.0)
    fcf_growth = float((fcf_ttm - fcf_previous_ttm) / denominator)
    turnaround = bool(fcf_previous_ttm <= 0 < fcf_ttm)
    profitable_quarters = sum(value > 0 for value in net_values[:4])
    fcf_pass = bool(fcf_ttm > 0 and fcf_ttm > fcf_previous_ttm)
    profit_pass = bool(net_income_ttm > 0)
    fcf_signal = float(np.clip(fcf_growth, 0, 3.0))
    margin_component = np.clip(net_margin or 0.0, 0, 0.35) / 0.35
    profit_signal = float(0.75 * margin_component + 0.25 * profitable_quarters / 4)
    latest_date = max([*fcf_dates, *net_dates], key=pd.Timestamp)

    return FundamentalMetrics(
        fcf_ttm=fcf_ttm,
        fcf_previous_ttm=fcf_previous_ttm,
        fcf_growth=fcf_growth,
        fcf_turnaround=turnaround,
        net_income_ttm=net_income_ttm,
        revenue_ttm=revenue_ttm,
        net_margin_ttm=net_margin,
        profitable_quarters=profitable_quarters,
        fundamental_period_end=str(pd.Timestamp(latest_date).date()),
        fcf_signal=fcf_signal,
        profit_signal=profit_signal,
        fcf_pass=fcf_pass,
        profit_pass=profit_pass,
    )


def _positive_growth_median(
    dated_values: tuple[tuple[str, float], ...],
) -> float | None:
    """Median year-on-year growth, using only positive comparable bases."""

    if len(dated_values) < 2:
        return None
    ordered = sorted(dated_values, key=lambda item: pd.Timestamp(item[0]))
    growth = []
    for (_, previous), (_, current) in zip(ordered, ordered[1:]):
        if previous > 0 and current > 0:
            growth.append(current / previous - 1.0)
    return float(median(growth)) if growth else None


def _fx_value(
    value: float | None,
    currency: str | None,
    fx_to_usd: dict[str, float],
) -> float | None:
    if value is None or not currency:
        return None
    rate = fx_to_usd.get(currency.upper())
    if rate is None or not np.isfinite(rate) or rate <= 0:
        return None
    converted = float(value) * float(rate)
    return converted if np.isfinite(converted) else None


def _fair_price_from_equity_value(
    current_price: float,
    fair_equity_value_usd: float | None,
    market_cap_usd: float | None,
) -> float | None:
    """Scale the listing price by fair/current total equity value.

    This ratio-based conversion remains valid for ADRs, share classes and
    price-unit conventions without assuming a one-to-one diluted-share ratio.
    """

    if (
        fair_equity_value_usd is None
        or market_cap_usd is None
        or fair_equity_value_usd <= 0
        or market_cap_usd <= 0
        or current_price <= 0
    ):
        return None
    value = current_price * fair_equity_value_usd / market_cap_usd
    return float(value) if np.isfinite(value) and value > 0 else None


def _dcf_equity_value(
    fcf_ttm_usd: float | None,
    annual_fcf: tuple[tuple[str, float], ...],
    current_fcf_growth: float,
    discount_rate: float,
    terminal_growth: float,
    years: int = 5,
) -> float | None:
    if (
        fcf_ttm_usd is None
        or fcf_ttm_usd <= 0
        or years < 1
        or discount_rate <= terminal_growth
        or discount_rate <= 0
    ):
        return None
    recent_annual = sorted(
        annual_fcf, key=lambda item: pd.Timestamp(item[0]), reverse=True
    )[:3]
    if len(recent_annual) < 3 or sum(value > 0 for _, value in recent_annual) < 2:
        return None
    historical_growth = _positive_growth_median(annual_fcf)
    growth_candidates = [
        value
        for value in [historical_growth, current_fcf_growth]
        if value is not None and np.isfinite(value)
    ]
    if not growth_candidates:
        return None
    initial_growth = float(np.clip(median(growth_candidates), 0.0, 0.12))
    cash_flow = float(fcf_ttm_usd)
    present_value = 0.0
    for year in range(1, years + 1):
        fade = year / years
        growth = initial_growth * (1.0 - fade) + terminal_growth * fade
        cash_flow *= 1.0 + growth
        present_value += cash_flow / (1.0 + discount_rate) ** year
    terminal_value = cash_flow * (1.0 + terminal_growth) / (
        discount_rate - terminal_growth
    )
    present_value += terminal_value / (1.0 + discount_rate) ** years
    return float(present_value) if np.isfinite(present_value) and present_value > 0 else None


def _peter_lynch_equity_value(
    net_income_ttm_usd: float | None,
    annual_diluted_eps: tuple[tuple[str, float], ...],
) -> float | None:
    if net_income_ttm_usd is None or net_income_ttm_usd <= 0:
        return None
    eps_growth = _positive_growth_median(annual_diluted_eps)
    if eps_growth is None or not np.isfinite(eps_growth) or eps_growth <= 0:
        return None
    fair_pe = float(np.clip(eps_growth * 100.0, 5.0, 20.0))
    value = net_income_ttm_usd * fair_pe
    return float(value) if np.isfinite(value) and value > 0 else None


def _historical_ev_sales_multiple(
    enterprise_values: tuple[tuple[str, float], ...],
    enterprise_currency: str | None,
    revenues: tuple[tuple[str, float], ...],
    revenue_currency: str | None,
    fx_to_usd: dict[str, float],
) -> float | None:
    if not enterprise_currency or not revenue_currency:
        return None
    ev_by_year = {pd.Timestamp(date).year: value for date, value in enterprise_values}
    revenue_by_year = {pd.Timestamp(date).year: value for date, value in revenues}
    ratios = []
    for year in sorted(set(ev_by_year) & set(revenue_by_year)):
        ev_usd = _fx_value(ev_by_year[year], enterprise_currency, fx_to_usd)
        revenue_usd = _fx_value(revenue_by_year[year], revenue_currency, fx_to_usd)
        if ev_usd is not None and revenue_usd is not None and revenue_usd > 0:
            ratio = ev_usd / revenue_usd
            if 0.1 <= ratio <= 30.0:
                ratios.append(ratio)
    if len(ratios) < 2:
        return None
    return float(median(ratios))


def fair_value_within_tolerance(
    current_price: float,
    fair_value: float | None,
    max_overvaluation: float = 0.15,
) -> bool:
    """Return whether price is no more than ``max_overvaluation`` above fair value.

    A stock trading at or below fair value always passes.  When it trades above
    fair value, overvaluation is measured as ``current_price / fair_value - 1``.
    """

    if max_overvaluation < 0:
        raise ValueError("max_overvaluation cannot be negative")
    if (
        fair_value is None
        or not np.isfinite(current_price)
        or not np.isfinite(fair_value)
        or current_price <= 0
        or fair_value <= 0
    ):
        return False
    overvaluation = current_price / fair_value - 1.0
    return bool(overvaluation <= max_overvaluation + 1e-12)


def calculate_valuation_metrics(
    current_price: float,
    fundamental: FundamentalMetrics,
    inputs: ValuationInputs,
    fx_to_usd: dict[str, float],
    min_market_cap_usd: float = 10_000_000_000.0,
    max_overvaluation: float = 0.15,
    min_methods: int = 2,
    min_confirmations: int = 2,
    dcf_discount_rate: float = 0.10,
    dcf_terminal_growth: float = 0.025,
) -> ValuationMetrics:
    """Estimate fair value with DCF, Peter Lynch and historical EV/Sales.

    The arithmetic mean is reported for comparison only.  The hard
    filter requires independent model confirmations: undervalued estimates
    always pass, while an overvalued estimate passes only when price is no more
    than ``max_overvaluation`` above its fair value.
    """

    market_cap_usd = _fx_value(
        inputs.market_cap, inputs.market_cap_currency, fx_to_usd
    )
    fcf_ttm_usd = _fx_value(
        fundamental.fcf_ttm, inputs.fcf_currency, fx_to_usd
    )
    net_income_ttm_usd = _fx_value(
        fundamental.net_income_ttm, inputs.net_income_currency, fx_to_usd
    )
    revenue_ttm_usd = _fx_value(
        fundamental.revenue_ttm, inputs.revenue_currency, fx_to_usd
    )
    cash_usd = _fx_value(inputs.cash, inputs.cash_currency, fx_to_usd)
    debt_usd = _fx_value(inputs.total_debt, inputs.debt_currency, fx_to_usd)

    annual_fcf_usd = tuple(
        (date, converted)
        for date, value in inputs.annual_fcf
        if (converted := _fx_value(value, inputs.fcf_currency, fx_to_usd)) is not None
    )
    dcf_equity = _dcf_equity_value(
        fcf_ttm_usd,
        annual_fcf_usd,
        fundamental.fcf_growth,
        dcf_discount_rate,
        dcf_terminal_growth,
    )
    lynch_equity = _peter_lynch_equity_value(
        net_income_ttm_usd, inputs.annual_diluted_eps
    )
    ev_sales_multiple = _historical_ev_sales_multiple(
        inputs.annual_enterprise_value,
        inputs.annual_enterprise_value_currency,
        inputs.annual_revenue,
        inputs.annual_revenue_currency,
        fx_to_usd,
    )
    ev_sales_equity = None
    if (
        ev_sales_multiple is not None
        and revenue_ttm_usd is not None
        and revenue_ttm_usd > 0
        and cash_usd is not None
        and debt_usd is not None
    ):
        ev_sales_equity = ev_sales_multiple * revenue_ttm_usd - debt_usd + cash_usd
        if not np.isfinite(ev_sales_equity) or ev_sales_equity <= 0:
            ev_sales_equity = None

    fair_values = [
        _fair_price_from_equity_value(current_price, dcf_equity, market_cap_usd),
        _fair_price_from_equity_value(current_price, lynch_equity, market_cap_usd),
        _fair_price_from_equity_value(
            current_price, ev_sales_equity, market_cap_usd
        ),
    ]
    valid_values = [value for value in fair_values if value is not None]
    average_fair_value = float(np.mean(valid_values)) if valid_values else None
    consensus_fair_value = float(median(valid_values)) if valid_values else None
    methods_available = len(valid_values)
    undervalued_methods = sum(value > current_price for value in valid_values)
    acceptable_methods = sum(
        fair_value_within_tolerance(current_price, value, max_overvaluation)
        for value in valid_values
    )
    margin_of_safety = (
        (consensus_fair_value - current_price) / consensus_fair_value
        if consensus_fair_value is not None and consensus_fair_value > 0
        else None
    )
    upside = (
        consensus_fair_value / current_price - 1.0
        if consensus_fair_value is not None and current_price > 0
        else None
    )
    size_pass = bool(
        market_cap_usd is not None and market_cap_usd >= min_market_cap_usd
    )
    valuation_pass = bool(
        methods_available >= min_methods
        and acceptable_methods >= min_confirmations
    )
    minimum_acceptable_upside = 1.0 / (1.0 + max_overvaluation) - 1.0
    valuation_signal = float(
        np.log1p(max(0.0, (upside or 0.0) - minimum_acceptable_upside))
        * (acceptable_methods / max(1, methods_available))
    )
    return ValuationMetrics(
        market_cap_local=inputs.market_cap,
        market_cap_currency=inputs.market_cap_currency,
        market_cap_usd=market_cap_usd,
        market_cap_as_of=inputs.market_cap_as_of,
        dcf_fair_value=fair_values[0],
        peter_lynch_fair_value=fair_values[1],
        ev_sales_fair_value=fair_values[2],
        average_fair_value=average_fair_value,
        consensus_fair_value=consensus_fair_value,
        margin_of_safety=margin_of_safety,
        upside_to_fair_value=upside,
        valuation_methods=methods_available,
        undervalued_methods=undervalued_methods,
        acceptable_methods=acceptable_methods,
        size_pass=size_pass,
        valuation_pass=valuation_pass,
        valuation_signal=valuation_signal,
    )
