from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen


VANGUARD_GRAPHQL_URL = "https://www.vanguard.co.uk/gpx/graphql"
VANGUARD_PRODUCT_URL = (
    "https://www.vanguard.co.uk/professional/product/etf/equity/9679/"
    "ftse-all-world-ucits-etf-usd-accumulating"
)

EQUITY_SECURITY_TYPES = [
    "EQ.DRCPT",
    "EQ.FSH",
    "EQ.PREF",
    "EQ.PSH",
    "EQ.REIT",
    "EQ.STOCK",
]

HOLDINGS_QUERY = """
query FundsHoldingsQuery(
  $portIds: [String!],
  $securityTypes: [String!],
  $lastItemKey: String
) {
  funds(portIds: $portIds) {
    profile { fundFullName fundCurrency primarySectorEquityClassification }
  }
  borHoldings(portIds: $portIds) {
    holdings(limit: 1500, securityTypes: $securityTypes, lastItemKey: $lastItemKey) {
      items {
        issuerName
        securityLongDescription
        gicsSectorDescription
        icbSectorDescription
        icbIndustryDescription
        marketValuePercentage
        sedol1
        ticker
        securityType
        effectiveDate
        bloombergIsoCountry
      }
      totalHoldings
      lastItemKey
    }
  }
}
"""


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    ticker: str
    name: str
    country: str = ""
    sector: str = ""
    weight_pct: float | None = None
    sedol: str = ""
    security_type: str = ""
    as_of: str = ""


COUNTRY_SUFFIX = {
    "AR": ".BA",
    "AT": ".VI",
    "AU": ".AX",
    "BE": ".BR",
    "BR": ".SA",
    "CA": ".TO",
    "CH": ".SW",
    "CL": ".SN",
    "CO": ".CL",
    "CZ": ".PR",
    "DE": ".DE",
    "DK": ".CO",
    "EG": ".CA",
    "ES": ".MC",
    "FI": ".HE",
    "FR": ".PA",
    "GB": ".L",
    "GR": ".AT",
    "HK": ".HK",
    "HU": ".BD",
    "ID": ".JK",
    "IE": ".IR",
    "IL": ".TA",
    "IN": ".NS",
    "IS": ".IC",
    "IT": ".MI",
    "JP": ".T",
    "KR": ".KS",
    "KW": ".KW",
    "LK": ".CM",
    "LU": ".LU",
    "MX": ".MX",
    "MY": ".KL",
    "NL": ".AS",
    "NO": ".OL",
    "NZ": ".NZ",
    "PE": ".LM",
    "PH": ".PS",
    "PK": ".KA",
    "PL": ".WA",
    "PT": ".LS",
    "QA": ".QA",
    "RO": ".RO",
    "RU": ".ME",
    "SA": ".SR",
    "SE": ".ST",
    "SG": ".SI",
    "TH": ".BK",
    "TR": ".IS",
    "TW": ".TW",
    "AE": ".AE",
    "US": "",
    "VN": ".VN",
    "ZA": ".JO",
}


def _clean_ticker(ticker: str) -> str:
    return re.sub(r"\s+", "", str(ticker or "").strip().upper())


def to_yahoo_symbol(ticker: str, country: str) -> str:
    """Convert Vanguard/Bloomberg local tickers to common Yahoo symbols.

    Country-based conversion is intentionally transparent. A custom universe can
    bypass it by supplying an explicit ``yahoo_symbol`` column.
    """

    ticker = _clean_ticker(ticker)
    country = str(country or "").strip().upper()
    if not ticker or ticker in {"-", "N/A", "NA", "NULL", "NONE"}:
        return ""

    if country == "US":
        return ticker.replace("/", "-").replace(".", "-")
    if country == "HK" and ticker.isdigit():
        ticker = ticker.zfill(4)
    if country == "KR" and ticker.isdigit():
        ticker = ticker.zfill(6)
    if country == "CN":
        suffix = ".SS" if ticker[:1] in {"5", "6", "9"} else ".SZ"
        return ticker + suffix

    suffix = COUNTRY_SUFFIX.get(country, "")
    return ticker + suffix if suffix else ticker


