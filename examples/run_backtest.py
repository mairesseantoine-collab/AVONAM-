"""Exemple minimal d'utilisation du pipeline sans passer par la CLI, utile
pour expérimenter directement en Python (ex. dans un notebook).

Lancer : python examples/run_backtest.py
"""

from avonam.backtest.engine import BacktestEngine
from avonam.data.loader import load_csv
from avonam.journal.journal import TradeJournal, analyze_results
from avonam.risk.manager import RiskManager
from avonam.strategy.sma_crossover import SMACrossoverStrategy


def main() -> None:
    data = load_csv("data/sample/DEMO.csv")

    strategy = SMACrossoverStrategy(fast_period=20, slow_period=50)
    risk_manager = RiskManager(
        initial_capital=10_000,
        risk_per_trade_pct=1.0,
        stop_loss_pct=2.0,
        take_profit_pct=4.0,
        max_drawdown_pct=20.0,
    )
    engine = BacktestEngine(risk_manager, commission_pct=0.05, slippage_pct=0.05)

    result = engine.run(data, strategy)

    journal = TradeJournal(output_dir="output")
    journal.save_trades_csv(result.trades)
    analyze_results(result.equity_curve, result.metrics, journal)


if __name__ == "__main__":
    main()
