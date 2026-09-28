from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def _fmt_pct(value, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value) * 100:+.{digits}f}%"


def _fmt_money(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    sign = "−" if value < 0 else ""
    value = abs(value)
    for divisor, suffix in [(1e12, "T"), (1e9, "B"), (1e6, "M")]:
        if value >= divisor:
            return f"{sign}{value / divisor:.2f}{suffix}"
    return f"{sign}{value:,.0f}"


def _fmt_price(value) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):,.2f}"


def _bar(score: float, color: str) -> str:
    width = max(0, min(100, float(score)))
    return f'<div class="bar"><span style="width:{width:.1f}%;background:{color}"></span></div>'


def write_reports(
    scored: pd.DataFrame,
    output_dir: str | Path,
    universe_count: int,
    technical_count: int,
    quality_count: int,
    size_count: int,
    valuation_count: int,
    data_error_count: int,
    top_n: int = 5,
    universe_source: str = "Vanguard FTSE All-World UCITS ETF holdings",
    min_market_cap_usd: float = 10_000_000_000.0,
    max_overvaluation: float = 0.15,
    preliminary_count: int | None = None,
    price_series_count: int | None = None,
    technical_metrics_count: int | None = None,
    cache_only: bool = False,
) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "all_candidates.csv"
    json_path = output_dir / "top5.json"
    html_path = output_dir / "top5.html"
    summary_path = output_dir / "scan_summary.md"
    scored.to_csv(csv_path, index=False)
    top = scored.head(top_n).replace({np.nan: None})
    json_path.write_text(
        json.dumps(top.to_dict(orient="records"), indent=2, default=str),
        encoding="utf-8",
    )

    cards = []
    colors = {
        "correction_score": "#7dd3fc",
        "support_score": "#2dd4bf",
        "momentum_score": "#60a5fa",
        "fcf_score": "#34d399",
        "profit_score": "#fbbf24",
        "obv_score": "#c084fc",
        "valuation_score": "#fb7185",
    }
    for index, row in top.iterrows():
        margin = _fmt_pct(row.get("net_margin_ttm"))
        fcf_label = "turnaround" if row.get("fcf_turnaround") else _fmt_pct(row.get("fcf_growth"))
        sector = str(row.get("sector") or "")
        sector_warning = ""
        if sector == "Financials":
            sector_warning = (
                '<div class="warning">⚠ FCF and EV/Sales are less comparable for financial companies.</div>'
            )
        elif sector == "Real Estate":
            sector_warning = (
                '<div class="warning">⚠ Also check NAV and FFO/AFFO.</div>'
            )
        obv_label = (
            "3D swing confirmed"
            if row.get("obv_swing_3d_pass")
            else f"slope {float(row['obv_slope_3d_z']):+.3f}"
        )
        support_kind = {
            "established": "historical",
            "emerging_reclaim": "reclaimed emerging base",
        }.get(str(row.get("support_kind")), "—")
        momentum_label = (
            f"{int(row.get('momentum_confirmations') or 0)}/2 confirmations"
            + f" · {int(row.get('momentum_signal_age') or 0)}d ago"
            + (" · bullish regime" if row.get("bullish_regime_pass") else " · early reversal")
        )
        upside = row.get("upside_to_fair_value")
        valuation_class = (
            "positive"
            if upside is not None and not pd.isna(upside) and float(upside) >= 0
            else "negative"
        )
        cards.append(
            f"""
            <article class="stock-card">
              <div class="rank">#{int(row['rank'])}</div>
              <div class="identity">
                <div><span class="symbol">{html.escape(str(row['symbol']))}</span>
                <span class="country">{html.escape(str(row.get('country') or ''))}</span></div>
                <h2>{html.escape(str(row.get('name') or row['symbol']))}</h2>
                <p>{html.escape(sector or 'Sector not available')}</p>{sector_warning}
              </div>
              <div class="total"><small>Score</small><strong>{float(row['total_score']):.1f}</strong></div>
              <div class="metrics">
                <div><small>Price</small><strong>{_fmt_price(row.get('last_price'))}</strong></div>
                <div><small>3-month return</small><strong class="negative">{_fmt_pct(row['return_3m'])}</strong></div>
                <div><small>Support</small><strong>{_fmt_price(row.get('support_level'))}</strong></div>
                <div><small>Support type</small><strong>{support_kind}</strong></div>
                <div><small>Distance from support</small><strong>{float(row.get('support_distance_atr')):+.2f} ATR</strong></div>
                <div><small>Momentum</small><strong class="positive">{momentum_label}</strong></div>
                <div><small>RSI 14 / ROC 5d</small><strong>{float(row.get('rsi14')):.1f} / {_fmt_pct(row.get('roc5'))}</strong></div>
                <div><small>FCF TTM</small><strong>{_fmt_money(row['fcf_ttm'])}</strong></div>
                <div><small>FCF growth</small><strong class="positive">{fcf_label}</strong></div>
                <div><small>TTM net margin</small><strong>{margin}</strong></div>
                <div><small>Market cap</small><strong>{_fmt_money(row.get('market_cap_usd'))} USD</strong></div>
                <div><small>Consensus fair value</small><strong class="positive">{_fmt_price(row.get('consensus_fair_value'))}</strong></div>
                <div><small>Fair value vs price</small><strong class="{valuation_class}">{_fmt_pct(upside)}</strong></div>
                <div><small>OBV 3D</small><strong class="positive">{obv_label}</strong></div>
              </div>
              <div class="fair-values">
                <div><small>DCF (FCFE)</small><strong>{_fmt_price(row.get('dcf_fair_value'))}</strong></div>
                <div><small>Peter Lynch</small><strong>{_fmt_price(row.get('peter_lynch_fair_value'))}</strong></div>
                <div><small>Historical EV / Sales</small><strong>{_fmt_price(row.get('ev_sales_fair_value'))}</strong></div>
                <div><small>Methods within tolerance</small><strong>{int(row.get('acceptable_methods') or 0)}/{int(row.get('valuation_methods') or 0)}</strong></div>
              </div>
              <div class="score-grid">
                <div><span>Correction</span><b>{float(row['correction_score']):.0f}</b>{_bar(row['correction_score'], colors['correction_score'])}</div>
                <div><span>Support</span><b>{float(row['support_score']):.0f}</b>{_bar(row['support_score'], colors['support_score'])}</div>
                <div><span>Momentum</span><b>{float(row['momentum_score']):.0f}</b>{_bar(row['momentum_score'], colors['momentum_score'])}</div>
                <div><span>FCF</span><b>{float(row['fcf_score']):.0f}</b>{_bar(row['fcf_score'], colors['fcf_score'])}</div>
                <div><span>Profit</span><b>{float(row['profit_score']):.0f}</b>{_bar(row['profit_score'], colors['profit_score'])}</div>
                <div><span>OBV</span><b>{float(row['obv_score']):.0f}</b>{_bar(row['obv_score'], colors['obv_score'])}</div>
                <div><span>Valuation</span><b>{float(row['valuation_score']):.0f}</b>{_bar(row['valuation_score'], colors['valuation_score'])}</div>
              </div>
            </article>
            """
        )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    empty_note = (
        ""
        if cards
        else f'<div class="empty">No stock passes all eight filters with the available data. Thresholds are never loosened to fill the top {top_n}.</div>'
    )
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Global Reversal Screener · Top {top_n}</title>
<style>
:root{{--bg:#081018;--card:#101b26;--line:#213244;--text:#edf5fb;--muted:#8ca1b4;--green:#34d399;--red:#fb7185;}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at 15% 0,#133046 0,transparent 34%),var(--bg);color:var(--text);font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}}
main{{max-width:1180px;margin:auto;padding:54px 24px 80px}} header{{display:grid;grid-template-columns:1fr auto;gap:28px;align-items:end;margin-bottom:30px}}
.eyebrow{{color:#7dd3fc;text-transform:uppercase;letter-spacing:.16em;font-size:12px;font-weight:800}} h1{{font-size:clamp(34px,6vw,68px);line-height:.98;margin:10px 0 14px;letter-spacing:-.055em;max-width:760px}} header p{{color:var(--muted);max-width:720px;margin:0}}
.summary{{display:grid;grid-template-columns:repeat(5,minmax(95px,1fr));background:#0c1721;border:1px solid var(--line);border-radius:18px;padding:16px;gap:18px}} .summary div{{display:flex;flex-direction:column}} .summary strong{{font-size:24px}} .summary small,.metrics small,.fair-values small,.total small{{color:var(--muted)}}
.method{{border:1px solid var(--line);border-radius:18px;padding:16px 20px;margin:20px 0 24px;color:#b8c8d6;background:#0b151f}} .method b{{color:#fff}}
.stock-card{{position:relative;display:grid;grid-template-columns:52px minmax(230px,1.2fr) minmax(390px,2fr) 90px;grid-template-areas:'rank identity metrics total' 'rank fair fair total' 'rank scores scores total';gap:20px 24px;padding:24px;margin:14px 0;background:linear-gradient(135deg,rgba(18,32,45,.98),rgba(11,22,32,.98));border:1px solid var(--line);border-radius:22px;box-shadow:0 18px 55px rgba(0,0,0,.18)}}
.rank{{grid-area:rank;font-size:18px;color:#7dd3fc;font-weight:800;padding-top:6px}} .identity{{grid-area:identity}} .identity h2{{font-size:19px;margin:8px 0 2px}} .identity p{{color:var(--muted);margin:0}} .symbol{{font-size:25px;font-weight:900;letter-spacing:-.03em}} .country{{font-size:11px;border:1px solid #38516a;border-radius:999px;padding:3px 7px;margin-left:7px;color:#b6c7d6}}
.warning{{margin-top:9px;color:#fbbf24;font-size:11px;max-width:260px}}
.total{{grid-area:total;display:flex;flex-direction:column;align-items:flex-end}} .total strong{{font-size:36px;color:#fff}} .metrics{{grid-area:metrics;display:grid;grid-template-columns:repeat(3,1fr);gap:14px}} .metrics div{{display:flex;flex-direction:column;padding:9px 12px;border-left:1px solid var(--line)}} .metrics strong{{font-size:16px}} .positive{{color:var(--green)!important}} .negative{{color:var(--red)!important}}
.fair-values{{grid-area:fair;display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:12px;border:1px solid #294158;border-radius:14px;background:#0a151f}} .fair-values div{{display:flex;flex-direction:column}} .fair-values strong{{font-size:15px}}
.score-grid{{grid-area:scores;display:grid;grid-template-columns:repeat(7,1fr);gap:16px;margin-top:2px}} .score-grid>div{{display:grid;grid-template-columns:1fr auto;align-items:center;gap:7px;color:#aebfcd;font-size:12px}} .score-grid b{{color:#eaf2f8}} .bar{{grid-column:1/-1;height:5px;background:#22313f;border-radius:99px;overflow:hidden}} .bar span{{display:block;height:100%;border-radius:99px}}
.foot{{margin:28px 4px;color:#768b9e;font-size:12px}} .empty{{padding:30px;border:1px dashed #3b5063;border-radius:18px;color:#b6c7d6}}
@media(max-width:900px){{header{{grid-template-columns:1fr}} .stock-card{{grid-template-columns:44px 1fr 72px;grid-template-areas:'rank identity total' 'metrics metrics metrics' 'fair fair fair' 'scores scores scores'}} .metrics{{grid-template-columns:repeat(2,1fr)}} .summary{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:560px){{main{{padding:34px 15px}} .summary{{grid-template-columns:1fr 1fr}} .stock-card{{padding:18px;gap:14px}} .score-grid,.fair-values{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main>
<header><div><div class="eyebrow">Global contrarian value & quality screener</div><h1>Global Reversal Screener<br>Top {top_n}</h1><p>Significant correction near support, early bullish momentum turn, rising FCF, positive earnings, 3D OBV confirmation, minimum size and multi-method valuation within tolerance.</p></div>
<div class="summary"><div><small>Universe</small><strong>{universe_count:,}</strong></div><div><small>Technical setups</small><strong>{technical_count:,}</strong></div><div><small>Quality</small><strong>{quality_count:,}</strong></div><div><small>≥ ${min_market_cap_usd / 1e9:.0f}B</small><strong>{size_count:,}</strong></div><div><small>Valuation OK</small><strong>{valuation_count:,}</strong></div></div></header>
<div class="method"><b>Hard filters:</b> correction (−5% over 3 months or −12% drawdown), nearby historical support or reclaimed emerging base, RSI failure swing + breakout above the 5-session high, 3D OBV, market cap ≥ ${min_market_cap_usd / 1e9:.1f}B, rising TTM FCF, positive TTM net income, at least 2 valid models and 2 confirmations. Each method confirms when the stock is undervalued or when price / fair value − 1 ≤ {max_overvaluation:.0%}. <b>Score:</b> 10% correction · 15% support · 10% momentum · 25% FCF · 10% profitability · 10% OBV · 20% valuation.</div>
{''.join(cards)}{empty_note}
<div class="foot">Generated {generated} · Universe: {html.escape(universe_source)} · {data_error_count:,} missing data points/errors. Consensus = median of DCF (FCFE), simplified Peter Lynch and historical EV/Sales; the arithmetic mean is kept in the CSV/JSON for comparison. A positive “fair value vs price” means undervaluation, a negative one means overvaluation. Estimates are sensitive to growth, discount rate, currency and historical multiples. Quantitative screening only, not investment advice.</div>
</main></body></html>"""
    html_path.write_text(document, encoding="utf-8")

    result_lines = []
    if top.empty:
        result_lines.append(
            "No stock passes every filter at the same time; thresholds were "
            f"not loosened to fill the top {top_n}."
        )
    else:
        result_lines.extend(
            [
                "| Rank | Symbol | Company | Score | Market cap (USD) | Consensus fair value |",
                "|---:|---|---|---:|---:|---:|",
            ]
        )
        for _, row in top.iterrows():
            result_lines.append(
                f"| {int(row['rank'])} | {row['symbol']} | {row.get('name') or row['symbol']} "
                f"| {float(row['total_score']):.1f} | {_fmt_money(row.get('market_cap_usd'))} "
                f"| {_fmt_price(row.get('consensus_fair_value'))} |"
            )
    cache_note = (
        "This scan ran in cache-only mode: no network price requests were made and "
        "only exact OHLCV v3 caches were accepted."
        if cache_only
        else "This scan used the live data feed together with any compatible caches."
    )
    summary = f"""# Global Reversal Screener — scan summary

Generated: {generated}

## Result

{chr(10).join(result_lines)}

## Coverage

| Stage | Count |
|---|---:|
| Universe | {universe_count:,} |
| Correction pre-filter | {(preliminary_count if preliminary_count is not None else 0):,} |
| Exact OHLCV series | {(price_series_count if price_series_count is not None else 0):,} |
| Technical metrics computed | {(technical_metrics_count if technical_metrics_count is not None else 0):,} |
| Support + momentum + OBV | {technical_count:,} |
| FCF/earnings quality | {quality_count:,} |
| Market cap ≥ ${min_market_cap_usd / 1e9:.0f}B | {size_count:,} |
| Valuation within tolerance | {valuation_count:,} |

{cache_note} Missing data points/errors recorded: {data_error_count:,}.

## Active rules

- correction: 63-session return ≤ −5% or drawdown from the quarterly high ≥ 12%;
- nearby historical support or reclaimed emerging base;
- RSI(14) bullish failure swing plus a breakout above the previous five-session high, still valid within five sessions;
- OBV confirmation on calendar-3D bars;
- rising TTM free cash flow and positive TTM net income;
- minimum market cap ${min_market_cap_usd / 1e9:.0f}B;
- at least two available fair values and two within the {max_overvaluation:.0%} overvaluation tolerance.

Quantitative screening only, not investment advice.
"""
    summary_path.write_text(summary, encoding="utf-8")
    return {
        "csv": csv_path,
        "json": json_path,
        "html": html_path,
        "summary": summary_path,
    }
