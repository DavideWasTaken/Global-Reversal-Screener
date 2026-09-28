"""Generate the files in ``examples/sample-output/`` without any network access.

The real ``scan`` pipeline (``reversal_screener.cli.run_scan``) is executed
end to end against a small, fully synthetic universe. Only the data provider
is replaced by an offline stub:

* ``DEMO1``, ``DEMO2``, ``DEMO4`` and ``DEMO5`` reuse the Salesforce daily
  price history stored in ``tests/fixtures`` (the regression fixture for the
  RSI failure swing, emerging-base and 3D OBV divergence rules);
* ``DEMO3`` and ``DEMO6`` use generated price paths;
* every fundamental and valuation input is invented for illustration.

Each demo company is designed to stop at a different gate, so the output shows
how the funnel works and that the screener reports fewer than five names
instead of loosening its filters.

Usage (from the repository root)::

    python examples/generate_sample_output.py
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

EXAMPLES_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from reversal_screener import cli  # noqa: E402
from reversal_screener.metrics import (  # noqa: E402
    CompanyMetrics,
    ValuationInputs,
    calculate_fundamental_metrics,
)

UNIVERSE_CSV = "sample_universe.csv"
DEFAULT_OUTPUT = EXAMPLES_DIR / "sample-output"
OUTPUT_FILES = (
    "all_candidates.csv",
    "diagnostics.json",
    "scan_summary.md",
    "top5.html",
    "top5.json",
)
PRICE_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "crm_2025-12-01_2026-06-26.csv"
QUARTER_ENDS = pd.to_datetime(
    ["2026-04-30", "2026-01-31", "2025-10-31", "2025-07-31", "2025-04-30"]
)


def _fixture_prices() -> pd.DataFrame:
    frame = pd.read_csv(PRICE_FIXTURE, parse_dates=["Date"]).set_index("Date")
    frame = frame[["High", "Low", "Close", "Volume"]].astype(float)
    frame.attrs.update(
        {"currency": "USD", "exchange_timezone": "America/New_York", "low_is_proxy": False}
    )
    return frame


def _synthetic_prices(start: float, end: float, sessions: int = 150) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-06-26", periods=sessions)
    close = np.linspace(start, end, sessions)
    frame = pd.DataFrame(
        {
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": np.full(sessions, 2_000_000.0),
        },
        index=index,
    )
    frame.attrs.update(
        {"currency": "USD", "exchange_timezone": "America/New_York", "low_is_proxy": False}
    )
    return frame


def _company(
    fcf_quarters: list[float],
    net_income_quarters: list[float],
    market_cap: float,
) -> CompanyMetrics:
    """Build synthetic fundamentals (values in USD, most recent quarter first)."""

    cashflow = pd.DataFrame([fcf_quarters], index=["Free Cash Flow"], columns=QUARTER_ENDS)
    income = pd.DataFrame(
        [net_income_quarters, [9.8e9, 9.6e9, 9.4e9, 9.2e9, 9.0e9]],
        index=["Net Income", "Total Revenue"],
        columns=QUARTER_ENDS,
    )
    fundamental = calculate_fundamental_metrics(cashflow, income)
    assert fundamental is not None
    inputs = ValuationInputs(
        market_cap=market_cap,
        market_cap_currency="USD",
        market_cap_as_of="2026-06-26",
        cash=14.0e9,
        cash_currency="USD",
        total_debt=11.0e9,
        debt_currency="USD",
        fcf_currency="USD",
        net_income_currency="USD",
        revenue_currency="USD",
        annual_fcf=(("2026-01-31", 12.4e9), ("2025-01-31", 11.8e9), ("2024-01-31", 11.0e9)),
        annual_diluted_eps=(("2026-01-31", 6.4), ("2025-01-31", 6.2), ("2024-01-31", 6.0)),
        annual_enterprise_value=(
            ("2026-01-31", 150.0e9),
            ("2025-01-31", 170.0e9),
            ("2024-01-31", 160.0e9),
        ),
        annual_enterprise_value_currency="USD",
        annual_revenue=(
            ("2026-01-31", 37.8e9),
            ("2025-01-31", 34.9e9),
            ("2024-01-31", 31.4e9),
        ),
        annual_revenue_currency="USD",
    )
    return CompanyMetrics(fundamental, inputs)


RISING_FCF = [3.9e9, 3.4e9, 3.1e9, 3.6e9, 3.3e9]
FALLING_FCF = [2.1e9, 3.4e9, 3.1e9, 3.6e9, 3.3e9]
NET_INCOME = [2.0e9, 2.0e9, 1.9e9, 2.0e9, 1.8e9]


def sample_data() -> tuple[dict[str, pd.DataFrame], dict[str, CompanyMetrics]]:
    fixture = _fixture_prices()
    prices = {
        "DEMO1": fixture,  # passes every gate
        "DEMO2": fixture,  # technical setup, but TTM free cash flow is falling
        "DEMO3": _synthetic_prices(100.0, 130.0),  # no correction: rejected by the pre-filter
        "DEMO4": fixture,  # quality passes, market cap below the $10B minimum
        "DEMO5": fixture,  # quality and size pass, valuation far above fair value
        "DEMO6": _synthetic_prices(130.0, 100.0),  # correction without a momentum turn
    }
    companies = {
        "DEMO1": _company(RISING_FCF, NET_INCOME, market_cap=130.0e9),
        "DEMO2": _company(FALLING_FCF, NET_INCOME, market_cap=130.0e9),
        "DEMO3": _company(RISING_FCF, NET_INCOME, market_cap=130.0e9),
        "DEMO4": _company(RISING_FCF, NET_INCOME, market_cap=6.0e9),
        "DEMO5": _company(RISING_FCF, NET_INCOME, market_cap=900.0e9),
        "DEMO6": _company(RISING_FCF, NET_INCOME, market_cap=130.0e9),
    }
    return prices, companies


def make_offline_provider(
    prices: dict[str, pd.DataFrame], companies: dict[str, CompanyMetrics]
) -> type:
    """Return a drop-in replacement for ``YahooFinanceProvider`` backed by memory."""

    class OfflineProvider:
        def __init__(self, batch_size: int = 80, workers: int = 6, cache_dir=None) -> None:
            self.cache_dir = cache_dir

        @staticmethod
        def _select(symbols, source):
            found = {symbol: source[symbol] for symbol in symbols if symbol in source}
            missing = {
                symbol: "price_error: symbol not in sample data"
                for symbol in symbols
                if symbol not in source
            }
            return found, missing

        def download_close_history(self, symbols, period="9mo", progress=None):
            found, missing = self._select(list(symbols), prices)
            return {symbol: frame[["Close"]] for symbol, frame in found.items()}, missing

        def download_prices(self, symbols, period="9mo", progress=None, refresh=False):
            return self._select(list(symbols), prices)

        def load_cached_prices(self, symbols, period="9mo", max_age_hours=24, progress=None):
            return self._select(list(symbols), prices)

        def read_cached_fundamental_gate(self, symbol, max_age_hours=24):
            return None

        def download_fundamentals(self, symbols, progress=None):
            values = {symbol: companies[symbol] for symbol in symbols if symbol in companies}
            errors = {
                symbol: "fundamental_error: symbol not in sample data"
                for symbol in symbols
                if symbol not in companies
            }
            return values, errors

        def download_fx_rates(self, currencies, progress=None):
            return {"USD": 1.0}, {}

    return OfflineProvider


@contextmanager
def _working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def generate(
    output_dir: str | Path = DEFAULT_OUTPUT,
    extra_args: list[str] | None = None,
    companies_override: dict[str, CompanyMetrics] | None = None,
) -> int:
    """Run the real scan pipeline offline and write its reports to ``output_dir``."""

    output_dir = Path(output_dir).resolve()
    # Remove previous outputs so the scan cannot resume from stale checkpoints.
    for name in OUTPUT_FILES + ("preliminary.json",):
        (output_dir / name).unlink(missing_ok=True)
    prices, companies = sample_data()
    if companies_override is not None:
        companies = companies_override
    args = cli.build_parser().parse_args(
        ["scan", "--universe", UNIVERSE_CSV, "--output", str(output_dir), *(extra_args or [])]
    )
    original_provider = cli.YahooFinanceProvider
    cli.YahooFinanceProvider = make_offline_provider(prices, companies)
    try:
        # Run from examples/ so the report shows the relative universe path.
        with _working_directory(EXAMPLES_DIR):
            status = cli.run_scan(args)
    finally:
        cli.YahooFinanceProvider = original_provider
    # Internal resume checkpoint, not a user-facing output.
    (output_dir / "preliminary.json").unlink(missing_ok=True)
    return status


if __name__ == "__main__":
    raise SystemExit(generate())
