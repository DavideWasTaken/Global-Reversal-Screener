# Global Reversal Screener

A strict, auditable stock screener that looks for quality companies turning up from a correction across a global FTSE All-World universe.

[![checks](https://github.com/DavideWasTaken/Global-Reversal-Screener/actions/workflows/checks.yml/badge.svg)](https://github.com/DavideWasTaken/Global-Reversal-Screener/actions/workflows/checks.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## What it does

The screener scans a broad proxy of the FTSE All-World index (the equity
holdings of the Vanguard FTSE All-World UCITS ETF, several thousand stocks). It
returns **at most five** stocks that pass **every** hard gate below at the same
time: a significant correction, price at support, an early bullish momentum
turn, 3D on-balance-volume accumulation, rising free cash flow, positive
earnings, a minimum size and a valuation confirmed by at least two independent
models.

Filters are never loosened to fill the list. If only one stock qualifies, the
report shows one stock. If none qualify, it says so.

For each scan it writes an HTML dashboard, JSON and CSV exports, a Markdown
summary of the gate funnel and a diagnostics file listing every data error.

## The gates

All eight gates are hard filters, applied before any ranking.

| # | Gate | Rule |
|---|---|---|
| 1 | Significant correction | 63-session return ≤ −5% **or** drawdown from the 63-session high ≥ 12% |
| 2 | Rising free cash flow | TTM free cash flow > 0 and above the previous (overlapping) TTM window |
| 3 | Profitability | TTM net income > 0 |
| 4 | Support | Price near a historical support **or** a confirmed reclaim of an emerging base (see below) |
| 5 | Early bullish momentum turn | Completed Wilder RSI(14) bottom failure swing **and** a close above the previous five-session high, still valid for up to five sessions |
| 6 | 3D OBV bullish divergence | On three-calendar-day bars: positive OBV slope over the last 20 bars while the stock is in a correction, **or** a confirmed swing divergence on the last three bars |
| 7 | Size | Market cap ≥ $10B in USD (configurable with `--min-market-cap-usd`) |
| 8 | Valuation | At least two of three fair-value methods available and at least two within the overvaluation tolerance (see [Valuation rule](#valuation-rule)) |

### Support

One of two setups is required:

- **Historical support:** at least two pivot lows at least 15 sessions apart,
  clustered in a band of `max(2% of price, 0.75 × ATR20)`. Price must sit
  between −0.5 and +1 ATR from the level, and fewer than two of the last five
  closes may be more than 1 ATR below it.
- **Reclaimed emerging base:** at least three tests of a floor in the previous
  ten sessions, spread over at least three sessions, followed by a close above
  the zone and above the previous session's high. The reclaim may not extend
  more than 1.5 ATR above the base low.

The second setup recognises a new floor without pretending it is a long-standing
support.

### Momentum turn

The momentum gate distinguishes an **early reversal** from an already mature
uptrend. It requires:

1. Wilder RSI(14) makes a first low ≤ 30;
2. RSI rebounds above 30, forming an intermediate peak;
3. RSI pulls back to a higher second low that stays above 30;
4. RSI crosses above the intermediate peak;
5. in the same session, price closes above the high of the previous five sessions.

The signal remains valid for up to five sessions as long as price stays above
the breakout level and RSI stays above the second low. `momentum_signal_age`
reports how many sessions ago it fired.

This is the RSI *bottom failure swing* described by the
[CMT Association](https://cmtassociation.org/technically_speaking/technically-speaking-december-2011/)
and in
[Fidelity's RSI guide](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI).

Relative volume, close location within the daily range, ROC(5) and MACD
histogram improvement feed the score and the diagnostics, but are not extra
gates. `bullish_regime_pass` (RSI ≥ 50, MACD above its signal line, price above
a rising EMA20) is a slower diagnostic flag and is not a gate either.

### 3D OBV divergence

3D bars are **three-calendar-day** windows anchored to the Unix epoch, built in
the exchange's local time zone; empty weekend/holiday bins are dropped. The
gate passes when either:

- the (z-scored) OBV slope over the last 20 bars is positive: combined with the
  correction gate, OBV is rising while price has fallen; or
- the last three bars show a confirmed swing divergence: a lower price low with a
  higher OBV, followed by a bar where both close and OBV rise.

The second clause keeps a long regression from hiding sharp, recent accumulation.

## Valuation rule

Three fair values are computed and expressed in the same unit as the quoted price:

| Method | Implementation |
|---|---|
| DCF (FCFE) | TTM free cash flow projected for 5 years; growth is normalised, capped at 12% and fades towards 2.5% terminal growth; 10% cost of equity. Because `OCF − CapEx` is treated as cash flow to equity, debt is not subtracted a second time. Only valid if at least 2 of the last 3 annual FCF values are positive. |
| Peter Lynch | TTM net income × fair P/E, where the fair P/E is the median annual EPS growth (in percent) clamped to 5–20. Not applicable when EPS growth is not positive. |
| EV / Sales | The company's own median historical EV/Sales multiple applied to TTM revenue; debt is subtracted and cash added to reach equity value. |

Each method is checked on its own:

```text
price / fair value − 1 ≤ 15%
```

A method always confirms when the stock is undervalued (`price ≤ fair value`).
When price is above fair value, it confirms only up to 15% overvaluation: with a
fair value of 100, prices up to 115 are accepted. A stock is eligible only if at
least **2** methods are available and at least **2** confirm. The tolerance and
both counts are configurable (`--max-overvaluation`,
`--min-valuation-methods`, `--min-valuation-confirmations`).

All amounts are converted to USD with current Yahoo FX rates. The ratio of fair
equity value to market cap is then applied to the listing price, which avoids
errors from ADRs, share classes and prices quoted in cents.

The report shows the **median** of the available fair values as the consensus;
the arithmetic mean is exported in the CSV/JSON for comparison.

## Scoring

Stocks that pass all gates are ranked by percentile scores computed among the
finalists only:

| Block | Weight | Signal |
|---|---:|---|
| Correction | 10% | 3-month return and 63-session log-price slope |
| Support | 15% | ATR-normalised distance, number of tests, base reclaim |
| Momentum | 10% | RSI failure swing, 5-session breakout, breakout volume and close quality, MACD histogram improvement |
| FCF growth | 25% | Current vs previous TTM free cash flow |
| Profitability | 10% | TTM net margin and number of profitable quarters |
| OBV divergence | 10% | OBV slope or confirmed swing divergence on 3D bars |
| Valuation | 20% | Upside to the consensus fair value and share of methods within tolerance |

Size is a gate only and earns no points, so mega-caps are not favoured just for being large.

## Universe

By default the screener downloads the current equity holdings of the
**Vanguard FTSE All-World UCITS ETF (portfolio 9679)**. This is a broad,
reproducible proxy for the FTSE All-World index: the official constituent list
is a licensed FTSE Russell dataset, and the ETF uses representative sampling.
Local tickers are mapped to Yahoo Finance symbols using the listing country.

You can pass your own universe CSV with `--universe`. Recognised columns:
`yahoo_symbol` (or `ticker` + `country`, from which the Yahoo symbol is built),
`name`, `country`, `sector` and `weight_pct`. See
[`examples/sample_universe.csv`](examples/sample_universe.csv).

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/DavideWasTaken/Global-Reversal-Screener.git
cd Global-Reversal-Screener
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .                 # or: pip install -e ".[dev]" to include pytest
```

The only runtime dependencies are `numpy` and `pandas`; all data is fetched
with the Python standard library.

## Usage

The package installs a `reversal-screener` command; `python -m reversal_screener`
is equivalent.

Full scan, writing reports to `results/`:

```bash
reversal-screener scan --output results --top 5
```

Stricter overvaluation tolerance (10% instead of 15%):

```bash
reversal-screener scan --max-overvaluation 0.10
```

Lower size threshold ($5B instead of $10B):

```bash
reversal-screener scan --min-market-cap-usd 5e9
```

Quick test run on the first 300 symbols of the universe:

```bash
reversal-screener scan --output results-quick --max-symbols 300
```

Re-run without any network price requests, using only exact OHLCV caches up to
24 hours old (for example after the provider starts rate limiting):

```bash
reversal-screener scan --output results --cache-only
```

Custom universe:

```bash
reversal-screener scan --universe my_universe.csv --output results
```

Download and save the default universe only:

```bash
reversal-screener universe --output results/universe.csv
```

All `scan` options:

| Flag | Default | Description |
|---|---|---|
| `--universe PATH` | Vanguard holdings | Custom universe CSV |
| `--output DIR` | `results` | Output directory |
| `--top N` | `5` | Maximum number of stocks to report |
| `--max-symbols N` | `0` | Only scan the first N symbols (0 = no limit) |
| `--min-weight PCT` | `0` | Minimum weight (percent) in the proxy ETF |
| `--min-market-cap-usd X` | `10000000000` | Minimum market cap in USD |
| `--max-overvaluation X` | `0.15` | Overvaluation tolerance per valuation method |
| `--min-valuation-methods N` | `2` | Minimum number of computable valuation methods |
| `--min-valuation-confirmations N` | `2` | Minimum number of methods within tolerance |
| `--dcf-discount-rate X` | `0.10` | DCF cost of equity |
| `--dcf-terminal-growth X` | `0.025` | DCF terminal growth |
| `--batch-size N` | `80` | Symbols per price request batch |
| `--workers N` | `6` | Concurrent download workers |
| `--cache-only` | off | No network price requests; exact OHLCV caches only |

Run `reversal-screener scan --help` for the same list.

## Output files

A scan writes to the `--output` directory (default `results/`, which is git-ignored):

| File | Content |
|---|---|
| `top5.html` | Dashboard with one card per stock: metrics, fair values and score breakdown |
| `top5.json` | Top-N results with every metric, machine-readable |
| `all_candidates.csv` | Every stock that passed all gates, with full metrics and scores |
| `scan_summary.md` | Results table, gate-by-gate funnel counts and active rules |
| `diagnostics.json` | Stage counts, settings and every price, fundamental and FX error |
| `universe.csv` | Snapshot of the downloaded universe (default universe only) |
| `.cache/`, `preliminary.json` | Price, fundamental and FX caches and the pre-filter checkpoint |

The file names stay `top5.*` whatever the value of `--top`. Caches and
checkpoints let an interrupted scan resume: the universe snapshot and pre-filter
checkpoint are reused for 12 hours, and symbols that failed in the last 12 hours
are not retried.

[`examples/sample-output/`](examples/sample-output/) contains the output of an
offline run on a synthetic six-stock universe, regenerated with:

```bash
python examples/generate_sample_output.py
```

That run uses the real scan pipeline with an in-memory data provider. Four demo
stocks reuse the Salesforce price history from the test fixture, two use
generated price paths, and all fundamentals are invented. Each demo stock stops
at a different gate, so only one of them reaches the report.

## Running tests

```bash
pip install -e ".[dev]"
pytest -q
```

The tests are fully offline. They include a regression fixture
(`tests/fixtures/crm_2025-12-01_2026-06-26.csv`): Salesforce daily price history
used to check the RSI failure swing, emerging-base support and 3D OBV divergence
rules point in time, with no look-ahead. They also run the full scan pipeline
end to end against the synthetic sample universe.

## Limitations and disclaimer

- **This is a research tool, not investment advice.** Nothing it outputs is a
  recommendation to buy or sell any security.
- Data comes from public Yahoo Finance endpoints, which are not an institutional
  feed. Data may be incomplete, delayed or wrong, requests may be rate limited,
  and ticker mappings may be imperfect. Failed symbols are recorded in
  `diagnostics.json` and excluded; missing values are never imputed.
- DCF and other fair values depend heavily on growth, discount rate, historical
  multiples and currency. They are estimates, not observable values.
- A historical EV/Sales multiple can embed a premium the market no longer pays.
- Free cash flow is not very informative for banks and insurers; always check
  the sector and the original filings.
- An OBV divergence signals relative accumulation, not a guaranteed reversal.
- RSI, EMA and MACD are all transformations of the same price series, so they
  are not independent sources of information.
- Supports are statistical zones, not exact prices; an emerging base is less
  robust than an established support.

## License

[MIT](LICENSE) © 2026 Davide Gaglione
