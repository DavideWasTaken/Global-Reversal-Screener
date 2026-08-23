from __future__ import annotations

import json
import hashlib
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pandas as pd

from .metrics import (
    CompanyMetrics,
    FundamentalMetrics,
    ValuationInputs,
    calculate_fundamental_metrics,
)


YAHOO_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_SPARK_URL = "https://query2.finance.yahoo.com/v7/finance/spark"
YAHOO_FUNDAMENTALS_URL = (
    "https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
)
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
]


class YahooFinanceProvider:
    """Dependency-light Yahoo public-endpoint adapter with bounded concurrency."""

    def __init__(
        self,
        batch_size: int = 80,
        workers: int = 6,
        pause_seconds: float = 0.15,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.batch_size = max(1, batch_size)
        self.workers = max(1, workers)
        self.pause_seconds = max(0.0, pause_seconds)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _price_cache_path(self, symbol: str, period: str) -> Path | None:
        if not self.cache_dir:
            return None
        readable = re.sub(r"[^A-Za-z0-9._-]+", "_", symbol)[:40]
        digest = hashlib.sha1(symbol.encode()).hexdigest()[:10]
        safe_period = re.sub(r"[^A-Za-z0-9._-]+", "_", period)
        return self.cache_dir / f"price_v2_{safe_period}_{readable}_{digest}.csv"

    def _price_meta_cache_path(self, symbol: str, period: str) -> Path | None:
        path = self._price_cache_path(symbol, period)
        return path.with_suffix(".meta.json") if path else None

    def _legacy_price_cache_path(self, symbol: str) -> Path | None:
        if not self.cache_dir:
            return None
        readable = re.sub(r"[^A-Za-z0-9._-]+", "_", symbol)[:40]
        digest = hashlib.sha1(symbol.encode()).hexdigest()[:10]
        return self.cache_dir / f"price_{readable}_{digest}.csv"

    def _read_cached_price(
        self, symbol: str, period: str, max_age_hours: int = 12
    ) -> pd.DataFrame | None:
        path = self._price_cache_path(symbol, period)
        if path and path.exists() and time.time() - path.stat().st_mtime <= max_age_hours * 3600:
            try:
                frame = pd.read_csv(path, index_col=0, parse_dates=True)
                if {"Low", "Close", "Volume"}.issubset(frame.columns) and not frame.empty:
                    frame = frame[["Low", "Close", "Volume"]]
                    meta_path = self._price_meta_cache_path(symbol, period)
                    if meta_path and meta_path.exists():
                        frame.attrs.update(json.loads(meta_path.read_text(encoding="utf-8")))
                    return frame
            except Exception:
                pass

        legacy_path = self._legacy_price_cache_path(symbol)
        if (
            legacy_path
            and legacy_path.exists()
            and time.time() - legacy_path.stat().st_mtime <= max_age_hours * 3600
        ):
            try:
                frame = pd.read_csv(legacy_path, index_col=0, parse_dates=True)
                if {"Close", "Volume"}.issubset(frame.columns) and not frame.empty:
                    frame = frame[["Close", "Volume"]]
                    frame.insert(0, "Low", frame["Close"])
                    frame.attrs["low_is_proxy"] = True
                    return frame
            except Exception:
                return None
        return None

    @staticmethod
    def _get_json(url: str, timeout: int = 25, attempts: int = 3) -> dict:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                attempt_url = (
                    url.replace("query2.finance.yahoo.com", "query1.finance.yahoo.com")
                    if attempt % 2
                    else url
                )
                request = Request(
                    attempt_url,
                    headers={
                        "User-Agent": random.choice(USER_AGENTS),
                        "Accept": "application/json,text/plain,*/*",
                    },
                )
                with urlopen(request, timeout=timeout) as response:
                    return json.load(response)
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    break
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt + 1 < attempts:
                delay = (5.0 * (attempt + 1)) if isinstance(last_error, HTTPError) and last_error.code == 429 else (1.3**attempt)
                time.sleep(delay + random.random() * 0.5)
        raise RuntimeError(f"Yahoo request failed: {last_error}")

    def _fetch_one_price(
        self,
        symbol: str,
        period: str,
        alternate_host: bool = False,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        if use_cache:
            cached = self._read_cached_price(symbol, period)
            if cached is not None:
                return cached
        params = urlencode(
            {
                "range": period,
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        url = YAHOO_CHART_URL.format(symbol=quote(symbol, safe="")) + "?" + params
        use_query1 = (sum(map(ord, symbol)) % 2 == 1) ^ alternate_host
        if use_query1:
            url = url.replace("query2.finance.yahoo.com", "query1.finance.yahoo.com")
        payload = self._get_json(url, timeout=18, attempts=2)
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise ValueError(chart["error"])
        results = chart.get("result") or []
        if not results:
            raise ValueError("nessuna serie prezzo")
        result = results[0]
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quote_data = (indicators.get("quote") or [{}])[0]
        close_data = quote_data.get("close") or []
        low_data = quote_data.get("low") or close_data
        volume_data = quote_data.get("volume") or []
        row_count = min(len(timestamps), len(low_data), len(close_data), len(volume_data))
        if row_count == 0:
            raise ValueError("serie prezzo vuota")
        frame = pd.DataFrame(
            {
                "Low": low_data[:row_count],
                "Close": close_data[:row_count],
                "Volume": volume_data[:row_count],
            },
            index=pd.to_datetime(timestamps[:row_count], unit="s", utc=True),
        )
        frame = frame.apply(pd.to_numeric, errors="coerce").dropna()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        if frame.empty:
            raise ValueError("serie prezzo senza righe valide")
        meta = result.get("meta") or {}
        frame.attrs["currency"] = meta.get("currency")
        frame.attrs["exchange_timezone"] = meta.get("exchangeTimezoneName")
        frame.attrs["low_is_proxy"] = False
        cache_path = self._price_cache_path(symbol, period)
        if cache_path:
            frame.to_csv(cache_path)
            meta_path = self._price_meta_cache_path(symbol, period)
            if meta_path:
                meta_path.write_text(
                    json.dumps(
                        {
                            "currency": frame.attrs.get("currency"),
                            "exchange_timezone": frame.attrs.get("exchange_timezone"),
                            "low_is_proxy": False,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
        return frame

    @staticmethod
    def _spark_result_to_frame(response: dict) -> pd.DataFrame:
        timestamps = response.get("timestamp") or []
        quote_data = ((response.get("indicators") or {}).get("quote") or [{}])[0]
        close_data = quote_data.get("close") or []
        row_count = min(len(timestamps), len(close_data))
        if row_count == 0:
            raise ValueError("serie spark vuota")
        frame = pd.DataFrame(
            {"Close": close_data[:row_count]},
            index=pd.to_datetime(timestamps[:row_count], unit="s", utc=True),
        )
        frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
        return frame.dropna().sort_index()

    def _fetch_spark_batch(
        self, symbols: list[str], period: str
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        params = urlencode(
            {
                "symbols": ",".join(symbols),
                "range": period,
                "interval": "1d",
                "indicators": "close",
            }
        )
        payload = self._get_json(YAHOO_SPARK_URL + "?" + params, timeout=40)
        spark = payload.get("spark") or {}
        if spark.get("error"):
            raise ValueError(spark["error"])
        frames: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        for result in spark.get("result") or []:
            symbol = str(result.get("symbol") or "")
            responses = result.get("response") or []
            try:
                if not symbol or not responses:
                    raise ValueError("risposta spark assente")
                frames[symbol] = self._spark_result_to_frame(responses[0])
            except Exception as exc:
                if symbol:
                    errors[symbol] = f"spark_error: {type(exc).__name__}: {exc}"
        returned = set(frames) | set(errors)
        for symbol in set(symbols) - returned:
            errors[symbol] = "spark_error: simbolo non restituito"
        return frames, errors

    def download_close_history(
        self,
        symbols: Iterable[str],
        period: str = "9mo",
        progress: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        symbols = list(dict.fromkeys(symbols))
        spark_batch_size = min(self.batch_size, 20)
        batches = [
            symbols[start : start + spark_batch_size]
            for start in range(0, len(symbols), spark_batch_size)
        ]
        prices: dict[str, pd.DataFrame] = {}
        errors: dict[str, str] = {}
        completed = 0
        with ThreadPoolExecutor(max_workers=min(self.workers, 6)) as executor:
            futures = {
                executor.submit(self._fetch_spark_batch, batch, period): batch
                for batch in batches
            }
            for future in as_completed(futures):
                batch = futures[future]
                completed += len(batch)
                try:
                    batch_prices, batch_errors = future.result()
                    prices.update(batch_prices)
                    errors.update(batch_errors)
                except Exception as exc:
                    for symbol in batch:
                        errors[symbol] = f"spark_batch_error: {type(exc).__name__}: {exc}"
                if progress:
                    progress(f"Pre-filtro prezzi {min(completed, len(symbols))}/{len(symbols)}")
        return prices, errors

    def download_prices(
        self,
        symbols: Iterable[str],
        period: str = "9mo",
        progress: Callable[[str], None] | None = None,
        refresh: bool = False,
    ) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
        symbols = list(dict.fromkeys(symbols))
        prices: dict[str, pd.DataFrame] = {}

        def run_pass(
            pass_symbols: list[str], alternate_host: bool, retry: bool
        ) -> dict[str, str]:
            pass_errors: dict[str, str] = {}
            price_batch_size = min(self.batch_size, 20)
            for start in range(0, len(pass_symbols), price_batch_size):
                batch = pass_symbols[start : start + price_batch_size]
                with ThreadPoolExecutor(max_workers=min(self.workers, 2)) as executor:
                    futures = {
                        executor.submit(
                            self._fetch_one_price,
                            symbol,
                            period,
                            alternate_host,
                            not refresh,
                        ): symbol
                        for symbol in batch
                    }
                    for future in as_completed(futures):
                        symbol = futures[future]
                        try:
                            prices[symbol] = future.result()
                        except Exception as exc:  # provider boundary
                            pass_errors[symbol] = (
                                f"price_error: {type(exc).__name__}: {exc}"
                            )
                if progress:
                    prefix = "Retry prezzo+volume" if retry else "Prezzo+volume"
                    progress(
                        f"{prefix} {min(start + len(batch), len(pass_symbols))}/"
                        f"{len(pass_symbols)}"
                    )
                if start + len(batch) < len(pass_symbols):
                    time.sleep(max(self.pause_seconds, 1.0))
            return pass_errors

        errors = run_pass(symbols, alternate_host=False, retry=False)
        if errors:
            time.sleep(15.0)
            errors = run_pass(list(errors), alternate_host=True, retry=True)
        return prices, errors

    def _fundamental_cache_path(self, symbol: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = symbol.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"fundamental_v2_{safe}.json"

    def _legacy_fundamental_cache_path(self, symbol: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe = symbol.replace("/", "_").replace("\\", "_")
        return self.cache_dir / f"fundamental_{safe}.json"

    def read_cached_fundamental_gate(
        self, symbol: str, max_age_hours: int = 24
    ) -> FundamentalMetrics | None:
        current = self._read_cached_fundamental(symbol, max_age_hours=max_age_hours)
        if current:
            return current.fundamental
        path = self._legacy_fundamental_cache_path(symbol)
        if (
            not path
            or not path.exists()
            or time.time() - path.stat().st_mtime > max_age_hours * 3600
        ):
            return None
        try:
            return FundamentalMetrics(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    def _read_cached_fundamental(
        self, symbol: str, max_age_hours: int = 24
    ) -> CompanyMetrics | None:
        path = self._fundamental_cache_path(symbol)
        if not path or not path.exists():
            return None
        if time.time() - path.stat().st_mtime > max_age_hours * 3600:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw_inputs = payload["valuation_inputs"]
            for key in [
                "annual_fcf",
                "annual_diluted_eps",
                "annual_enterprise_value",
                "annual_revenue",
            ]:
                raw_inputs[key] = tuple(tuple(item) for item in raw_inputs.get(key, []))
            return CompanyMetrics(
                fundamental=FundamentalMetrics(**payload["fundamental"]),
                valuation_inputs=ValuationInputs(**raw_inputs),
            )
        except Exception:
            return None

    @staticmethod
    def _timeseries_items(
        results: list[dict], types: list[str]
    ) -> dict[str, list[dict]]:
        collected: dict[str, list[dict]] = {}
        for result in results:
            meta_types = (result.get("meta") or {}).get("type") or []
            result_type = next((item for item in meta_types if item in types), None)
            if not result_type:
                result_type = next((item for item in types if item in result), None)
            if not result_type:
                continue
            items = result.get(result_type) or []
            if items:
                collected[result_type] = items
        return collected

    @staticmethod
    def _timeseries_frame(
        items_by_type: dict[str, list[dict]], types: list[str]
    ) -> pd.DataFrame:
        rows: dict[str, dict[pd.Timestamp, float]] = {}
        for result_type in types:
            values = {}
            for item in items_by_type.get(result_type) or []:
                raw = (item.get("reportedValue") or {}).get("raw")
                date = item.get("asOfDate")
                if raw is not None and date:
                    values[pd.Timestamp(date)] = float(raw)
            if values:
                rows[result_type] = values
        return pd.DataFrame.from_dict(rows, orient="index")

    @staticmethod
    def _latest_item(
        items_by_type: dict[str, list[dict]], *types: str
    ) -> dict | None:
        candidates = []
        for result_type in types:
            for item in items_by_type.get(result_type) or []:
                raw = (item.get("reportedValue") or {}).get("raw")
                date = item.get("asOfDate")
                if raw is not None and date:
                    candidates.append((pd.Timestamp(date), item))
            if candidates:
                break
        return max(candidates, key=lambda value: value[0])[1] if candidates else None

    @staticmethod
    def _item_value(item: dict | None) -> float | None:
        raw = ((item or {}).get("reportedValue") or {}).get("raw")
        return float(raw) if raw is not None else None

    @staticmethod
    def _dated_values(
        items_by_type: dict[str, list[dict]], result_type: str
    ) -> tuple[tuple[str, float], ...]:
        values = []
        for item in items_by_type.get(result_type) or []:
            raw = (item.get("reportedValue") or {}).get("raw")
            date = item.get("asOfDate")
            if raw is not None and date:
                values.append((str(date), float(raw)))
        return tuple(sorted(values, key=lambda value: pd.Timestamp(value[0]), reverse=True))

    def _fetch_one_fundamental(self, symbol: str) -> CompanyMetrics:
        cached = self._read_cached_fundamental(symbol)
        if cached:
            return cached
        types = [
            "quarterlyFreeCashFlow",
            "quarterlyOperatingCashFlow",
            "quarterlyCapitalExpenditure",
            "quarterlyNetIncome",
            "quarterlyTotalRevenue",
            "quarterlyCashCashEquivalentsAndShortTermInvestments",
            "quarterlyTotalDebt",
            "trailingMarketCap",
            "quarterlyMarketCap",
            "annualFreeCashFlow",
            "annualDilutedEPS",
            "annualEnterpriseValue",
            "annualTotalRevenue",
        ]
        now = datetime.now(timezone.utc)
        params = urlencode(
            {
                "symbol": symbol,
                "type": ",".join(types),
                "period1": int((now - timedelta(days=6 * 365)).timestamp()),
                "period2": int((now + timedelta(days=7)).timestamp()),
            }
        )
        url = YAHOO_FUNDAMENTALS_URL.format(symbol=quote(symbol, safe="")) + "?" + params
        payload = self._get_json(url)
        timeseries = payload.get("timeseries") or {}
        if timeseries.get("error"):
            raise ValueError(timeseries["error"])
        items = self._timeseries_items(timeseries.get("result") or [], types)
        frame = self._timeseries_frame(items, types)
        cashflow = frame.reindex(
            [
                "quarterlyFreeCashFlow",
                "quarterlyOperatingCashFlow",
                "quarterlyCapitalExpenditure",
            ]
        ).rename(
            index={
                "quarterlyFreeCashFlow": "Free Cash Flow",
                "quarterlyOperatingCashFlow": "Operating Cash Flow",
                "quarterlyCapitalExpenditure": "Capital Expenditure",
            }
        )
        income = frame.reindex(
            ["quarterlyNetIncome", "quarterlyTotalRevenue"]
        ).rename(
            index={
                "quarterlyNetIncome": "Net Income",
                "quarterlyTotalRevenue": "Total Revenue",
            }
        )
        metrics = calculate_fundamental_metrics(cashflow, income)
        if metrics is None:
            raise ValueError("storico trimestrale FCF/reddito insufficiente")

        market_cap_item = self._latest_item(
            items, "trailingMarketCap", "quarterlyMarketCap"
        )
        cash_item = self._latest_item(
            items, "quarterlyCashCashEquivalentsAndShortTermInvestments"
        )
        debt_item = self._latest_item(items, "quarterlyTotalDebt")
        fcf_item = self._latest_item(items, "quarterlyFreeCashFlow")
        income_item = self._latest_item(items, "quarterlyNetIncome")
        revenue_item = self._latest_item(items, "quarterlyTotalRevenue")
        annual_ev_item = self._latest_item(items, "annualEnterpriseValue")
        annual_revenue_item = self._latest_item(items, "annualTotalRevenue")
        valuation_inputs = ValuationInputs(
            market_cap=self._item_value(market_cap_item),
            market_cap_currency=(market_cap_item or {}).get("currencyCode"),
            market_cap_as_of=(market_cap_item or {}).get("asOfDate"),
            cash=self._item_value(cash_item),
            cash_currency=(cash_item or {}).get("currencyCode"),
            total_debt=self._item_value(debt_item),
            debt_currency=(debt_item or {}).get("currencyCode"),
            fcf_currency=(fcf_item or {}).get("currencyCode"),
            net_income_currency=(income_item or {}).get("currencyCode"),
            revenue_currency=(revenue_item or {}).get("currencyCode"),
            annual_fcf=self._dated_values(items, "annualFreeCashFlow"),
            annual_diluted_eps=self._dated_values(items, "annualDilutedEPS"),
            annual_enterprise_value=self._dated_values(items, "annualEnterpriseValue"),
            annual_enterprise_value_currency=(annual_ev_item or {}).get("currencyCode"),
            annual_revenue=self._dated_values(items, "annualTotalRevenue"),
            annual_revenue_currency=(annual_revenue_item or {}).get("currencyCode"),
        )
        company = CompanyMetrics(metrics, valuation_inputs)
        path = self._fundamental_cache_path(symbol)
        if path:
            path.write_text(
                json.dumps(
                    {
                        "fundamental": asdict(metrics),
                        "valuation_inputs": asdict(valuation_inputs),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        return company

    def download_fundamentals(
        self,
        symbols: Iterable[str],
        progress: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, CompanyMetrics], dict[str, str]]:
        symbols = list(dict.fromkeys(symbols))
        values: dict[str, CompanyMetrics] = {}

        def run_pass(pass_symbols: list[str], workers: int, retry: bool) -> dict[str, str]:
            errors: dict[str, str] = {}
            completed = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(self._fetch_one_fundamental, symbol): symbol
                    for symbol in pass_symbols
                }
                for future in as_completed(futures):
                    symbol = futures[future]
                    completed += 1
                    try:
                        values[symbol] = future.result()
                    except Exception as exc:  # provider boundary
                        errors[symbol] = (
                            f"fundamental_error: {type(exc).__name__}: {exc}"
                        )
                    if progress and (
                        completed == len(pass_symbols) or completed % 5 == 0
                    ):
                        prefix = "Retry fondamentali" if retry else "Fondamentali"
                        progress(f"{prefix} {completed}/{len(pass_symbols)}")
            return errors

        errors = run_pass(symbols, workers=min(self.workers, 2), retry=False)
        if errors:
            time.sleep(15.0)
            errors = run_pass(list(errors), workers=1, retry=True)
        return values, errors

    def _fx_cache_path(self, currency: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"fx_{currency.upper()}_USD.json"

    def _fetch_fx_rate(self, currency: str) -> float:
        currency = currency.upper()
        if currency == "USD":
            return 1.0
        cache_path = self._fx_cache_path(currency)
        if cache_path and cache_path.exists() and time.time() - cache_path.stat().st_mtime < 24 * 3600:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
            rate = float(payload["rate"])
            if rate > 0:
                return rate

        pairs = [(f"{currency}USD=X", False), (f"USD{currency}=X", True)]
        last_error: Exception | None = None
        for symbol, inverse in pairs:
            params = urlencode({"range": "5d", "interval": "1d"})
            url = YAHOO_CHART_URL.format(symbol=quote(symbol, safe="")) + "?" + params
            try:
                payload = self._get_json(url, attempts=2)
                result = ((payload.get("chart") or {}).get("result") or [])[0]
                closes = (((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
                rate = next(float(value) for value in reversed(closes) if value is not None)
                if inverse:
                    rate = 1.0 / rate
                if not pd.notna(rate) or rate <= 0:
                    raise ValueError("cambio non valido")
                if cache_path:
                    cache_path.write_text(
                        json.dumps(
                            {
                                "currency": currency,
                                "rate": rate,
                                "as_of": datetime.now(timezone.utc).date().isoformat(),
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                return rate
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"cambio {currency}/USD non disponibile: {last_error}")

    def download_fx_rates(
        self,
        currencies: Iterable[str],
        progress: Callable[[str], None] | None = None,
    ) -> tuple[dict[str, float], dict[str, str]]:
        currencies = sorted({currency.upper() for currency in currencies if currency})
        rates: dict[str, float] = {"USD": 1.0}
        errors: dict[str, str] = {}
        requested = [currency for currency in currencies if currency != "USD"]
        with ThreadPoolExecutor(max_workers=min(2, max(1, len(requested)))) as executor:
            futures = {
                executor.submit(self._fetch_fx_rate, currency): currency
                for currency in requested
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                currency = futures[future]
                try:
                    rates[currency] = future.result()
                except Exception as exc:
                    errors[currency] = f"fx_error: {type(exc).__name__}: {exc}"
                if progress:
                    progress(f"Cambi valuta {completed}/{len(requested)}")
        return rates, errors
