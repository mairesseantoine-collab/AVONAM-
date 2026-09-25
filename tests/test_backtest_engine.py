import numpy as np
import pandas as pd

from avonam.backtest.engine import BacktestEngine
from avonam.execution.paper import PaperTradingSession
from avonam.journal.journal import TradeJournal
from avonam.risk.manager import RiskManager
from avonam.strategy.base import Strategy
from avonam.strategy.sma_crossover import SMACrossoverStrategy


def _make_ohlcv(n=120, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    volume = np.full(n, 100_000)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


class AlwaysLongStrategy(Strategy):
    """Stratégie triviale pour des tests déterministes : toujours long dès
    qu'on a une barre d'historique."""

    def generate_signals(self, data):
        signal = pd.Series(1, index=data.index, dtype=int)
        if len(signal) > 0:
            signal.iloc[0] = 0
        return signal


def test_engine_opens_and_tracks_position():
    data = _make_ohlcv()
    risk_manager = RiskManager(initial_capital=10_000, risk_per_trade_pct=1.0, stop_loss_pct=50.0, take_profit_pct=50.0)
    engine = BacktestEngine(risk_manager, commission_pct=0.0, slippage_pct=0.0)

    result = engine.run(data, AlwaysLongStrategy())

    assert len(result.equity_curve) == len(data) - 1
    # Une position doit avoir été ouverte à un moment donné.
    assert engine.open_trade is not None or len(result.trades) > 0


def test_engine_never_exceeds_max_drawdown_by_opening_new_trades():
    data = _make_ohlcv(n=250, seed=7)
    risk_manager = RiskManager(
        initial_capital=10_000,
        risk_per_trade_pct=5.0,
        stop_loss_pct=1.0,
        take_profit_pct=100.0,
        max_drawdown_pct=10.0,
    )
    engine = BacktestEngine(risk_manager, commission_pct=0.1, slippage_pct=0.1)

    result = engine.run(data, SMACrossoverStrategy(fast_period=5, slow_period=15))

    if result.halted_at is not None:
        # Une fois le kill switch déclenché, plus aucune ouverture après.
        halted_idx = result.equity_curve.index.get_loc(result.halted_at)
        opens_after = [t for t in result.trades if t.entry_date > result.halted_at]
        assert opens_after == []


def test_metrics_present_in_result():
    data = _make_ohlcv()
    risk_manager = RiskManager(initial_capital=10_000)
    engine = BacktestEngine(risk_manager)

    result = engine.run(data, SMACrossoverStrategy(fast_period=5, slow_period=20))

    for key in ("total_return_pct", "max_drawdown_pct", "sharpe_ratio", "num_trades"):
        assert key in result.metrics


def test_paper_trading_matches_backtest_no_lookahead():
    """Le paper trading recalcule les signaux sur une fenêtre glissante,
    alors que le backtest les calcule d'un coup. S'il n'y a aucune fuite de
    données (look-ahead bias) dans la stratégie, les deux doivent donner
    exactement le même P&L final : c'est un bon test de non-régression pour
    détecter un biais caché si quelqu'un modifie la stratégie plus tard.
    """
    data = _make_ohlcv(n=150, seed=3)
    strategy_cfg = dict(fast_period=5, slow_period=20)

    risk_manager_bt = RiskManager(initial_capital=10_000)
    engine_bt = BacktestEngine(risk_manager_bt)
    result_bt = engine_bt.run(data, SMACrossoverStrategy(**strategy_cfg))

    risk_manager_paper = RiskManager(initial_capital=10_000)
    engine_paper = BacktestEngine(risk_manager_paper)
    journal = TradeJournal(output_dir="output/_test_paper")
    session = PaperTradingSession(engine_paper, journal)
    result_paper = session.run(data, SMACrossoverStrategy(**strategy_cfg))

    assert result_bt.equity_curve.iloc[-1] == result_paper.equity_curve.iloc[-1]
    assert len(result_bt.trades) == len(result_paper.trades)
