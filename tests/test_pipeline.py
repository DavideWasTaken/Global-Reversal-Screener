"""Offline end-to-end tests of the ``scan`` pipeline.

The sample-output generator in ``examples/`` runs the real CLI pipeline with an
in-memory data provider, so these tests need no network access.
"""

import importlib.util
import json
from pathlib import Path

import pandas as pd

from reversal_screener.cli import build_parser
from reversal_screener.metrics import calculate_valuation_metrics
from reversal_screener.universe import load_universe_csv

GENERATOR = Path(__file__).resolve().parents[1] / "examples" / "generate_sample_output.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_sample_output", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scan_reports_fewer_than_top_n_instead_of_loosening_filters(tmp_path):
    generator = _load_generator()
    assert generator.generate(tmp_path) == 0

    top = json.loads((tmp_path / "top5.json").read_text(encoding="utf-8"))
    assert [row["symbol"] for row in top] == ["DEMO1"]
    assert top[0]["rank"] == 1
    assert top[0]["acceptable_methods"] == 2

    diagnostics = json.loads((tmp_path / "diagnostics.json").read_text(encoding="utf-8"))
    assert diagnostics["universe_count"] == 6
    assert diagnostics["technical_pass_count"] == 4
    assert diagnostics["quality_pass_count"] == 3
    assert diagnostics["size_pass_count"] == 2
    assert diagnostics["valuation_pass_count"] == 1
    assert diagnostics["eligible_count"] == 1

    candidates = pd.read_csv(tmp_path / "all_candidates.csv")
    assert list(candidates["symbol"]) == ["DEMO1"]
    assert (tmp_path / "top5.html").exists()
    summary = (tmp_path / "scan_summary.md").read_text(encoding="utf-8")
    assert "| 1 | DEMO1 | Demo Reversal Corp |" in summary
    assert not (tmp_path / "preliminary.json").exists()


def test_scan_with_no_survivors_writes_an_explicit_empty_report(tmp_path):
    generator = _load_generator()
    falling_fcf = generator._company(
        generator.FALLING_FCF, generator.NET_INCOME, market_cap=130.0e9
    )
    companies = {f"DEMO{index}": falling_fcf for index in range(1, 7)}
    assert generator.generate(tmp_path, companies_override=companies) == 0

    assert json.loads((tmp_path / "top5.json").read_text(encoding="utf-8")) == []
    assert "No stock passes every filter" in (tmp_path / "scan_summary.md").read_text(
        encoding="utf-8"
    )
    assert "Thresholds are never loosened" in (tmp_path / "top5.html").read_text(
        encoding="utf-8"
    )


def test_valuation_needs_the_configured_number_of_confirmations():
    generator = _load_generator()
    company = generator._company(
        generator.RISING_FCF, generator.NET_INCOME, market_cap=130.0e9
    )

    def valuation(**kwargs):
        return calculate_valuation_metrics(
            158.37, company.fundamental, company.valuation_inputs, {"USD": 1.0}, **kwargs
        )

    default = valuation()
    assert default.valuation_methods == 3
    # DCF and EV/Sales confirm; Peter Lynch is far below the price.
    assert default.acceptable_methods == 2
    assert default.valuation_pass
    assert not valuation(min_confirmations=3).valuation_pass
    assert not valuation(min_methods=4).valuation_pass
    assert default.size_pass
    assert not valuation(min_market_cap_usd=200e9).size_pass


def test_custom_universe_csv_builds_yahoo_symbols_and_parses_weights(tmp_path):
    path = tmp_path / "universe.csv"
    path.write_text(
        "ticker,name,country,sector,weight_pct\n"
        "700,Tencent,HK,Technology,\"0,85\"\n"
        "BRK/B,Berkshire Hathaway,US,Financials,1.2%\n"
        "7203,Toyota,JP,Consumer Discretionary,\n",
        encoding="utf-8",
    )
    members = load_universe_csv(path)
    assert [member.symbol for member in members] == ["0700.HK", "BRK-B", "7203.T"]
    assert members[0].weight_pct == 0.85
    assert members[1].weight_pct == 1.2
    assert members[2].weight_pct is None


def test_cli_defaults_match_the_documented_gates():
    args = build_parser().parse_args(["scan"])
    assert args.output == "results"
    assert args.top == 5
    assert args.min_market_cap_usd == 10_000_000_000.0
    assert args.max_overvaluation == 0.15
    assert args.min_valuation_methods == 2
    assert args.min_valuation_confirmations == 2
    assert not args.cache_only
