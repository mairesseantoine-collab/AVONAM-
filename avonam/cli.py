"""Interface en ligne de commande minimale.

Usage :
    python -m avonam.cli backtest --config config/strategy.example.yaml
    python -m avonam.cli paper --config config/strategy.example.yaml
"""

from __future__ import annotations

import argparse

import yaml

from avonam.backtest.engine import BacktestEngine
from avonam.data.loader import load_csv
from avonam.execution.paper import PaperTradingSession
from avonam.journal.journal import TradeJournal, analyze_results
from avonam.risk.manager import RiskManager
from avonam.strategy.sma_crossover import SMACrossoverStrategy

STRATEGIES = {
    "sma_crossover": SMACrossoverStrategy,
}


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_strategy(cfg: dict):
    strat_cfg = dict(cfg["strategy"])
    name = strat_cfg.pop("name")
    if name not in STRATEGIES:
        raise ValueError(f"Stratégie inconnue : {name}. Disponibles : {list(STRATEGIES)}")
    return STRATEGIES[name](**strat_cfg)


def build_risk_manager(cfg: dict) -> RiskManager:
    return RiskManager(**cfg["risk"])


def build_engine(cfg: dict, risk_manager: RiskManager) -> BacktestEngine:
    exec_cfg = cfg.get("execution", {})
    return BacktestEngine(
        risk_manager=risk_manager,
        commission_pct=exec_cfg.get("commission_pct", 0.05),
        slippage_pct=exec_cfg.get("slippage_pct", 0.05),
    )


def cmd_backtest(cfg: dict) -> None:
    data = load_csv(cfg["data"]["csv_path"])
    strategy = build_strategy(cfg)
    risk_manager = build_risk_manager(cfg)
    engine = build_engine(cfg, risk_manager)
    journal = TradeJournal(cfg.get("output", {}).get("dir", "output"))

    print(f"Backtest sur {len(data)} barres, capital initial = {risk_manager.initial_capital}")
    result = engine.run(data, strategy)

    for trade in result.trades:
        journal.log_trade_closed(trade)
    if result.halted_at is not None:
        journal.log_kill_switch(result.halted_at)

    trades_path = journal.save_trades_csv(result.trades)
    print(f"Journal des trades sauvé dans : {trades_path}")
    analyze_results(result.equity_curve, result.metrics, journal)


def cmd_paper(cfg: dict) -> None:
    data = load_csv(cfg["data"]["csv_path"])
    strategy = build_strategy(cfg)
    risk_manager = build_risk_manager(cfg)
    engine = build_engine(cfg, risk_manager)
    journal = TradeJournal(cfg.get("output", {}).get("dir", "output"))

    print(f"Paper trading sur {len(data)} barres, capital initial = {risk_manager.initial_capital}")
    session = PaperTradingSession(engine, journal)
    result = session.run(data, strategy)

    if result.halted_at is not None:
        journal.log_kill_switch(result.halted_at)

    trades_path = journal.save_trades_csv(result.trades)
    print(f"Journal des trades sauvé dans : {trades_path}")
    analyze_results(result.equity_curve, result.metrics, journal)


def main() -> None:
    parser = argparse.ArgumentParser(description="AVONAM — plateforme de trading algorithmique (simulation)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("backtest", "paper"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--config", required=True, help="Chemin vers le fichier YAML de configuration")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "backtest":
        cmd_backtest(cfg)
    elif args.command == "paper":
        cmd_paper(cfg)


if __name__ == "__main__":
    main()
