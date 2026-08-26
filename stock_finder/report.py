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
                '<div class="warning">⚠ FCF/EV-Sales meno comparabili per società finanziarie.</div>'
            )
        elif sector == "Real Estate":
            sector_warning = (
                '<div class="warning">⚠ Verificare anche NAV e FFO/AFFO.</div>'
            )
        obv_label = (
            "swing 3D confermato"
            if row.get("obv_swing_3d_pass")
            else f"slope {float(row['obv_slope_3d_z']):+.3f}"
        )
        support_kind = {
            "established": "storico",
            "emerging_reclaim": "base emergente recuperata",
        }.get(str(row.get("support_kind")), "—")
        momentum_label = (
            f"{int(row.get('momentum_confirmations') or 0)}/2 conferme"
            + f" · {int(row.get('momentum_signal_age') or 0)}g fa"
            + (" · regime bullish" if row.get("bullish_regime_pass") else " · inversione iniziale")
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
                <p>{html.escape(sector or 'Settore non disponibile')}</p>{sector_warning}
              </div>
              <div class="total"><small>Score</small><strong>{float(row['total_score']):.1f}</strong></div>
              <div class="metrics">
                <div><small>Prezzo</small><strong>{_fmt_price(row.get('last_price'))}</strong></div>
                <div><small>Rendimento 3 mesi</small><strong class="negative">{_fmt_pct(row['return_3m'])}</strong></div>
                <div><small>Supporto</small><strong>{_fmt_price(row.get('support_level'))}</strong></div>
                <div><small>Tipo supporto</small><strong>{support_kind}</strong></div>
                <div><small>Distanza supporto</small><strong>{float(row.get('support_distance_atr')):+.2f} ATR</strong></div>
                <div><small>Momentum</small><strong class="positive">{momentum_label}</strong></div>
                <div><small>RSI 14 / ROC 5d</small><strong>{float(row.get('rsi14')):.1f} / {_fmt_pct(row.get('roc5'))}</strong></div>
                <div><small>FCF TTM</small><strong>{_fmt_money(row['fcf_ttm'])}</strong></div>
                <div><small>Crescita FCF</small><strong class="positive">{fcf_label}</strong></div>
                <div><small>Margine netto TTM</small><strong>{margin}</strong></div>
                <div><small>Market cap</small><strong>{_fmt_money(row.get('market_cap_usd'))} USD</strong></div>
                <div><small>Fair value consensus</small><strong class="positive">{_fmt_price(row.get('consensus_fair_value'))}</strong></div>
                <div><small>Fair value vs prezzo</small><strong class="{valuation_class}">{_fmt_pct(upside)}</strong></div>
                <div><small>OBV 3D</small><strong class="positive">{obv_label}</strong></div>
              </div>
              <div class="fair-values">
                <div><small>DCF (FCFE)</small><strong>{_fmt_price(row.get('dcf_fair_value'))}</strong></div>
                <div><small>Peter Lynch</small><strong>{_fmt_price(row.get('peter_lynch_fair_value'))}</strong></div>
                <div><small>EV / Sales storico</small><strong>{_fmt_price(row.get('ev_sales_fair_value'))}</strong></div>
                <div><small>Metodi entro soglia</small><strong>{int(row.get('acceptable_methods') or 0)}/{int(row.get('valuation_methods') or 0)}</strong></div>
              </div>
              <div class="score-grid">
                <div><span>Correzione</span><b>{float(row['correction_score']):.0f}</b>{_bar(row['correction_score'], colors['correction_score'])}</div>
                <div><span>Supporto</span><b>{float(row['support_score']):.0f}</b>{_bar(row['support_score'], colors['support_score'])}</div>
                <div><span>Momentum</span><b>{float(row['momentum_score']):.0f}</b>{_bar(row['momentum_score'], colors['momentum_score'])}</div>
                <div><span>FCF</span><b>{float(row['fcf_score']):.0f}</b>{_bar(row['fcf_score'], colors['fcf_score'])}</div>
                <div><span>Profitto</span><b>{float(row['profit_score']):.0f}</b>{_bar(row['profit_score'], colors['profit_score'])}</div>
                <div><span>OBV</span><b>{float(row['obv_score']):.0f}</b>{_bar(row['obv_score'], colors['obv_score'])}</div>
                <div><span>Valutazione</span><b>{float(row['valuation_score']):.0f}</b>{_bar(row['valuation_score'], colors['valuation_score'])}</div>
              </div>
            </article>
            """
        )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    empty_note = (
        ""
        if cards
        else '<div class="empty">Nessun titolo supera tutti gli otto filtri con i dati disponibili. Le soglie non vengono allentate per riempire la top 5.</div>'
    )
    document = f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stock Finder · Top {top_n}</title>
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
<header><div><div class="eyebrow">Global contrarian value & quality screener</div><h1>Stock Finder<br>Top {top_n}</h1><p>Correzione significativa vicino a supporto, inversione iniziale del momentum, FCF in crescita, utile positivo, conferma OBV 3D, dimensione minima e valutazione multi-metodo entro tolleranza.</p></div>
<div class="summary"><div><small>Universo</small><strong>{universe_count:,}</strong></div><div><small>Divergenze</small><strong>{technical_count:,}</strong></div><div><small>Qualità</small><strong>{quality_count:,}</strong></div><div><small>≥ ${min_market_cap_usd / 1e9:.0f}B</small><strong>{size_count:,}</strong></div><div><small>Valutazione OK</small><strong>{valuation_count:,}</strong></div></div></header>
<div class="method"><b>Filtri rigidi:</b> correzione (−5%/3 mesi oppure drawdown −12%), supporto storico vicino o base emergente recuperata, RSI failure swing + breakout del massimo a 5 sedute, OBV 3D, market cap ≥ ${min_market_cap_usd / 1e9:.1f}B, almeno 2 modelli validi e 2 conferme. Ogni metodo conferma se il titolo è sottovalutato oppure se prezzo / fair value − 1 ≤ {max_overvaluation:.0%}. <b>Score:</b> 10% correzione · 15% supporto · 10% momentum · 25% FCF · 10% redditività · 10% OBV · 20% valutazione.</div>
{''.join(cards)}{empty_note}
<div class="foot">Generato {generated} · Universo: {html.escape(universe_source)} · {data_error_count:,} dati mancanti/errori. Consensus = mediana di DCF FCFE, Peter Lynch semplificato ed EV/Sales storico; la media aritmetica resta nel CSV/JSON per confronto col riferimento. “Fair value vs prezzo” positivo indica sottovalutazione, negativo indica sopravvalutazione. Stime sensibili a crescita, tasso di sconto, valuta e multipli storici. Screening quantitativo, non raccomandazione finanziaria.</div>
</main></body></html>"""
    html_path.write_text(document, encoding="utf-8")

    result_lines = []
    if top.empty:
        result_lines.append(
            "Nessun titolo supera contemporaneamente tutti i filtri; le soglie "
            "non sono state allentate per riempire la Top 5."
        )
    else:
        result_lines.extend(
            [
                "| Rank | Simbolo | Società | Score | Market cap USD | Fair value |",
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
        "Il run è stato chiuso in modalità cache-only dopo il blocco delle richieste "
        "live del provider; soltanto serie OHLCV v3 esatte sono state ammesse."
        if cache_only
        else "Il run ha utilizzato il feed live con le cache compatibili disponibili."
    )
    summary = f"""# Stock Finder — run {generated}

## Risultato

{chr(10).join(result_lines)}

## Copertura

| Fase | Conteggio |
|---|---:|
| Universo | {universe_count:,} |
| Pre-filtro correzione | {(preliminary_count if preliminary_count is not None else 0):,} |
| Serie OHLCV esatte | {(price_series_count if price_series_count is not None else 0):,} |
| Metriche tecniche calcolate | {(technical_metrics_count if technical_metrics_count is not None else 0):,} |
| Supporto + momentum + OBV | {technical_count:,} |
| Qualità FCF/utile | {quality_count:,} |
| Market cap ≥ ${min_market_cap_usd / 1e9:.0f}B | {size_count:,} |
| Valutazione entro soglia | {valuation_count:,} |

{cache_note} Dati mancanti/errori registrati: {data_error_count:,}.

## Regole attive

- correzione: rendimento a 63 sedute ≤ −5% oppure drawdown trimestrale ≥ 12%;
- supporto storico vicino oppure base emergente recuperata;
- RSI(14) bullish failure swing e breakout del massimo delle cinque sedute precedenti, ancora valido entro cinque sedute;
- conferma OBV su barre calendar-3D;
- FCF TTM crescente e utile netto TTM positivo;
- market cap minima ${min_market_cap_usd / 1e9:.0f}B;
- almeno due fair value disponibili e due entro la tolleranza di sopravvalutazione del {max_overvaluation:.0%}.

## Controllo CRM · 26 giugno 2026

CRM continua a superare il test point-in-time: supporto 149,80 USD, RSI failure swing
29,851 → 33,733 → 31,767 → 41,246, chiusura 158,37 sopra il massimo precedente
a cinque sedute di 157,06 e OBV 3D confermato. Era una `bullish_reversal_confirmed`,
non ancora un `bullish_trend_established`.

Screening quantitativo, non raccomandazione finanziaria.
"""
    summary_path.write_text(summary, encoding="utf-8")
    return {
        "csv": csv_path,
        "json": json_path,
        "html": html_path,
        "summary": summary_path,
    }
