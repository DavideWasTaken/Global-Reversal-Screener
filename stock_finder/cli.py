from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .data import YahooFinanceProvider
from .metrics import (
    calculate_technical_metrics,
    calculate_valuation_metrics,
    passes_preliminary_downtrend,
)
from .report import write_reports
from .scoring import Candidate, score_candidates
from .universe import (
    fetch_vanguard_ftse_all_world,
    load_universe_csv,
    save_universe_csv,
)


def log(message: str) -> None:
    print(message, flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-finder",
        description="Global stock finder: correction + support + rising FCF + profit + 3D OBV divergence.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    universe = sub.add_parser("universe", help="Scarica e salva l'universo globale")
    universe.add_argument("--output", default="results/universe.csv")

    scan = sub.add_parser("scan", help="Esegue lo screening e genera la top 5")
    scan.add_argument("--universe", help="CSV custom; default: holdings Vanguard FTSE All-World")
    scan.add_argument("--output", default="results")
    scan.add_argument("--top", type=int, default=5)
    scan.add_argument("--batch-size", type=int, default=80)
    scan.add_argument("--workers", type=int, default=6)
    scan.add_argument("--max-symbols", type=int, default=0, help="0 = nessun limite")
    scan.add_argument(
        "--min-weight",
        type=float,
        default=0.0,
        help="Peso minimo percentuale nell'ETF proxy (default 0)",
    )
    scan.add_argument(
        "--min-market-cap-usd",
        type=float,
        default=10_000_000_000.0,
        help="Market cap minima in USD (default: 10 miliardi)",
    )
    scan.add_argument(
        "--max-overvaluation",
        type=float,
        default=0.15,
        help=(
            "Sopravvalutazione massima ammessa per ogni metodo, misurata come "
            "prezzo/fair value - 1 (default: 0.15)"
        ),
    )
    scan.add_argument("--min-valuation-methods", type=int, default=2)
    scan.add_argument("--min-valuation-confirmations", type=int, default=2)
    scan.add_argument("--dcf-discount-rate", type=float, default=0.10)
    scan.add_argument("--dcf-terminal-growth", type=float, default=0.025)
    scan.add_argument(
        "--cache-only",
        action="store_true",
        help="Non effettua richieste di rete; usa solo cache OHLCV v3 esatte fino a 24 ore",
    )
    return parser


def run_universe(output: str) -> int:
    log("Scarico holdings Vanguard FTSE All-World…")
    members = fetch_vanguard_ftse_all_world()
    path = save_universe_csv(members, output)
    log(f"Salvati {len(members):,} titoli in {path}")
    return 0


def run_scan(args: argparse.Namespace) -> int:
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.universe:
        members = load_universe_csv(args.universe)
        source = str(Path(args.universe))
    else:
        cached_universe = output_dir / "universe.csv"
        if cached_universe.exists() and time.time() - cached_universe.stat().st_mtime < 12 * 3600:
            log("Riprendo lo snapshot universo dal checkpoint…")
            members = load_universe_csv(cached_universe)
        else:
            log("Scarico l'universo globale Vanguard FTSE All-World…")
            members = fetch_vanguard_ftse_all_world()
        source = "Vanguard FTSE All-World UCITS ETF holdings"
        save_universe_csv(members, output_dir / "universe.csv")

    members = [member for member in members if (member.weight_pct or 0) >= args.min_weight]
    if args.max_symbols > 0:
        members = members[: args.max_symbols]
    if not members:
        raise RuntimeError("L'universo è vuoto dopo i filtri")
    log(f"Universo analizzato: {len(members):,} simboli")

    provider = YahooFinanceProvider(
        batch_size=args.batch_size,
        workers=args.workers,
        cache_dir=output_dir / ".cache",
    )
    universe_fingerprint = hashlib.sha256(
        "\n".join(member.symbol for member in members).encode()
    ).hexdigest()
    preliminary_checkpoint = output_dir / "preliminary.json"
    checkpoint = None
    if preliminary_checkpoint.exists() and time.time() - preliminary_checkpoint.stat().st_mtime < 12 * 3600:
        try:
            candidate = json.loads(preliminary_checkpoint.read_text(encoding="utf-8"))
            checkpoint_filter = candidate.get("preliminary_filter_version")
            if checkpoint_filter is None and candidate.get("technical_filter_version") in {
                "support_momentum_v2",
                "support_momentum_v3",
            }:
                checkpoint_filter = "correction_v1"
            if (
                candidate.get("universe_fingerprint") == universe_fingerprint
                and checkpoint_filter == "correction_v1"
            ):
                checkpoint = candidate
        except Exception:
            checkpoint = None
    if checkpoint:
        preliminary = set(checkpoint["symbols"])
        spark_errors = checkpoint.get("spark_errors", {})
        close_series_count = int(checkpoint.get("close_series_count", 0))
        log("Riprendo il pre-filtro prezzi dal checkpoint…")
    else:
        close_history, spark_errors = provider.download_close_history(
            [member.symbol for member in members], progress=log
        )
        preliminary = {
            symbol
            for symbol, frame in close_history.items()
            if passes_preliminary_downtrend(frame)
        }
        close_series_count = len(close_history)
        preliminary_checkpoint.write_text(
            json.dumps(
                {
                    "universe_fingerprint": universe_fingerprint,
                    "preliminary_filter_version": "correction_v1",
                    "technical_filter_version": "support_momentum_v3",
                    "symbols": sorted(preliminary),
                    "close_series_count": close_series_count,
                    "spark_errors": spark_errors,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    log(
        f"Pre-filtro correzione: {len(preliminary):,} titoli su "
        f"{close_series_count:,} serie analizzabili"
    )
    recent_chart_errors = {}
    previous_diagnostics = {}
    diagnostics_path = output_dir / "diagnostics.json"
    if diagnostics_path.exists() and time.time() - diagnostics_path.stat().st_mtime < 12 * 3600:
        try:
            previous_diagnostics = json.loads(
                diagnostics_path.read_text(encoding="utf-8")
            )
            recent_chart_errors = {
                symbol: message
                for symbol, message in previous_diagnostics.get("price_errors", {}).items()
                if symbol in preliminary and str(message).startswith("price_error")
            }
        except Exception:
            recent_chart_errors = {}
    price_symbols = preliminary - set(recent_chart_errors)
    if recent_chart_errors:
        log(
            f"Salto {len(recent_chart_errors):,} errori chart già verificati nelle "
            "ultime 12 ore"
        )
    if args.cache_only:
        log("Modalità cache-only: nessuna richiesta prezzo di rete…")
        prices, chart_errors = provider.load_cached_prices(
            price_symbols, progress=log
        )
    else:
        prices, chart_errors = provider.download_prices(price_symbols, progress=log)
    chart_errors = {**recent_chart_errors, **chart_errors}
    price_errors = {**spark_errors, **chart_errors}
    technical = {}
    for symbol, frame in prices.items():
        metrics = calculate_technical_metrics(frame)
        if metrics:
            technical[symbol] = metrics

    recent_swing_errors = {
        symbol
        for symbol, message in previous_diagnostics.get("price_errors", {}).items()
        if str(message).startswith("swing_refresh_")
    }
    low_refresh = [
        symbol
        for symbol, metrics in technical.items()
        if metrics.correction_pass
        and prices[symbol].attrs.get("low_is_proxy", False)
        and symbol not in recent_swing_errors
    ]
    if low_refresh and not args.cache_only:
        log(
            f"Confermo High/Low OHLC per {len(low_refresh):,} potenziali setup su supporto…"
        )
        exact_prices, refresh_errors = provider.download_prices(
            low_refresh, progress=log, refresh=True
        )
        for symbol, frame in exact_prices.items():
            metrics = calculate_technical_metrics(frame)
            if metrics:
                prices[symbol] = frame
                technical[symbol] = metrics
        for symbol, message in refresh_errors.items():
            chart_errors[symbol] = f"swing_refresh_{message}"
            price_errors[symbol] = f"swing_refresh_{message}"
    technical_pass = {
        symbol: metrics
        for symbol, metrics in technical.items()
        if metrics.correction_pass
        and metrics.support_pass
        and metrics.momentum_pass
        and not prices[symbol].attrs.get("low_is_proxy", False)
        and (
            metrics.obv_slope_3d_z > 0
            or (
                metrics.obv_swing_3d_pass
                and not prices[symbol].attrs.get("low_is_proxy", False)
            )
        )
    }
    log(
        f"Filtro tecnico: {len(technical_pass):,} setup supporto + momentum + OBV validi su "
        f"{len(technical):,} serie analizzabili"
    )

    recent_fundamental_errors = {
        symbol: message
        for symbol, message in previous_diagnostics.get(
            "fundamental_errors", {}
        ).items()
        if symbol in technical_pass and str(message).startswith("fundamental_error")
    }
    valuation_fetch_symbols = []
    fundamental_gate_count = 0
    for symbol in technical_pass:
        gate = provider.read_cached_fundamental_gate(symbol)
        if gate is not None:
            fundamental_gate_count += 1
            if gate.fcf_pass and gate.profit_pass:
                valuation_fetch_symbols.append(symbol)
        elif symbol not in recent_fundamental_errors:
            valuation_fetch_symbols.append(symbol)
    if recent_fundamental_errors:
        log(
            f"Salto {len(recent_fundamental_errors):,} errori fondamentali già "
            "verificati nelle ultime 12 ore"
        )
    companies, fundamental_errors = provider.download_fundamentals(
        valuation_fetch_symbols, progress=log
    )
    fundamental_errors = {**recent_fundamental_errors, **fundamental_errors}
    quality_companies = {
        symbol: company
        for symbol, company in companies.items()
        if company.fundamental.fcf_pass and company.fundamental.profit_pass
    }
    log(
        f"Filtro qualità: {len(quality_companies):,} titoli con FCF in crescita "
        "e utile TTM positivo"
    )
    currencies = set()
    for company in quality_companies.values():
        currencies.update(company.valuation_inputs.currencies())
    fx_rates, fx_errors = provider.download_fx_rates(currencies, progress=log)

    valuations = {
        symbol: calculate_valuation_metrics(
            current_price=technical_pass[symbol].last_price,
            fundamental=company.fundamental,
            inputs=company.valuation_inputs,
            fx_to_usd=fx_rates,
            min_market_cap_usd=args.min_market_cap_usd,
            max_overvaluation=args.max_overvaluation,
            min_methods=args.min_valuation_methods,
            min_confirmations=args.min_valuation_confirmations,
            dcf_discount_rate=args.dcf_discount_rate,
            dcf_terminal_growth=args.dcf_terminal_growth,
        )
        for symbol, company in quality_companies.items()
    }
    size_count = sum(value.size_pass for value in valuations.values())
    valuation_count = sum(
        value.size_pass and value.valuation_pass for value in valuations.values()
    )
    log(
        f"Filtro dimensione: {size_count:,}/{len(valuations):,} sopra "
        f"${args.min_market_cap_usd / 1e9:.1f}B; valutazione entro soglia: "
        f"{valuation_count:,}"
    )
    member_map = {member.symbol: member for member in members}
    candidates = [
        Candidate(
            member_map[symbol],
            technical_pass[symbol],
            company.fundamental,
            valuations[symbol],
        )
        for symbol, company in quality_companies.items()
        if symbol in member_map and symbol in valuations
    ]
    scored = score_candidates(candidates)
    reports = write_reports(
        scored,
        output_dir,
        universe_count=len(members),
        technical_count=len(technical_pass),
        quality_count=len(quality_companies),
        size_count=size_count,
        valuation_count=valuation_count,
        data_error_count=len(set(price_errors) | set(fundamental_errors)) + len(fx_errors),
        top_n=args.top,
        universe_source=source,
        min_market_cap_usd=args.min_market_cap_usd,
        max_overvaluation=args.max_overvaluation,
        preliminary_count=len(preliminary),
        price_series_count=len(prices),
        technical_metrics_count=len(technical),
        cache_only=args.cache_only,
    )
    diagnostics = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_count": len(members),
        "price_series_count": len(prices),
        "technical_metrics_count": len(technical),
        "technical_pass_count": len(technical_pass),
        "fundamental_metrics_count": len(companies),
        "fundamental_gate_cache_count": fundamental_gate_count,
        "quality_pass_count": len(quality_companies),
        "size_pass_count": size_count,
        "valuation_pass_count": valuation_count,
        "eligible_count": len(scored),
        "data_error_count": len(set(price_errors) | set(fundamental_errors))
        + len(fx_errors),
        "settings": {
            "min_market_cap_usd": args.min_market_cap_usd,
            "max_overvaluation": args.max_overvaluation,
            "min_valuation_methods": args.min_valuation_methods,
            "min_valuation_confirmations": args.min_valuation_confirmations,
            "dcf_discount_rate": args.dcf_discount_rate,
            "dcf_terminal_growth": args.dcf_terminal_growth,
            "technical_filter_version": "support_momentum_v3",
            "cache_only": args.cache_only,
        },
        "price_errors": price_errors,
        "fundamental_errors": fundamental_errors,
        "fx_errors": fx_errors,
    }
    (output_dir / "diagnostics.json").write_text(
        json.dumps(diagnostics, indent=2), encoding="utf-8"
    )
    if scored.empty:
        log("Nessun titolo supera tutti i filtri con i dati disponibili.")
    else:
        columns = [
            "rank",
            "symbol",
            "name",
            "total_score",
            "return_3m",
            "drawdown_3m",
            "support_level",
            "support_distance_atr",
            "support_kind",
            "roc5",
            "rsi14",
            "macd_histogram",
            "momentum_confirmations",
            "momentum_signal_age",
            "fcf_growth",
            "net_margin_ttm",
            "obv_slope_3d_z",
            "market_cap_usd",
            "consensus_fair_value",
            "upside_to_fair_value",
            "acceptable_methods",
        ]
        print(scored.head(args.top)[columns].to_string(index=False), flush=True)
    log(f"Report HTML: {reports['html']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "universe":
            return run_universe(args.output)
        return run_scan(args)
    except KeyboardInterrupt:
        log("Interrotto.")
        return 130
    except Exception as exc:
        print(f"Errore: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
