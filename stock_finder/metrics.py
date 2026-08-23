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
    """Cheap price-only gate used before requesting per-symbol volume history."""

    if "Close" not in frame:
        return False
    close = (
        pd.to_numeric(frame["Close"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if len(close) < max(126, trend_sessions, price_bars * 3) or (close <= 0).any():
        return False
    daily_log_slope = _slope(np.log(close.iloc[-trend_sessions:].to_numpy()))
    annualized_slope = float(np.expm1(daily_log_slope * 252))
    return_3m = float(close.iloc[-1] / close.iloc[-64] - 1)
    complete_rows = (len(close) // 3) * 3
    three_session_close = close.iloc[-complete_rows:].to_numpy().reshape(-1, 3)[:, -1]
    price_slope_z = _slope(_zscore(three_session_close[-price_bars:]))
    return bool(return_3m < 0 and annualized_slope < 0 and price_slope_z < 0)


def on_balance_volume(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    return (direction * volume.fillna(0.0)).cumsum()


def calculate_technical_metrics(
    frame: pd.DataFrame,
    trend_sessions: int = 63,
    obv_bars: int = 20,
) -> TechnicalMetrics | None:
    columns = ["Close", "Volume"] + (["Low"] if "Low" in frame else [])
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
        raise ValueError("max_overvaluation non può essere negativo")
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

    The arithmetic mean is reported to mirror the user's reference.  The hard
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
