from avonam.backtest.engine import BacktestEngine
from avonam.risk.manager import RiskManager
from avonam.strategy.sma_crossover import SMACrossoverStrategy
from broker.kraken.market_data import fetch_ohlc_dataframe


def test_fetch_ohlc_dataframe_plugs_into_avonam_engine_unmodified(broker_stack):
    """Le point clé du module broker : les données de marché Kraken (via
    endpoint public, sans clé API) doivent être utilisables telles quelles
    par le moteur de trading avonam existant, sans aucune adaptation."""
    data = fetch_ohlc_dataframe("XBTEUR", interval_minutes=60, client=broker_stack.client)

    strategy = SMACrossoverStrategy(fast_period=5, slow_period=20)
    risk_manager = RiskManager(initial_capital=10_000)
    engine = BacktestEngine(risk_manager)

    result = engine.run(data, strategy)

    assert len(result.equity_curve) == len(data) - 1
    assert "total_return_pct" in result.metrics