def _post_graphql(variables: dict, timeout: int = 90) -> dict:
    body = json.dumps({"query": HOLDINGS_QUERY, "variables": variables}).encode()
    request = Request(
        VANGUARD_GRAPHQL_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "global-reversal-screener/1.0 (research screener)",
            "X-Consumer-ID": "uk2",
            "Referer": VANGUARD_PRODUCT_URL,
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(f"Vanguard GraphQL error: {payload['errors']}")
    return payload["data"]


def fetch_vanguard_ftse_all_world() -> list[UniverseMember]:
    """Fetch the latest equity holdings of Vanguard's FTSE All-World UCITS ETF.

    The ETF uses representative sampling, so this is a broad, reproducible proxy
    for the licensed FTSE All-World constituent file rather than the licensed
    index file itself.
    """

    all_items: list[dict] = []
    last_item_key: str | None = None
    while True:
        data = _post_graphql(
            {
                "portIds": ["9679"],
                "securityTypes": EQUITY_SECURITY_TYPES,
                "lastItemKey": last_item_key,
            }
        )
        holdings = data["borHoldings"][0]["holdings"]
        all_items.extend(holdings.get("items") or [])
        last_item_key = holdings.get("lastItemKey")
        if not last_item_key:
            break

    by_symbol: dict[str, UniverseMember] = {}
    for item in all_items:
        ticker = _clean_ticker(item.get("ticker", ""))
        country = str(item.get("bloombergIsoCountry") or "").upper()
        symbol = to_yahoo_symbol(ticker, country)
        if not symbol:
            continue
        weight = item.get("marketValuePercentage")
        member = UniverseMember(
            symbol=symbol,
            ticker=ticker,
            name=item.get("issuerName") or item.get("securityLongDescription") or ticker,
            country=country,
            sector=(
                item.get("icbIndustryDescription")
                or item.get("gicsSectorDescription")
                or item.get("icbSectorDescription")
                or ""
            ),
            weight_pct=float(weight) if weight is not None else None,
            sedol=str(item.get("sedol1") or ""),
            security_type=str(item.get("securityType") or ""),
            as_of=str(item.get("effectiveDate") or ""),
        )
        current = by_symbol.get(symbol)
        if current is None or (member.weight_pct or 0) > (current.weight_pct or 0):
            by_symbol[symbol] = member

    return sorted(
        by_symbol.values(),
        key=lambda member: (-(member.weight_pct or 0), member.symbol),
    )


def _first(row: dict[str, str], aliases: Iterable[str], default: str = "") -> str:
    lowered = {str(key).strip().lower(): value for key, value in row.items()}
    for alias in aliases:
        if alias in lowered and str(lowered[alias]).strip():
            return str(lowered[alias]).strip()
    return default


def load_universe_csv(path: str | Path) -> list[UniverseMember]:
    members: list[UniverseMember] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            ticker = _first(row, ["ticker", "local_ticker", "symbol"])
            country = _first(row, ["country", "country_code", "bloombergisocountry"])
            explicit_symbol = _first(row, ["yahoo_symbol", "yahoo ticker"])
            symbol = _clean_ticker(explicit_symbol) or to_yahoo_symbol(ticker, country)
            if not symbol:
                continue
            weight_text = _first(row, ["weight_pct", "weight", "% of market value"])
            try:
                weight = float(weight_text.replace("%", "").replace(",", "."))
            except (TypeError, ValueError):
                weight = None
            members.append(
                UniverseMember(
                    symbol=symbol,
                    ticker=_clean_ticker(ticker) or symbol,
                    name=_first(row, ["name", "issuer_name", "holding name"], symbol),
                    country=country.upper(),
                    sector=_first(row, ["sector", "industry"]),
                    weight_pct=weight,
                    sedol=_first(row, ["sedol", "sedol1"]),
                    security_type=_first(row, ["security_type", "type"]),
                    as_of=_first(row, ["as_of", "effective_date", "date"]),
                )
            )
    return list({member.symbol: member for member in members}.values())


def save_universe_csv(members: Iterable[UniverseMember], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [asdict(member) for member in members]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(UniverseMember.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(rows)
    return path

